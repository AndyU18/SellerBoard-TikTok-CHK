"""Guarda los datos de TikTok Creator y orquesta su sincronización (modo completo semanal / ligero diario).

Idempotente: cada tabla tiene clave natural (id de TikTok, o id + día para el historial) y volver a
sincronizar actualiza la fila en vez de duplicarla. Qué se conserva y qué se retira:
  * Productos: nunca se borran. Tras una carga COMPLETA los que dejaron de aparecer pasan a `active=false`
    (y su `first_seen_at` se conserva si reaparecen). OJO: TikTok solo deja ver 10.000 productos por consulta,
    así que "inactivo" significa "ya no está entre los visibles", no necesariamente "retirado".
  * Historial: una foto por semana (lunes) y producto; se borra lo de más de HISTORY_WEEKS semanas.
  * Vitrina: se marca inactivo lo que ya no aparece tras leer TODA la vitrina.
  * Una sección que falló NUNCA retira filas: un error de red no debe parecer "TikTok ya no lo tiene".

Por qué hay dos modos: el sincronizador automático corre cada 6 horas y la carga completa son ~500 consultas
de 1,6 s (~13 min). El modo completo corre a lo sumo cada FULL_INTERVAL; el resto de las vueltas lee solo el
perfil, los pedidos, la vitrina y las primeras páginas del catálogo ordenado por unidades y por comisión.
La fecha de la última carga completa se deduce de `ops.sync_run.stats` (sin tabla de estado aparte).

Importado solo por `app.sync_jobs` y `app.tiktok_creator_analytics`. Los upserts hacen `flush`; el commit lo
decide quien orquesta.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import ColumnElement, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_creator import (
    MAX_CATALOG_PAGES,
    CreatorReauthRequired,
    Page,
    Row,
    TikTokCreatorClient,
    TikTokCreatorError,
    build_history_row,
    build_order,
    build_product,
    build_profile,
    build_showcase_product,
)
from app.connectors.tiktok_shop import TikTokShopError
from app.models import (
    SyncRun,
    TikTokCreatorOrder,
    TikTokCreatorProduct,
    TikTokCreatorProductHistory,
    TikTokCreatorProfile,
    TikTokCreatorShowcaseProduct,
)

log = logging.getLogger("chk.creator")

JOB_TYPE = "tiktok_creator"
MODE_FULL = "full"
MODE_LIGHT = "light"
MODE_SKIPPED = "skipped"

FULL_INTERVAL = timedelta(days=7)  # cada cuánto corre la carga completa del catálogo
FULL_RETRY_AFTER = timedelta(hours=12)  # tras una carga completa incompleta no se reintenta antes de esto
LIGHT_SORT_FIELDS: tuple[str, ...] = ("units_sold", "commission")  # vistas del modo ligero (orden descendente)
LIGHT_PAGES_PER_SORT = 5  # 5 páginas x 20 = 100 productos por vista
PAGES_PER_COMMIT = 10  # la carga completa guarda y confirma cada 200 productos: un corte no pierde todo
UPSERT_CHUNK = 200  # ids por consulta de lectura (SQLite limita las variables a 999)
HISTORY_WEEKS = 26
SHOWCASE_ORIGINS: tuple[str, ...] = ("SHOWCASE",)  # LIVE devuelve la misma lista; ver `explore` en el reporte
DEFAULT_ORDER_DAYS = 90
RUNS_SCANNED = 60  # ejecuciones recientes que se revisan para ubicar la última carga completa

TRANSIENT_ERRORS = (TikTokShopError, TikTokCreatorError, httpx.HTTPError)


def week_start(day: date) -> date:
    """Lunes de la semana de `day`: el historial guarda UNA foto por semana."""
    return day - timedelta(days=day.weekday())


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


# --- upserts ----------------------------------------------------------------------------------------------


def _dedupe(rows: list[Row], key_fields: tuple[str, ...]) -> list[Row]:
    """Una fila por clave (gana la última): un producto puede venir en dos vistas del mismo lote."""
    return list({tuple(r[k] for k in key_fields): r for r in rows}.values())


async def _upsert(
    session: AsyncSession,
    model: Any,
    key_fields: tuple[str, ...],
    rows: list[Row],
    *,
    extra: Row | None = None,
    create_only: Row | None = None,
    scope: ColumnElement[bool] | None = None,
) -> int:
    """Inserta o actualiza por clave natural, leyendo en bloques de UPSERT_CHUNK ids (no una consulta por fila).

    `extra` se aplica siempre; `create_only` solo a filas nuevas (p. ej. `first_seen_at`); `scope` acota qué
    filas existentes se cargan (p. ej. solo el origen de la vitrina o la foto de la semana).
    """
    unique = _dedupe(rows, key_fields)
    first_key = key_fields[0]
    for start in range(0, len(unique), UPSERT_CHUNK):
        chunk = unique[start : start + UPSERT_CHUNK]
        query = select(model).where(getattr(model, first_key).in_([r[first_key] for r in chunk]))
        if scope is not None:
            query = query.where(scope)
        existing = {tuple(getattr(r, k) for k in key_fields): r for r in await session.scalars(query)}
        for row in chunk:
            values = {**row, **(extra or {})}
            found = existing.get(tuple(row[k] for k in key_fields))
            if found is None:
                session.add(model(**values, **(create_only or {})))
            else:
                for name, value in values.items():
                    setattr(found, name, value)
        await session.flush()
    return len(unique)


async def upsert_profile(session: AsyncSession, row: Row, now: datetime) -> None:
    """Una foto por día. Los totales que esta vez no se pudieron leer (None) no pisan los ya guardados."""
    existing = await session.get(TikTokCreatorProfile, row["snapshot_date"])
    values = {k: v for k, v in {**row, "captured_at": now}.items() if v is not None or k not in ("catalog_total", "showcase_total")}
    if existing is None:
        session.add(TikTokCreatorProfile(**values))
    else:
        for name, value in values.items():
            setattr(existing, name, value)
    await session.flush()


async def upsert_products(session: AsyncSession, rows: list[Row], now: datetime) -> int:
    """Productos vistos: renueva `last_seen_at`, reactiva los inactivos y conserva `first_seen_at`."""
    return await _upsert(
        session, TikTokCreatorProduct, ("product_id",), rows,
        extra={"last_seen_at": now, "active": True}, create_only={"first_seen_at": now},
    )


async def upsert_history(session: AsyncSession, rows: list[Row], snapshot_date: date) -> int:
    return await _upsert(
        session, TikTokCreatorProductHistory, ("product_id", "snapshot_date"), rows,
        scope=TikTokCreatorProductHistory.snapshot_date == snapshot_date,
    )


async def upsert_showcase(session: AsyncSession, rows: list[Row], origin: str, now: datetime) -> int:
    return await _upsert(
        session, TikTokCreatorShowcaseProduct, ("product_id", "origin"), rows,
        extra={"last_seen_at": now, "active": True}, scope=TikTokCreatorShowcaseProduct.origin == origin,
    )


async def upsert_orders(session: AsyncSession, rows: list[Row], now: datetime) -> int:
    return await _upsert(session, TikTokCreatorOrder, ("order_id",), rows, extra={"synced_at": now})


async def deactivate_unseen_products(session: AsyncSession, run_started: datetime) -> int:
    """Marca inactivos los productos activos que esta carga COMPLETA no volvió a ver."""
    result = await session.execute(
        update(TikTokCreatorProduct)
        .where(TikTokCreatorProduct.active.is_(True), TikTokCreatorProduct.last_seen_at < run_started)
        .values(active=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def deactivate_unseen_showcase(session: AsyncSession, origin: str, run_started: datetime) -> int:
    result = await session.execute(
        update(TikTokCreatorShowcaseProduct)
        .where(
            TikTokCreatorShowcaseProduct.origin == origin,
            TikTokCreatorShowcaseProduct.active.is_(True),
            TikTokCreatorShowcaseProduct.last_seen_at < run_started,
        )
        .values(active=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_history(session: AsyncSession, today: date) -> None:
    cutoff = week_start(today) - timedelta(weeks=HISTORY_WEEKS)
    await session.execute(delete(TikTokCreatorProductHistory).where(TikTokCreatorProductHistory.snapshot_date < cutoff))


# --- modo de sincronización --------------------------------------------------------------------------------


async def full_runs(session: AsyncSession) -> list[SyncRun]:
    """Ejecuciones recientes en modo completo (de la más nueva a la más vieja), con o sin catálogo completo."""
    runs = (
        await session.scalars(
            select(SyncRun)
            .where(SyncRun.job_type == JOB_TYPE, SyncRun.status.in_(("success", "partial")))
            .order_by(SyncRun.started_at.desc())
            .limit(RUNS_SCANNED)
        )
    ).all()
    return [r for r in runs if (r.stats or {}).get("mode") == MODE_FULL]


def _is_complete(run: SyncRun) -> bool:
    return bool((run.stats or {}).get("catalog_complete"))


async def last_complete_full_at(session: AsyncSession) -> datetime | None:
    """Fin de la última carga completa que SÍ llegó al final del catálogo."""
    for run in await full_runs(session):
        if _is_complete(run):
            return _aware(run.finished_at or run.started_at)
    return None


async def first_complete_full_at(session: AsyncSession) -> datetime | None:
    """Fin de la PRIMERA carga completa: lo que aparece después es "producto nuevo" de verdad."""
    runs = (
        await session.scalars(
            select(SyncRun)
            .where(SyncRun.job_type == JOB_TYPE, SyncRun.status.in_(("success", "partial")))
            .order_by(SyncRun.started_at.asc())
        )
    ).all()
    for run in runs:
        if (run.stats or {}).get("mode") == MODE_FULL and _is_complete(run):
            return _aware(run.finished_at or run.started_at)
    return None


async def choose_mode(session: AsyncSession, now: datetime) -> str:
    """Completo si pasó FULL_INTERVAL desde la última carga completa (y no se intentó hace poco); si no, ligero."""
    runs = await full_runs(session)
    complete = next((r for r in runs if _is_complete(r)), None)
    if complete is not None and now - (_aware(complete.finished_at or complete.started_at) or now) < FULL_INTERVAL:
        return MODE_LIGHT
    attempt = runs[0] if runs else None
    if attempt is not None and not _is_complete(attempt) and now - (_aware(attempt.started_at) or now) < FULL_RETRY_AFTER:
        return MODE_LIGHT  # el último intento completo quedó a medias: no repetir 13 minutos cada 6 horas
    return MODE_FULL


# --- lectura en hilo ---------------------------------------------------------------------------------------


async def _next_page(pages: Iterator[Page]) -> Page | None:
    """El cliente es HTTP síncrono (con pausas y reintentos): cada página se lee fuera del bucle de eventos."""
    return await asyncio.to_thread(next, pages, None)


class _CatalogResult:
    """Resultado de leer el catálogo: cuántos productos, el total que declara TikTok y si llegó hasta el final."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.total: int | None = None
        self.complete = True
        self.pages = 0


async def _load_catalog(
    session: AsyncSession, client: TikTokCreatorClient, mode: str, now: datetime, today: date, result: _CatalogResult
) -> None:
    """Lee el catálogo en las vistas del modo y guarda por lotes (producto + foto semanal).

    `result` lo crea quien llama: si una página falla a mitad de carga, lo ya guardado queda contabilizado.
    """
    if mode == MODE_FULL:
        views: list[Iterator[Page]] = [client.iter_open_collab_pages(max_pages=MAX_CATALOG_PAGES)]
    else:
        views = [
            client.iter_open_collab_pages(sort_field=field, sort_order="DESC", max_pages=LIGHT_PAGES_PER_SORT)
            for field in LIGHT_SORT_FIELDS
        ]
    for view in views:
        batch: list[Row] = []
        last_page: Page | None = None
        while (page := await _next_page(view)) is not None:
            last_page = page
            result.pages += 1
            result.total = page.total_count if page.total_count is not None else result.total
            batch.extend(p for raw in page.items if (p := build_product(raw)) is not None)
            if result.pages % PAGES_PER_COMMIT == 0:
                await _save_catalog_batch(session, batch, now, today, result)
                batch = []
        await _save_catalog_batch(session, batch, now, today, result)
        # Completo solo si la vista terminó porque TikTok no tenía más páginas (no por el tope de páginas).
        if mode == MODE_FULL and (last_page is None or last_page.has_more):
            result.complete = False


async def _save_catalog_batch(
    session: AsyncSession, rows: list[Row], now: datetime, today: date, result: _CatalogResult
) -> None:
    if not rows:
        return
    await upsert_products(session, rows, now)
    await upsert_history(session, [build_history_row(r, week_start(today)) for r in rows], week_start(today))
    await session.commit()
    result.seen.update(r["product_id"] for r in rows)


async def _load_showcase(session: AsyncSession, client: TikTokCreatorClient, now: datetime, stats: dict[str, Any]) -> None:
    total = 0
    for origin in SHOWCASE_ORIGINS:
        pages = client.iter_showcase_pages(origin)
        rows: list[Row] = []
        while (page := await _next_page(pages)) is not None:
            rows.extend(r for raw in page.items if (r := build_showcase_product(raw, origin)) is not None)
        await upsert_showcase(session, rows, origin, now)
        stats["showcase_deactivated"] = stats.get("showcase_deactivated", 0) + await deactivate_unseen_showcase(session, origin, now)
        total += len(rows)
    stats["showcase"] = total
    await session.commit()


async def _load_orders(session: AsyncSession, client: TikTokCreatorClient, now: datetime, stats: dict[str, Any]) -> None:
    pages = client.iter_orders()
    rows: list[Row] = []
    while (page := await _next_page(pages)) is not None:
        rows.extend(r for raw in page.items if (r := build_order(raw)) is not None)
    stats["orders"] = await upsert_orders(session, rows, now)
    await session.commit()


async def _guarded(
    session: AsyncSession, name: str, problems: list[str], section: Callable[[], Awaitable[None]]
) -> bool:
    """Ejecuta una sección; un fallo de lectura queda como problema y no detiene a las demás.

    La falta de autorización SÍ sube: sin token válido ninguna sección puede funcionar.
    """
    try:
        await section()
    except CreatorReauthRequired:
        raise
    except TRANSIENT_ERRORS as exc:
        await session.rollback()
        problems.append(f"{name}: {exc}")
        return False
    return True


async def sync_creator(
    session: AsyncSession, client: TikTokCreatorClient, *, mode: str | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """Una sincronización de TikTok Creator. Devuelve `{"days", "stats", "problems"}` como las demás fuentes.

    `mode=None` elige solo (ver `choose_mode`); pasar "full" fuerza la carga completa. Sin autorización
    válida devuelve un resultado `skipped` con `needs_reauth=True` en vez de lanzar: el estado de la API
    dice "hay que reautorizar" y el resto de las fuentes siguen su curso.
    """
    now = now or datetime.now(UTC)
    today = now.date()
    mode = mode or await choose_mode(session, now)
    stats: dict[str, Any] = {"mode": mode, "needs_reauth": False, "catalog_complete": False}
    problems: list[str] = []
    try:
        profile_raw = await asyncio.to_thread(client.profile)  # primera llamada: valida (y renueva) el token
        catalog = _CatalogResult()

        loaded = await _guarded(session, "catálogo", problems, lambda: _load_catalog(session, client, mode, now, today, catalog))
        stats.update(products=len(catalog.seen), catalog_pages=catalog.pages, catalog_total=catalog.total)
        if loaded and mode == MODE_FULL and catalog.complete:
            stats["catalog_complete"] = True
            stats["deactivated"] = await deactivate_unseen_products(session, now)
            await session.commit()
        await _guarded(session, "vitrina", problems, lambda: _load_showcase(session, client, now, stats))
        await _guarded(session, "pedidos", problems, lambda: _load_orders(session, client, now, stats))
        profile = build_profile(profile_raw, today)
        profile.update(catalog_total=catalog.total, showcase_total=stats.get("showcase"))
        await upsert_profile(session, profile, now)
        await prune_history(session, today)
        await session.commit()
    except CreatorReauthRequired as exc:
        await session.rollback()
        log.warning("TikTok Creator necesita reautorización: %s", exc)
        return {"days": 0, "stats": {"mode": MODE_SKIPPED, "needs_reauth": True, "catalog_complete": False}, "problems": [str(exc)]}
    return {"days": 0, "stats": stats, "problems": problems}
