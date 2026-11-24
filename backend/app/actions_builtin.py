"""Proveedores integrados del Centro de acciones: correcciones del carrier, órdenes con pérdida y listados.

Importado solo por `app.action_providers` (que carga los módulos al arrancar); no lo importa nadie más.
Cada proveedor es una capa fina sobre un reporte que YA existe (redo_analytics, order_profit, reports):
aquí solo se decide qué merece una acción y cuánto dinero está en juego. Los cálculos no se duplican.

Todos los importes son proyecciones a 30 días (`PROJECTION_DAYS`) para que las acciones de áreas distintas
se puedan comparar entre sí. Son estimaciones: cada acción explica en `impact_basis` cómo salió el número.

Datos de entrada: tablas de ventas, cargos de Redo y `sales.listing_stock` (solo lectura).
Salida: `list[ActionItem]` con claves estables "<kind>:<entidad>".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.action_center import ActionItem, action_provider
from app.gold import load_gold_frame
from app.order_profit import DRIVERS, order_profit_report
from app.profit import summarize
from app.redo_analytics import redo_corrections_by_sku
from app.reports import inventory_report

PROJECTION_DAYS = 30  # horizonte común de los importes
MAX_ACTIONS_PER_PROVIDER = 15

CORRECTIONS_WINDOW_DAYS = 90  # ventana de cargos del carrier
LOSS_WINDOW_DAYS = 90  # ventana de órdenes con pérdida
LOSS_ROWS_SCAN = 20_000  # tope de órdenes con pérdida que se leen para hallar el SKU más afectado
LISTING_SALES_WINDOW_DAYS = 60  # ventana para el ritmo de venta y la utilidad por unidad de un listado

# Esfuerzo típico de corregir cada causa de pérdida (cambiar un costo cuesta más que ajustar una promo).
LOSS_EFFORT: dict[str, str] = {
    "shipping": "medium",
    "fees": "high",
    "refunds": "medium",
    "cogs": "medium",
    "promo": "low",
}

LOSS_DETAIL_HINT: dict[str, str] = {
    "shipping": "Revisa tarifas, peso declarado y el servicio de envío.",
    "fees": "Revisa categoría y precio: la comisión supera el margen.",
    "refunds": "Revisa la calidad, la descripción y los motivos de reembolso.",
    "cogs": "Revisa el costo cargado o renegocia con el proveedor.",
    "promo": "Revisa los descuentos: se están comiendo el margen.",
}


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _today() -> date:
    return datetime.now(UTC).date()


def _top(items: list[ActionItem]) -> list[ActionItem]:
    """Las acciones de más impacto, hasta el tope por proveedor."""
    return sorted(items, key=lambda i: (-i.impact_usd, i.key))[:MAX_ACTIONS_PER_PROVIDER]


# ------------------------------------------------------------------ medidas mal declaradas
@action_provider("carrier_dimensions", "Medidas mal declaradas")
async def carrier_dimensions(session: AsyncSession) -> list[ActionItem]:
    """Un SKU marcado por `redo_corrections_by_sku` (muchas correcciones del carrier) = peso o medidas mal.

    Impacto: lo que ya costaron las correcciones de ese SKU en la ventana, llevado a 30 días. Si no se
    corrige la ficha, el cargo se repite al ritmo actual.
    """
    report = await redo_corrections_by_sku(session, days=CORRECTIONS_WINDOW_DAYS, today=_today())
    days = int(report["days"]) or CORRECTIONS_WINDOW_DAYS
    out: list[ActionItem] = []
    for row in report["skus"]:
        amount = float(row["correction_amount"])
        if not row["flag"] or amount <= 0:
            continue
        reasons = row["top_reasons"]
        main_reason = f" Motivo principal: {reasons[0]['reason']}." if reasons else ""
        out.append(
            ActionItem(
                key=f"carrier_dimensions:{row['sku']}",
                kind="carrier_dimensions",
                title=f"{row['sku']}: corrige peso y medidas declarados",
                detail=(
                    f"{row['orders_with_correction']} de {row['shipped_orders']} envíos con corrección "
                    f"({row['correction_rate']:.0%}), {_money(amount)} en {days} días.{main_reason} "
                    f"{row['suggestion'] or ''}"
                ).strip(),
                impact_usd=amount * PROJECTION_DAYS / days,
                impact_basis=(
                    f"Correcciones del carrier de este SKU en {days} días ({_money(amount)}) "
                    f"proyectadas a {PROJECTION_DAYS} días."
                ),
                effort="low",
                target="redoCorrections",
                entity=row["sku"],
                meta={"orders": row["shipped_orders"], "rate": row["correction_rate"]},
            )
        )
    return _top(out)


# ------------------------------------------------------------------ órdenes con pérdida
def _worst_skus(rows: list[dict[str, Any]]) -> dict[str, tuple[str, float]]:
    """Por causa: (SKU, pérdida) del SKU más afectado. La pérdida de una orden se reparte entre sus SKU."""
    losses: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        driver, skus = r.get("driver"), r.get("skus") or []
        if not driver or not skus or r["profit"] >= 0:
            continue
        share = -float(r["profit"]) / len(skus)
        for sku in skus:
            losses[driver][sku] += share
    return {d: max(by_sku.items(), key=lambda kv: (kv[1], kv[0])) for d, by_sku in losses.items()}


@action_provider("loss_orders", "Órdenes con pérdida")
async def loss_orders(session: AsyncSession) -> list[ActionItem]:
    """Una acción por causa principal de pérdida (envío, comisiones, reembolsos, costo, promoción).

    Impacto: la pérdida TOTAL de esa causa en la ventana, tal cual (no se proyecta: ya es un acumulado
    real de órdenes que perdieron dinero). Se agrupa por causa y no por orden para no inundar la lista.
    """
    today = _today()
    report = await order_profit_report(
        session,
        start=today - timedelta(days=LOSS_WINDOW_DAYS),
        end=today,
        only_loss=True,
        limit=LOSS_ROWS_SCAN,
    )
    worst = _worst_skus(report["rows"])
    out: list[ActionItem] = []
    for d in report["by_driver"]:
        amount = float(d["amount"])
        if amount <= 0 or d["key"] not in DRIVERS:
            continue
        key = d["key"]
        sku_text = ""
        if key in worst:
            sku, sku_loss = worst[key]
            sku_text = f" SKU más afectado: {sku} ({_money(sku_loss)})."
        out.append(
            ActionItem(
                key=f"loss_orders:{key}",
                kind="loss_orders",
                title=f"{d['orders']} órdenes pierden dinero por {d['label'].lower()}",
                detail=f"{_money(amount)} perdidos en {LOSS_WINDOW_DAYS} días.{sku_text} {LOSS_DETAIL_HINT[key]}",
                impact_usd=amount,
                impact_basis=(
                    f"Suma de la pérdida de las órdenes cuya causa principal es «{d['label']}» "
                    f"en los últimos {LOSS_WINDOW_DAYS} días."
                ),
                effort=LOSS_EFFORT[key],  # type: ignore[arg-type]
                target="orderProfit",
                entity=key,
                meta={"orders": d["orders"], "top_sku": worst[key][0] if key in worst else None},
            )
        )
    return _top(out)


# ------------------------------------------------------------------ listados despublicados
async def _unit_profit_by_sku(session: AsyncSession) -> dict[str, float]:
    """Utilidad por unidad de cada SKU en la ventana, con el mismo cálculo que el resto de la app."""
    today = _today()
    df = await load_gold_frame(session, start=today - timedelta(days=LISTING_SALES_WINDOW_DAYS), end=today)
    if not df.height:
        return {}
    summary = summarize(df, ["sku"]).filter(pl.col("unit_profit").is_not_null())
    return {r["sku"]: float(r["unit_profit"]) for r in summary.select("sku", "unit_profit").to_dicts()}


@action_provider("unpublished_listings", "Listados despublicados")
async def unpublished_listings(session: AsyncSession) -> list[ActionItem]:
    """Listados no publicados que se vendían: cada día sin publicar es venta que no entra.

    Impacto: unidades/día de la ventana x 30 x utilidad por unidad del SKU. Sin ventas históricas o sin
    utilidad positiva el impacto es 0 y se omite (no hay dinero que recuperar al republicarlo).
    """
    inventory = await inventory_report(session, days=LISTING_SALES_WINDOW_DAYS, today=_today())
    candidates = [r for r in inventory["rows"] if r["status"] == "unpublished" and r["daily_units"] > 0]
    if not candidates:
        return []
    unit_profit = await _unit_profit_by_sku(session)
    out: list[ActionItem] = []
    for r in candidates:
        profit = unit_profit.get(r["sku"])
        if profit is None or profit <= 0:
            continue
        daily = float(r["daily_units"])
        impact = daily * PROJECTION_DAYS * profit
        reason = f" Motivo: {r['unpublished_reasons']}." if r["unpublished_reasons"] else ""
        out.append(
            ActionItem(
                key=f"unpublished_listings:{r['account']}:{r['sku']}",
                kind="unpublished_listings",
                title=f"{r['sku']} está despublicado en {r['account']}",
                detail=(
                    f"Estado {r['published_status'] or 'desconocido'}; se vendían ~{daily:.1f} unidades al día."
                    f"{reason} Corrígelo en el Seller Center."
                ),
                impact_usd=impact,
                impact_basis=(
                    f"{daily:.1f} unidades/día (últimos {LISTING_SALES_WINDOW_DAYS} días) x {PROJECTION_DAYS} días "
                    f"x {_money(profit)} de utilidad por unidad."
                ),
                effort="low",
                target="inventory",
                entity=f"{r['account']}:{r['sku']}",
                meta={"account": r["account"], "avail_to_sell": r["avail_to_sell"]},
            )
        )
    return _top(out)
