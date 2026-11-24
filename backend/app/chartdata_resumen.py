"""Datos para las gráficas del resumen ejecutivo: serie mensual por producto (solo lectura)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

import polars as pl
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.gold import load_gold_frame
from app.models import Channel
from app.profit import summarize

router = APIRouter(prefix="/api/chartdata")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]

_COLUMNS = ["units", "gross_sales", "ad_spend", "refund_amount", "net_profit"]


@router.get("/product-monthly", dependencies=VIEW)
async def product_monthly(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> dict:
    """Ventas, unidades, publicidad, reembolsos y utilidad por producto y mes (los `limit` con más ventas)."""
    df = await load_gold_frame(session, start=start, end=end, channel=channel, account=account)
    if df.height == 0:
        return {"months": [], "rows": []}
    df = df.with_columns(pl.col("date").dt.truncate("1mo").alias("period"))
    top = (
        df.group_by("sku")
        .agg(pl.col("gross_sales").sum().alias("_sales"))
        .sort("_sales", descending=True)
        .head(limit)
    )
    names = df.group_by("sku").agg(pl.col("product_name").drop_nulls().first())
    monthly = summarize(df.filter(pl.col("sku").is_in(top["sku"].implode())), ["period", "sku"]).join(
        names, on="sku", how="left"
    )
    rows = monthly.select("period", "sku", "product_name", *_COLUMNS).sort(["sku", "period"]).to_dicts()
    for r in rows:
        r["period"] = str(r["period"])
    return {"months": sorted({r["period"] for r in rows}), "rows": rows}


_GROUPS: dict[str, list[str]] = {
    "channel": ["channel"],
    "account": ["channel", "account"],
    "product": ["sku"],
    "channel_product": ["channel", "sku"],
}
_EXTRA = ["promo", "platform_fees", "shipping_cost", "cogs"]


@router.get("/versus-series", dependencies=VIEW)
async def versus_series(
    session: SessionDep,
    group_by: Literal["channel", "account", "product", "channel_product"] = "channel",
    interval: Literal["day", "month"] = "month",
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Serie por período de cada entidad (canal, tienda, producto o producto×canal) para superponer líneas."""
    df = await load_gold_frame(session, start=start, end=end, channel=channel, account=account)
    if df.height == 0:
        return {"interval": interval, "rows": []}
    keys = _GROUPS[group_by]
    df = df.with_columns(pl.col("date").dt.truncate("1d" if interval == "day" else "1mo").alias("period"))
    out = summarize(df, ["period", *keys])
    named = "sku" in keys
    if named:
        names = df.group_by("sku").agg(pl.col("product_name").drop_nulls().first())
        out = out.join(names, on="sku", how="left")
    rows = out.select("period", *keys, *(["product_name"] if named else []), *_COLUMNS, *_EXTRA).to_dicts()
    for r in rows:
        r["period"] = str(r["period"])
    return {"interval": interval, "rows": rows}
