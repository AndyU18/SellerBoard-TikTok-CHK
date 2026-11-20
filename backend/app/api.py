"""Rutas HTTP: importación, utilidad por canal/producto, productos (COGS)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.connectors.tiktok_shop import TikTokShopClient, TikTokShopError
from app.connectors.tiktok_shop_sync import sync_tiktok_shop
from app.costs import load_cost_history, set_product_cost
from app.db import get_session
from app.gold import load_gold_frame, refresh_daily_profit
from app.importers.canonical import (
    ImportValidationError,
    normalize_ad_spend,
    normalize_cogs,
    normalize_orders,
    read_table,
)
from app.importers.persist import import_ad_spend, import_cogs, import_orders
from app.models import Account, Channel, GoldRefreshLog, Product
from app.profit import overall, summarize

router = APIRouter(prefix="/api")
SessionDep = Annotated[AsyncSession, Depends(get_session)]

GroupBy = Literal["channel", "product", "channel_product", "account"]
GROUP_COLUMNS: dict[str, list[str]] = {
    "channel": ["channel"],
    "product": ["sku"],
    "channel_product": ["channel", "sku"],
    "account": ["channel", "account"],
}


class CogsUpdate(BaseModel):
    cogs_unit: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    valid_from: date | None = None  # sin fecha: ver costs.set_product_cost


def _public(result) -> dict:
    """Resultado de importación sin los campos internos (rango tocado)."""
    out = asdict(result)
    out.pop("touched_from", None)
    out.pop("touched_to", None)
    return out


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _normalized(
    file: UploadFile, normalize: Callable[[pl.DataFrame], pl.DataFrame]
):
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Archivo demasiado grande")
    try:
        return normalize(read_table(data, file.filename or ""))
    except ImportValidationError as exc:
        raise HTTPException(422, {"problems": exc.problems}) from exc
    except pl.exceptions.PolarsError as exc:
        raise HTTPException(
            422, {"problems": [f"No se pudo leer el archivo: {exc}"]}
        ) from exc


@router.post("/import/orders")
async def upload_orders(session: SessionDep, file: UploadFile) -> dict:
    df = await _normalized(file, normalize_orders)
    result = await import_orders(session, df)
    # El refresco de la capa oro va en la misma transacción que el cambio que lo provoca.
    await refresh_daily_profit(
        session,
        result.touched_from or df["date"].min(),
        result.touched_to or df["date"].max(),
        reason="import_orders",
    )
    await session.commit()
    return _public(result)


@router.post("/import/ad-spend")
async def upload_ad_spend(session: SessionDep, file: UploadFile) -> dict:
    df = await _normalized(file, normalize_ad_spend)
    result = await import_ad_spend(session, df)
    await refresh_daily_profit(session, df["date"].min(), df["date"].max(), reason="import_ads")
    await session.commit()
    return _public(result)


@router.post("/import/cogs")
async def upload_cogs(session: SessionDep, file: UploadFile) -> dict:
    df = await _normalized(file, normalize_cogs)
    result = await import_cogs(session, df)
    # Un costo cambia la utilidad desde su vigencia; sin fecha puede regir desde siempre.
    start = None if df["valid_from"].null_count() else df["valid_from"].min()
    await refresh_daily_profit(session, start, None, reason="import_cogs")
    await session.commit()
    return _public(result)


@router.post("/sync/tiktok-shop")
async def sync_tiktok(
    session: SessionDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> dict:
    """Trae órdenes liquidadas de TikTok Shop de los últimos `days` días."""
    try:
        report = await sync_tiktok_shop(session, TikTokShopClient(), days=days)
    except TikTokShopError as exc:
        raise HTTPException(502, {"problems": [str(exc)]}) from exc
    await refresh_daily_profit(
        session,
        report.imported.touched_from or date.today() - timedelta(days=days + 1),
        None,
        reason="sync_tiktok_shop",
    )
    await session.commit()
    return {
        "days": report.days,
        "stats": report.stats,
        "imported": _public(report.imported),
    }


@router.get("/profit")
async def profit(
    session: SessionDep,
    group_by: GroupBy = "channel",
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    df = await load_gold_frame(
        session, start=start, end=end, channel=channel, account=account
    )
    rows = summarize(df, GROUP_COLUMNS[group_by])
    if "sku" in rows.columns:
        names = df.group_by("sku").agg(pl.col("product_name").drop_nulls().first())
        rows = rows.join(names, on="sku", how="left")
    return {"group_by": group_by, "rows": rows.to_dicts(), "totals": overall(df)}


@router.post("/gold/refresh")
async def gold_refresh(
    session: SessionDep, start: date | None = None, end: date | None = None
) -> dict:
    """Reconstruye la capa oro (todo, o solo el rango dado) desde la capa plata."""
    rows = await refresh_daily_profit(session, start, end, reason="manual")
    await session.commit()
    return {"rows_written": rows}


@router.get("/gold/status")
async def gold_status(session: SessionDep) -> dict:
    last = await session.scalar(
        select(GoldRefreshLog).order_by(GoldRefreshLog.id.desc()).limit(1)
    )
    if last is None:
        return {"last_refresh": None}
    return {
        "last_refresh": {
            "at": last.refreshed_at,
            "date_from": last.date_from,
            "date_to": last.date_to,
            "rows_written": last.rows_written,
            "reason": last.reason,
        }
    }


@router.get("/profit/timeseries")
async def profit_timeseries(
    session: SessionDep,
    interval: Literal["day", "month"] = "month",
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    df = await load_gold_frame(
        session, start=start, end=end, channel=channel, account=account
    )
    every = "1d" if interval == "day" else "1mo"
    df = df.with_columns(pl.col("date").dt.truncate(every).alias("period"))
    return {
        "interval": interval,
        "rows": summarize(df, ["period", "channel"]).to_dicts(),
    }


@router.get("/accounts")
async def list_accounts(session: SessionDep) -> list[dict]:
    rows = (
        await session.scalars(
            select(Account).order_by(Account.channel_code, Account.name)
        )
    ).all()
    return [{"id": a.id, "channel": a.channel_code, "name": a.name} for a in rows]


@router.get("/products")
async def list_products(session: SessionDep) -> list[dict]:
    """Productos con su costo unitario vigente hoy."""
    rows = (await session.scalars(select(Product).order_by(Product.sku))).all()
    costs = await load_cost_history(session)
    today = date.today()
    return [
        {
            "id": p.id,
            "sku": p.sku,
            "name": p.name,
            "cogs_unit": costs.unit_cost(p.id, today),
        }
        for p in rows
    ]


@router.put("/products/{product_id}/cogs")
async def update_cogs(product_id: int, body: CogsUpdate, session: SessionDep) -> dict:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Producto no encontrado")
    await set_product_cost(session, product.id, body.cogs_unit, body.valid_from)
    await refresh_daily_profit(session, body.valid_from, None, reason="cogs_change")
    await session.commit()
    costs = await load_cost_history(session)
    return {
        "id": product.id,
        "sku": product.sku,
        "cogs_unit": costs.unit_cost(product.id, date.today()),
    }
