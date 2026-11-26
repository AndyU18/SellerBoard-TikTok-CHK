"""Análisis del mercado de creadores del vendedor: puntaje de CANDIDATO explicado y anti-recomendación.

Importado solo por `app.tiktok_creator_market_api` y `app.tiktok_creator_market_actions`. Solo lectura sobre
las tablas que llena la fuente `tiktok_creator_market`. Qué es cada pieza y por qué:

PUNTAJE (0-100) = ajuste + rendimiento + tamaño, y cada parte trae su razón en español:
  * Ajuste a nuestro negocio (FIT_WEIGHT): categorías de primer nivel que TikTok asigna al creador (hogar,
    cocina) y palabras de su usuario/apodo (decoración navideña, empaque, hogar, cocina). La API no filtra por
    categoría en el servidor y no entrega texto de sus videos, así que este es el mejor indicio disponible.
  * Rendimiento reportado (PERFORMANCE_WEIGHT): GMV (importe o piso de su rango), unidades vendidas (piso del
    rango) y, si ya se leyó su ficha, engagement. Escala logarítmica: la diferencia entre $0 y $1.000 importa más
    que entre $50.000 y $60.000.
  * Tamaño (SIZE_WEIGHT): los creadores medianos (10 mil a 100 mil seguidores) son los que más rinden por
    muestra enviada; los muy pequeños casi no mueven ventas y los muy grandes rara vez aceptan una muestra.

ANTI-RECOMENDACIÓN: se REÚSA `sample_policy` (muestras, órdenes y recomendación por creador) y `creator_profit`
(órdenes y utilidad), sin duplicar su lógica. Un creador al que ya enviamos muestras y no vendió, o al que la
política manda Pausar/Cortar, NO es candidato ("No enviar muestras"); uno que ya vende con nosotros se marca
"ya trabajamos con él" y tampoco entra a la lista de nuevos. Se cruza por usuario en minúsculas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_creator_market import ROUTES_IN_USE
from app.creator_profit import creator_profit_report
from app.models import (
    SyncRun,
    TikTokMarketCreator,
    TikTokMarketCreatorSnapshot,
    TikTokOpenCollaboration,
    TikTokTargetCollaboration,
)
from app.sample_policy import CUT, PAUSE, sample_policy_report
from app.tiktok_creator_market_ops import JOB_TYPE

Row = dict[str, Any]
Sort = Literal["score", "followers", "gmv", "newest"]
CollabKind = Literal["open", "target", "all"]

# --- pesos del puntaje (suman 100) -------------------------------------------------------------------------
FIT_WEIGHT = 35.0
PERFORMANCE_WEIGHT = 35.0
SIZE_WEIGHT = 30.0
CANDIDATE_SCORE = 55.0  # desde aquí: "Candidato"
WATCH_SCORE = 30.0  # desde aquí: "Vigilar"; por debajo: "Bajo ajuste"

# --- nuestro negocio: hogar, cocina, decoración navideña y empaque -----------------------------------------
# Categorías de primer nivel de TikTok Shop (árbol V2, `/product/202309/categories`); los ids son estables.
TOP_CATEGORY_NAMES: dict[str, str] = {
    "600001": "Home Supplies", "600024": "Kitchenware", "600154": "Textiles & Soft Furnishings",
    "600942": "Household Appliances", "601152": "Womenswear & Underwear", "601303": "Modest Fashion",
    "601352": "Shoes", "601450": "Beauty & Personal Care", "601739": "Phones & Electronics",
    "601755": "Computers & Office Equipment", "602118": "Pet Supplies", "602284": "Baby & Maternity",
    "603014": "Sports & Outdoor", "604206": "Toys & Hobbies", "604453": "Furniture", "604579": "Tools & Hardware",
    "604968": "Home Improvement", "605196": "Automotive & Motorcycle", "605248": "Fashion Accessories",
    "700437": "Food & Beverages", "700645": "Health", "801928": "Books, Magazines & Audio",
    "802184": "Kids' Fashion", "824328": "Menswear & Underwear", "824584": "Luggage & Bags",
    "834312": "Virtual Products", "856720": "Pre-Owned", "951432": "Collectibles",
    "953224": "Jewelry Accessories & Derivatives",
}
INTEREST_CATEGORY_IDS: dict[str, frozenset[str]] = {
    "hogar": frozenset({"600001", "600154", "604453", "604968"}),
    "cocina": frozenset({"600024"}),
}
OUR_CATEGORY_IDS = frozenset().union(*INTEREST_CATEGORY_IDS.values())
INTEREST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "decoración navideña": ("christmas", "xmas", "navidad", "holiday", "tinsel", "santa", "decor", "ornament", "wreath", "garland"),
    "empaque": ("gift", "wrap", "packag", "unbox"),
    "hogar": ("home", "house", "cozy", "diy"),
    "cocina": ("kitchen", "cook"),
}
CATEGORY_HIT_BASE = 0.5  # un creador con alguna categoría nuestra ya tiene la mitad del ajuste
KEYWORD_FIT_FIRST = 0.6  # primera familia de palabras que coincide
KEYWORD_FIT_EXTRA = 0.2  # cada familia adicional
KEYWORD_FIT_IN_TOTAL = 0.5  # las palabras valen la mitad que la categoría oficial (son un indicio débil)

# --- rendimiento -------------------------------------------------------------------------------------------
GMV_FULL_USD = 10_000.0  # GMV que da rendimiento pleno
UNITS_FULL = 1_000.0
ENGAGEMENT_FULL = 0.02  # fracción (unidad de TikTok sin documentar: por eso pesa poco)
PERF_GMV_SHARE, PERF_UNITS_SHARE, PERF_ENGAGEMENT_SHARE = 0.60, 0.20, 0.20  # con ficha leída
PERF_GMV_SHARE_NO_DETAIL, PERF_UNITS_SHARE_NO_DETAIL = 0.75, 0.25

# --- tamaño: (seguidores desde, nombre, valor 0-1) ---------------------------------------------------------
SIZE_TIERS: tuple[tuple[int, str, float], ...] = (
    (0, "nano", 0.1),
    (1_000, "micro", 0.6),
    (10_000, "mediano", 1.0),
    (100_000, "grande", 0.8),
    (500_000, "macro", 0.5),
    (1_000_000, "mega", 0.3),
)

# --- relación con nuestras muestras ------------------------------------------------------------------------
RELATION_NONE = "none"
RELATION_SAMPLED_NO_SALES = "sampled_no_sales"
RELATION_PAUSED = "paused_by_policy"
RELATION_WORKING = "already_working"
EXCLUDED_RELATIONS = frozenset({RELATION_SAMPLED_NO_SALES, RELATION_PAUSED, RELATION_WORKING})
RECOMMENDATION_NO_SAMPLES = "No enviar muestras"
RECOMMENDATION_WORKING = "Ya trabajamos con él"

NEW_WINDOW = timedelta(days=7)
BASELINE_GRACE = timedelta(days=1)  # lo visto en el primer día es la carga inicial, no "creadores nuevos"
GROWTH_MIN_WEEKS = 2
DEFAULT_LIMIT = 50
SHORTLIST_SIZE = 10
TOP_IN_SUMMARY = 10
UNLIMITED = 10**9


@dataclass(frozen=True, slots=True)
class CreatorFacts:
    """Lo que el puntaje necesita de un creador (separa el cálculo de la base de datos)."""

    username: str | None
    nickname: str | None
    follower_count: int
    category_ids: tuple[str, ...]
    gmv: float
    units_sold: float
    engagement_rate: float | None


@dataclass(frozen=True, slots=True)
class OurHistory:
    """Lo nuestro con un creador: muestras enviadas, órdenes que vendió y lo que dice la política de muestras."""

    samples: int
    orders: int
    profit: float
    recommendation: str | None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _log_scale(value: float, full: float) -> float:
    """0 con valor 0 y 1 en `full`; logarítmica para que los primeros dólares/unidades pesen más."""
    return _clamp01(math.log10(1 + max(value, 0.0)) / math.log10(1 + full))


def size_tier(followers: int) -> tuple[str, float]:
    """(nombre, valor 0-1) del tramo de seguidores."""
    name, value = SIZE_TIERS[0][1], SIZE_TIERS[0][2]
    for start, tier, tier_value in SIZE_TIERS:
        if followers >= start:
            name, value = tier, tier_value
    return name, value


def matched_interests(facts: CreatorFacts) -> tuple[list[str], list[str]]:
    """(intereses por categoría oficial, intereses por palabras del usuario/apodo) en que coincide el creador."""
    ids = set(facts.category_ids)
    by_category = [name for name, group in INTEREST_CATEGORY_IDS.items() if ids & group]
    text = f"{facts.username or ''} {facts.nickname or ''}".lower()
    by_keyword = [name for name, words in INTEREST_KEYWORDS.items() if any(word in text for word in words)]
    return by_category, by_keyword


def fit_value(facts: CreatorFacts) -> tuple[float, str]:
    """Ajuste 0-1 a nuestro negocio y su razón."""
    by_category, by_keyword = matched_interests(facts)
    hits = len(set(facts.category_ids) & OUR_CATEGORY_IDS)
    category = CATEGORY_HIT_BASE + (1 - CATEGORY_HIT_BASE) * hits / len(facts.category_ids) if hits else 0.0
    keyword = min(1.0, KEYWORD_FIT_FIRST + KEYWORD_FIT_EXTRA * (len(by_keyword) - 1)) if by_keyword else 0.0
    value = _clamp01(category + KEYWORD_FIT_IN_TOTAL * keyword)
    parts = []
    if by_category:
        parts.append(f"categoría de TikTok: {', '.join(by_category)}")
    if by_keyword:
        parts.append(f"su nombre sugiere: {', '.join(by_keyword)}")
    return value, "; ".join(parts) or "sin categoría ni palabras de nuestro negocio"


def performance_value(facts: CreatorFacts) -> tuple[float, str]:
    """Rendimiento 0-1 con lo que TikTok reporta (GMV, unidades y, si hay ficha, engagement) y su razón."""
    gmv, units = _log_scale(facts.gmv, GMV_FULL_USD), _log_scale(facts.units_sold, UNITS_FULL)
    if facts.engagement_rate is None:
        value = PERF_GMV_SHARE_NO_DETAIL * gmv + PERF_UNITS_SHARE_NO_DETAIL * units
    else:
        engagement = _clamp01(facts.engagement_rate / ENGAGEMENT_FULL)
        value = PERF_GMV_SHARE * gmv + PERF_UNITS_SHARE * units + PERF_ENGAGEMENT_SHARE * engagement
    reason = f"GMV reportado ${facts.gmv:,.0f}, {facts.units_sold:,.0f}+ unidades"
    if facts.engagement_rate is not None:
        reason += f", engagement {facts.engagement_rate:.2%}"
    return _clamp01(value), reason


def score_creator(facts: CreatorFacts) -> tuple[float, list[Row]]:
    """Puntaje 0-100 y sus partes: `[{name, points, max, reason}]` (lo que se muestra para explicarlo)."""
    tier, size = size_tier(facts.follower_count)
    fit, fit_reason = fit_value(facts)
    perf, perf_reason = performance_value(facts)
    parts = [
        {"name": "ajuste", "points": round(FIT_WEIGHT * fit, 1), "max": FIT_WEIGHT, "reason": fit_reason},
        {"name": "rendimiento", "points": round(PERFORMANCE_WEIGHT * perf, 1), "max": PERFORMANCE_WEIGHT, "reason": perf_reason},
        {"name": "tamaño", "points": round(SIZE_WEIGHT * size, 1), "max": SIZE_WEIGHT,
         "reason": f"{facts.follower_count:,} seguidores ({tier})"},
    ]
    return round(sum(p["points"] for p in parts), 1), parts


def classify_relation(history: OurHistory | None) -> tuple[str, str | None]:
    """(relación, razón) del creador con nosotros. Orden de prioridad: sin ventas tras muestras, política, ya vende."""
    if history is None:
        return RELATION_NONE, None
    if history.samples > 0 and history.orders == 0:
        return RELATION_SAMPLED_NO_SALES, f"Ya le enviamos {history.samples} muestra(s) y no vendió"
    if history.recommendation in (PAUSE, CUT):
        return RELATION_PAUSED, f"La política de muestras indica {history.recommendation}"
    if history.orders > 0:
        return RELATION_WORKING, f"Ya trabajamos con él: {history.orders} orden(es) y ${history.profit:,.0f} de utilidad"
    return RELATION_NONE, None


def recommendation_for(score: float, relation: str) -> str:
    if relation == RELATION_WORKING:
        return RECOMMENDATION_WORKING
    if relation in EXCLUDED_RELATIONS:
        return RECOMMENDATION_NO_SAMPLES
    if score >= CANDIDATE_SCORE:
        return "Candidato"
    return "Vigilar" if score >= WATCH_SCORE else "Bajo ajuste"


# --- carga de datos ----------------------------------------------------------------------------------------


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _facts(row: TikTokMarketCreator) -> CreatorFacts:
    return CreatorFacts(
        username=row.username,
        nickname=row.nickname,
        follower_count=int(row.follower_count or 0),
        category_ids=tuple(str(c) for c in (row.category_ids or [])),
        gmv=_f(row.gmv_amount if row.gmv_amount is not None else row.gmv_range_min),
        units_sold=_f(row.units_sold_min),
        engagement_rate=float(row.engagement_rate) if row.engagement_rate is not None else None,
    )


async def load_our_history(session: AsyncSession) -> dict[str, OurHistory]:
    """Muestras, órdenes y utilidad por usuario (minúsculas), reusando `sample_policy` y `creator_profit`."""
    policy = {r["creator"].lower(): r for r in (await sample_policy_report(session))["rows"]}
    profit = {
        (r["creator"] or r["key"]).lower(): r
        for r in (await creator_profit_report(session, level="creator", limit=UNLIMITED))["rows"]
    }
    history: dict[str, OurHistory] = {}
    for name in policy.keys() | profit.keys():
        pol, prof = policy.get(name), profit.get(name)
        history[name] = OurHistory(
            samples=int(pol["samples"]) if pol else 0,
            orders=int((pol or prof or {}).get("orders") or 0),
            profit=_f((prof or pol or {}).get("profit")),
            recommendation=pol["recommendation"] if pol else None,
        )
    return history


async def _growth(session: AsyncSession) -> dict[str, float]:
    """Crecimiento de seguidores (fracción) entre la primera y la última foto semanal de cada creador."""
    rows = (
        await session.execute(
            select(
                TikTokMarketCreatorSnapshot.creator_open_id,
                TikTokMarketCreatorSnapshot.week_start,
                TikTokMarketCreatorSnapshot.follower_count,
            ).order_by(TikTokMarketCreatorSnapshot.creator_open_id, TikTokMarketCreatorSnapshot.week_start)
        )
    ).all()
    series: dict[str, list[int]] = {}
    for open_id, _week, followers in rows:
        series.setdefault(open_id, []).append(int(followers))
    return {
        oid: (values[-1] - values[0]) / values[0]
        for oid, values in series.items()
        if len(values) >= GROWTH_MIN_WEEKS and values[0] > 0
    }


async def _baseline(session: AsyncSession) -> datetime | None:
    first = await session.scalar(select(func.min(TikTokMarketCreator.first_seen_at)))
    return _aware(first) + BASELINE_GRACE if first is not None else None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _category_names(ids: tuple[str, ...]) -> list[str]:
    return [TOP_CATEGORY_NAMES.get(i, i) for i in ids]


def _row(
    creator: TikTokMarketCreator, history: dict[str, OurHistory], growth: dict[str, float], now: datetime, baseline: datetime | None
) -> Row:
    facts = _facts(creator)
    score, parts = score_creator(facts)
    relation, relation_reason = classify_relation(history.get((creator.username or "").lower()))
    tier, _ = size_tier(facts.follower_count)
    first_seen = _aware(creator.first_seen_at)
    return {
        "creator_open_id": creator.creator_open_id,
        "username": creator.username,
        "nickname": creator.nickname,
        "avatar_url": creator.avatar_url,
        "region": creator.region,
        "followers": facts.follower_count,
        "size_tier": tier,
        "categories": _category_names(facts.category_ids),
        "category_ids": list(facts.category_ids),
        "gmv": facts.gmv or None,
        "gmv_range": creator.gmv_range,
        "units_sold_range": creator.units_sold_range,
        "engagement_rate": facts.engagement_rate,
        "gpm": _f(creator.gpm_amount) if creator.gpm_amount is not None else None,
        "follower_growth": growth.get(creator.creator_open_id),
        "score": score,
        "score_parts": parts,
        "recommendation": recommendation_for(score, relation),
        "relation": relation,
        "relation_reason": relation_reason,
        "is_candidate": relation not in EXCLUDED_RELATIONS and score >= CANDIDATE_SCORE,
        "is_new": baseline is not None and first_seen > baseline and now - first_seen <= NEW_WINDOW,
        "first_seen_at": first_seen.isoformat(),
        "last_seen_at": _aware(creator.last_seen_at).isoformat(),
        "has_detail": creator.detail_at is not None,
    }


async def scored_creators(session: AsyncSession, now: datetime | None = None) -> list[Row]:
    """Todos los creadores activos con puntaje, relación con nuestras muestras y banderas (de mayor a menor puntaje)."""
    now = now or datetime.now(UTC)
    creators = (await session.scalars(select(TikTokMarketCreator).where(TikTokMarketCreator.active.is_(True)))).all()
    if not creators:
        return []
    history, growth, baseline = await load_our_history(session), await _growth(session), await _baseline(session)
    rows = [_row(c, history, growth, now, baseline) for c in creators]
    rows.sort(key=lambda r: (-r["score"], r["username"] or ""))
    return rows


# --- consultas para la API ---------------------------------------------------------------------------------


def _matches(row: Row, q: str | None, category: str | None, min_followers: int | None) -> bool:
    if min_followers is not None and row["followers"] < min_followers:
        return False
    if q and q.strip().lower() not in f"{row['username'] or ''} {row['nickname'] or ''}".lower():
        return False
    if category:
        wanted = category.strip().lower()
        return wanted in row["category_ids"] or any(wanted in name.lower() for name in row["categories"])
    return True


_SORTS = {
    "score": lambda r: (-r["score"], r["username"] or ""),
    "followers": lambda r: (-r["followers"], r["username"] or ""),
    "gmv": lambda r: (-(r["gmv"] or 0.0), r["username"] or ""),
    "newest": lambda r: (r["first_seen_at"],),
}


async def marketplace(
    session: AsyncSession, *, q: str | None, category: str | None, min_followers: int | None, sort: Sort,
    limit: int, offset: int, now: datetime | None = None,
) -> Row:
    rows = [r for r in await scored_creators(session, now) if _matches(r, q, category, min_followers)]
    rows.sort(key=_SORTS[sort], reverse=sort == "newest")
    return {"total": len(rows), "limit": limit, "offset": offset, "rows": rows[offset : offset + limit], "notes": _NOTES}


async def candidates(session: AsyncSession, limit: int = SHORTLIST_SIZE, now: datetime | None = None) -> Row:
    """Shortlist: los mejores creadores NUEVOS (vistos después de la carga inicial) que son candidatos y no excluidos."""
    pool = [r for r in await scored_creators(session, now) if r["is_candidate"] and r["is_new"]]
    return {
        "limit": limit,
        "total": len(pool),
        "rows": [{**r, "reason": _candidate_reason(r)} for r in pool[:limit]],
        "notes": _NOTES,
    }


def _candidate_reason(row: Row) -> str:
    """Una frase con las dos partes que más puntos aportan (la razón de que esté en la lista)."""
    best = sorted(row["score_parts"], key=lambda p: -p["points"])[:2]
    return f"Puntaje {row['score']:g}: " + "; ".join(f"{p['name']} {p['points']:g}/{p['max']:g} ({p['reason']})" for p in best)


async def summary(session: AsyncSession, now: datetime | None = None) -> Row:
    rows = await scored_creators(session, now)
    by_category: dict[str, int] = {}
    by_size: dict[str, int] = {}
    for r in rows:
        by_size[r["size_tier"]] = by_size.get(r["size_tier"], 0) + 1
        for name in r["categories"] or ["sin categoría"]:
            by_category[name] = by_category.get(name, 0) + 1
    ordered = [t[1] for t in SIZE_TIERS]
    pool = [r for r in rows if r["is_candidate"]]
    return {
        "creators": len(rows),
        "candidates": len(pool),
        "excluded": sum(1 for r in rows if r["relation"] in EXCLUDED_RELATIONS),
        "new": sum(1 for r in rows if r["is_new"]),
        "by_category": [{"category": k, "creators": v} for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])],
        "by_size": [{"tier": t, "creators": by_size.get(t, 0)} for t in ordered],
        "top_candidates": pool[:TOP_IN_SUMMARY],
        "notes": _NOTES,
    }


def _open_row(c: TikTokOpenCollaboration) -> Row:
    return {
        "kind": "open", "id": c.collaboration_id, "title": c.product_title, "status": c.status, "product_id": c.product_id,
        "product_status": c.product_status, "commission_rate": _f(c.commission_rate) if c.commission_rate is not None else None,
        "creators": c.creator_count, "showcase_creators": c.showcase_creator_count, "invited": None,
        "price_min": _f(c.price_min) if c.price_min is not None else None, "inventory": c.inventory,
        "start": c.commission_start.isoformat() if c.commission_start else None,
        "end": c.commission_end.isoformat() if c.commission_end else None,
    }


def _target_row(c: TikTokTargetCollaboration) -> Row:
    return {
        "kind": "target", "id": c.collaboration_id, "title": c.name, "status": c.status, "product_id": None,
        "product_status": None, "commission_rate": None, "creators": c.creator_count,
        "showcase_creators": c.showcase_creator_count, "invited": c.invited_count, "products": c.product_count,
        "free_sample": c.has_free_sample, "start": c.start_time.isoformat() if c.start_time else None,
        "end": c.end_time.isoformat() if c.end_time else None,
    }


async def collaborations(session: AsyncSession, kind: CollabKind, status: str | None) -> Row:
    """Colaboraciones abiertas y/o dirigidas (solo activas), con su estado y creadores involucrados."""
    rows: list[Row] = []
    if kind in ("open", "all"):
        rows += [_open_row(c) for c in await session.scalars(select(TikTokOpenCollaboration).where(TikTokOpenCollaboration.active.is_(True)))]
    if kind in ("target", "all"):
        rows += [_target_row(c) for c in await session.scalars(select(TikTokTargetCollaboration).where(TikTokTargetCollaboration.active.is_(True)))]
    wanted = status.strip().upper() if status else None
    if wanted:
        rows = [r for r in rows if (r["status"] or "").upper() == wanted]
    counts: dict[str, int] = {}
    for r in rows:
        key = f"{r['kind']}:{r['status']}"
        counts[key] = counts.get(key, 0) + 1
    rows.sort(key=lambda r: (r["kind"], -(r["creators"] or 0), r["id"]))
    return {"kind": kind, "status": wanted, "total": len(rows), "counts": counts, "rows": rows}


async def status(session: AsyncSession) -> Row:
    """Rutas/versiones en uso, cobertura de datos y última sincronización."""
    async def count(model: Any, *where: Any) -> int:
        return await session.scalar(select(func.count()).select_from(model).where(*where)) or 0

    run = await session.scalar(select(SyncRun).where(SyncRun.job_type == JOB_TYPE).order_by(SyncRun.started_at.desc()).limit(1))
    creators = await count(TikTokMarketCreator)
    return {
        "routes": ROUTES_IN_USE,
        "unavailable": _UNAVAILABLE,
        "coverage": {
            "creators": creators,
            "creators_active": await count(TikTokMarketCreator, TikTokMarketCreator.active.is_(True)),
            "creators_with_gmv": await count(TikTokMarketCreator, TikTokMarketCreator.gmv_amount.is_not(None)),
            "creators_with_detail": await count(TikTokMarketCreator, TikTokMarketCreator.detail_at.is_not(None)),
            "open_collaborations": await count(TikTokOpenCollaboration, TikTokOpenCollaboration.active.is_(True)),
            "target_collaborations": await count(TikTokTargetCollaboration, TikTokTargetCollaboration.active.is_(True)),
        },
        "last_sync": None if run is None else {
            "status": run.status, "started_at": run.started_at, "finished_at": run.finished_at,
            "stats": run.stats, "error": run.error,
        },
        "limits": _LIMITS,
    }


_UNAVAILABLE = [
    "Filtro avanzado del mercado (documentado solo para 202608): ninguna ruta probada responde.",
    "Filtros de servidor por categoría o seguidores: la API los ignora; se filtran al leer.",
    "Colaboraciones dirigidas con otro estado que ONGOING: la API responde 'invalid params'.",
]
_LIMITS = {
    "creator_page_size": 20,
    "max_pages_per_view": {"light": "3 + 1 por palabra clave", "full": "25 + 10 por palabra clave"},
    "details_per_run": {"light": 10, "full": 40},
    "snapshot_weeks": 26,
}
_NOTES = [
    "El GMV y las unidades son los que TikTok reporta (importe exacto o piso de su rango), no ventas nuestras.",
    "La categoría es la de primer nivel que TikTok asigna al creador; no se conocen los productos que promociona.",
    "Datos públicos únicamente; el id del creador es el id opaco de TikTok.",
]
