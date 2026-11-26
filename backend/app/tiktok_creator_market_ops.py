"""Guarda el mercado de creadores y las colaboraciones del vendedor y orquesta su sincronización.

Idempotente: cada tabla tiene clave natural (`creator_open_id`, id de colaboración, o id + semana para la foto)
y volver a sincronizar actualiza la fila en vez de duplicarla. Qué se conserva y qué se retira:
  * Creadores: nunca se borran. La búsqueda del mercado NO es un listado exhaustivo, así que "no apareció" no
    significa "ya no existe": solo tras una carga COMPLETA se marca `active=false` a quien no se ve hace más de
    CREATOR_STALE_AFTER. `first_seen_at` se conserva si reaparecen. Un dato que esta vez no vino (None) no pisa
    el valor guardado: el mismo creador sale con y sin GMV según la vista en que aparezca.
  * Foto semanal (lunes): seguidores y GMV por creador; se borra lo de más de HISTORY_WEEKS semanas.
  * Colaboraciones: tras leer TODAS las páginas, las que ya no aparecen pasan a `active=false`. Una sección que
    falló o se cortó por el tope de páginas NUNCA retira filas.

Por qué dos modos: la lectura cuesta ~1,9 s por consulta y comparte límite de tasa con el resto de la app. El modo
ligero (cada vuelta del sincronizador) lee pocas páginas del mercado, las colaboraciones completas y unas pocas
fichas; el completo (a lo sumo cada FULL_INTERVAL) abre más páginas por cada palabra clave de nuestro negocio y
enriquece más fichas. La fecha de la última carga completa se deduce de `ops.sync_run.stats` (sin tabla de estado).

Importado solo por `app.sync_jobs` y `app.tiktok_creator_market_analytics`. Los upserts hacen `flush`; el commit lo
decide quien orquesta.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import case, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_creator import Page, Row
from app.connectors.tiktok_creator_market import (
    CreatorMarketClient,
    TikTokCreatorMarketError,
    build_creator,
    build_creator_detail,
    build_open_collaboration,
    build_snapshot_row,
    build_target_collaboration,
)
from app.connectors.tiktok_shop import TikTokShopError
from app.models import (
    SyncRun,
    TikTokMarketCreator,
    TikTokMarketCreatorSnapshot,
    TikTokOpenCollaboration,
    TikTokTargetCollaboration,
)
from app.tiktok_creator_ops import (
    _upsert as upsert_by_key,  # mismo upsert por lotes que Creator: no se duplica
)
from app.tiktok_creator_ops import week_start

log = logging.getLogger("chk.creator_market")

JOB_TYPE = "tiktok_creator_market"
MODE_FULL = "full"
MODE_LIGHT = "light"

# Palabras de búsqueda de nuestro negocio: hogar, cocina, decoración navideña y empaque.
SEARCH_KEYWORDS: tuple[str, ...] = ("home", "kitchen", "christmas", "holiday decor", "home decor", "gift wrap", "packaging", "elf")
BASE_PAGES = {"light": 3, "full": 25}  # páginas del orden por defecto del mercado (20 creadores por página)
KEYWORD_PAGES = {"light": 1, "full": 10}  # páginas por palabra clave
DETAIL_LIMIT = {"light": 10, "full": 40}  # fichas de creador que se enriquecen por corrida (1 consulta cada una)
MAX_COLLAB_PAGES = 20  # 400 colaboraciones por tipo (hoy hay 110 abiertas y 50 dirigidas)
DETAIL_PAUSE_SECONDS = 0.3
DETAIL_TTL = timedelta(days=30)  # una ficha leída hace menos que esto no se vuelve a leer
CREATOR_STALE_AFTER = timedelta(days=45)
FULL_INTERVAL = timedelta(days=7)
FULL_RETRY_AFTER = timedelta(hours=12)
HISTORY_WEEKS = 26
PAGES_PER_COMMIT = 5  # la carga guarda y confirma cada 100 creadores: un corte no pierde todo
RUNS_SCANNED = 60
TARGET_STATUS = "ONGOING"  # el único estado que la API acepta consultar

TRANSIENT_ERRORS = (TikTokShopError, TikTokCreatorMarketError, httpx.HTTPError)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _present(row: Row) -> Row:
    """Solo lo que vino: un None no debe pisar un valor ya guardado (ver el docstring del módulo)."""
    return {key: value for key, value in row.items() if value is not None}


# --- upserts ----------------------------------------------------------------------------------------------


async def upsert_creators(session: AsyncSession, rows: list[Row], now: datetime) -> int:
    """Creadores vistos: renueva `last_seen_at`, reactiva los inactivos y conserva `first_seen_at`."""
    return await upsert_by_key(
        session, TikTokMarketCreator, ("creator_open_id",), [_present(r) for r in rows],
        extra={"last_seen_at": now, "active": True}, create_only={"first_seen_at": now},
    )


async def upsert_snapshots(session: AsyncSession, rows: list[Row], day: date) -> int:
    return await upsert_by_key(
        session, TikTokMarketCreatorSnapshot, ("creator_open_id", "week_start"), rows,
        scope=TikTokMarketCreatorSnapshot.week_start == week_start(day),
    )


async def upsert_open_collaborations(session: AsyncSession, rows: list[Row], now: datetime) -> int:
    return await upsert_by_key(
        session, TikTokOpenCollaboration, ("collaboration_id",), rows,
        extra={"last_seen_at": now, "active": True}, create_only={"first_seen_at": now},
    )


async def upsert_target_collaborations(session: AsyncSession, rows: list[Row], now: datetime) -> int:
    return await upsert_by_key(
        session, TikTokTargetCollaboration, ("collaboration_id",), rows,
        extra={"last_seen_at": now, "active": True}, create_only={"first_seen_at": now},
    )


async def deactivate_unseen(session: AsyncSession, model: Any, run_started: datetime) -> int:
    """Marca inactivas las filas activas que esta carga COMPLETA no volvió a ver."""
    result = await session.execute(
        update(model).where(model.active.is_(True), model.last_seen_at < run_started).values(active=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def deactivate_stale_creators(session: AsyncSession, now: datetime) -> int:
    result = await session.execute(
        update(TikTokMarketCreator)
        .where(TikTokMarketCreator.active.is_(True), TikTokMarketCreator.last_seen_at < now - CREATOR_STALE_AFTER)
        .values(active=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_snapshots(session: AsyncSession, today: date) -> None:
    cutoff = week_start(today) - timedelta(weeks=HISTORY_WEEKS)
    await session.execute(delete(TikTokMarketCreatorSnapshot).where(TikTokMarketCreatorSnapshot.week_start < cutoff))


# --- modo de sincronización --------------------------------------------------------------------------------


async def choose_mode(session: AsyncSession, now: datetime) -> str:
    """Completo si pasó FULL_INTERVAL desde la última carga completa buena (y no se intentó hace poco); si no, ligero."""
    runs = (
        await session.scalars(
            select(SyncRun)
            .where(SyncRun.job_type == JOB_TYPE, SyncRun.status.in_(("success", "partial")))
            .order_by(SyncRun.started_at.desc())
            .limit(RUNS_SCANNED)
        )
    ).all()
    fulls = [r for r in runs if (r.stats or {}).get("mode") == MODE_FULL]
    good = next((r for r in fulls if (r.stats or {}).get("full_complete")), None)
    if good is not None and now - (_aware(good.finished_at or good.started_at) or now) < FULL_INTERVAL:
        return MODE_LIGHT
    if fulls and now - (_aware(fulls[0].started_at) or now) < FULL_RETRY_AFTER:
        return MODE_LIGHT  # el último intento completo quedó a medias: no repetirlo en cada vuelta
    return MODE_FULL


# --- lectura en hilo ---------------------------------------------------------------------------------------


async def _next_page(pages: Iterator[Page]) -> Page | None:
    """El cliente es HTTP síncrono (con pausas y reintentos): cada página se lee fuera del bucle de eventos."""
    return await asyncio.to_thread(next, pages, None)


class _CreatorLoad:
    """Cuántos creadores y páginas se leyeron; si una vista falla a mitad, lo ya guardado queda contabilizado."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.pages = 0
        self.views = 0


def _creator_views(client: CreatorMarketClient, mode: str) -> list[tuple[str | None, Iterator[Page]]]:
    """Vistas del mercado a leer: el orden por defecto y una búsqueda por cada palabra clave del negocio."""
    views: list[tuple[str | None, Iterator[Page]]] = [(None, client.iter_creator_pages(max_pages=BASE_PAGES[mode]))]
    views += [(kw, client.iter_creator_pages(keyword=kw, max_pages=KEYWORD_PAGES[mode])) for kw in SEARCH_KEYWORDS]
    return views


async def _save_creator_batch(session: AsyncSession, rows: list[Row], now: datetime, load: _CreatorLoad) -> None:
    if not rows:
        return
    await upsert_creators(session, rows, now)
    await upsert_snapshots(session, [build_snapshot_row(r, week_start(now.date())) for r in rows], now.date())
    await session.commit()
    load.seen.update(r["creator_open_id"] for r in rows)


async def _load_creators(session: AsyncSession, client: CreatorMarketClient, mode: str, now: datetime, load: _CreatorLoad) -> None:
    for _keyword, view in _creator_views(client, mode):
        batch: list[Row] = []
        pages_in_batch = 0
        while (page := await _next_page(view)) is not None:
            load.pages += 1
            pages_in_batch += 1
            batch.extend(c for raw in page.items if (c := build_creator(raw)) is not None)
            if pages_in_batch % PAGES_PER_COMMIT == 0:
                await _save_creator_batch(session, batch, now, load)
                batch = []
        await _save_creator_batch(session, batch, now, load)
        load.views += 1


async def _creators_needing_detail(session: AsyncSession, now: datetime, limit: int) -> list[str]:
    """Los de mayor GMV reportado entre los que nunca se enriquecieron o cuya ficha ya venció."""
    best_gmv = case(
        (TikTokMarketCreator.gmv_amount.is_not(None), TikTokMarketCreator.gmv_amount),
        else_=TikTokMarketCreator.gmv_range_min,
    )
    query = (
        select(TikTokMarketCreator.creator_open_id)
        .where(
            TikTokMarketCreator.active.is_(True),
            (TikTokMarketCreator.detail_at.is_(None)) | (TikTokMarketCreator.detail_at < now - DETAIL_TTL),
        )
        .order_by(best_gmv.desc().nulls_last(), TikTokMarketCreator.follower_count.desc())
        .limit(limit)
    )
    return list((await session.scalars(query)).all())


async def _enrich_details(
    session: AsyncSession, client: CreatorMarketClient, mode: str, now: datetime, stats: dict[str, Any], pause: float
) -> None:
    """Lee la ficha de los creadores más relevantes sin ficha reciente. Un id que TikTok rechaza se salta."""
    done = 0
    try:
        for open_id in await _creators_needing_detail(session, now, DETAIL_LIMIT[mode]):
            if done:
                await asyncio.sleep(pause)
            raw = await asyncio.to_thread(client.creator_detail, open_id)
            creator = await session.get(TikTokMarketCreator, open_id)
            if creator is None or not raw:
                continue
            for name, value in {**_present(build_creator_detail(raw)), "detail_at": now}.items():
                setattr(creator, name, value)
            done += 1
    finally:
        stats["details"] = done
        await session.commit()  # lo ya enriquecido se conserva aunque una ficha posterior falle


async def _load_collaborations(
    session: AsyncSession, pages: Iterator[Page], build: Callable[[Row], Row | None],
    upsert: Callable[[AsyncSession, list[Row], datetime], Awaitable[int]], model: Any, now: datetime,
) -> tuple[int, int]:
    """Lee TODAS las páginas, guarda y, solo si se llegó al final, retira las que ya no aparecen. (guardadas, retiradas)."""
    rows: list[Row] = []
    last: Page | None = None
    while (page := await _next_page(pages)) is not None:
        last = page
        rows.extend(r for raw in page.items if (r := build(raw)) is not None)
    saved = await upsert(session, rows, now)
    complete = last is not None and not last.has_more
    retired = await deactivate_unseen(session, model, now) if complete else 0
    await session.commit()
    return saved, retired


async def _guarded(
    session: AsyncSession, name: str, problems: list[str], section: Callable[[], Awaitable[None]]
) -> bool:
    """Ejecuta una sección; un fallo de lectura queda como problema y no detiene a las demás."""
    try:
        await section()
    except TRANSIENT_ERRORS as exc:
        await session.rollback()
        problems.append(f"{name}: {exc}")
        return False
    return True


async def sync_creator_market(
    session: AsyncSession,
    client: CreatorMarketClient,
    *,
    mode: str | None = None,
    now: datetime | None = None,
    detail_pause: float = DETAIL_PAUSE_SECONDS,
) -> dict[str, Any]:
    """Una sincronización del mercado de creadores. Devuelve `{"days", "stats", "problems"}` como las demás fuentes.

    `mode=None` elige solo (ver `choose_mode`). `full_complete` queda en las estadísticas solo si el modo fue
    completo y NINGUNA sección falló: es lo que `choose_mode` usa para no repetir la carga pesada.
    """
    now = now or datetime.now(UTC)
    mode = mode or await choose_mode(session, now)
    stats: dict[str, Any] = {"mode": mode, "full_complete": False}
    problems: list[str] = []
    load = _CreatorLoad()

    creators_ok = await _guarded(session, "creadores", problems, lambda: _load_creators(session, client, mode, now, load))
    stats.update(creators=len(load.seen), creator_pages=load.pages, creator_views=load.views)
    if creators_ok and mode == MODE_FULL:
        stats["creators_deactivated"] = await deactivate_stale_creators(session, now)
    details_ok = await _guarded(session, "fichas", problems, lambda: _enrich_details(session, client, mode, now, stats, detail_pause))

    async def open_section() -> None:
        pages = client.iter_open_collaboration_pages(max_pages=MAX_COLLAB_PAGES)
        stats["open_collaborations"], stats["open_deactivated"] = await _load_collaborations(
            session, pages, build_open_collaboration, upsert_open_collaborations, TikTokOpenCollaboration, now
        )

    async def target_section() -> None:
        pages = client.iter_target_collaboration_pages(TARGET_STATUS, max_pages=MAX_COLLAB_PAGES)
        stats["target_collaborations"], stats["target_deactivated"] = await _load_collaborations(
            session, pages, lambda raw: build_target_collaboration(raw, TARGET_STATUS),
            upsert_target_collaborations, TikTokTargetCollaboration, now,
        )

    open_ok = await _guarded(session, "colaboraciones abiertas", problems, open_section)
    target_ok = await _guarded(session, "colaboraciones dirigidas", problems, target_section)
    await prune_snapshots(session, now.date())
    await session.commit()
    stats["full_complete"] = mode == MODE_FULL and creators_ok and details_ok and open_ok and target_ok
    return {"days": 0, "stats": stats, "problems": problems}
