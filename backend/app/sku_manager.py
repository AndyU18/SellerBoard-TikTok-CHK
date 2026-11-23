"""Gestor de SKU: detecta productos duplicados, los fusiona sin perder datos y administra alias.

Un mismo producto suele llegar con SKU distintos según la plataforma ("XMAS8027", "XMAS-8027",
"GDL-187E", "XMAS-7702-1PK"). Aquí se agrupan por una clave normalizada (`sku_key`), se sugiere cuál
es el producto maestro y, al fusionar, TODO lo del producto origen pasa al destino y el SKU origen
queda como alias: así una importación posterior con ese SKU cae en el producto maestro y no vuelve
a crear un duplicado.

Las funciones no hacen commit: el llamador (el endpoint) agrupa la fusión con el refresco de la
capa oro en una sola transacción.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.economics import PACK_SUFFIX
from app.gold import UNASSIGNED_SKU
from app.landed import ECOMM_SUFFIX
from app.models import (
    Account,
    AdSpendDaily,
    AffiliateOrder,
    ChannelListing,
    GoldDailyProfit,
    OrderLine,
    Product,
    ProductCostHistory,
    ReturnCaseLine,
    SampleLine,
    SkuAlias,
)

CHUNK = 500  # tamaño de los IN (...) para no mandar listas enormes
NON_ALNUM = re.compile(r"[^A-Z0-9]")
# Columnas de OrderLine que se suman cuando ambos productos tienen línea en la misma orden.
LINE_AMOUNTS = (
    "units",
    "gross_sales",
    "promo",
    "platform_fees",
    "shipping_cost",
    "refund_amount",
    "platform_discount",
    "list_amount",
)


class SkuError(Exception):
    """Operación de SKU inválida. `problems` son mensajes legibles para la persona usuaria."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def sku_key(sku: str) -> str:
    """Clave que une las variantes de un mismo SKU.

    Mayúsculas, sin sufijo de empaque (`-1PK`), sin la `E` final tras un dígito (variante ecomm)
    y sin caracteres no alfanuméricos: "XMAS8027", "XMAS-8027" y "xmas-8027-2pk" dan la misma clave.
    Si el SKU no tiene ningún carácter alfanumérico se devuelve tal cual (en mayúsculas).
    """
    upper = sku.strip().upper()
    base = ECOMM_SUFFIX.sub("", PACK_SUFFIX.sub("", upper))
    return NON_ALNUM.sub("", base) or upper


def _chunks(items: list, size: int = CHUNK):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


@dataclass
class ProductSales:
    units: int = 0
    sales: float = 0.0
    channels: set[str] = field(default_factory=set)


async def product_sales(session: AsyncSession, since: date) -> dict[int, ProductSales]:
    """Unidades, ventas y canales por producto desde `since` (inclusive)."""
    query = (
        select(
            OrderLine.product_id,
            Account.channel_code,
            func.sum(OrderLine.units),
            func.sum(OrderLine.gross_sales),
        )
        .join(Account, OrderLine.account_id == Account.id)
        .where(OrderLine.order_date >= since)
        .group_by(OrderLine.product_id, Account.channel_code)
    )
    out: dict[int, ProductSales] = defaultdict(ProductSales)
    for pid, channel, units, gross in (await session.execute(query)).all():
        entry = out[pid]
        entry.units += int(units or 0)
        entry.sales += _f(gross)
        entry.channels.add(channel)
    return out


async def duplicate_groups(
    session: AsyncSession, days: int = 90, today: date | None = None
) -> dict[str, Any]:
    """Grupos de productos con la misma clave normalizada (más de un miembro).

    El producto sugerido como maestro es el que tiene costo, luego el de más ventas y, a igualdad,
    el que usa guion (formato "XMAS-8027"). Los grupos salen ordenados por las ventas involucradas.
    """
    today = today or datetime.now(UTC).date()
    since = today - timedelta(days=days)

    products = (await session.scalars(select(Product))).all()
    by_key: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if p.sku != UNASSIGNED_SKU:
            by_key[sku_key(p.sku)].append(p)
    candidates = {k: v for k, v in by_key.items() if len(v) > 1}
    if not candidates:
        return {"days": days, "count": 0, "groups": []}

    sales = await product_sales(session, since)
    with_cost = {pid for (pid,) in (await session.execute(select(ProductCostHistory.product_id).distinct())).all()}
    listings: dict[int, int] = defaultdict(int)
    for (pid,) in (await session.execute(select(ChannelListing.product_id))).all():
        listings[pid] += 1
    aliases: dict[int, list[str]] = defaultdict(list)
    for alias, pid in (await session.execute(select(SkuAlias.alias, SkuAlias.product_id))).all():
        aliases[pid].append(alias)

    groups: list[dict[str, Any]] = []
    for key, members in candidates.items():
        rows = []
        for p in members:
            s = sales.get(p.id, ProductSales())
            rows.append(
                {
                    "sku": p.sku,
                    "name": p.name,
                    "units": s.units,
                    "sales": s.sales,
                    "channels": sorted(s.channels),
                    "has_cost": p.id in with_cost,
                    "listings": listings.get(p.id, 0),
                    "aliases": sorted(aliases.get(p.id, [])),
                }
            )
        rows.sort(key=lambda r: (r["has_cost"], r["sales"], "-" in r["sku"], r["sku"]), reverse=True)
        for i, r in enumerate(rows):
            r["suggested"] = i == 0
        groups.append(
            {
                "key": key,
                "suggested_sku": rows[0]["sku"],
                "total_sales": sum(r["sales"] for r in rows),
                "total_units": sum(r["units"] for r in rows),
                "members": rows,
            }
        )
    groups.sort(key=lambda g: (-g["total_sales"], g["key"]))
    return {"days": days, "count": len(groups), "groups": groups}


async def _product_by_sku(session: AsyncSession, sku: str) -> Product | None:
    return await session.scalar(select(Product).where(Product.sku == sku.strip()))


async def _merge_order_lines(session: AsyncSession, source: Product, target: Product) -> dict[str, Any]:
    """Pasa las líneas de venta al destino. Si la orden ya tiene línea del destino, las suma."""
    lines = (await session.scalars(select(OrderLine).where(OrderLine.product_id == source.id))).all()
    existing: dict[int, OrderLine] = {}
    order_ids = [ln.order_id for ln in lines]
    for part in _chunks(order_ids):
        for ln in await session.scalars(
            select(OrderLine).where(OrderLine.product_id == target.id, OrderLine.order_id.in_(part))
        ):
            existing[ln.order_id] = ln

    moved = merged = 0
    dates: list[date] = []
    for ln in lines:
        dates.append(ln.order_date)
        twin = existing.get(ln.order_id)
        if twin is None:
            ln.product_id = target.id  # (order_id, product_id) sigue siendo único
            moved += 1
            continue
        for col in LINE_AMOUNTS:
            setattr(twin, col, getattr(twin, col) + getattr(ln, col))
        await session.delete(ln)
        merged += 1
    await session.flush()
    return {
        "lines_moved": moved,
        "lines_merged": merged,
        "touched_from": min(dates) if dates else None,
        "touched_to": max(dates) if dates else None,
    }


async def _merge_cost_history(session: AsyncSession, source: Product, target: Product) -> tuple[int, int]:
    """Conserva el costo del destino; del origen solo pasan las vigencias que el destino no tiene."""
    owned = {
        (h.component_type_id, h.valid_from)
        for h in await session.scalars(
            select(ProductCostHistory).where(ProductCostHistory.product_id == target.id)
        )
    }
    moved = dropped = 0
    for h in (
        await session.scalars(select(ProductCostHistory).where(ProductCostHistory.product_id == source.id))
    ).all():
        if (h.component_type_id, h.valid_from) in owned:
            await session.delete(h)
            dropped += 1
        else:
            h.product_id = target.id
            moved += 1
    await session.flush()
    return moved, dropped


async def _merge_sample_lines(session: AsyncSession, source: Product, target: Product) -> tuple[int, int]:
    """Las muestras guardan el SKU como texto; si la orden ya tiene el SKU destino, suma unidades."""
    taken = {
        (s.account_id, s.external_order_id): s
        for s in await session.scalars(select(SampleLine).where(SampleLine.sku == target.sku))
    }
    renamed = merged = 0
    for s in (await session.scalars(select(SampleLine).where(SampleLine.sku == source.sku))).all():
        twin = taken.get((s.account_id, s.external_order_id))
        if twin is None:
            s.sku = target.sku
            renamed += 1
        else:
            twin.units += s.units
            await session.delete(s)
            merged += 1
    await session.flush()
    return renamed, merged


async def merge_products(
    session: AsyncSession, source_sku: str, target_sku: str, reason: str | None = None
) -> dict[str, Any]:
    """Fusiona el producto `source_sku` en `target_sku` sin perder datos (ver módulo).

    Mueve líneas de venta, listados, gasto publicitario, historial de costos, aliases y las
    referencias por SKU en devoluciones, muestras y afiliados; crea el alias del SKU origen,
    borra el producto origen y sus filas de la capa oro (el llamador debe reconstruirla).
    Lanza `SkuError` si la fusión no es válida. No hace commit.
    """
    source_sku, target_sku = source_sku.strip(), target_sku.strip()
    problems: list[str] = []
    if not source_sku or not target_sku:
        raise SkuError(["Indica el SKU origen y el SKU destino."])
    if source_sku == target_sku:
        raise SkuError(["El SKU origen y el destino son el mismo producto."])
    source = await _product_by_sku(session, source_sku)
    target = await _product_by_sku(session, target_sku)
    if source is None:
        problems.append(f"No existe el producto origen '{source_sku}'.")
    if target is None:
        problems.append(f"No existe el producto destino '{target_sku}'.")
    if problems or source is None or target is None:
        raise SkuError(problems)

    stats: dict[str, Any] = {"source_sku": source.sku, "target_sku": target.sku}
    stats.update(await _merge_order_lines(session, source, target))

    stats["listings_moved"] = (
        await session.execute(
            update(ChannelListing)
            .where(ChannelListing.product_id == source.id)
            .values(product_id=target.id)
            .execution_options(synchronize_session=False)
        )
    ).rowcount  # (account_id, channel_sku) es único en toda la tabla: nunca choca con los del destino
    stats["ad_rows_moved"] = (
        await session.execute(
            update(AdSpendDaily)
            .where(AdSpendDaily.product_id == source.id)
            .values(product_id=target.id)
            .execution_options(synchronize_session=False)
        )
    ).rowcount
    stats["cost_rows_moved"], stats["cost_rows_dropped"] = await _merge_cost_history(session, source, target)

    stats["returns_updated"] = (
        await session.execute(
            update(ReturnCaseLine)
            .where(ReturnCaseLine.sku == source.sku)
            .values(sku=target.sku)
            .execution_options(synchronize_session=False)
        )
    ).rowcount
    stats["affiliate_updated"] = (
        await session.execute(
            update(AffiliateOrder)
            .where(AffiliateOrder.sku == source.sku)
            .values(sku=target.sku)
            .execution_options(synchronize_session=False)
        )
    ).rowcount
    stats["samples_updated"], stats["samples_merged"] = await _merge_sample_lines(session, source, target)

    stats["aliases_repointed"] = (
        await session.execute(
            update(SkuAlias)
            .where(SkuAlias.product_id == source.id)
            .values(product_id=target.id)
            .execution_options(synchronize_session=False)
        )
    ).rowcount
    if not target.name and source.name:
        target.name = source.name

    # El SKU origen queda como alias del destino (si ya era alias de otro producto, se re-apunta).
    note = reason or f"Fusionado desde {source.sku}"
    existing_alias = await session.scalar(select(SkuAlias).where(SkuAlias.alias == source.sku))
    if existing_alias is None:
        session.add(SkuAlias(alias=source.sku, product_id=target.id, reason=note))
    else:
        existing_alias.product_id, existing_alias.reason = target.id, note

    stats["gold_rows_deleted"] = (
        await session.execute(
            delete(GoldDailyProfit)
            .where(GoldDailyProfit.product_id == source.id)
            .execution_options(synchronize_session=False)
        )
    ).rowcount
    await session.flush()
    await session.execute(
        delete(Product).where(Product.id == source.id).execution_options(synchronize_session=False)
    )
    await session.flush()
    session.expunge(source)
    return stats


def _alias_row(alias: SkuAlias, product: Product) -> dict[str, Any]:
    return {
        "id": alias.id,
        "alias": alias.alias,
        "sku": product.sku,
        "product_name": product.name,
        "reason": alias.reason,
        "created_at": alias.created_at.isoformat() if alias.created_at else None,
    }


async def add_alias(
    session: AsyncSession, alias: str, target_sku: str, reason: str | None = None
) -> dict[str, Any]:
    """Registra `alias` como otro nombre del producto `target_sku`. Lanza `SkuError` si no procede."""
    alias, target_sku = alias.strip(), target_sku.strip()
    problems: list[str] = []
    if not alias:
        problems.append("Indica el alias.")
    target = await _product_by_sku(session, target_sku)
    if target is None:
        problems.append(f"No existe el producto '{target_sku}'.")
    if alias and alias == target_sku:
        problems.append("El alias no puede ser igual al SKU del producto.")
    if alias and await _product_by_sku(session, alias) is not None:
        problems.append(f"'{alias}' ya es un producto: usa Fusionar para unirlo con otro.")
    if alias and await session.scalar(select(SkuAlias.id).where(SkuAlias.alias == alias)) is not None:
        problems.append(f"'{alias}' ya es un alias.")
    if problems or target is None:
        raise SkuError(problems)
    row = SkuAlias(alias=alias, product_id=target.id, reason=reason or None)
    session.add(row)
    await session.flush()
    return _alias_row(row, target)


async def list_aliases(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(SkuAlias, Product).join(Product, SkuAlias.product_id == Product.id).order_by(SkuAlias.alias)
    )
    return [_alias_row(a, p) for a, p in rows.all()]


async def delete_alias(session: AsyncSession, alias_id: int) -> bool:
    """Quita un alias. Devuelve False si no existe. No deshace fusiones ya hechas."""
    result = await session.execute(delete(SkuAlias).where(SkuAlias.id == alias_id))
    return bool(result.rowcount)
