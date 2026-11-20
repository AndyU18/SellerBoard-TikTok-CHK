"""Capa oro: tabla resumen gold.daily_profit (por día, cuenta y producto).

- `refresh_daily_profit` recalcula desde la capa plata SOLO el rango de fechas afectado
  (borra ese rango y lo reescribe). Sin rango = reconstrucción completa.
- `load_gold_frame` lee la capa oro con las columnas que consume profit.py.

La fórmula de utilidad sigue en un único lugar (profit.py): aquí solo se agregan montos y se
calcula el COGS con el costo vigente en la fecha de cada línea.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from decimal import Decimal

import polars as pl
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import load_cost_history
from app.models import (
    Account,
    AdSpendDaily,
    Channel,
    GoldDailyProfit,
    GoldRefreshLog,
    OrderLine,
    Product,
    SalesOrder,
    ShippingLabelCost,
)

UNASSIGNED_SKU = "(sin asignar)"
CHUNK = 1000
MONEY = [
    "gross_sales",
    "promo",
    "platform_fees",
    "shipping_cost",
    "refund_amount",
    "ad_spend",
    "cogs",
    "allocated_expenses",
]
KEYS = ["profit_date", "channel_code", "account_id", "product_id"]
DAILY_SCHEMA: dict[str, pl.DataType] = {
    "profit_date": pl.Date,
    "channel_code": pl.Utf8,
    "account_id": pl.Int64,
    "product_id": pl.Int64,
    "orders": pl.Int64,
    "units": pl.Int64,
    **dict.fromkeys(MONEY, pl.Float64),
}
GOLD_FRAME_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date,
    "channel": pl.Utf8,
    "account": pl.Utf8,
    "sku": pl.Utf8,
    "product_name": pl.Utf8,
    "units": pl.Int64,
    **dict.fromkeys(MONEY, pl.Float64),
}


async def _label_costs(session: AsyncSession) -> dict[str, float]:
    """Costo de etiquetas por id de orden externo (suma de todas las fuentes)."""
    totals: dict[str, float] = {}
    for ext_id, amount in (
        await session.execute(select(ShippingLabelCost.external_order_id, ShippingLabelCost.amount))
    ).all():
        totals[ext_id] = totals.get(ext_id, 0.0) + float(amount)
    return totals


async def _silver_daily(session: AsyncSession, start: date | None, end: date | None) -> pl.DataFrame:
    """Agrega la capa plata por (día, canal, cuenta, producto)."""
    order_q = (
        select(
            OrderLine.order_date,
            Account.channel_code,
            OrderLine.account_id,
            OrderLine.product_id,
            OrderLine.units,
            OrderLine.gross_sales,
            OrderLine.promo,
            OrderLine.platform_fees,
            OrderLine.shipping_cost,
            OrderLine.refund_amount,
            SalesOrder.external_order_id,
            OrderLine.order_id,
        )
        .join(Account, OrderLine.account_id == Account.id)
        .join(SalesOrder, OrderLine.order_id == SalesOrder.id)
    )
    ad_q = select(
        AdSpendDaily.spend_date,
        Account.channel_code,
        AdSpendDaily.account_id,
        AdSpendDaily.product_id,
        AdSpendDaily.spend,
    ).join(Account, AdSpendDaily.account_id == Account.id)
    if start:
        order_q = order_q.where(OrderLine.order_date >= start)
        ad_q = ad_q.where(AdSpendDaily.spend_date >= start)
    if end:
        order_q = order_q.where(OrderLine.order_date <= end)
        ad_q = ad_q.where(AdSpendDaily.spend_date <= end)

    costs = await load_cost_history(session)
    labels = await _label_costs(session)
    zeros = dict.fromkeys(MONEY, 0.0)
    fetched = (await session.execute(order_q)).all()

    # El costo de etiqueta es de la ORDEN: se reparte entre sus líneas según sus ventas
    # (o sus unidades si no hubo ventas). El resto del envío ya viene por línea.
    order_gross: dict[int, float] = {}
    order_units: dict[int, int] = {}
    for row in fetched:
        order_gross[row[11]] = order_gross.get(row[11], 0.0) + float(row[5])
        order_units[row[11]] = order_units.get(row[11], 0) + row[4]

    rows: list[dict] = []
    for day, ch, acc, pid, units, gross, promo, fees, ship, refund, ext_id, order_id in fetched:
        label = labels.get(ext_id, 0.0)
        if label:
            total = order_gross[order_id]
            share = float(gross) / total if total else units / (order_units[order_id] or 1)
            label *= share
        rows.append(
            {
                "profit_date": day,
                "channel_code": ch,
                "account_id": acc,
                "product_id": pid,
                "orders": 1,  # una línea = una orden que contiene este producto
                "units": units,
                **zeros,
                "gross_sales": float(gross),
                "promo": float(promo),
                "platform_fees": float(fees),
                "shipping_cost": float(ship) + label,
                "refund_amount": float(refund),
                "cogs": units * costs.unit_cost(pid, day),
            }
        )
    for day, ch, acc, pid, spend in (await session.execute(ad_q)).all():
        rows.append(
            {
                "profit_date": day,
                "channel_code": ch,
                "account_id": acc,
                "product_id": pid,
                "orders": 0,
                "units": 0,
                **zeros,
                "ad_spend": float(spend),
            }
        )
    frame = pl.DataFrame(rows, schema=DAILY_SCHEMA)
    return frame.group_by(KEYS).agg(
        pl.col("orders").sum(), pl.col("units").sum(), *[pl.col(c).sum() for c in MONEY]
    )


def _dec(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


async def refresh_daily_profit(
    session: AsyncSession,
    start: date | None = None,
    end: date | None = None,
    reason: str = "manual",
) -> int:
    """Reescribe gold.daily_profit para [start, end] (sin límites = todo). Devuelve las filas.

    No hace commit: el llamador lo agrupa con el cambio que provocó el refresco.
    """
    daily = await _silver_daily(session, start, end)

    delete_q = delete(GoldDailyProfit)
    if start:
        delete_q = delete_q.where(GoldDailyProfit.profit_date >= start)
    if end:
        delete_q = delete_q.where(GoldDailyProfit.profit_date <= end)
    await session.execute(delete_q)

    records = [
        {
            **{k: row[k] for k in ("profit_date", "channel_code", "account_id", "product_id")},
            "orders": int(row["orders"]),
            "units": int(row["units"]),
            **{c: _dec(row[c]) for c in MONEY},
        }
        for row in daily.iter_rows(named=True)
    ]
    for i in range(0, len(records), CHUNK):
        await session.execute(insert(GoldDailyProfit), records[i : i + CHUNK])

    dates = daily["profit_date"] if daily.height else None
    session.add(
        GoldRefreshLog(
            date_from=start or (dates.min() if dates is not None else date.today()),
            date_to=end or (dates.max() if dates is not None else date.today()),
            rows_written=len(records),
            reason=reason,
        )
    )
    await session.flush()
    return len(records)


async def load_gold_frame(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> pl.DataFrame:
    """Capa oro como DataFrame para profit.py (ya trae `cogs`, no `cogs_unit`)."""
    g = GoldDailyProfit
    q = (
        select(
            g.profit_date,
            g.channel_code,
            Account.name,
            Product.sku,
            Product.name,
            g.units,
            g.gross_sales,
            g.promo,
            g.platform_fees,
            g.shipping_cost,
            g.refund_amount,
            g.ad_spend,
            g.cogs,
            g.allocated_expenses,
        )
        .join(Account, g.account_id == Account.id)
        .outerjoin(Product, g.product_id == Product.id)
    )
    if start:
        q = q.where(g.profit_date >= start)
    if end:
        q = q.where(g.profit_date <= end)
    if channel:
        q = q.where(g.channel_code == channel.value)
    if account:
        q = q.where(Account.name == account)

    rows = [
        {
            "date": day,
            "channel": ch,
            "account": acc,
            "sku": sku or UNASSIGNED_SKU,
            "product_name": pname,
            "units": units,
            **{c: float(v) for c, v in zip(MONEY, money, strict=True)},
        }
        for day, ch, acc, sku, pname, units, *money in (await session.execute(q)).all()
    ]
    return pl.DataFrame(rows, schema=GOLD_FRAME_SCHEMA)


async def main(start: date | None, end: date | None) -> None:
    from app.db import SessionLocal, init_db

    await init_db()
    async with SessionLocal() as session:
        n = await refresh_daily_profit(session, start, end, reason="cli")
        await session.commit()
    print(f"gold.daily_profit: {n} filas escritas")


if __name__ == "__main__":
    # Uso: python -m app.gold [AAAA-MM-DD [AAAA-MM-DD]]   (sin fechas = reconstrucción completa)
    args = [date.fromisoformat(a) for a in sys.argv[1:3]]
    asyncio.run(main(*(args + [None, None])[:2]))
