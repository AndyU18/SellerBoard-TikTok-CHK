"""Base del simulador "¿y si...?": la economía unitaria real de cada SKU (y canal) en los últimos N días.

Por SKU y canal devuelve unidades, precio promedio, promo, comisión, envío (línea + parte de la etiqueta
de Redo, igual que en shipping_profit), reembolsos y COGS vigente, todo por unidad, más la utilidad por
unidad y la mensual proyectada (unidades/día x 30). La publicidad asignada al SKU va aparte y no entra
en la utilidad base. El navegador recalcula los escenarios sobre estos números sin volver a llamar.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import load_cost_history
from app.models import (
    Account,
    AdSpendDaily,
    Channel,
    OrderLine,
    Product,
    SalesOrder,
    ShippingLabelCost,
)

MONTH_DAYS = 30


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _per(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def _row(key: tuple[str, str], b: dict[str, Any], days: int, ad_spend: float) -> dict[str, Any]:
    units = b["units"]
    net = b["gross"] - b["promo"]
    profit = net - b["fees"] - b["shipping"] - b["refunds"] - b["cogs"]
    per_day = units / days
    monthly = profit / days * MONTH_DAYS
    return {
        "sku": key[0],
        "channel": key[1],
        "product_name": b["name"],
        "has_cost": b["has_cost"],
        "units": units,
        "units_per_day": per_day,
        "gross_sales": b["gross"],
        "promo": b["promo"],
        "platform_fees": b["fees"],
        "shipping": b["shipping"],
        "refunds": b["refunds"],
        "cogs": b["cogs"],
        "net_sales": net,
        "avg_price": _per(b["gross"], units),
        "promo_per_unit": _per(b["promo"], units),
        "fee_per_unit": _per(b["fees"], units),
        "fee_pct_of_net": _per(b["fees"], net),
        "shipping_per_unit": _per(b["shipping"], units),
        "refund_per_unit": _per(b["refunds"], units),
        "refund_rate": _per(b["refunds"], net),
        "cogs_per_unit": b["unit_cost"],
        "profit": profit,
        "profit_per_unit": _per(profit, units),
        "monthly_profit": monthly,
        "ad_spend": ad_spend,
        "ad_per_unit": _per(ad_spend, units),
        "monthly_ad_spend": ad_spend / days * MONTH_DAYS,
        "profit_after_ads": profit - ad_spend,
        "monthly_profit_after_ads": monthly - ad_spend / days * MONTH_DAYS,
    }


async def simulator_baseline(
    session: AsyncSession,
    *,
    days: int = 90,
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
            Account.channel_code,
            Product.id,
            Product.sku,
            Product.name,
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
        order_gross[row[0]] += _f(row[7])
        order_units[row[0]] += row[6]

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    product_ids: dict[tuple[str, str], int] = {}
    for order_id, ext_id, ch, pid, sku, name, units, gross, promo, fees, ship, refund in fetched:
        label = labels.get(ext_id, 0.0)
        if label:
            total = order_gross[order_id]
            label *= _f(gross) / total if total else units / (order_units[order_id] or 1)
        key = (sku, ch)
        b = buckets.get(key)
        if b is None:
            unit_cost = float(costs.unit_cost(pid, today))
            b = buckets[key] = {
                "name": name,
                "has_cost": costs.has_cost(pid) and unit_cost > 0,
                "unit_cost": unit_cost,
                "units": 0,
                "gross": 0.0,
                "promo": 0.0,
                "fees": 0.0,
                "shipping": 0.0,
                "refunds": 0.0,
                "cogs": 0.0,
            }
            product_ids[key] = pid
        b["units"] += units
        b["gross"] += _f(gross)
        b["promo"] += _f(promo)
        b["fees"] += _f(fees)
        b["shipping"] += _f(ship) + label
        b["refunds"] += _f(refund)
        b["cogs"] += units * b["unit_cost"]

    ad_query = (
        select(AdSpendDaily.product_id, Account.channel_code, func.sum(AdSpendDaily.spend))
        .join(Account, AdSpendDaily.account_id == Account.id)
        .where(
            AdSpendDaily.spend_date >= start,
            AdSpendDaily.spend_date <= today,
            AdSpendDaily.product_id.is_not(None),
        )
        .group_by(AdSpendDaily.product_id, Account.channel_code)
    )
    if channel:
        ad_query = ad_query.where(Account.channel_code == channel.value)
    if account:
        ad_query = ad_query.where(Account.name == account)
    ads: dict[tuple[int, str], float] = {(p, c): _f(s) for p, c, s in (await session.execute(ad_query)).all()}

    rows = [_row(k, b, days, ads.get((product_ids[k], k[1]), 0.0)) for k, b in buckets.items()]
    rows.sort(key=lambda r: -r["monthly_profit"])

    totals_raw = {
        "units": sum(r["units"] for r in rows),
        "gross": sum(r["gross_sales"] for r in rows),
        "promo": sum(r["promo"] for r in rows),
        "fees": sum(r["platform_fees"] for r in rows),
        "shipping": sum(r["shipping"] for r in rows),
        "refunds": sum(r["refunds"] for r in rows),
        "cogs": sum(r["cogs"] for r in rows),
    }
    ad_total = sum(r["ad_spend"] for r in rows)
    totals = _row(
        ("(todo)", "all"),
        {**totals_raw, "name": None, "has_cost": all(r["has_cost"] for r in rows), "unit_cost": None},
        days,
        ad_total,
    )
    totals["cogs_per_unit"] = _per(totals_raw["cogs"], totals_raw["units"])
    return {
        "days": days,
        "start": start.isoformat(),
        "end": today.isoformat(),
        "month_days": MONTH_DAYS,
        "skus": len(rows),
        "skus_without_cost": sum(1 for r in rows if not r["has_cost"]),
        "rows": rows,
        "totals": totals,
    }
