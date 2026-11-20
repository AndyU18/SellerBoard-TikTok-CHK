"""Carga de la base de datos al DataFrame que consume profit.py."""

from __future__ import annotations

from datetime import date

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import load_cost_history
from app.models import Account, AdSpendDaily, Channel, OrderLine, Product

UNASSIGNED_SKU = "(sin asignar)"

FRAME_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date,
    "channel": pl.Utf8,
    "account": pl.Utf8,
    "sku": pl.Utf8,
    "product_name": pl.Utf8,
    "units": pl.Int64,
    "gross_sales": pl.Float64,
    "promo": pl.Float64,
    "platform_fees": pl.Float64,
    "shipping_cost": pl.Float64,
    "refund_amount": pl.Float64,
    "ad_spend": pl.Float64,
    "cogs_unit": pl.Float64,
}


async def load_profit_frame(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> pl.DataFrame:
    """Órdenes + gasto publicitario como filas con las columnas que pide profit.py.

    El gasto publicitario entra como filas con 0 unidades, para que sume al
    canal/producto correcto sin inventar ventas. El costo unitario de cada línea es el vigente
    en la fecha de la orden (historial de costos).
    """
    order_q = (
        select(OrderLine, Account.channel_code, Account.name, Product.sku, Product.name)
        .join(Account, OrderLine.account_id == Account.id)
        .join(Product, OrderLine.product_id == Product.id)
    )
    ad_q = (
        select(
            AdSpendDaily.spend_date,
            Account.channel_code,
            Account.name,
            Product.sku,
            AdSpendDaily.spend,
        )
        .join(Account, AdSpendDaily.account_id == Account.id)
        .outerjoin(Product, AdSpendDaily.product_id == Product.id)
    )
    if start:
        order_q = order_q.where(OrderLine.order_date >= start)
        ad_q = ad_q.where(AdSpendDaily.spend_date >= start)
    if end:
        order_q = order_q.where(OrderLine.order_date <= end)
        ad_q = ad_q.where(AdSpendDaily.spend_date <= end)
    if channel:
        order_q = order_q.where(Account.channel_code == channel.value)
        ad_q = ad_q.where(Account.channel_code == channel.value)
    if account:
        order_q = order_q.where(Account.name == account)
        ad_q = ad_q.where(Account.name == account)

    costs = await load_cost_history(session)
    rows: list[dict] = []
    for ol, ch, acc, sku, pname in (await session.execute(order_q)).all():
        rows.append(
            {
                "date": ol.order_date,
                "channel": ch,
                "account": acc,
                "sku": sku,
                "product_name": pname,
                "units": ol.units,
                "gross_sales": float(ol.gross_sales),
                "promo": float(ol.promo),
                "platform_fees": float(ol.platform_fees),
                "shipping_cost": float(ol.shipping_cost),
                "refund_amount": float(ol.refund_amount),
                "ad_spend": 0.0,
                "cogs_unit": costs.unit_cost(ol.product_id, ol.order_date),
            }
        )
    for spend_date, ch, acc, sku, amount in (await session.execute(ad_q)).all():
        rows.append(
            {
                "date": spend_date,
                "channel": ch,
                "account": acc,
                "sku": sku or UNASSIGNED_SKU,
                "product_name": None,
                "units": 0,
                "gross_sales": 0.0,
                "promo": 0.0,
                "platform_fees": 0.0,
                "shipping_cost": 0.0,
                "refund_amount": 0.0,
                "ad_spend": float(amount),
                "cogs_unit": 0.0,
            }
        )
    return pl.DataFrame(rows, schema=FRAME_SCHEMA)
