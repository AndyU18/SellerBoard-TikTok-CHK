"""Guarda los datos del apartado Walmart (calidad, WFS, feeds, repricer, plantillas, reportes).

Todo es idempotente: cada tabla tiene una clave natural (día, sku+nodo, id de Walmart) y volver a
sincronizar actualiza la fila en vez de duplicarla. Las tablas que son una "foto" (inventario, repricer,
plantillas, SKU del reporte ITEM) además retiran lo que Walmart ya no informa.

Importado solo por `app.sync_jobs`. Los upserts hacen `flush`; quien llama decide el `commit`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    WalmartFeed,
    WalmartItemSnapshot,
    WalmartListingQuality,
    WalmartReportRequest,
    WalmartRepricerStrategy,
    WalmartShippingTemplate,
    WalmartWfsInventory,
    WalmartWfsInventoryHistory,
)

INVENTORY_HISTORY_DAYS = 90  # historial diario acotado: más viejo que esto se borra en cada sincronización


async def _upsert(
    session: AsyncSession,
    model: Any,
    key_fields: tuple[str, ...],
    rows: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> int:
    """Inserta o actualiza `rows` por su clave natural. `extra` se agrega a cada fila (p. ej. snapshot_at)."""
    for row in rows:
        values = {**row, **(extra or {})}
        key = tuple(values[k] for k in key_fields)
        existing = await session.get(model, key if len(key) > 1 else key[0])
        if existing is None:
            session.add(model(**values))
        else:
            for name, value in values.items():
                setattr(existing, name, value)
    await session.flush()
    return len(rows)


async def upsert_listing_quality(session: AsyncSession, row: dict[str, Any], now: datetime) -> int:
    """Una foto por día: sincronizar dos veces el mismo día deja la última lectura."""
    return await _upsert(session, WalmartListingQuality, ("snapshot_date",), [row], {"captured_at": now})


async def replace_wfs_inventory(
    session: AsyncSession, rows: list[dict[str, Any]], today: date, now: datetime
) -> int:
    """Deja la última foto por (sku, nodo), agrega la del día al historial y poda el historial viejo."""
    await _upsert(session, WalmartWfsInventory, ("sku", "node"), rows, {"snapshot_at": now})
    await _drop_missing(session, WalmartWfsInventory, ("sku", "node"), rows)
    history = [
        {
            "sku": r["sku"],
            "node": r["node"],
            "snapshot_date": today,
            "avail_to_sell": r["avail_to_sell"],
            "on_hand": r["on_hand"],
        }
        for r in rows
    ]
    await _upsert(session, WalmartWfsInventoryHistory, ("sku", "node", "snapshot_date"), history)
    await session.execute(
        delete(WalmartWfsInventoryHistory).where(
            WalmartWfsInventoryHistory.snapshot_date < today - timedelta(days=INVENTORY_HISTORY_DAYS)
        )
    )
    return len(rows)


async def _drop_missing(
    session: AsyncSession, model: Any, key_fields: tuple[str, ...], rows: list[dict[str, Any]]
) -> None:
    """Borra de una tabla "foto" las claves que la última respuesta de Walmart ya no trae."""
    keep = {tuple(r[k] for k in key_fields) for r in rows}
    columns = [getattr(model, k) for k in key_fields]
    for found in (await session.execute(select(*columns))).all():
        if tuple(found) not in keep:
            stale = await session.get(model, tuple(found) if len(found) > 1 else found[0])
            if stale is not None:
                await session.delete(stale)
    await session.flush()


async def upsert_feeds(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    return await _upsert(session, WalmartFeed, ("feed_id",), rows)


async def replace_repricer_strategies(session: AsyncSession, rows: list[dict[str, Any]], now: datetime) -> int:
    count = await _upsert(session, WalmartRepricerStrategy, ("collection_id",), rows, {"snapshot_at": now})
    await _drop_missing(session, WalmartRepricerStrategy, ("collection_id",), rows)
    return count


async def replace_shipping_templates(session: AsyncSession, rows: list[dict[str, Any]], now: datetime) -> int:
    """Si el detalle de una plantilla no se pudo leer esta vez, conserva el último detalle guardado."""
    for row in rows:
        if row.get("detail") is None:
            existing = await session.get(WalmartShippingTemplate, row["id"])
            if existing is not None:
                row["detail"] = existing.detail
    count = await _upsert(session, WalmartShippingTemplate, ("id",), rows, {"snapshot_at": now})
    await _drop_missing(session, WalmartShippingTemplate, ("id",), rows)
    return count


async def upsert_report_requests(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    return await _upsert(session, WalmartReportRequest, ("request_id",), rows)


async def replace_item_snapshots(session: AsyncSession, rows: list[dict[str, Any]], now: datetime) -> int:
    """Foto por SKU del último reporte ITEM; los SKU que ya no aparecen en el reporte se retiran."""
    count = await _upsert(session, WalmartItemSnapshot, ("sku",), rows, {"snapshot_at": now})
    await _drop_missing(session, WalmartItemSnapshot, ("sku",), rows)
    return count
