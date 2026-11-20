"""Rutas HTTP: importación, utilidad por canal/producto, productos (COGS)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date
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
from app.importers.canonical import (
    ImportValidationError,
    normalize_ad_spend,
    normalize_cogs,
    normalize_orders,
    read_table,
)
from app.importers.persist import import_ad_spend, import_cogs, import_orders
from app.models import Account, Channel, Product
from app.profit import overall, summarize
from app.queries import load_profit_frame

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
    await session.commit()
    return asdict(result)


@router.post("/import/ad-spend")
async def upload_ad_spend(session: SessionDep, file: UploadFile) -> dict:
    df = await _normalized(file, normalize_ad_spend)
    result = await import_ad_spend(session, df)
    await session.commit()
    return asdict(result)


@router.post("/import/cogs")
async def upload_cogs(session: SessionDep, file: UploadFile) -> dict:
    df = await _normalized(file, normalize_cogs)
    result = await import_cogs(session, df)
    await session.commit()
    return asdict(result)


@router.post("/sync/tiktok-shop")
async def sync_tiktok(
    session: SessionDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> dict:
    """Trae órdenes liquidadas de TikTok Shop de los últimos `days` días."""
    try:
        report = await sync_tiktok_shop(session, TikTokShopClient(), days=days)
    except TikTokShopError as exc:
        raise HTTPException(502, {"problems": [str(exc)]}) from exc
    return {
        "days": report.days,
        "stats": report.stats,
        "imported": asdict(report.imported),
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
    df = await load_profit_frame(
        session, start=start, end=end, channel=channel, account=account
    )
    rows = summarize(df, GROUP_COLUMNS[group_by])
    if "sku" in rows.columns:
        names = df.group_by("sku").agg(pl.col("product_name").drop_nulls().first())
        rows = rows.join(names, on="sku", how="left")
    return {"group_by": group_by, "rows": rows.to_dicts(), "totals": overall(df)}


@router.get("/profit/timeseries")
async def profit_timeseries(
    session: SessionDep,
    interval: Literal["day", "month"] = "month",
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    df = await load_profit_frame(
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
    await session.commit()
    costs = await load_cost_history(session)
    return {
        "id": product.id,
        "sku": product.sku,
        "cogs_unit": costs.unit_cost(product.id, date.today()),
    }
