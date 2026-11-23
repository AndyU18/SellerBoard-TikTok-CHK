"""Economía unitaria por canal y SKU: las tasas y costos por unidad que usan precios, aranceles,
reposición y pronóstico.

Todo sale de la capa oro (`gold.daily_profit`) en una ventana de días. Convención de márgenes:
sobre las **ventas netas** (ventas brutas menos promociones del vendedor), que es el precio que
realmente cobras por unidad.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.gold import load_gold_frame
from app.models import Channel

PACK_SUFFIX = re.compile(r"-\d+PK$")


def base_sku(sku: str) -> str:
    """SKU sin sufijo de empaque ("XMAS-7702-1PK" -> "XMAS-7702"): une las variantes del mismo producto."""
    return PACK_SUFFIX.sub("", sku.strip())


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


@dataclass(frozen=True)
class UnitEconomics:
    """Totales de un (canal, SKU) en la ventana, con sus tasas por unidad."""

    channel: str
    sku: str
    product_name: str | None
    units: int
    gross_sales: float
    promo: float
    platform_fees: float
    shipping_cost: float
    refund_amount: float
    cogs: float
    ad_spend: float
    net_profit: float

    @property
    def net_sales(self) -> float:
        return self.gross_sales - self.promo

    @property
    def list_price(self) -> float | None:
        """Precio de lista promedio por unidad (antes de promociones)."""
        return _ratio(self.gross_sales, self.units)

    @property
    def realized_price(self) -> float | None:
        """Precio promedio que realmente se cobra por unidad (después de promociones)."""
        return _ratio(self.net_sales, self.units)

    @property
    def promo_rate(self) -> float:
        """Promoción como fracción del precio de lista."""
        return _ratio(self.promo, self.gross_sales) or 0.0

    @property
    def fee_rate(self) -> float:
        """Comisión de la plataforma como fracción de las ventas netas."""
        return _ratio(self.platform_fees, self.net_sales) or 0.0

    @property
    def refund_rate(self) -> float:
        """Reembolsos como fracción de las ventas netas."""
        return _ratio(self.refund_amount, self.net_sales) or 0.0

    @property
    def cogs_unit(self) -> float:
        return _ratio(self.cogs, self.units) or 0.0

    @property
    def shipping_unit(self) -> float:
        return _ratio(self.shipping_cost, self.units) or 0.0

    @property
    def ad_unit(self) -> float:
        return _ratio(self.ad_spend, self.units) or 0.0

    @property
    def unit_profit(self) -> float | None:
        return self.net_profit / self.units if self.units else None

    @property
    def margin(self) -> float | None:
        """Utilidad sobre ventas netas."""
        return _ratio(self.net_profit, self.net_sales)


async def load_unit_economics(
    session: AsyncSession,
    *,
    days: int = 60,
    today: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> list[UnitEconomics]:
    """Una fila por (canal, SKU) con ventas en los últimos `days` días."""
    today = today or datetime.now(UTC).date()
    df = await load_gold_frame(
        session, start=today - timedelta(days=days - 1), end=today, channel=channel, account=account
    )
    if not df.height:
        return []
    names = df.group_by("sku").agg(pl.col("product_name").drop_nulls().first())
    totals = (
        df.group_by(["channel", "sku"])
        .agg(
            pl.col("units").sum(),
            pl.col("gross_sales").sum(),
            pl.col("promo").sum(),
            pl.col("platform_fees").sum(),
            pl.col("shipping_cost").sum(),
            pl.col("refund_amount").sum(),
            pl.col("cogs").sum(),
            pl.col("ad_spend").sum(),
            pl.col("allocated_expenses").sum(),
        )
        .join(names, on="sku", how="left")
    )
    out: list[UnitEconomics] = []
    for r in totals.iter_rows(named=True):
        if r["units"] <= 0:
            continue
        profit = (
            r["gross_sales"]
            - r["promo"]
            - r["platform_fees"]
            - r["shipping_cost"]
            - r["refund_amount"]
            - r["ad_spend"]
            - r["cogs"]
            - r["allocated_expenses"]
        )
        out.append(
            UnitEconomics(
                channel=r["channel"],
                sku=r["sku"],
                product_name=r["product_name"],
                units=int(r["units"]),
                gross_sales=r["gross_sales"],
                promo=r["promo"],
                platform_fees=r["platform_fees"],
                shipping_cost=r["shipping_cost"],
                refund_amount=r["refund_amount"],
                cogs=r["cogs"],
                ad_spend=r["ad_spend"],
                net_profit=profit,
            )
        )
    return sorted(out, key=lambda e: (e.channel, e.sku))
