"""Análisis de TikTok Creator: estado de la conexión, perfil, catálogo de afiliados con puntaje de oportunidad,
vitrina y pedidos. Solo lee lo que dejó `sync_jobs` (`tiktok_creator`); no habla con TikTok ni escribe nada.

PUNTAJE DE OPORTUNIDAD (0-100) de un producto del catálogo de afiliados:

    puntaje = 100 x factor_comisión x factor_demanda x disponibilidad x afinidad

  * factor_comisión = min(1, comisión por unidad / COMMISSION_REFERENCE_USD). Se usa el monto por unidad, no la
    tasa: una tasa alta sobre un producto de $2 paga menos que una tasa media sobre uno de $60.
  * factor_demanda = ln(1 + unidades vendidas) / ln(1 + DEMAND_SATURATION_UNITS), acotado a [DEMAND_FLOOR, 1].
    La escala es logarítmica (pasar de 10 a 100 unidades pesa lo mismo que de 1.000 a 10.000) y el piso evita
    que un producto recién publicado, sin ventas aún, valga cero: no probado no es lo mismo que malo.
  * disponibilidad = 1 si TikTok dice que hay inventario, 0 si no (no se promociona lo que no se puede comprar).
  * afinidad = qué tanto se parece a nuestro negocio (hogar, cocina, navidad, empaque):
      AFFINITY_CATEGORY si una palabra clave del grupo aparece en la CATEGORÍA, AFFINITY_TITLE si solo aparece
      en el título, AFFINITY_NONE si no aparece. Las palabras están en constantes editables (AFFINITY_KEYWORDS).
  * Un producto `suspect` (importes o unidades imposibles, datos de prueba de la API) puntúa 0.

RELACIÓN CON NUESTRO CATÁLOGO: contra los productos activos de TikTok Shop (`ops.tiktok_shop_product`):
"misma categoría" si comparten un nombre de categoría; "equivalente" si los títulos comparten al menos
MIN_SHARED_TOKENS palabras significativas y su similitud (Jaccard) llega a MIN_JACCARD; entonces se compara el precio.

"Producto nuevo": visto por primera vez en los últimos NEW_DAYS días Y después de que terminó la primera carga
completa. Antes de esa carga TODO sería "nuevo" (el catálogo recién se estaba descubriendo).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_creator import (
    CATALOG_VISIBLE_CAP,
    CATEGORY_SEPARATOR,
    TikTokCreatorSettings,
    token_status,
)
from app.models import (
    SyncRun,
    TikTokCreatorOrder,
    TikTokCreatorProduct,
    TikTokCreatorProductHistory,
    TikTokCreatorProfile,
    TikTokCreatorShowcaseProduct,
    TikTokShopProduct,
)
from app.tiktok_creator_ops import (
    FULL_INTERVAL,
    JOB_TYPE,
    MODE_FULL,
    MODE_LIGHT,
    first_complete_full_at,
    last_complete_full_at,
)

Row = dict[str, Any]

# --- puntaje ----------------------------------------------------------------------------------------------
COMMISSION_REFERENCE_USD = 5.0  # una comisión de $5 por unidad (o más) satura el factor
DEMAND_SATURATION_UNITS = 10_000
DEMAND_FLOOR = 0.1
AFFINITY_CATEGORY = 1.0
AFFINITY_TITLE = 0.8
AFFINITY_NONE = 0.4
SCORE_SCALE = 100

# Palabras clave por grupo de negocio (minúsculas; coinciden como palabra completa). Editables sin tocar la lógica.
AFFINITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hogar": (
        "home", "home decor", "decor", "decoration", "furniture", "bedding", "bath", "storage", "organization",
        "lighting", "candle", "candles", "rug", "curtain", "hogar", "decoración", "decoracion", "vela", "velas",
    ),
    "cocina": (
        "kitchen", "cookware", "bakeware", "dining", "tableware", "utensil", "utensils", "cutlery", "coffee",
        "cocina", "vajilla", "utensilios",
    ),
    "navidad": (
        "christmas", "xmas", "holiday", "santa", "ornament", "ornaments", "wreath", "garland", "stocking",
        "tinsel", "elf", "navidad", "navideño", "navideña", "navideno", "navidena",
    ),
    "empaque": (
        "packaging", "gift box", "gift bag", "gift wrap", "wrapping", "ribbon", "mailer", "empaque",
        "envoltura", "cinta",
    ),
}
_AFFINITY_PATTERNS = {
    group: re.compile(r"\b(?:" + "|".join(re.escape(k) for k in sorted(words, key=len, reverse=True)) + r")\b")
    for group, words in AFFINITY_KEYWORDS.items()
}

# --- relación con nuestro catálogo ------------------------------------------------------------------------
MIN_SHARED_TOKENS = 2
MIN_JACCARD = 0.4
MIN_TOKEN_LENGTH = 3
STOPWORDS = frozenset({"the", "and", "for", "with", "set", "pack", "pcs", "piece", "pieces", "new", "los", "las", "con", "para", "del"})
OUR_ACTIVE_STATUS = "ACTIVATE"

# --- ventanas y límites -----------------------------------------------------------------------------------
NEW_DAYS = 7
UNITS_LIFETIME_MONTHS = 12  # `units_sold` es acumulado: se reparte en un año para estimar la demanda mensual
TOP_N = 10
MAX_CHANGES = 20
DEFAULT_LIMIT = 50
SORTS = ("score", "commission", "units", "price", "newest")
PERMISSION_EXPLANATIONS: dict[str, tuple[str, str]] = {
    "LIVE_STREAM_PERMISSION": (
        "Transmisiones en vivo",
        "La cuenta puede hacer LIVE en TikTok y mostrar productos durante la transmisión.",
    ),
    "SELF_SALE_PERMISSION": (
        "Autoventa",
        "La cuenta puede promocionar y vender productos de su propia tienda como creador.",
    ),
}
PERMISSIONS_NOTE = "Descripciones interpretadas del nombre del permiso; TikTok no las devuelve en el perfil."


def _f(value: Decimal | float | None) -> float | None:
    return float(value) if value is not None else None


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


# --- reglas puras -----------------------------------------------------------------------------------------


def affinity(category: str | None, title: str | None) -> tuple[float, list[str]]:
    """(factor de afinidad, grupos que coincidieron). La categoría pesa más que el título."""
    category_text, title_text = (category or "").lower(), (title or "").lower()
    in_category = [g for g, rx in _AFFINITY_PATTERNS.items() if rx.search(category_text)]
    in_title = [g for g, rx in _AFFINITY_PATTERNS.items() if rx.search(title_text)]
    if in_category:
        return AFFINITY_CATEGORY, sorted(set(in_category) | set(in_title))
    if in_title:
        return AFFINITY_TITLE, sorted(in_title)
    return AFFINITY_NONE, []


def commission_per_unit(amount: float | None, rate: float | None, price: float | None) -> float | None:
    """Comisión por unidad: el monto que informa TikTok; si falta, tasa x precio."""
    if amount is not None:
        return amount
    if rate is not None and price is not None:
        return rate * price
    return None


def demand_factor(units_sold: int) -> float:
    raw = math.log1p(max(units_sold, 0)) / math.log1p(DEMAND_SATURATION_UNITS)
    return max(DEMAND_FLOOR, min(1.0, raw))


def score_product(
    *, per_unit: float | None, units_sold: int, has_inventory: bool, affinity_factor: float, suspect: bool
) -> Row:
    """Puntaje 0-100 con su desglose (ver el docstring del módulo)."""
    commission_factor = min(1.0, max(per_unit or 0.0, 0.0) / COMMISSION_REFERENCE_USD)
    demand = demand_factor(units_sold)
    availability = 1.0 if has_inventory else 0.0
    total = 0.0 if suspect else SCORE_SCALE * commission_factor * demand * availability * affinity_factor
    return {
        "score": round(total, 1),
        "commission_factor": round(commission_factor, 3),
        "demand_factor": round(demand, 3),
        "availability": availability,
        "affinity": affinity_factor,
    }


def monthly_units(units_sold: int) -> float:
    """Demanda mensual aproximada: unidades acumuladas repartidas en UNITS_LIFETIME_MONTHS meses."""
    return max(units_sold, 0) / UNITS_LIFETIME_MONTHS


def title_tokens(title: str | None) -> frozenset[str]:
    words = re.findall(r"[a-záéíóúñü0-9]+", (title or "").lower())
    return frozenset(w for w in words if len(w) >= MIN_TOKEN_LENGTH and w not in STOPWORDS)


def token_similarity(a: frozenset[str], b: frozenset[str]) -> tuple[int, float]:
    """(palabras compartidas, similitud de Jaccard)."""
    shared = len(a & b)
    union = len(a | b)
    return shared, (shared / union if union else 0.0)


@dataclass(frozen=True, slots=True)
class OurProduct:
    product_id: str
    title: str
    tokens: frozenset[str]
    category_names: frozenset[str]
    price: float | None


def best_equivalent(tokens: frozenset[str], ours: list[OurProduct]) -> tuple[OurProduct, float] | None:
    """Nuestro producto más parecido (por palabras del título) si pasa los umbrales; None si ninguno."""
    best: tuple[OurProduct, float] | None = None
    for ours_item in ours:
        shared, jaccard = token_similarity(tokens, ours_item.tokens)
        if shared >= MIN_SHARED_TOKENS and jaccard >= MIN_JACCARD and (best is None or jaccard > best[1]):
            best = (ours_item, jaccard)
    return best


def relation_to_catalog(category: str | None, price: float | None, tokens: frozenset[str], ours: list[OurProduct]) -> Row:
    """Relación de un producto del catálogo de afiliados con lo que ya vendemos."""
    names = {n.lower() for n in (category or "").split(CATEGORY_SEPARATOR) if n}
    same = [o for o in ours if o.category_names & names]
    match = best_equivalent(tokens, ours)
    equivalent: Row | None = None
    if match is not None:
        item, similarity = match
        diff = (price - item.price) / item.price if price is not None and item.price else None
        equivalent = {
            "product_id": item.product_id, "title": item.title, "our_price": item.price, "their_price": price,
            "price_difference_pct": round(diff * 100, 1) if diff is not None else None, "similarity": round(similarity, 2),
        }
    return {"sells_same_category": bool(same), "our_products_in_category": len(same), "equivalent": equivalent}


async def our_catalog(session: AsyncSession) -> list[OurProduct]:
    """Productos activos de nuestra tienda de TikTok Shop, listos para comparar."""
    rows = await session.scalars(select(TikTokShopProduct).where(TikTokShopProduct.status == OUR_ACTIVE_STATUS))
    return [
        OurProduct(
            product_id=r.product_id,
            title=r.title or "",
            tokens=title_tokens(r.title),
            category_names=frozenset(str(c).lower() for c in (r.categories or [])),
            price=_f(r.min_price),
        )
        for r in rows
    ]


# --- catálogo puntuado ------------------------------------------------------------------------------------


async def _new_cutoff(session: AsyncSession, now: datetime) -> datetime | None:
    """Desde cuándo un producto cuenta como nuevo; None si aún no hay una primera carga completa."""
    first_full = await first_complete_full_at(session)
    return max(first_full, now - timedelta(days=NEW_DAYS)) if first_full is not None else None


def _scored(row: TikTokCreatorProduct, ours: list[OurProduct], new_cutoff: datetime | None) -> Row:
    price = _f(row.sale_price_min if row.sale_price_min is not None else row.original_price_min)
    per_unit = commission_per_unit(_f(row.commission_amount), _f(row.commission_rate), price)
    affinity_factor, groups = affinity(row.category, row.title)
    first_seen = _aware(row.first_seen_at)
    return {
        "product_id": row.product_id,
        "title": row.title,
        "image_url": row.image_url,
        "detail_link": row.detail_link,
        "category": row.category,
        "category_leaf": row.category_leaf,
        "price": price,
        "original_price": _f(row.original_price_min),
        "currency": row.currency,
        "commission_rate": _f(row.commission_rate),
        "commission_per_unit": per_unit,
        "units_sold": row.units_sold,
        "has_inventory": row.has_inventory,
        "shop_name": row.shop_name,
        "sale_region": row.sale_region,
        "suspect": row.suspect,
        "first_seen_at": first_seen,
        "is_new": bool(new_cutoff is not None and first_seen is not None and first_seen > new_cutoff),
        "affinity_groups": groups,
        **score_product(
            per_unit=per_unit, units_sold=row.units_sold, has_inventory=row.has_inventory,
            affinity_factor=affinity_factor, suspect=row.suspect,
        ),
        "relation": relation_to_catalog(row.category, price, title_tokens(row.title), ours),
    }


_SORT_KEYS: dict[str, Any] = {
    "score": lambda i: (i["score"], i["units_sold"]),
    "commission": lambda i: i["commission_per_unit"] or 0.0,
    "units": lambda i: i["units_sold"],
    "price": lambda i: i["price"] or 0.0,
    "newest": lambda i: i["first_seen_at"] or datetime.min.replace(tzinfo=UTC),
}


async def scored_products(
    session: AsyncSession,
    *,
    q: str | None = None,
    category: str | None = None,
    min_commission: float | None = None,
    min_units: int | None = None,
    include_suspect: bool = False,
    now: datetime | None = None,
) -> list[Row]:
    """Productos activos filtrados en SQL y puntuados en Python (son a lo sumo ~10.000)."""
    now = now or datetime.now(UTC)
    query = select(TikTokCreatorProduct).where(TikTokCreatorProduct.active.is_(True))
    if not include_suspect:
        query = query.where(TikTokCreatorProduct.suspect.is_(False))
    if q:
        query = query.where(TikTokCreatorProduct.title.icontains(q, autoescape=True))
    if category:
        query = query.where(TikTokCreatorProduct.category.icontains(category, autoescape=True))
    if min_commission is not None:
        query = query.where(TikTokCreatorProduct.commission_rate >= Decimal(str(min_commission)) / 100)
    if min_units is not None:
        query = query.where(TikTokCreatorProduct.units_sold >= min_units)
    ours = await our_catalog(session)
    cutoff = await _new_cutoff(session, now)
    return [_scored(r, ours, cutoff) for r in await session.scalars(query)]


async def opportunities(
    session: AsyncSession,
    *,
    q: str | None = None,
    category: str | None = None,
    min_commission: float | None = None,
    min_units: int | None = None,
    sort: str = "score",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    include_suspect: bool = False,
) -> Row:
    """Catálogo de afiliados puntuado, filtrable y ordenable (descendente). `min_commission` va en porcentaje."""
    items = await scored_products(
        session, q=q, category=category, min_commission=min_commission, min_units=min_units, include_suspect=include_suspect
    )
    items.sort(key=_SORT_KEYS[sort], reverse=True)
    return {
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "items": items[offset : offset + limit],
        "score_formula": "100 x factor_comisión x factor_demanda x disponibilidad x afinidad",
        "constants": {
            "commission_reference_usd": COMMISSION_REFERENCE_USD,
            "demand_saturation_units": DEMAND_SATURATION_UNITS,
            "demand_floor": DEMAND_FLOOR,
            "affinity": {"category": AFFINITY_CATEGORY, "title": AFFINITY_TITLE, "none": AFFINITY_NONE},
            "affinity_keywords": {g: list(w) for g, w in AFFINITY_KEYWORDS.items()},
        },
    }


async def _history_changes(session: AsyncSession) -> Row:
    """Cambios de precio y comisión entre las dos últimas fotos semanales."""
    days = (
        await session.scalars(
            select(TikTokCreatorProductHistory.snapshot_date).distinct().order_by(TikTokCreatorProductHistory.snapshot_date.desc()).limit(2)
        )
    ).all()
    if len(days) < 2:
        return {"available": False, "reason": "Se necesitan dos fotos semanales para comparar.", "price": [], "commission": []}
    latest, previous = days
    rows = (
        await session.scalars(
            select(TikTokCreatorProductHistory).where(TikTokCreatorProductHistory.snapshot_date.in_((latest, previous)))
        )
    ).all()
    by_product: dict[str, dict[Any, TikTokCreatorProductHistory]] = defaultdict(dict)
    for row in rows:
        by_product[row.product_id][row.snapshot_date] = row
    titles = {p.product_id: p.title for p in await session.scalars(select(TikTokCreatorProduct).where(TikTokCreatorProduct.product_id.in_(list(by_product))))}
    price_changes: list[Row] = []
    commission_changes: list[Row] = []
    for product_id, pair in by_product.items():
        old, new = pair.get(previous), pair.get(latest)
        if old is None or new is None:
            continue
        if old.price is not None and new.price is not None and old.price != new.price:
            price_changes.append(_change(product_id, titles.get(product_id), _f(old.price), _f(new.price)))
        if old.commission_rate is not None and new.commission_rate is not None and old.commission_rate != new.commission_rate:
            commission_changes.append(_change(product_id, titles.get(product_id), _f(old.commission_rate), _f(new.commission_rate)))
    price_changes.sort(key=lambda c: -abs(c["change_pct"] or 0))
    commission_changes.sort(key=lambda c: -abs(c["change_pct"] or 0))
    return {
        "available": True, "from": previous, "to": latest,
        "price_count": len(price_changes), "commission_count": len(commission_changes),
        "price": price_changes[:MAX_CHANGES], "commission": commission_changes[:MAX_CHANGES],
    }


def _change(product_id: str, title: str | None, old: float | None, new: float | None) -> Row:
    pct = (new - old) / old * 100 if old and new is not None else None
    return {"product_id": product_id, "title": title, "old": old, "new": new, "change_pct": round(pct, 1) if pct is not None else None}


async def summary(session: AsyncSession) -> Row:
    """Distribución por categoría, comisión promedio, productos nuevos, cambios desde la foto anterior y top 10."""
    items = await scored_products(session)
    suspect = await session.scalar(
        select(func.count()).select_from(TikTokCreatorProduct).where(TikTokCreatorProduct.active.is_(True), TikTokCreatorProduct.suspect.is_(True))
    ) or 0
    rates = [i["commission_rate"] for i in items if i["commission_rate"] is not None]
    groups: dict[str, list[Row]] = defaultdict(list)
    for item in items:
        groups[(item["category"] or "Sin categoría").split(CATEGORY_SEPARATOR)[0]].append(item)
    by_category = sorted(
        (
            {
                "category": name,
                "products": len(group),
                "avg_commission_rate": _avg([i["commission_rate"] for i in group if i["commission_rate"] is not None]),
                "units_sold": sum(i["units_sold"] for i in group),
                "avg_score": round(sum(i["score"] for i in group) / len(group), 1),
                "affine": sum(1 for i in group if i["affinity_groups"]),
            }
            for name, group in groups.items()
        ),
        key=lambda c: -c["products"],
    )
    new = sorted((i for i in items if i["is_new"]), key=lambda i: -i["score"])
    top = sorted(items, key=_SORT_KEYS["score"], reverse=True)[:TOP_N]
    return {
        "products": len(items),
        "suspect_products": suspect,
        "avg_commission_rate": _avg(rates),
        "by_category": by_category,
        "new_this_week": {"count": len(new), "items": new[:TOP_N]},
        "changes": await _history_changes(session),
        "top": top,
    }


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


async def new_products(session: AsyncSession, now: datetime | None = None) -> list[Row]:
    """Productos nuevos de la semana ordenados por puntaje (para el Centro de acciones)."""
    items = await scored_products(session, now=now)
    return sorted((i for i in items if i["is_new"]), key=_SORT_KEYS["score"], reverse=True)


# --- perfil, vitrina y pedidos ----------------------------------------------------------------------------


async def profile(session: AsyncSession) -> Row:
    """Última foto del perfil y sus permisos explicados en español."""
    row = await session.scalar(select(TikTokCreatorProfile).order_by(TikTokCreatorProfile.snapshot_date.desc()).limit(1))
    if row is None:
        return {"available": False, "profile": None, "permissions": [], "permissions_note": PERMISSIONS_NOTE}
    granted = [str(p) for p in (row.permissions or [])]
    known = [{"code": c, "label": label, "explanation": text, "granted": c in granted} for c, (label, text) in PERMISSION_EXPLANATIONS.items()]
    extra = [{"code": c, "label": c, "explanation": "Permiso sin descripción conocida.", "granted": True} for c in granted if c not in PERMISSION_EXPLANATIONS]
    return {
        "available": True,
        "profile": {
            "username": row.username, "creator_user_id": row.creator_user_id, "avatar_url": row.avatar_url,
            "user_type": row.user_type, "seller_type": row.seller_type, "register_region": row.register_region,
            "selection_region": row.selection_region, "snapshot_date": row.snapshot_date,
            "catalog_total": row.catalog_total, "showcase_total": row.showcase_total,
        },
        "permissions": known + extra,
        "permissions_note": PERMISSIONS_NOTE,
    }


async def showcase(session: AsyncSession) -> Row:
    """Vitrina propia: productos con su estado de revisión e inventario."""
    rows = (
        await session.scalars(
            select(TikTokCreatorShowcaseProduct).where(TikTokCreatorShowcaseProduct.active.is_(True)).order_by(TikTokCreatorShowcaseProduct.title)
        )
    ).all()
    by_review: dict[str, int] = defaultdict(int)
    by_inventory: dict[str, int] = defaultdict(int)
    for r in rows:
        by_review[r.review_status or "UNKNOWN"] += 1
        by_inventory[r.inventory_status or "UNKNOWN"] += 1
    return {
        "available": bool(rows),
        "summary": {"products": len(rows), "hidden": sum(1 for r in rows if r.is_hidden), "by_review_status": dict(by_review), "by_inventory_status": dict(by_inventory)},
        "products": [
            {
                "product_id": r.product_id, "origin": r.origin, "title": r.title, "image_url": r.image_url,
                "original_price_min": _f(r.original_price_min), "original_price_max": _f(r.original_price_max),
                "discount_price_min": _f(r.discount_price_min), "discount_price_max": _f(r.discount_price_max),
                "currency": r.currency, "review_status": r.review_status, "inventory_status": r.inventory_status,
                "added_status": r.added_status, "is_hidden": r.is_hidden, "shop_name": r.shop_name,
            }
            for r in rows
        ],
    }


async def orders(session: AsyncSession, days: int, now: datetime | None = None) -> Row:
    """Pedidos de afiliado de los últimos `days` días con sus comisiones. Vacío si no hay (hoy no hay ninguno).

    La estructura del pedido de TikTok no pudo verificarse (la cuenta no tenía pedidos): el crudo no se expone.
    """
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    rows = [
        r
        for r in await session.scalars(select(TikTokCreatorOrder).order_by(TikTokCreatorOrder.created_at.desc()))
        if (created := _aware(r.created_at)) is None or created >= since
    ]
    return {
        "days": days,
        "available": bool(rows),
        "summary": {"orders": len(rows), "commission": round(sum(float(r.commission_amount or 0) for r in rows), 2)},
        "orders": [
            {"order_id": r.order_id, "status": r.status, "created_at": r.created_at, "commission_amount": _f(r.commission_amount), "currency": r.currency}
            for r in rows
        ],
    }


# --- estado de la conexión --------------------------------------------------------------------------------


async def _latest_runs(session: AsyncSession) -> tuple[SyncRun | None, SyncRun | None]:
    """(última ejecución, última ejecución ligera con éxito)."""
    runs = (await session.scalars(select(SyncRun).where(SyncRun.job_type == JOB_TYPE).order_by(SyncRun.started_at.desc()).limit(60))).all()
    light = next((r for r in runs if r.status in ("success", "partial") and (r.stats or {}).get("mode") == MODE_LIGHT), None)
    return (runs[0] if runs else None), light


def _tokens_file() -> Path | None:
    try:
        return TikTokCreatorSettings().tokens_file
    except ValidationError:
        return None


async def status(session: AsyncSession, now: datetime | None = None, tokens_file: Path | None = None) -> Row:
    """¿Configurado, conectado, o hay que reautorizar? Vencimientos, últimas cargas y cobertura del catálogo."""
    now = now or datetime.now(UTC)
    path = tokens_file or _tokens_file()
    tokens = token_status(path) if path is not None else None
    last_run, last_light = await _latest_runs(session)
    last_full = await last_complete_full_at(session)
    run_stats = (last_run.stats or {}) if last_run else {}
    configured = path is not None
    needs_reauth = configured and (not (tokens or {}).get("usable") or not (tokens or {}).get("refresh_valid") or bool(run_stats.get("needs_reauth")))
    loaded = await session.scalar(select(func.count()).select_from(TikTokCreatorProduct).where(TikTokCreatorProduct.active.is_(True))) or 0
    latest = await session.scalar(select(TikTokCreatorProfile).order_by(TikTokCreatorProfile.snapshot_date.desc()).limit(1))
    total = latest.catalog_total if latest else None
    return {
        "configured": configured,
        "connected": bool(configured and not needs_reauth and last_run is not None and last_run.status in ("success", "partial")),
        "needs_reauth": needs_reauth,
        "message": _status_message(configured, needs_reauth),
        "tokens": (
            {"access_expires_at": tokens["access_expires_at"], "refresh_expires_at": tokens["refresh_expires_at"],
             "access_valid": tokens["access_valid"], "refresh_valid": tokens["refresh_valid"]}
            if tokens else None
        ),
        "last_run": (
            {"status": last_run.status, "mode": run_stats.get("mode"), "started_at": _aware(last_run.started_at), "error": last_run.error}
            if last_run else None
        ),
        "last_light_sync_at": _aware(last_light.finished_at or last_light.started_at) if last_light else None,
        "last_full_sync_at": last_full,
        "next_full_due_at": (last_full + FULL_INTERVAL) if last_full else None,
        "coverage": {
            "loaded": loaded,
            "total": total,
            "pct": round(loaded / total, 4) if total else None,
            "visible_cap": CATALOG_VISIBLE_CAP,
            "note": "TikTok solo deja ver 10.000 productos por consulta; lo que está fuera de ese tope no se puede cargar.",
        },
        "modes": {"full": MODE_FULL, "light": MODE_LIGHT},
    }


def _status_message(configured: bool, needs_reauth: bool) -> str:
    if not configured:
        return "TikTok Creator no está autorizado: falta el archivo de tokens."
    if needs_reauth:
        return "Hay que reautorizar la cuenta de creador: el token ya no se puede renovar."
    return "Conectado."
