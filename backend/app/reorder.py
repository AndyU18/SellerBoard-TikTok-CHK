"""Reposición: cuándo pedir y cuánto, y cuánto dinero tienes parado en inventario.

Para cada SKU publicado (existencias de `listing_stock` contra su ritmo de venta):

    demanda diaria   = unidades vendidas en la ventana / días  (x multiplicador de temporada)
    punto de reorden = demanda diaria x (tiempo de entrega + colchón de seguridad)
    cantidad a pedir = demanda diaria x (entrega + colchón + días a cubrir) - existencias

El tiempo de entrega no está en los datos de los contenedores (solo traen la fecha de recepción),
así que es un parámetro. Tampoco hay pedidos en tránsito: si los hay, réstalos de la cantidad.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import load_cost_history
from app.models import Channel, Product
from app.reports import inventory_report

DEFAULT_LEAD_TIME_DAYS = 60
DEFAULT_SAFETY_DAYS = 14
DEFAULT_COVER_DAYS = 60
OVERSTOCK_DAYS = 180  # más de esto de inventario se considera exceso
STATUS_ORDER = ("late", "order_now", "ok", "overstock", "dead", "no_demand")


def classify(
    avail: int, daily: float, lead: int, safety: int, units_sold: int, overstock_days: int = OVERSTOCK_DAYS
) -> str:
    """late: se agota antes de que llegue un pedido hecho hoy; order_now: ya toca pedir."""
    if daily <= 0:
        return "dead" if avail > 0 and units_sold == 0 else "no_demand"
    cover = avail / daily
    if cover < lead:
        return "late"
    if cover <= lead + safety:
        return "order_now"
    if cover > overstock_days:
        return "overstock"
    return "ok"


def plan_row(
    row: dict[str, Any],
    *,
    unit_cost: float,
    lead: int,
    safety: int,
    cover: int,
    multiplier: float,
    window_days: int,
    today: date,
) -> dict[str, Any]:
    avail = row["avail_to_sell"] or 0
    daily = row["units_sold"] / window_days * multiplier
    status = classify(avail, daily, lead, safety, row["units_sold"])
    target_stock = daily * (lead + safety + cover)
    order_qty = max(0, math.ceil(target_stock - avail)) if daily > 0 else 0
    days_left = avail / daily if daily > 0 else None
    stock_value = avail * unit_cost
    excess_units = max(0.0, avail - target_stock) if daily > 0 else float(avail)
    return {
        "account": row["account"],
        "channel": row["channel"],
        "sku": row["sku"],
        "product_name": row["product_name"],
        "published_status": row["published_status"],
        "avail_to_sell": avail,
        "daily_units": daily,
        "days_of_cover": days_left,
        "reorder_point": math.ceil(daily * (lead + safety)),
        "order_qty": order_qty,
        "order_by": (today + timedelta(days=int(days_left - lead))).isoformat() if days_left is not None else None,
        "days_late": max(0, math.ceil(lead - days_left)) if (days_left is not None and status == "late") else 0,
        "unit_cost": unit_cost,
        "has_cost": unit_cost > 0,
        "stock_value": stock_value,
        "order_cost": order_qty * unit_cost,
        "excess_value": excess_units * unit_cost if status in ("overstock", "dead") else 0.0,
        "status": status,
    }


async def reorder_report(
    session: AsyncSession,
    *,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    safety_days: int = DEFAULT_SAFETY_DAYS,
    cover_days: int = DEFAULT_COVER_DAYS,
    demand_multiplier: float = 1.0,
    days: int = 30,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    inventory = await inventory_report(session, channel, account, days=days, today=today)
    costs = await load_cost_history(session)
    product_ids = {sku: pid for pid, sku in (await session.execute(select(Product.id, Product.sku))).all()}

    rows = []
    for r in inventory["rows"]:
        pid = product_ids.get(r["sku"])
        unit_cost = costs.unit_cost(pid, today) if pid is not None else 0.0
        rows.append(
            plan_row(
                r,
                unit_cost=float(unit_cost),
                lead=lead_time_days,
                safety=safety_days,
                cover=cover_days,
                multiplier=demand_multiplier,
                window_days=days,
                today=today,
            )
        )
    rows.sort(
        key=lambda r: (
            STATUS_ORDER.index(r["status"]),
            r["days_of_cover"] if r["days_of_cover"] is not None else 1e9,
            -r["stock_value"],
        )
    )
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_ORDER}
    to_order = [r for r in rows if r["status"] in ("late", "order_now") and r["order_qty"] > 0]
    return {
        "params": {
            "lead_time_days": lead_time_days,
            "safety_days": safety_days,
            "cover_days": cover_days,
            "demand_multiplier": demand_multiplier,
            "window_days": days,
        },
        "summary": {
            "skus": len(rows),
            **counts,
            "units_to_order": sum(r["order_qty"] for r in to_order),
            "order_cost": sum(r["order_cost"] for r in to_order),
            "capital_in_stock": sum(r["stock_value"] for r in rows),
            "capital_excess": sum(r["excess_value"] for r in rows),
            "capital_dead": sum(r["stock_value"] for r in rows if r["status"] == "dead"),
            "without_cost": sum(1 for r in rows if not r["has_cost"] and r["avail_to_sell"]),
        },
        "rows": rows,
    }
