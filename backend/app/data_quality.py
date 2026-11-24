"""Calidad de datos: revisiones que detectan información que falta o está mal unida y distorsiona la utilidad.

Cada revisión devuelve un `check` con severidad (critical | warning | info | ok), cuántos elementos
afecta, el dinero afectado (si aplica) y la pantalla donde se corrige. Reutiliza las auditorías de
conciliación y el estado de sincronización para no duplicar reglas.

Puntuación (0-100): parte de 100 y resta una penalización fija por cada revisión con problemas, según
su severidad: crítica 20, advertencia 8, informativa 2. El resultado se acota a [0, 100]. Es
deliberadamente simple y explicable: arreglar una revisión siempre sube la puntuación.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gold import UNASSIGNED_SKU
from app.models import (
    ChannelListing,
    ListingStock,
    OrderLine,
    Product,
    ProductCostHistory,
    ReturnCaseLine,
    SalesOrder,
    SkuAlias,
)
from app.reconciliation import fee_audit, label_audit, settlement_audit
from app.sku_manager import duplicate_groups, product_sales
from app.sync_jobs import sync_status

SALES_DAYS = 90  # ventana para decidir qué productos "se venden"
ORDER_DAYS = 30  # ventana para revisar el estado de destino de las órdenes
PENALTY = {"critical": 20, "warning": 8, "info": 2}
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
SAMPLE = 5  # cuántos ejemplos se nombran en el detalle


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _names(items: list[str]) -> str:
    shown = ", ".join(items[:SAMPLE])
    return shown + (f" y {len(items) - SAMPLE} más" if len(items) > SAMPLE else "")


def _check(
    check_id: str,
    title: str,
    severity: str,
    count: int,
    detail: str,
    page: str,
    label: str,
    impact: float | None = None,
    ok_detail: str = "Sin problemas.",
    skus: list[str] | None = None,
) -> dict[str, Any]:
    """Arma una revisión; con `count == 0` queda en `ok` sin importar la severidad indicada."""
    healthy = count == 0
    return {
        "id": check_id,
        "title": title,
        "severity": "ok" if healthy else severity,
        "count": count,
        "impact": None if healthy else impact,
        "detail": ok_detail if healthy else detail,
        "action": {"page": page, "label": label},
        "skus": [] if healthy else (skus or [])[:50],
    }


def score_of(checks: list[dict[str, Any]]) -> int:
    """Puntuación 0-100 (ver el docstring del módulo)."""
    return max(0, 100 - sum(PENALTY.get(c["severity"], 0) for c in checks))


async def _known_skus(session: AsyncSession) -> set[str]:
    """SKU que el sistema reconoce: productos y alias."""
    skus = {s for (s,) in (await session.execute(select(Product.sku))).all()}
    skus |= {a for (a,) in (await session.execute(select(SkuAlias.alias))).all()}
    return skus


async def _missing_cost(session: AsyncSession, today: date) -> dict[str, Any]:
    sales = await product_sales(session, today - timedelta(days=SALES_DAYS))
    with_cost = {pid for (pid,) in (await session.execute(select(ProductCostHistory.product_id).distinct())).all()}
    skus = dict((await session.execute(select(Product.id, Product.sku))).all())
    missing = sorted(
        ((skus[pid], s.sales) for pid, s in sales.items() if pid not in with_cost and pid in skus and s.units > 0),
        key=lambda x: -x[1],
    )
    return _check(
        "missing_cost",
        "Productos vendidos sin costo",
        "critical",
        len(missing),
        f"Se vendieron en los últimos {SALES_DAYS} días y no tienen COGS, así que su utilidad sale inflada: "
        + _names([sku for sku, _ in missing]),
        "costs",
        "Cargar costos",
        impact=sum(v for _, v in missing),
        ok_detail="Todos los productos vendidos tienen costo.",
        skus=[sku for sku, _ in missing],
    )


async def _duplicates(session: AsyncSession, today: date) -> dict[str, Any]:
    report = await duplicate_groups(session, SALES_DAYS, today)
    groups = report["groups"]
    return _check(
        "duplicate_skus",
        "SKU duplicados",
        "warning",
        report["count"],
        "Varios SKU parecen ser el mismo producto y reparten sus ventas y costos: "
        + _names([" / ".join(m["sku"] for m in g["members"]) for g in groups]),
        "skus",
        "Revisar duplicados",
        impact=sum(g["total_sales"] for g in groups),
        ok_detail="No hay SKU que parezcan duplicados.",
        skus=[m["sku"] for g in groups for m in g["members"]],
    )


async def _unassigned_lines(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            select(func.count(OrderLine.id), func.coalesce(func.sum(OrderLine.gross_sales), 0))
            .join(Product, OrderLine.product_id == Product.id)
            .where(or_(Product.sku == UNASSIGNED_SKU, func.trim(OrderLine.channel_sku) == ""))
        )
    ).one()
    return _check(
        "unassigned_lines",
        "Ventas con SKU sin asignar",
        "warning",
        int(row[0]),
        "Hay líneas de venta sin SKU: no se les puede asignar costo ni analizar por producto.",
        "import",
        "Reimportar con SKU",
        impact=_f(row[1]),
        ok_detail="Todas las líneas de venta tienen SKU.",
    )


async def _nameless(session: AsyncSession) -> dict[str, Any]:
    skus = [
        s
        for (s,) in (
            await session.execute(
                select(Product.sku).where(
                    or_(Product.name.is_(None), func.trim(Product.name) == ""), Product.sku != UNASSIGNED_SKU
                )
            )
        ).all()
    ]
    return _check(
        "nameless_products",
        "Productos sin nombre",
        "info",
        len(skus),
        "Se muestran solo con su SKU en los reportes: " + _names(sorted(skus)),
        "import",
        "Importar catálogo",
        ok_detail="Todos los productos tienen nombre.",
        skus=sorted(skus),
    )


async def _labels(session: AsyncSession) -> dict[str, Any]:
    audit = await label_audit(session)
    return _check(
        "unassigned_labels",
        "Etiquetas de Redo sin orden",
        "warning",
        audit["unmatched_count"],
        "Cargos de etiquetas que no coinciden con ninguna orden de venta ni muestra: dinero que sale sin explicación.",
        "reconciliation",
        "Conciliar etiquetas",
        impact=audit["unmatched_total"],
        ok_detail="Todas las etiquetas se unen a una orden.",
    )


async def _zero_fees(session: AsyncSession, today: date) -> dict[str, Any]:
    audit = await fee_audit(session, today)
    return _check(
        "zero_fee_lines",
        "Líneas con comisión de $0",
        "warning",
        audit["zero_fee_count"],
        "Ventas con comisión cero en las últimas semanas: lo más probable es que falte el dato y la utilidad salga alta.",
        "reconciliation",
        "Revisar comisiones",
        ok_detail="Ninguna línea reciente tiene comisión cero.",
    )


async def _unknown_stock_skus(session: AsyncSession) -> dict[str, Any]:
    known = await _known_skus(session)
    known |= {c for (c,) in (await session.execute(select(ChannelListing.channel_sku))).all()}
    unknown = sorted({s for (s,) in (await session.execute(select(ListingStock.sku))).all() if s not in known})
    return _check(
        "unknown_stock_skus",
        "Inventario con SKU desconocido",
        "warning",
        len(unknown),
        "Hay existencias de SKU que no son producto ni alias (no se pueden costear ni reponer): "
        + _names(unknown),
        "inventory",
        "Ver inventario",
        ok_detail="Todos los SKU del inventario son productos o alias.",
        skus=unknown,
    )


async def _missing_state(session: AsyncSession, today: date) -> dict[str, Any]:
    since = today - timedelta(days=ORDER_DAYS)
    total, missing = (
        await session.execute(
            select(
                func.count(SalesOrder.id),
                func.coalesce(func.sum(case((SalesOrder.ship_state.is_(None), 1), else_=0)), 0),
            ).where(SalesOrder.order_date >= since)
        )
    ).one()
    total, missing = int(total), int(missing)
    return _check(
        "orders_without_state",
        "Órdenes sin estado de destino",
        "warning" if total and missing / total > 0.5 else "info",
        missing,
        f"{missing} de {total} órdenes de los últimos {ORDER_DAYS} días no traen el estado de destino: "
        "el análisis de impuestos y envío por estado queda incompleto.",
        "import",
        "Reimportar órdenes",
        ok_detail="Todas las órdenes recientes traen estado de destino.",
    )


async def _settlements(session: AsyncSession, today: date) -> dict[str, Any]:
    audit = await settlement_audit(session, today)
    failed, stuck = audit["failed_count"], audit["stuck_count"]
    parts = []
    if failed:
        parts.append(f"{failed} pago(s) fallido(s)")
    if stuck:
        parts.append(f"{stuck} estado(s) de cuenta sin depositar tras {audit['stuck_days']} días")
    return _check(
        "settlements",
        "Estados de cuenta fallidos o atorados",
        "critical" if failed else "warning",
        failed + stuck,
        "Dinero que la plataforma no ha depositado: " + " y ".join(parts) + ".",
        "reconciliation",
        "Revisar pagos",
        impact=audit["failed_total"] + audit["stuck_total"],
        ok_detail="Los estados de cuenta están al corriente.",
    )


async def _syncs(session: AsyncSession) -> dict[str, Any]:
    status = await sync_status(session)
    failed = [s["label"] for s in status["sources"] if s["configured"] and s["status"] in ("failed", "partial")]
    overdue = [s["label"] for s in status["sources"] if s["configured"] and s["overdue"] and s["label"] not in failed]
    parts = []
    if failed:
        parts.append("falló la última sincronización de " + _names(failed))
    if overdue:
        parts.append("está atrasada la de " + _names(overdue))
    return _check(
        "syncs",
        "Sincronizaciones atrasadas o fallidas",
        "critical" if failed else "warning",
        len(failed) + len(overdue),
        "Los datos pueden estar desactualizados: " + " y ".join(parts) + ".",
        "alerts",
        "Ver alertas",
        ok_detail="Las sincronizaciones configuradas están al día.",
    )


async def _return_skus(session: AsyncSession) -> dict[str, Any]:
    known = await _known_skus(session)
    rows = (
        await session.execute(
            select(ReturnCaseLine.sku, func.coalesce(func.sum(ReturnCaseLine.refund_total), 0)).group_by(
                ReturnCaseLine.sku
            )
        )
    ).all()
    unknown = [(sku, _f(total)) for sku, total in rows if sku not in known]
    return _check(
        "return_unknown_skus",
        "Devoluciones con SKU desconocido",
        "warning",
        len(unknown),
        "Devoluciones de SKU que no son producto ni alias, así que no se pueden atribuir a un producto: "
        + _names(sorted(sku for sku, _ in unknown)),
        "skus",
        "Crear alias",
        impact=sum(total for _, total in unknown),
        ok_detail="Todas las devoluciones apuntan a un producto conocido.",
        skus=sorted(sku for sku, _ in unknown),
    )


async def data_quality_report(session: AsyncSession, today: date | None = None) -> dict[str, Any]:
    """Puntuación y lista de revisiones (primero las más graves, luego las de mayor impacto)."""
    today = today or datetime.now(UTC).date()
    checks = [
        await _missing_cost(session, today),
        await _duplicates(session, today),
        await _unassigned_lines(session),
        await _nameless(session),
        await _labels(session),
        await _zero_fees(session, today),
        await _unknown_stock_skus(session),
        await _missing_state(session, today),
        await _settlements(session, today),
        await _syncs(session),
        await _return_skus(session),
    ]
    checks.sort(key=lambda c: (SEVERITY_ORDER[c["severity"]], -(c["impact"] or 0)))
    counts = {sev: sum(1 for c in checks if c["severity"] == sev) for sev in SEVERITY_ORDER}
    score = score_of(checks)
    problems = len(checks) - counts["ok"]
    return {
        "score": score,
        "summary": {
            **counts,
            "total": len(checks),
            "headline": "Tus datos están en orden."
            if not problems
            else f"{problems} de {len(checks)} revisiones necesitan atención.",
        },
        "checks": checks,
    }
