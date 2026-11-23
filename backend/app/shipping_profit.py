"""Rentabilidad del envío: cuánto del precio se va en la etiqueta, por estado de destino y por SKU.

El costo de envío de una línea es el que ya trae la línea más la parte que le toca de la etiqueta de su
orden (según sus ventas, igual que en la capa oro). La contribución es lo que queda de las ventas
netas después de comisiones, envío, reembolsos y costo del producto.

El estado de destino solo existe en las órdenes sincronizadas después de activar esta función
(Walmart y TikTok Shop lo informan); en las demás aparece como "(sin dato)".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import load_cost_history
from app.models import (
    Account,
    Channel,
    OrderLine,
    Product,
    SalesOrder,
    ShippingLabelCost,
)

SHIPPING_SHARE_ALERT = 0.20  # envío como fracción del precio cobrado desde la que se avisa
MIN_UNITS = 3
NO_STATE = "(sin dato)"


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


async def shipping_profitability_report(
    session: AsyncSession,
    *,
    days: int = 90,
    threshold: float = SHIPPING_SHARE_ALERT,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)

    query = (
        select(
            OrderLine.order_id,
            SalesOrder.external_order_id,
            SalesOrder.ship_state,
            Account.channel_code,
            Product.id,
            Product.sku,
            Product.name,
            OrderLine.order_date,
            OrderLine.units,
            OrderLine.gross_sales,
            OrderLine.promo,
            OrderLine.platform_fees,
            OrderLine.shipping_cost,
            OrderLine.refund_amount,
        )
        .join(SalesOrder, OrderLine.order_id == SalesOrder.id)
        .join(Account, OrderLine.account_id == Account.id)
        .join(Product, OrderLine.product_id == Product.id)
        .where(OrderLine.order_date >= start, OrderLine.order_date <= today)
    )
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)
    fetched = (await session.execute(query)).all()

    labels: dict[str, float] = defaultdict(float)
    for oid, amount in (
        await session.execute(select(ShippingLabelCost.external_order_id, ShippingLabelCost.amount))
    ).all():
        labels[oid] += _f(amount)
    costs = await load_cost_history(session)

    order_gross: dict[int, float] = defaultdict(float)
    order_units: dict[int, int] = defaultdict(int)
    for row in fetched:
        order_gross[row[0]] += _f(row[9])
        order_units[row[0]] += row[8]

    def blank() -> dict[str, Any]:
        return {
            "orders": set(),
            "units": 0,
            "gross": 0.0,
            "promo": 0.0,
            "fees": 0.0,
            "shipping": 0.0,
            "refunds": 0.0,
            "cogs": 0.0,
        }

    states: dict[str, dict[str, Any]] = defaultdict(blank)
    skus: dict[str, dict[str, Any]] = defaultdict(blank)
    for order_id, ext_id, state, _ch, pid, sku, name, day, units, gross, promo, fees, ship, refund in fetched:
        label = labels.get(ext_id, 0.0)
        if label:
            total = order_gross[order_id]
            label *= _f(gross) / total if total else units / (order_units[order_id] or 1)
        shipping = _f(ship) + label
        cogs = units * float(costs.unit_cost(pid, day))
        for bucket in (states[state or NO_STATE], skus[sku]):
            bucket["orders"].add(order_id)
            bucket["units"] += units
            bucket["gross"] += _f(gross)
            bucket["promo"] += _f(promo)
            bucket["fees"] += _f(fees)
            bucket["shipping"] += shipping
            bucket["refunds"] += _f(refund)
            bucket["cogs"] += cogs
        skus[sku]["name"] = name

    def finish(key: str, b: dict[str, Any]) -> dict[str, Any]:
        net = b["gross"] - b["promo"]
        contribution = net - b["fees"] - b["shipping"] - b["refunds"] - b["cogs"]
        orders = len(b["orders"])
        return {
            "key": key,
            "orders": orders,
            "units": b["units"],
            "net_sales": net,
            "shipping": b["shipping"],
            "shipping_share": _ratio(b["shipping"], net),
            "shipping_per_order": _ratio(b["shipping"], orders),
            "shipping_per_unit": _ratio(b["shipping"], b["units"]),
            "contribution": contribution,
            "margin": _ratio(contribution, net),
        }

    state_rows = sorted((finish(k, v) for k, v in states.items()), key=lambda r: -r["net_sales"])
    sku_rows = []
    for k, v in skus.items():
        row = finish(k, v)
        row["product_name"] = v.get("name")
        row["flag"] = bool(
            row["units"] >= MIN_UNITS and row["shipping_share"] is not None and row["shipping_share"] >= threshold
        )
        sku_rows.append(row)
    sku_rows.sort(key=lambda r: (not r["flag"], -(r["shipping_share"] or 0)))

    known_orders = sum(r["orders"] for r in state_rows if r["key"] != NO_STATE)
    all_orders = sum(r["orders"] for r in state_rows)
    return {
        "days": days,
        "threshold": threshold,
        "summary": {
            "orders": all_orders,
            "orders_with_state": known_orders,
            "state_coverage": _ratio(known_orders, all_orders),
            "shipping": sum(r["shipping"] for r in state_rows),
            "net_sales": sum(r["net_sales"] for r in state_rows),
            "skus_over_threshold": sum(1 for r in sku_rows if r["flag"]),
        },
        "by_state": state_rows,
        "by_sku": sku_rows,
    }
