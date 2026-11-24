"""Proveedores del Centro de acciones para el apartado Walmart.

Importado solo por `app.action_providers` (que lo carga al arrancar). Cada proveedor mira los datos que
dejó la sincronización `walmart_insights` (o, para los pedidos, Walmart en vivo con caché de 60 s) y
devuelve acciones concretas. Todas apuntan a la pantalla "walmart". Solo lectura.

Criterio de impacto (el Centro oculta las acciones con impacto 0, así que cada proveedor lo define):
  * pedidos: ventas de los pedidos vencidos / por vencer (dinero en riesgo real).
  * calidad: no hay dólares directos. Se usa un indicador: ventas de 30 días de Walmart x brecha bajo el
    umbral / 100 x EXPOSURE_FACTOR (supuesto conservador: 10 % de lo expuesto sufre menos visibilidad),
    con piso MIN_IMPACT_USD para que una cuenta sin ventas igual vea la acción. No es un pronóstico.
  * feeds: ítems fallidos x precio unitario promedio de Walmart en 30 días (cambios que no entraron).
  * repricer: unidades vendidas en 30 días x (precio mínimo real - precio mínimo del repricer / realizado).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.action_center import ActionItem, action_provider
from app.connectors.walmart import WalmartClient
from app.walmart_analytics import (
    FEED_FAILURE_WINDOW_DAYS,
    OPEN_ORDERS_CACHE,
    QUALITY_LABELS,
    WalmartSales,
    latest_listing_quality,
    open_orders,
    recent_feeds,
    repricer_report,
    walmart_sales,
)

TARGET = "walmart"
SALES_DAYS = 30
QUALITY_THRESHOLD = 70.0  # un componente bajo este puntaje (0-100) merece atención
EXPOSURE_FACTOR = 0.10
MIN_IMPACT_USD = 1.0
MAX_ACTIONS = 15
FAILED_STATUSES = {"ERROR", "FAILED"}


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _client() -> WalmartClient | None:
    """Cliente de Walmart, o None si no hay credenciales (el proveedor entonces no aporta acciones)."""
    try:
        return WalmartClient()
    except ValidationError:
        return None


@action_provider("walmart_orders_due", "Pedidos de Walmart por vencer")
async def orders_due_actions(_session: AsyncSession) -> list[ActionItem]:
    """Pedidos por surtir ya vencidos o que vencen en 24 h. Impacto: ventas de esos pedidos."""
    client = _client()
    if client is None:
        return []
    now = datetime.now(UTC)
    view = await open_orders(client, now=now, cache=OPEN_ORDERS_CACHE)
    summary = view["summary"]
    items: list[ActionItem] = []
    groups = (
        ("overdue", "vencidos", summary["overdue"], summary["sales_overdue"], "Ya pasó el plazo de envío"),
        ("due_soon", "por vencer en 24 h", summary["due_soon"], summary["sales_due_soon"], "Vencen en menos de 24 h"),
    )
    for flag, label, count, sales, lead in groups:
        if not count:
            continue
        orders = [o for o in view["orders"] if o[flag]]
        worst = orders[0]
        items.append(
            ActionItem(
                key=f"walmart_orders_due:{flag}",
                kind="walmart_orders_due",
                title=f"{count} pedido(s) de Walmart {label}",
                detail=(
                    f"{lead}. El más urgente: {worst['order_id']} ({worst['hours_left']} h). "
                    "Surtir tarde baja el indicador OTD del vendedor."
                ),
                impact_usd=float(sales),
                impact_basis="Ventas de los pedidos afectados (precio de producto, sin impuesto).",
                effort="low",
                target=TARGET,
                entity=flag,
                meta={"orders": [o["order_id"] for o in orders[:MAX_ACTIONS]], "count": count},
            )
        )
    return items


@action_provider("walmart_listing_quality", "Calidad de listados de Walmart")
async def listing_quality_actions(session: AsyncSession) -> list[ActionItem]:
    """Un componente de calidad bajo el umbral. Impacto: indicador de exposición (ver el docstring del módulo)."""
    latest = await latest_listing_quality(session)
    if latest is None:
        return []
    sales = await walmart_sales(session, SALES_DAYS)
    items: list[ActionItem] = []
    for key, label in QUALITY_LABELS.items():
        score = getattr(latest, key)
        if score is None or float(score) >= QUALITY_THRESHOLD:
            continue
        gap = QUALITY_THRESHOLD - float(score)
        impact = max(sales.gross * (gap / 100) * EXPOSURE_FACTOR, MIN_IMPACT_USD)
        items.append(
            ActionItem(
                key=f"walmart_listing_quality:{key}",
                kind="walmart_listing_quality",
                title=f"Calidad de listados: {label} en {float(score):.0f}/100",
                detail=(
                    f"El componente está {gap:.0f} puntos bajo el umbral de {QUALITY_THRESHOLD:.0f}. "
                    f"Puntaje total {float(latest.listing_quality or 0):.0f}. Mejorarlo sube la visibilidad en búsqueda."
                ),
                impact_usd=round(impact, 2),
                impact_basis=(
                    f"Indicador, no pronóstico: ventas de {SALES_DAYS} días x brecha/100 x {EXPOSURE_FACTOR:.0%}, "
                    f"mínimo {_money(MIN_IMPACT_USD)}."
                ),
                effort="medium",
                target=TARGET,
                entity=key,
                meta={"score": float(score), "gap": round(gap, 2), "threshold": QUALITY_THRESHOLD},
            )
        )
    return items


def _unit_price(sales: WalmartSales) -> float:
    return sales.avg_unit_price or 0.0


@action_provider("walmart_feed_failures", "Cargas de Walmart con fallos")
async def feed_failure_actions(session: AsyncSession) -> list[ActionItem]:
    """Una acción por tipo de carga con ítems fallidos en la última semana. Impacto: fallidos x precio unitario."""
    feeds = [
        f
        for f in await recent_feeds(session, FEED_FAILURE_WINDOW_DAYS)
        if f.items_failed > 0 or (f.status or "").upper() in FAILED_STATUSES
    ]
    if not feeds:
        return []
    price = _unit_price(await walmart_sales(session, SALES_DAYS))
    by_type: dict[str, list[Any]] = {}
    for f in feeds:
        by_type.setdefault(f.feed_type or "UNKNOWN", []).append(f)
    items: list[ActionItem] = []
    for feed_type, group in by_type.items():
        failed = sum(f.items_failed for f in group)
        items.append(
            ActionItem(
                key=f"walmart_feed_failures:{feed_type}",
                kind="walmart_feed_failures",
                title=f"Carga {feed_type} con {failed} ítem(s) fallido(s)",
                detail=(
                    f"{len(group)} carga(s) en {FEED_FAILURE_WINDOW_DAYS} días no aplicaron todos sus cambios. "
                    "Revisa el detalle de errores en Seller Center y vuelve a enviarlos."
                ),
                impact_usd=round(max(failed * price, MIN_IMPACT_USD), 2),
                impact_basis=(
                    f"Ítems fallidos x precio unitario promedio de Walmart ({_money(price)}); mínimo {_money(MIN_IMPACT_USD)}."
                ),
                effort="low",
                target=TARGET,
                entity=feed_type,
                meta={"feeds": len(group), "items_failed": failed},
            )
        )
    return items


@action_provider("walmart_repricer_floor", "Repricer de Walmart bajo el precio mínimo")
async def repricer_floor_actions(session: AsyncSession) -> list[ActionItem]:
    """SKU asignados al repricer cuyo piso (o precio realizado) queda bajo el mínimo real con envío de Redo.

    Solo si el vínculo SKU -> estrategia existe (reporte ITEM) y el SKU tiene precio mínimo calculable.
    Impacto: unidades de 30 días x brecha por unidad.
    """
    report = await repricer_report(session)
    if not report["link"]["available"]:
        return []
    sales = await walmart_sales(session, SALES_DAYS)
    items: list[ActionItem] = []
    for row in report["assigned_skus"]:
        issue = row["issue"]
        if not issue or issue["gap"] is None or issue["gap"] <= 0:
            continue
        units = sales.units_by_sku.get(row["sku"], 0)
        what = (
            "se vende por debajo de su mínimo real"
            if issue["type"] == "realized_below_floor"
            else f"su piso en el repricer ({_money(row['repricer_min_price'])}) queda bajo el mínimo real"
        )
        items.append(
            ActionItem(
                key=f"walmart_repricer_floor:{row['sku']}",
                kind="walmart_repricer_floor",
                title=f"{row['sku']}: {what}",
                detail=(
                    f"Estrategia: {row['strategy']}. Mínimo real {_money(row['real_min_price'])} "
                    f"(con envío real de Redo); faltan {_money(issue['gap'])} por unidad. "
                    "Sube el precio mínimo permitido en el repricer."
                ),
                impact_usd=round(units * issue["gap"], 2),
                impact_basis=f"Unidades de {SALES_DAYS} días ({units}) x brecha por unidad.",
                effort="low",
                target=TARGET,
                entity=row["sku"],
                meta={"issue": issue["type"], "units_30d": units, "gap": issue["gap"]},
            )
        )
    return items[:MAX_ACTIONS]
