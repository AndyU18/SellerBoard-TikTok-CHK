"""Datos para la pantalla Productos: velocidad de ventas, beneficio por unidad y serie semanal (solo lectura).

Reutiliza la capa oro (`load_gold_frame`) y `profit.summarize`; no escribe nada.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.gold import load_gold_frame
from app.models import Channel
from app.profit import summarize

router = APIRouter(prefix="/api/chartdata")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]

VELOCITY_DAYS = 30  # ventana de la velocidad de ventas (unidades por día)
WEEKS = 12  # largo de la serie semanal de cada producto


def _week_index(day: date, end: date) -> int:
    """0 = la semana más antigua de la serie; WEEKS - 1 = la que termina en `end`."""
    return WEEKS - 1 - (end - day).days // 7


def build_products_overview(df: pl.DataFrame, start: date | None, end: date) -> dict[str, Any]:
    """Una fila por SKU con totales del período, velocidad de 30 días, canales y 12 semanas de tendencia."""
    if df.height == 0:
        return {"velocity_days": VELOCITY_DAYS, "weeks": WEEKS, "rows": []}

    in_range = df if start is None else df.filter(pl.col("date") >= start)
    in_range = in_range.filter(pl.col("date") <= end)
    names = df.group_by("sku").agg(pl.col("product_name").drop_nulls().first())

    totals = summarize(in_range, ["sku"]) if in_range.height else None
    by_channel: dict[str, dict[str, dict[str, float]]] = {}
    if in_range.height:
        for r in summarize(in_range, ["sku", "channel"]).iter_rows(named=True):
            by_channel.setdefault(r["sku"], {})[r["channel"]] = {
                "units": r["units"],
                "gross_sales": r["gross_sales"],
                "net_profit": r["net_profit"],
            }

    recent_start = end - timedelta(days=VELOCITY_DAYS - 1)
    recent = df.filter((pl.col("date") >= recent_start) & (pl.col("date") <= end))
    velocity = {
        r["sku"]: r["units"] / VELOCITY_DAYS for r in recent.group_by("sku").agg(pl.col("units").sum()).iter_rows(named=True)
    }

    week_start = end - timedelta(days=WEEKS * 7 - 1)
    weekly_src = df.filter((pl.col("date") >= week_start) & (pl.col("date") <= end))
    series: dict[str, dict[str, list[float]]] = {}
    if weekly_src.height:
        for r in summarize(weekly_src, ["sku", "date"]).iter_rows(named=True):
            s = series.setdefault(r["sku"], {"units": [0.0] * WEEKS, "net_profit": [0.0] * WEEKS})
            i = _week_index(r["date"], end)
            s["units"][i] += r["units"]
            s["net_profit"][i] += r["net_profit"]

    name_by_sku = {r["sku"]: r["product_name"] for r in names.iter_rows(named=True)}
    rows: list[dict[str, Any]] = []
    for r in (totals.iter_rows(named=True) if totals is not None else []):
        sku = r["sku"]
        units = r["units"]
        weekly = series.get(sku, {"units": [0.0] * WEEKS, "net_profit": [0.0] * WEEKS})
        rows.append(
            {
                "sku": sku,
                "product_name": name_by_sku.get(sku),
                "channels": sorted(by_channel.get(sku, {})),
                "by_channel": by_channel.get(sku, {}),
                "units": units,
                "gross_sales": r["gross_sales"],
                "net_profit": r["net_profit"],
                "unit_profit": r["unit_profit"],
                "margin": r["margin"],
                "avg_price": round(r["gross_sales"] / units, 2) if units else None,
                "units_per_day": round(velocity.get(sku, 0.0), 3),
                "weekly_units": weekly["units"],
                "weekly_profit": [round(v, 2) for v in weekly["net_profit"]],
            }
        )
    rows.sort(key=lambda x: (-x["gross_sales"], x["sku"]))
    return {"velocity_days": VELOCITY_DAYS, "weeks": WEEKS, "rows": rows}


@router.get("/products-overview", dependencies=VIEW)
async def products_overview(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Por producto: ventas y utilidad del período, beneficio por unidad, unidades/día (30 d) y 12 semanas."""
    end_day = end or datetime.now(UTC).date()
    # Se carga lo necesario para el período pedido y para la ventana de 12 semanas (84 días).
    lo = end_day - timedelta(days=WEEKS * 7 - 1)
    load_from = None if start is None else min(start, lo)
    df = await load_gold_frame(session, start=load_from, end=end_day, channel=channel, account=account)
    return build_products_overview(df, start, end_day)
