"""Persistencia y sincronización del servicio al cliente de TikTok Shop (solo cifras, nunca texto ni personas).

Todo es idempotente: cada tabla tiene clave natural (id de conversación, día, clave de estado) y volver a
sincronizar actualiza la fila en vez de duplicarla.

Sincronización incremental con topes por corrida:
  1. Se lista TODO (barato: 20 por llamada) y se reduce a metadatos.
  2. Solo se piden mensajes de lo nuevo, de lo que cambió y de lo que sigue abierto, hasta
     MAX_CONVERSATIONS_WITH_MESSAGES por corrida. Las que no caben quedan sin guardar, así que la siguiente
     corrida las vuelve a encontrar como pendientes: la base de datos ES el cursor de la carga inicial (el estado
     `sync` solo lo resume para la pantalla). Se confirma cada BATCH_SIZE conversaciones: un corte no pierde avance.
  3. Se reconstruye el historial diario de la ventana y se piden las métricas oficiales de los días que faltan.

Importado por `app.sync_jobs` (fuente `tiktok_service`) y por las pruebas. Los upserts hacen `flush`.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from statistics import mean, median
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.connectors.tiktok_service import (
    ListedConversation,
    fetch_agent_can_accept_chat,
    fetch_performance_rows,
    fetch_rows,
    list_conversations,
    partition_pending,
)
from app.connectors.tiktok_shop import TikTokShopClient
from app.models import TikTokCsConversation, TikTokCsDaily, TikTokCsState

log = logging.getLogger("chk.tiktok_service")

HISTORY_DAYS: Final = 90  # ventana de retención y de la carga inicial
MAX_CONVERSATIONS_WITH_MESSAGES: Final = 300  # tope de conversaciones con mensajes por corrida (~2-4 min)
BATCH_SIZE: Final = 50  # cada tanda se confirma: un corte a mitad de corrida no pierde lo ya guardado
IN_CLAUSE_CHUNK: Final = 500  # SQLite limita las variables de una consulta
PERFORMANCE_RECENT_DAYS: Final = 3  # estos días se piden siempre: TikTok los termina de calcular con retraso
MAX_PERFORMANCE_CALLS: Final = 30  # tope de días oficiales por corrida (el historial se completa en pocas corridas)

STATE_SYNC: Final = "sync"
STATE_AGENTS: Final = "agents"
WAITING_SELLER: Final = "waiting_seller"

Row = dict[str, Any]


def _aware(value: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona: se asume UTC."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# --- conversaciones ----------------------------------------------------------------------------------


async def known_conversations(session: AsyncSession) -> dict[str, tuple[datetime | None, str]]:
    """id -> (último mensaje guardado, estado guardado): lo que `select_pending` necesita para ser incremental."""
    rows = await session.execute(
        select(TikTokCsConversation.conversation_id, TikTokCsConversation.last_message_at, TikTokCsConversation.status)
    )
    return {cid: (_aware(last), status) for cid, last, status in rows.all()}


async def upsert_conversations(session: AsyncSession, rows: list[Row], now: datetime) -> int:
    """Inserta o actualiza conversaciones por su id opaco (una lectura por tanda, no una por fila)."""
    count = 0
    for chunk in _chunks(rows, IN_CLAUSE_CHUNK):
        ids = [r["conversation_id"] for r in chunk]
        existing = {
            c.conversation_id: c
            for c in await session.scalars(select(TikTokCsConversation).where(TikTokCsConversation.conversation_id.in_(ids)))
        }
        for row in chunk:
            values = {**row, "synced_at": now}
            found = existing.get(row["conversation_id"])
            if found is None:
                session.add(TikTokCsConversation(**values))
            else:
                for name, value in values.items():
                    setattr(found, name, value)
        count += len(chunk)
    await session.flush()
    return count


async def refresh_listed_fields(session: AsyncSession, listed: Iterable[ListedConversation]) -> int:
    """Actualiza no leídos y "puede enviar" de conversaciones ya guardadas, sin pedir mensajes (datos del listado)."""
    by_id = {item.conversation_id: item for item in listed}
    updated = 0
    for chunk in _chunks(list(by_id), IN_CLAUSE_CHUNK):
        rows = await session.scalars(select(TikTokCsConversation).where(TikTokCsConversation.conversation_id.in_(chunk)))
        for row in rows:
            item = by_id[row.conversation_id]
            if (row.unread_count, row.can_send_message) != (item.unread_count, item.can_send_message):
                row.unread_count, row.can_send_message = item.unread_count, item.can_send_message
                updated += 1
    await session.flush()
    return updated


async def purge_old(session: AsyncSession, cutoff: datetime) -> int:
    """Retención: se borran las conversaciones sin actividad desde antes de `cutoff` (la ventana de HISTORY_DAYS)."""
    result = await session.execute(
        delete(TikTokCsConversation).where(
            TikTokCsConversation.created_at < cutoff,
            TikTokCsConversation.last_message_at < cutoff,
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- historial diario --------------------------------------------------------------------------------


def compute_daily(conversations: Iterable[TikTokCsConversation], first_day: date, tz: ZoneInfo) -> dict[date, Row]:
    """Historial diario a partir de las conversaciones guardadas. Pura (no toca la base).

      * conversaciones nuevas: por día local de creación;
      * mensajes de compradores / vendedor: de `day_counts`, ya agrupados por día local;
      * respuesta media y mediana: primera respuesta humana de las conversaciones cuyo primer mensaje del
        comprador cayó ese día.
    """
    days: dict[date, Row] = {}
    response_times: dict[date, list[int]] = {}

    def slot(day: date) -> Row:
        return days.setdefault(
            day, {"new_conversations": 0, "buyer_messages": 0, "seller_messages": 0, "responded_conversations": 0}
        )

    for conv in conversations:
        created_day = conv.created_at.astimezone(tz).date() if conv.created_at.tzinfo else conv.created_at.date()
        if created_day >= first_day:
            slot(created_day)["new_conversations"] += 1
        for iso_day, counts in (conv.day_counts or {}).items():
            day = date.fromisoformat(iso_day)
            if day >= first_day:
                slot(day)["buyer_messages"] += int(counts.get("b", 0))
                slot(day)["seller_messages"] += int(counts.get("s", 0))
        if conv.first_buyer_at is not None and conv.first_response_seconds is not None:
            asked_day = _aware(conv.first_buyer_at).astimezone(tz).date()  # type: ignore[union-attr]
            if asked_day >= first_day:
                response_times.setdefault(asked_day, []).append(conv.first_response_seconds)

    for day, seconds in response_times.items():
        row = slot(day)
        row["responded_conversations"] = len(seconds)
        row["avg_response_seconds"] = round(mean(seconds))
        row["median_response_seconds"] = round(median(seconds))
    return days


async def rebuild_daily(session: AsyncSession, now: datetime, tz: ZoneInfo) -> int:
    """Recalcula los días de la ventana y fija `pending_at_close` de HOY (el único día cuyo estado se conoce)."""
    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=HISTORY_DAYS)
    window_start = now - timedelta(days=HISTORY_DAYS)
    stored = list(
        await session.scalars(
            select(TikTokCsConversation).where(
                (TikTokCsConversation.created_at >= window_start) | (TikTokCsConversation.last_message_at >= window_start)
            )
        )
    )
    computed = compute_daily(stored, first_day, tz)
    pending_now = sum(1 for c in stored if c.status == WAITING_SELLER)
    computed.setdefault(today, {"new_conversations": 0, "buyer_messages": 0, "seller_messages": 0, "responded_conversations": 0})
    computed[today]["pending_at_close"] = pending_now
    existing = {d.day: d for d in await session.scalars(select(TikTokCsDaily).where(TikTokCsDaily.day.in_(list(computed))))}
    for day, values in computed.items():
        # Los promedios de un día que ya no tiene respuestas deben volver a None: se escriben siempre.
        values = {"avg_response_seconds": None, "median_response_seconds": None, **values, "synced_at": now}
        found = existing.get(day)
        if found is None:
            session.add(TikTokCsDaily(day=day, **values))
        else:
            for name, value in values.items():
                setattr(found, name, value)
    await session.flush()
    return len(computed)


def performance_days_to_fetch(have_official: set[date], today: date) -> list[date]:
    """Días de métricas oficiales por pedir: los recientes siempre y, además, los que faltan (más nuevos primero)."""
    recent = [today - timedelta(days=offset) for offset in range(PERFORMANCE_RECENT_DAYS)]
    window = (today - timedelta(days=offset) for offset in range(PERFORMANCE_RECENT_DAYS, HISTORY_DAYS))
    missing = [day for day in window if day not in have_official]
    return [*recent, *missing][:MAX_PERFORMANCE_CALLS]


async def upsert_performance(session: AsyncSession, rows: list[Row], now: datetime) -> int:
    """Guarda las métricas oficiales en la fila del día (creándola si aún no existe)."""
    if not rows:
        return 0
    existing = {d.day: d for d in await session.scalars(select(TikTokCsDaily).where(TikTokCsDaily.day.in_([r["day"] for r in rows])))}
    for row in rows:
        found = existing.get(row["day"])
        if found is None:
            session.add(TikTokCsDaily(**row, synced_at=now))
        else:
            for name, value in row.items():
                setattr(found, name, value)
    await session.flush()
    return len(rows)


async def days_with_official(session: AsyncSession) -> set[date]:
    rows = await session.scalars(select(TikTokCsDaily.day).where(TikTokCsDaily.official_sessions.is_not(None)))
    return set(rows)


# --- estado ------------------------------------------------------------------------------------------


async def set_state(session: AsyncSession, key: str, value: dict[str, Any], now: datetime) -> None:
    row = await session.get(TikTokCsState, key)
    if row is None:
        session.add(TikTokCsState(key=key, value=value, updated_at=now))
    else:
        row.value, row.updated_at = value, now
    await session.flush()


async def get_state(session: AsyncSession, key: str) -> dict[str, Any] | None:
    row = await session.get(TikTokCsState, key)
    return dict(row.value) if row is not None and row.value else None


# --- sincronización ----------------------------------------------------------------------------------


def summarize_problems(problems: Iterable[str]) -> list[str]:
    """Agrupa los avisos repetidos ("mensajes: X" 40 veces -> una línea con el conteo)."""
    return [f"{text} (x{n})" if n > 1 else text for text, n in Counter(problems).items()]


async def _load_messages(
    session: AsyncSession,
    client: TikTokShopClient,
    pending: list[ListedConversation],
    now: datetime,
    tz: ZoneInfo,
) -> tuple[set[str], list[str]]:
    """Pide mensajes por tandas (en hilos: el cliente es HTTP síncrono) y confirma cada una."""
    done: set[str] = set()
    problems: list[str] = []
    for batch in _chunks(pending, BATCH_SIZE):
        rows, issues = await asyncio.to_thread(fetch_rows, client, batch, now, tz)
        problems.extend(issues)
        await upsert_conversations(session, rows, now)
        await session.commit()
        done.update(r["conversation_id"] for r in rows)
    return done, problems


async def _sync_agents(session: AsyncSession, client: TikTokShopClient, now: datetime) -> list[str]:
    try:
        can_accept = await asyncio.to_thread(fetch_agent_can_accept_chat, client)
    except Exception as exc:  # noqa: BLE001 - un ajuste que no se lee no debe frenar la sincronización
        return [f"agentes: {type(exc).__name__}"]
    await set_state(session, STATE_AGENTS, {"can_accept_chat": can_accept}, now)
    return []


async def _sync_performance(session: AsyncSession, client: TikTokShopClient, now: datetime, tz: ZoneInfo) -> tuple[int, list[str]]:
    days = performance_days_to_fetch(await days_with_official(session), now.astimezone(tz).date())
    rows, problems = await asyncio.to_thread(fetch_performance_rows, client, days)
    return await upsert_performance(session, rows, now), problems


async def sync_service(
    session: AsyncSession, client: TikTokShopClient, now: datetime | None = None
) -> tuple[dict[str, int], list[str]]:
    """Una vuelta de sincronización. Devuelve (conteos, problemas). Solo lectura en TikTok."""
    now = now or datetime.now(UTC)
    tz = ZoneInfo(settings.report_timezone)
    listed = await asyncio.to_thread(list_conversations, client, now - timedelta(days=HISTORY_DAYS))

    known = await known_conversations(session)
    fresh, still_open = partition_pending(listed, known)
    pending = [*fresh, *still_open][:MAX_CONVERSATIONS_WITH_MESSAGES]
    done, problems = await _load_messages(session, client, pending, now, tz)

    untouched = [item for item in listed if item.conversation_id in known and item.conversation_id not in done]
    refreshed = await refresh_listed_fields(session, untouched)
    remaining = sum(1 for item in fresh if item.conversation_id not in done)

    problems += await _sync_agents(session, client, now)
    performance_days, performance_issues = await _sync_performance(session, client, now, tz)
    problems += performance_issues
    purged = await purge_old(session, now - timedelta(days=HISTORY_DAYS))
    daily_days = await rebuild_daily(session, now, tz)

    stats = {
        "conversations_listed": len(listed),
        "conversations_with_messages": len(done),
        "new_conversations": sum(1 for item in fresh if item.conversation_id in done and item.conversation_id not in known),
        "listing_refreshed": refreshed,
        "pending_remaining": remaining,
        "daily_days": daily_days,
        "official_days": performance_days,
        "purged": purged,
    }
    await set_state(
        session,
        STATE_SYNC,
        {"conversations_listed": len(listed), "pending_remaining": remaining, "initial_load_complete": remaining == 0},
        now,
    )
    await session.commit()
    return stats, summarize_problems(problems)
