"""Rutas HTTP: importación, utilidad por canal/producto, productos (COGS)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

import polars as pl
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.importers.canonical import (
    ImportValidationError,
    normalize_ad_spend,
    normalize_orders,
    read_table,
)
from app.importers.persist import import_ad_spend, import_orders
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


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _normalized(file: UploadFile, normalize: Callable[[pl.DataFrame], pl.DataFrame]):
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Archivo demasiado grande")
    try:
        return normalize(read_table(data, file.filename or ""))
    except ImportValidationError as exc:
        raise HTTPException(422, {"problems": exc.problems}) from exc
    except pl.exceptions.PolarsError as exc:
        raise HTTPException(422, {"problems": [f"No se pudo leer el archivo: {exc}"]}) from exc


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


@router.get("/profit")
async def profit(
    session: SessionDep,
    group_by: GroupBy = "channel",
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    df = await load_profit_frame(session, start=start, end=end, channel=channel, account=account)
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
    df = await load_profit_frame(session, start=start, end=end, channel=channel, account=account)
    every = "1d" if interval == "day" else "1mo"
    df = df.with_columns(pl.col("date").dt.truncate(every).alias("period"))
    return {"interval": interval, "rows": summarize(df, ["period", "channel"]).to_dicts()}


@router.get("/accounts")
async def list_accounts(session: SessionDep) -> list[dict]:
    rows = (await session.scalars(select(Account).order_by(Account.channel, Account.name))).all()
    return [{"id": a.id, "channel": a.channel.value, "name": a.name} for a in rows]


@router.get("/products")
async def list_products(session: SessionDep) -> list[dict]:
    rows = (await session.scalars(select(Product).order_by(Product.sku))).all()
    return [
        {"id": p.id, "sku": p.sku, "name": p.name, "cogs_unit": float(p.cogs_unit)} for p in rows
    ]


@router.put("/products/{product_id}/cogs")
async def update_cogs(product_id: int, body: CogsUpdate, session: SessionDep) -> dict:
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Producto no encontrado")
    product.cogs_unit = body.cogs_unit
    await session.commit()
    return {"id": product.id, "sku": product.sku, "cogs_unit": float(product.cogs_unit)}
