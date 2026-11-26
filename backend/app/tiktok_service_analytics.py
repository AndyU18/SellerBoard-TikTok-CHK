"""Análisis del servicio al cliente de TikTok Shop. Solo lectura sobre cifras: no hay texto ni personas que mostrar.

Lee `ops.tiktok_cs_conversation`, `ops.tiktok_cs_daily` y `ops.tiktok_cs_state` (los llena la fuente
`tiktok_service`). Reglas del dominio:
  * Cuentan como atención las respuestas HUMANAS del vendedor; el bot automático de TikTok no resuelve dudas.
  * "Sin responder" = estado `waiting_seller`: el comprador escribió después de la última respuesta humana.
  * La espera se mide desde `waiting_since` (el mensaje más viejo sin respuesta), no desde el último mensaje.
  * Las horas del día y los días de la semana se calculan en la zona `settings.report_timezone`.
  * Las conversaciones sin ningún mensaje de comprador (avisos del bot o del sistema) se cuentan aparte: no tienen
    pregunta que contestar y distorsionarían los tiempos y las categorías.

Las funciones de reglas (`percentile`, `bucket_seconds`, `wait_bucket`...) son puras.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean, median
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.connectors.tiktok_service import CATEGORIES
from app.models import TikTokCsConversation, TikTokCsDaily, TikTokCsState

STATUSES: Final = ("waiting_seller", "waiting_buyer", "closed")
WAITING_SELLER: Final = "waiting_seller"
SECONDS_PER_HOUR: Final = 3600
FAST_RESPONSE_HOURS: Final = 1
SLOW_RESPONSE_HOURS: Final = 24
P90: Final = 90
MAX_LIST_LIMIT: Final = 500
WEEKDAY_NAMES: Final = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")

# (nombre, límite superior en segundos, exclusivo). El último no tiene tope.
RESPONSE_BUCKETS: Final[tuple[tuple[str, float], ...]] = (
    ("<5 min", 5 * 60),
    ("5-15 min", 15 * 60),
    ("15-60 min", 60 * 60),
    ("1-6 h", 6 * 3600),
    ("6-24 h", 24 * 3600),
    (">24 h", float("inf")),
)
# (nombre, límite superior en horas, exclusivo): antigüedad de lo que sigue sin responder.
WAIT_BUCKETS: Final[tuple[tuple[str, float], ...]] = (
    ("<1 h", 1),
    ("1-6 h", 6),
    ("6-24 h", 24),
    ("1-3 d", 72),
    (">3 d", float("inf")),
)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.report_timezone)


def _r(value: float, digits: int = 1) -> float:
    return round(value, digits)


# --- reglas puras ------------------------------------------------------------------------------------


def percentile(values: list[float], pct: float) -> float | None:
    """Percentil por interpolación lineal; None si no hay datos."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bucket_seconds(seconds: float) -> str:
    """Rango de tiempo de respuesta al que pertenece `seconds`."""
    return next(name for name, upper in RESPONSE_BUCKETS if seconds < upper)


def wait_bucket(hours: float) -> str:
    """Rango de antigüedad de una conversación sin responder."""
    return next(name for name, upper in WAIT_BUCKETS if hours < upper)


def wait_hours(conv: TikTokCsConversation, now: datetime) -> float | None:
    """Horas que lleva sin respuesta humana (None si no está esperando al vendedor)."""
    since = _aware(conv.waiting_since)
    if conv.status != WAITING_SELLER or since is None:
        return None
    return max((now - since).total_seconds() / SECONDS_PER_HOUR, 0.0)


def response_summary(seconds: list[int], asked: int) -> dict[str, Any]:
    """Resumen de tiempos de respuesta. `asked` = conversaciones con mensaje de comprador (el denominador de %)."""
    fast = sum(1 for s in seconds if s <= FAST_RESPONSE_HOURS * SECONDS_PER_HOUR)
    slow = sum(1 for s in seconds if s <= SLOW_RESPONSE_HOURS * SECONDS_PER_HOUR)
    return {
        "conversations_with_buyer_message": asked,
        "responded": len(seconds),
        "response_rate_pct": _r(len(seconds) / asked * 100) if asked else None,
        "avg_seconds": round(mean(seconds)) if seconds else None,
        "median_seconds": round(median(seconds)) if seconds else None,
        "p90_seconds": round(percentile([float(s) for s in seconds], P90) or 0) if seconds else None,
        "within_1h_pct": _r(fast / asked * 100) if asked else None,
        "within_24h_pct": _r(slow / asked * 100) if asked else None,
    }


# --- carga -------------------------------------------------------------------------------------------


async def _conversations_since(session: AsyncSession, since: datetime | None) -> list[TikTokCsConversation]:
    query = select(TikTokCsConversation)
    if since is not None:
        query = query.where(TikTokCsConversation.created_at >= since)
    return list(await session.scalars(query))


async def _state(session: AsyncSession, key: str) -> dict[str, Any]:
    row = await session.get(TikTokCsState, key)
    return dict(row.value) if row is not None and row.value else {}


def _asked_since(convs: list[TikTokCsConversation], since: datetime) -> list[TikTokCsConversation]:
    """Conversaciones cuyo primer mensaje de comprador cayó dentro de la ventana."""
    return [c for c in convs if c.first_buyer_at is not None and _aware(c.first_buyer_at) >= since]  # type: ignore[operator]


def _official_totals(days: list[TikTokCsDaily]) -> dict[str, Any]:
    """Métricas oficiales de TikTok agregadas; los porcentajes y tiempos se promedian ponderando por sesiones."""
    rated = [d for d in days if d.official_sessions]
    total = sum(d.official_sessions or 0 for d in rated)

    def weighted(attr: str) -> float | None:
        pairs = [(float(getattr(d, attr)), d.official_sessions or 0) for d in rated if getattr(d, attr) is not None]
        weight = sum(w for _, w in pairs)
        return _r(sum(v * w for v, w in pairs) / weight, 2) if weight else None

    response_seconds = weighted("official_response_seconds")
    return {
        "sessions": total,
        "response_percentage": weighted("official_response_percentage"),
        "response_seconds": round(response_seconds) if response_seconds is not None else None,
        "satisfaction_percentage": weighted("official_satisfaction_percentage"),
        "conversion_percentage": weighted("official_conversion_percentage"),
        "guided_gmv": _r(sum(float(d.official_guided_gmv or 0) for d in rated), 2),
        "days_with_data": len(rated),
    }


# --- endpoints ---------------------------------------------------------------------------------------


async def overview(session: AsyncSession, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    """Portada: abiertas, esperando al vendedor, no leídos, tiempos de respuesta, volumen por día y categorías."""
    moment = _now(now)
    since = moment - timedelta(days=days)
    convs = await _conversations_since(session, None)
    in_window = [c for c in convs if _aware(c.created_at) >= since]  # type: ignore[operator]
    asked = _asked_since(convs, since)
    seconds = [c.first_response_seconds for c in asked if c.first_response_seconds is not None]
    daily = list(
        await session.scalars(
            select(TikTokCsDaily).where(TikTokCsDaily.day >= since.astimezone(_tz()).date()).order_by(TikTokCsDaily.day)
        )
    )
    by_category = Counter(c.category for c in in_window if c.buyer_messages > 0)
    return {
        "available": bool(convs),
        "generated_at": moment,
        "window_days": days,
        "open_conversations": sum(1 for c in convs if c.status != "closed"),
        "waiting_seller": sum(1 for c in convs if c.status == WAITING_SELLER),
        "waiting_buyer": sum(1 for c in convs if c.status == "waiting_buyer"),
        "unread_total": sum(c.unread_count for c in convs),
        "conversations_in_window": len(in_window),
        "bot_only_conversations": sum(1 for c in in_window if c.buyer_messages == 0),
        "response": response_summary(seconds, len(asked)),
        "volume_by_day": [
            {
                "date": d.day,
                "new_conversations": d.new_conversations,
                "buyer_messages": d.buyer_messages,
                "seller_messages": d.seller_messages,
                "median_response_seconds": d.median_response_seconds,
                "pending_at_close": d.pending_at_close,
            }
            for d in daily
        ],
        "categories": [{"category": name, "conversations": by_category.get(name, 0)} for name in CATEGORIES],
        "official": _official_totals(daily),
        "sync": await _state(session, "sync"),
    }


async def conversations(
    session: AsyncSession,
    status: str | None = None,
    category: str | None = None,
    min_wait_hours: float | None = None,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Lista de conversaciones: SOLO metadatos (id opaco, fechas, estado, categoría, espera, conteos)."""
    moment = _now(now)
    query = select(TikTokCsConversation)
    if status:
        query = query.where(TikTokCsConversation.status == status)
    if category:
        query = query.where(TikTokCsConversation.category == category)
    items = [(c, wait_hours(c, moment)) for c in await session.scalars(query)]
    if min_wait_hours is not None:
        items = [(c, w) for c, w in items if w is not None and w >= min_wait_hours]
    # Lo que más espera primero; las que no esperan al vendedor, por actividad reciente.
    items.sort(key=lambda cw: (-(cw[1] if cw[1] is not None else -1.0), -(_aware(cw[0].last_message_at) or moment).timestamp()))
    page = items[offset : offset + min(limit, MAX_LIST_LIMIT)]
    return {
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "conversation_id": c.conversation_id,
                "created_at": c.created_at,
                "last_message_at": c.last_message_at,
                "status": c.status,
                "category": c.category,
                "wait_hours": _r(w, 2) if w is not None else None,
                "unread_count": c.unread_count,
                "message_count": c.message_count,
                "buyer_messages": c.buyer_messages,
                "seller_messages": c.seller_messages,
                "bot_messages": c.bot_messages,
                "first_response_seconds": c.first_response_seconds,
                "last_sender_role": c.last_sender_role,
                "can_send_message": c.can_send_message,
            }
            for c, w in page
        ],
    }


async def response_times(session: AsyncSession, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    """Distribución de la primera respuesta por rangos, por hora del día y por día de la semana (zona del reporte)."""
    moment = _now(now)
    tz = _tz()
    asked = _asked_since(await _conversations_since(session, None), moment - timedelta(days=days))
    distribution: Counter[str] = Counter()
    by_hour: dict[int, list[int]] = defaultdict(list)
    by_weekday: dict[int, list[int]] = defaultdict(list)
    asked_hour: Counter[int] = Counter()
    asked_weekday: Counter[int] = Counter()
    for conv in asked:
        local = _aware(conv.first_buyer_at).astimezone(tz)  # type: ignore[union-attr]
        asked_hour[local.hour] += 1
        asked_weekday[local.weekday()] += 1
        if conv.first_response_seconds is None:
            distribution["Sin respuesta"] += 1
            continue
        distribution[bucket_seconds(conv.first_response_seconds)] += 1
        by_hour[local.hour].append(conv.first_response_seconds)
        by_weekday[local.weekday()].append(conv.first_response_seconds)

    def slot(label: Any, asked_n: int, seconds: list[int], key: str) -> dict[str, Any]:
        return {key: label, "conversations": asked_n, "responded": len(seconds), "median_seconds": round(median(seconds)) if seconds else None}

    seconds_all = [c.first_response_seconds for c in asked if c.first_response_seconds is not None]
    return {
        "window_days": days,
        "timezone": settings.report_timezone,
        "summary": response_summary(seconds_all, len(asked)),
        "distribution": [
            {"bucket": name, "count": distribution.get(name, 0)} for name in [*(n for n, _ in RESPONSE_BUCKETS), "Sin respuesta"]
        ],
        "by_hour": [slot(h, asked_hour.get(h, 0), by_hour.get(h, []), "hour") for h in range(24)],
        "by_weekday": [slot(WEEKDAY_NAMES[d], asked_weekday.get(d, 0), by_weekday.get(d, []), "weekday") for d in range(7)],
    }


async def categories(session: AsyncSession, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    """Volumen, tiempo de respuesta y no resueltas por categoría (solo conversaciones con mensaje de comprador)."""
    moment = _now(now)
    in_window = [
        c for c in await _conversations_since(session, moment - timedelta(days=days)) if c.buyer_messages > 0
    ]
    groups: dict[str, list[TikTokCsConversation]] = defaultdict(list)
    for conv in in_window:
        groups[conv.category].append(conv)
    total = len(in_window)
    rows = []
    for name in CATEGORIES:
        members = groups.get(name, [])
        seconds = [c.first_response_seconds for c in members if c.first_response_seconds is not None]
        rows.append(
            {
                "category": name,
                "conversations": len(members),
                "share_pct": _r(len(members) / total * 100) if total else None,
                "responded": len(seconds),
                "avg_response_seconds": round(mean(seconds)) if seconds else None,
                "median_response_seconds": round(median(seconds)) if seconds else None,
                "unresolved": sum(1 for c in members if c.status == WAITING_SELLER),
            }
        )
    return {"window_days": days, "total_with_buyer_message": total, "categories": rows}


async def backlog(session: AsyncSession, now: datetime | None = None) -> dict[str, Any]:
    """Conversaciones sin responder por antigüedad: <1 h, 1-6 h, 6-24 h, 1-3 d, >3 d."""
    moment = _now(now)
    waiting = [
        (c, w)
        for c in await session.scalars(select(TikTokCsConversation).where(TikTokCsConversation.status == WAITING_SELLER))
        if (w := wait_hours(c, moment)) is not None
    ]
    counts: Counter[str] = Counter(wait_bucket(w) for _, w in waiting)
    unread: Counter[str] = Counter()
    for conv, hours in waiting:
        unread[wait_bucket(hours)] += conv.unread_count
    return {
        "generated_at": moment,
        "total": len(waiting),
        "oldest_wait_hours": _r(max((w for _, w in waiting), default=0.0), 2) if waiting else None,
        "buckets": [{"bucket": name, "count": counts.get(name, 0), "unread": unread.get(name, 0)} for name, _ in WAIT_BUCKETS],
    }


async def agents(session: AsyncSession, now: datetime | None = None) -> dict[str, Any]:
    """Estado de los agentes (¿aceptan chats?) y la carga actual. Solo lectura: nada se cambia en TikTok."""
    moment = _now(now)
    state = await session.get(TikTokCsState, "agents")
    value = dict(state.value) if state is not None and state.value else {}
    can_accept = value.get("can_accept_chat")
    latest = list(await session.scalars(select(TikTokCsDaily).order_by(TikTokCsDaily.day.desc()).limit(7)))
    waiting = await backlog(session, moment)
    return {
        "can_accept_chat": can_accept,
        "status": "unknown" if can_accept is None else ("accepting_chats" if can_accept else "not_accepting_chats"),
        "updated_at": state.updated_at if state is not None else None,
        "waiting_seller": waiting["total"],
        "oldest_wait_hours": waiting["oldest_wait_hours"],
        "official_last_7_days": _official_totals(latest),
    }
