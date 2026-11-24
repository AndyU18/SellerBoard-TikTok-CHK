"""Guarda los datos extra de TikTok Shop (catálogo, rendimiento, promociones, cupones, paquetes, pagos...).

Todo es idempotente: cada tabla tiene una clave natural (id de TikTok, o id + día para las fotos de
rendimiento) y volver a sincronizar actualiza la fila en vez de duplicarla. Qué se retira y qué no:
  * Fotos "de estado" (almacenes, webhooks, SKU de un producto): se retira lo que TikTok ya no informa.
  * Historial (productos, promociones, cupones, paquetes, pagos, retiros): se conserva, porque un producto
    eliminado o una promoción vencida siguen valiendo para el análisis.
  * Fotos de rendimiento: una por día; se borran las de más de PERFORMANCE_HISTORY_DAYS.
Una sección que falló al leerse NUNCA retira filas: un error de red no debe parecer "TikTok ya no lo tiene".

Importado solo por `app.connectors.tiktok_shop_extra` (la sincronización). Los upserts hacen `flush`; quien
llama decide el `commit`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    TikTokShopCoupon,
    TikTokShopPackage,
    TikTokShopPayment,
    TikTokShopProduct,
    TikTokShopProductPerformance,
    TikTokShopProductSku,
    TikTokShopPromotion,
    TikTokShopPromotionProduct,
    TikTokShopSkuPerformance,
    TikTokShopWarehouse,
    TikTokShopWebhook,
    TikTokShopWithdrawal,
)

if TYPE_CHECKING:
    from app.connectors.tiktok_shop_extra import ExtraSnapshot

PERFORMANCE_HISTORY_DAYS = 90  # fotos de rendimiento más viejas que esto se borran en cada sincronización

Row = dict[str, Any]


async def _upsert(
    session: AsyncSession,
    model: Any,
    key_fields: tuple[str, ...],
    rows: list[Row],
    extra: Row | None = None,
    scope: ColumnElement[bool] | None = None,
) -> int:
    """Inserta o actualiza `rows` por su clave natural con UNA consulta de lectura (no una por fila).

    `scope` acota qué filas existentes se cargan (p. ej. solo la foto de hoy) para no leer historial entero.
    """
    if not rows:
        return 0
    query = select(model)
    if scope is not None:
        query = query.where(scope)
    existing = {tuple(getattr(r, k) for k in key_fields): r for r in (await session.scalars(query))}
    for row in rows:
        values = {**row, **(extra or {})}
        found = existing.get(tuple(values[k] for k in key_fields))
        if found is None:
            session.add(model(**values))
        else:
            for name, value in values.items():
                setattr(found, name, value)
    await session.flush()
    return len(rows)


async def _drop_missing(
    session: AsyncSession,
    model: Any,
    key_fields: tuple[str, ...],
    rows: list[Row],
    scope: ColumnElement[bool] | None = None,
) -> int:
    """Borra de una tabla "foto" las claves que la última respuesta ya no trae (dentro de `scope`)."""
    keep = {tuple(r[k] for k in key_fields) for r in rows}
    query = select(model)
    if scope is not None:
        query = query.where(scope)
    stale = [r for r in await session.scalars(query) if tuple(getattr(r, k) for k in key_fields) not in keep]
    for row in stale:
        await session.delete(row)
    await session.flush()
    return len(stale)


async def save_catalog(session: AsyncSession, products: list[Row], skus: list[Row], now: datetime) -> int:
    """Productos (se conservan aunque TikTok los marque DELETED) y sus SKU (se retiran los que ya no existen)."""
    count = await _upsert(session, TikTokShopProduct, ("product_id",), products, {"synced_at": now})
    await _upsert(session, TikTokShopProductSku, ("sku_id",), skus)
    ids = [p["product_id"] for p in products]
    if ids:
        await _drop_missing(session, TikTokShopProductSku, ("sku_id",), skus, TikTokShopProductSku.product_id.in_(ids))
    return count


async def save_performance(
    session: AsyncSession, products: list[Row], skus: list[Row], today: date
) -> tuple[int, int]:
    """Foto del día de producto y SKU; poda el historial viejo."""
    product_count = await _upsert(
        session,
        TikTokShopProductPerformance,
        ("product_id", "snapshot_date"),
        products,
        scope=TikTokShopProductPerformance.snapshot_date == today,
    )
    sku_count = await _upsert(
        session,
        TikTokShopSkuPerformance,
        ("sku_id", "snapshot_date"),
        skus,
        scope=TikTokShopSkuPerformance.snapshot_date == today,
    )
    cutoff = today - timedelta(days=PERFORMANCE_HISTORY_DAYS)
    await session.execute(delete(TikTokShopProductPerformance).where(TikTokShopProductPerformance.snapshot_date < cutoff))
    await session.execute(delete(TikTokShopSkuPerformance).where(TikTokShopSkuPerformance.snapshot_date < cutoff))
    return product_count, sku_count


async def save_promotions(session: AsyncSession, activities: list[Row], products: list[Row], now: datetime) -> int:
    """Actividades + sus productos. Los productos de una actividad se reemplazan SOLO si el detalle los trajo."""
    count = await _upsert(session, TikTokShopPromotion, ("activity_id",), activities, {"synced_at": now})
    with_detail = sorted({p["activity_id"] for p in products})
    if with_detail:
        await _drop_missing(
            session,
            TikTokShopPromotionProduct,
            ("activity_id", "product_id"),
            products,
            TikTokShopPromotionProduct.activity_id.in_(with_detail),
        )
    await _upsert(session, TikTokShopPromotionProduct, ("activity_id", "product_id"), products)
    return count


async def save_packages(session: AsyncSession, rows: list[Row], now: datetime) -> int:
    """Paquetes. Si esta vez no se pidió el seguimiento, conserva el último evento guardado."""
    ids = [r["package_id"] for r in rows]
    previous = (
        {p.package_id: p for p in await session.scalars(select(TikTokShopPackage).where(TikTokShopPackage.package_id.in_(ids)))}
        if ids
        else {}
    )
    for row in rows:
        old = previous.get(row["package_id"])
        if old is not None and row["last_event_at"] is None:
            row["last_event_at"], row["last_event"] = old.last_event_at, old.last_event
    return await _upsert(session, TikTokShopPackage, ("package_id",), rows, {"synced_at": now})


async def save_snapshot(session: AsyncSession, snapshot: ExtraSnapshot, now: datetime) -> dict[str, int]:
    """Guarda todo lo leído y confirma. Devuelve los conteos por tabla (para `ops.sync_run.stats`)."""
    today = now.date()
    failed = snapshot.failed
    stats: dict[str, int] = {}
    stats["products"] = await save_catalog(session, snapshot.products, snapshot.product_skus, now)
    stats["product_skus"] = len(snapshot.product_skus)
    stats["product_performance"], stats["sku_performance"] = await save_performance(
        session, snapshot.product_performance, snapshot.sku_performance, today
    )
    stats["promotions"] = await save_promotions(session, snapshot.promotions, snapshot.promotion_products, now)
    stats["promotion_products"] = len(snapshot.promotion_products)
    stats["coupons"] = await _upsert(session, TikTokShopCoupon, ("coupon_id",), snapshot.coupons, {"synced_at": now})
    stats["packages"] = await save_packages(session, snapshot.packages, now)
    stats["payments"] = await _upsert(session, TikTokShopPayment, ("payment_id",), snapshot.payments)
    stats["withdrawals"] = await _upsert(session, TikTokShopWithdrawal, ("withdrawal_id",), snapshot.withdrawals)
    stats["warehouses"] = await _upsert(session, TikTokShopWarehouse, ("warehouse_id",), snapshot.warehouses)
    stats["webhooks"] = await _upsert(session, TikTokShopWebhook, ("event_type", "address"), snapshot.webhooks)
    if "almacenes" not in failed:
        await _drop_missing(session, TikTokShopWarehouse, ("warehouse_id",), snapshot.warehouses)
    if "webhooks" not in failed:
        await _drop_missing(session, TikTokShopWebhook, ("event_type", "address"), snapshot.webhooks)
    await session.commit()
    return stats
