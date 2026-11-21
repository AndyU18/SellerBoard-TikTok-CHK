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

from app.auth import require
from app.config import settings
from app.connectors.redo import RedoClient, RedoError, sync_redo
from app.connectors.tiktok_shop import TikTokShopClient, TikTokShopError
from app.connectors.tiktok_shop_sync import sync_tiktok_shop
from app.connectors.walmart import WalmartClient, WalmartError, sync_walmart
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
from app.importers.redo import import_label_costs, normalize_redo
from app.models import (
    Account,
    Channel,
    CostComponentType,
    GoldRefreshLog,
    Product,
    ProductCostHistory,
)
from app.profit import abc_classes, overall, summarize
from app.reports import (
    cashflow_report,
    creatives_report,
    dayparting_report,
    funnel_report,
    live_report,
    logistics_report,
    promotions_report,
    returns_report,
    samples_report,
)

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


async def _normalized_with_stats(file: UploadFile, normalize):
    """Como _normalized, para normalizadores que devuelven (DataFrame, estadísticas)."""
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Archivo demasiado grande")
    try:
        return normalize(read_table(data, file.filename or ""))
    except ImportValidationError as exc:
        raise HTTPException(422, {"problems": exc.problems}) from exc
    except pl.exceptions.PolarsError as exc:
        raise HTTPException(422, {"problems": [f"No se pudo leer el archivo: {exc}"]}) from exc


@router.post("/import/orders", dependencies=[Depends(require("import.run", writes=True))])
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


@router.post("/import/ad-spend", dependencies=[Depends(require("import.run", writes=True))])
async def upload_ad_spend(session: SessionDep, file: UploadFile) -> dict:
    df = await _normalized(file, normalize_ad_spend)
    result = await import_ad_spend(session, df)
    await refresh_daily_profit(session, df["date"].min(), df["date"].max(), reason="import_ads")
    await session.commit()
    return _public(result)


@router.post("/import/cogs", dependencies=[Depends(require("costs.edit", writes=True))])
async def upload_cogs(session: SessionDep, file: UploadFile) -> dict:
    df = await _normalized(file, normalize_cogs)
    result = await import_cogs(session, df)
    # Un costo cambia la utilidad desde su vigencia; sin fecha puede regir desde siempre.
    start = None if df["valid_from"].null_count() else df["valid_from"].min()
    await refresh_daily_profit(session, start, None, reason="import_cogs")
    await session.commit()
    return _public(result)


@router.post("/import/shipping-labels", dependencies=[Depends(require("import.run", writes=True))])
async def upload_shipping_labels(session: SessionDep, file: UploadFile) -> dict:
    """Reporte billing-transactions de Redo: costo real de las etiquetas por orden."""
    df, stats = await _normalized_with_stats(file, normalize_redo)
    result = await import_label_costs(session, df)
    if result.touched_from:
        await refresh_daily_profit(
            session, result.touched_from, result.touched_to, reason="import_shipping_labels"
        )
    await session.commit()
    out = _public(result)
    return {**out, "stats": asdict(stats)}


@router.post("/sync/tiktok-shop", dependencies=[Depends(require("sync.run", writes=True))])
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


@router.post("/sync/redo", dependencies=[Depends(require("sync.run", writes=True))])
async def sync_redo_labels(session: SessionDep) -> dict:
    """Trae de Redo (solo lectura) el costo real de las etiquetas, sin subir el CSV."""
    try:
        result, stats = await sync_redo(session, RedoClient())
    except RedoError as exc:
        raise HTTPException(502, {"problems": [str(exc)]}) from exc
    if result.touched_from:
        await refresh_daily_profit(session, result.touched_from, result.touched_to, reason="sync_redo")
    await session.commit()
    return {**_public(result), "stats": asdict(stats)}


@router.post("/sync/walmart",dependencies=[Depends(require("sync.run", writes=True))])
async def sync_walmart_orders(
    session: SessionDep, days: Annotated[int, Query(ge=1, le=180)] = 30
) -> dict:
    """Trae órdenes de Walmart (solo lectura en Walmart) de los últimos `days` días."""
    try:
        report = await sync_walmart(session, WalmartClient(), days=days)
    except WalmartError as exc:
        raise HTTPException(502, {"problems": [str(exc)]}) from exc
    await refresh_daily_profit(
        session,
        report.imported.touched_from or date.today() - timedelta(days=days + 1),
        None,
        reason="sync_walmart",
    )
    await session.commit()
    return {"days": report.days, "stats": report.stats, "imported": _public(report.imported)}


@router.get("/profit", dependencies=[Depends(require("profit.view"))])
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


@router.get("/profit/pareto", dependencies=[Depends(require("profit.view"))])
async def profit_pareto(
    session: SessionDep,
    metric: Literal["profit", "sales"] = "profit",
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
) -> dict:
    """Clasificación ABC (Pareto) de productos por utilidad neta o por ventas."""
    df = await load_gold_frame(session, start=start, end=end, channel=channel)
    products = summarize(df, ["sku"])
    if products.height == 0:
        return {"metric": metric, "rows": [], "summary": {}}
    names = df.group_by("sku").agg(pl.col("product_name").drop_nulls().first())
    ranked = abc_classes(products.join(names, on="sku", how="left"), metric)
    column = "net_profit" if metric == "profit" else "gross_sales"
    summary = {
        str(key[0]): {"products": g.height, "value": round(float(g[column].sum()), 2)}
        for key, g in ranked.partition_by("abc_class", as_dict=True, maintain_order=True).items()
    }
    return {"metric": metric, "rows": ranked.to_dicts(), "summary": summary}


@router.get("/promotions", dependencies=[Depends(require("profit.view"))])
async def promotions(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
) -> dict:
    """Auditoría de promociones: descuento del vendedor vs subsidio de la plataforma."""
    return await promotions_report(session, start, end, channel)


@router.get("/dayparting", dependencies=[Depends(require("profit.view"))])
async def dayparting(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
) -> dict:
    """Órdenes y ventas por día de la semana y hora (zona horaria CHK_REPORT_TIMEZONE)."""
    return await dayparting_report(session, settings.report_timezone, start, end, channel)


@router.get("/returns", dependencies=[Depends(require("profit.view"))])
async def returns(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
) -> dict:
    """Devoluciones y cancelaciones (RMA): motivos, productos y tasa de devolución."""
    return await returns_report(session, start, end, channel)


@router.get("/logistics", dependencies=[Depends(require("profit.view"))])
async def logistics(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
) -> dict:
    """Tiempos de envío, puntualidad y transportistas."""
    return await logistics_report(session, start, end, channel)


@router.get("/samples", dependencies=[Depends(require("profit.view"))])
async def samples(
    session: SessionDep, start: date | None = None, end: date | None = None
) -> dict:
    """ROI de las muestras a creadores: costo enviado vs utilidad de sus ventas."""
    return await samples_report(session, start, end)


@router.get("/funnel", dependencies=[Depends(require("profit.view"))])
async def funnel(
    session: SessionDep, start: date | None = None, end: date | None = None
) -> dict:
    """Embudo de tráfico: impresiones, vistas de producto y compradores, por origen y día."""
    return await funnel_report(session, start, end)


@router.get("/live", dependencies=[Depends(require("profit.view"))])
async def live(
    session: SessionDep, start: date | None = None, end: date | None = None
) -> dict:
    """Sesiones LIVE: ventas, duración y creadores que más venden."""
    return await live_report(session, start, end)


@router.get("/creatives", dependencies=[Depends(require("profit.view"))])
async def creatives(
    session: SessionDep, start: date | None = None, end: date | None = None
) -> dict:
    """Ventas y utilidad por video/LIVE de creadores (afiliados)."""
    return await creatives_report(session, start, end)


@router.get("/cashflow", dependencies=[Depends(require("profit.view"))])
async def cashflow(
    session: SessionDep, start: date | None = None, end: date | None = None
) -> dict:
    """Liquidaciones de las plataformas: lo cobrado y lo que está por cobrar."""
    return await cashflow_report(session, start, end)


@router.post("/gold/refresh", dependencies=[Depends(require("sync.run", writes=True))])
async def gold_refresh(
    session: SessionDep, start: date | None = None, end: date | None = None
) -> dict:
    """Reconstruye la capa oro (todo, o solo el rango dado) desde la capa plata."""
    rows = await refresh_daily_profit(session, start, end, reason="manual")
    await session.commit()
    return {"rows_written": rows}


@router.get("/gold/status", dependencies=[Depends(require("ops.view"))])
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


@router.get("/profit/timeseries", dependencies=[Depends(require("profit.view"))])
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


@router.get("/accounts", dependencies=[Depends(require("sales.view"))])
async def list_accounts(session: SessionDep) -> list[dict]:
    rows = (
        await session.scalars(
            select(Account).order_by(Account.channel_code, Account.name)
        )
    ).all()
    return [{"id": a.id, "channel": a.channel_code, "name": a.name} for a in rows]


@router.get("/products", dependencies=[Depends(require("catalog.view"))])
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
            "has_cost": costs.unit_cost(p.id, today) > 0,
        }
        for p in rows
    ]


@router.get("/products/{product_id}/cogs-history", dependencies=[Depends(require("catalog.view"))])
async def cogs_history(product_id: int, session: SessionDep) -> list[dict]:
    """Historial de costos del producto (más reciente primero)."""
    rows = (
        await session.execute(
            select(
                ProductCostHistory.valid_from,
                ProductCostHistory.amount,
                CostComponentType.code,
            )
            .join(CostComponentType, ProductCostHistory.component_type_id == CostComponentType.id)
            .where(ProductCostHistory.product_id == product_id)
            .order_by(ProductCostHistory.valid_from.desc())
        )
    ).all()
    return [
        {"valid_from": d, "amount": float(a), "component": code} for d, a, code in rows
    ]


@router.put("/products/{product_id}/cogs", dependencies=[Depends(require("costs.edit", writes=True))])
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
