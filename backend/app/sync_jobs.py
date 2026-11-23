"""Sincronización de las plataformas: ejecución registrada y programación automática.

Cada ejecución (manual o programada) queda en `ops.sync_run`. El programador despierta cada pocos
minutos y sincroniza solo las fuentes cuya última ejecución es más vieja que el intervalo; así un
reinicio o varios procesos del backend no duplican trabajo. Todos los conectores son de solo lectura.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.connectors.redo import RedoClient, RedoSettings, sync_redo
from app.connectors.tiktok_ads import (
    TikTokAdsClient,
    TikTokAdsSettings,
    sync_tiktok_ads,
)
from app.connectors.tiktok_shop import TikTokShopClient, TikTokShopSettings
from app.connectors.tiktok_shop_sync import sync_tiktok_shop
from app.connectors.walmart import WalmartClient, WalmartSettings, sync_walmart
from app.gold import refresh_daily_profit
from app.models import SyncRun

log = logging.getLogger("chk.sync")

# Orden: primero las órdenes, después las etiquetas de Redo, que se unen a ellas.
SOURCES = ("tiktok_shop", "tiktok_ads", "walmart", "redo")
LABELS = {
    "tiktok_shop": "TikTok Shop",
    "tiktok_ads": "TikTok Ads (publicidad)",
    "walmart": "Walmart",
    "redo": "Redo (etiquetas)",
}
STALE_RUNNING = timedelta(minutes=30)  # una ejecución "en curso" más vieja que esto se considera caída

Runner = Callable[[AsyncSession, int], Awaitable[dict[str, Any]]]


def _public(result: Any) -> dict[str, Any]:
    out = asdict(result)
    out.pop("touched_from", None)
    out.pop("touched_to", None)
    return out


async def _refresh(session: AsyncSession, touched_from: date | None, days: int, reason: str) -> None:
    await refresh_daily_profit(
        session, touched_from or date.today() - timedelta(days=days + 1), None, reason=reason
    )
    await session.commit()


async def run_tiktok(session: AsyncSession, days: int) -> dict[str, Any]:
    report = await sync_tiktok_shop(session, TikTokShopClient(), days=days)
    await _refresh(session, report.imported.touched_from, days, "sync_tiktok_shop")
    return {"days": report.days, "stats": report.stats, "imported": _public(report.imported)}


async def run_tiktok_ads(session: AsyncSession, days: int) -> dict[str, Any]:
    report = await sync_tiktok_ads(session, TikTokAdsClient(), days=days)
    await _refresh(session, report.imported.touched_from, days, "sync_tiktok_ads")
    return {
        "days": report.days,
        "stats": report.stats,
        "imported": _public(report.imported),
        "problems": report.problems,
    }


async def run_walmart(session: AsyncSession, days: int) -> dict[str, Any]:
    report = await sync_walmart(session, WalmartClient(), days=days)
    await _refresh(session, report.imported.touched_from, days, "sync_walmart")
    return {
        "days": report.days,
        "stats": report.stats,
        "imported": _public(report.imported),
        "problems": report.problems,
    }


async def run_redo(session: AsyncSession, days: int) -> dict[str, Any]:
    result, stats = await sync_redo(session, RedoClient())
    if result.touched_from:
        await refresh_daily_profit(session, result.touched_from, result.touched_to, reason="sync_redo")
    await session.commit()
    return {**_public(result), "stats": asdict(stats)}


RUNNERS: dict[str, Runner] = {
    "tiktok_shop": run_tiktok,
    "tiktok_ads": run_tiktok_ads,
    "walmart": run_walmart,
    "redo": run_redo,
}
SETTINGS: dict[str, Callable[[], Any]] = {
    "tiktok_shop": TikTokShopSettings,
    "tiktok_ads": TikTokAdsSettings,
    "walmart": WalmartSettings,
    "redo": RedoSettings,
}


def is_configured(source: str) -> bool:
    """¿Hay credenciales de esta fuente en el entorno?"""
    try:
        SETTINGS[source]()
    except ValidationError:
        return False
    return True


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def run_source(
    session: AsyncSession, source: str, days: int, triggered_by: str = "manual"
) -> dict[str, Any]:
    """Ejecuta una fuente y deja constancia en `ops.sync_run`. Relanza el error si falla."""
    run = SyncRun(job_type=source, triggered_by=triggered_by, status="running")
    session.add(run)
    await session.commit()
    run_id = run.id
    try:
        out = await RUNNERS[source](session, days)
    except Exception as exc:
        await session.rollback()
        await session.execute(
            update(SyncRun)
            .where(SyncRun.id == run_id)
            .values(status="failed", finished_at=datetime.now(UTC), error=str(exc)[:500])
        )
        await session.commit()
        raise
    problems = out.get("problems") or []
    await session.execute(
        update(SyncRun)
        .where(SyncRun.id == run_id)
        .values(
            status="partial" if problems else "success",
            finished_at=datetime.now(UTC),
            error="; ".join(problems)[:500] or None,
            stats=out.get("stats") or {},
        )
    )
    await session.commit()
    return out


async def last_runs(session: AsyncSession) -> dict[str, SyncRun]:
    """Última ejecución de cada fuente."""
    runs = (await session.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(200))).all()
    latest: dict[str, SyncRun] = {}
    for run in runs:
        latest.setdefault(run.job_type, run)
    return latest


async def last_success(session: AsyncSession) -> dict[str, datetime]:
    runs = (
        await session.scalars(
            select(SyncRun)
            .where(SyncRun.status.in_(("success", "partial")))
            .order_by(SyncRun.started_at.desc())
            .limit(200)
        )
    ).all()
    out: dict[str, datetime] = {}
    for run in runs:
        started = _aware(run.started_at)
        if started is not None:
            out.setdefault(run.job_type, started)
    return out


def interval() -> timedelta:
    return timedelta(minutes=settings.auto_sync_interval_minutes)


async def is_due(session: AsyncSession, source: str, now: datetime | None = None) -> bool:
    """Toca sincronizar si no hay ejecución reciente (ni una en curso que siga viva)."""
    now = now or datetime.now(UTC)
    run = (await last_runs(session)).get(source)
    if run is None:
        return True
    started = _aware(run.started_at) or now
    if run.status == "running":
        return now - started > STALE_RUNNING
    return now - started >= interval()


async def run_due(
    factory: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> dict[str, str]:
    """Sincroniza cada fuente configurada que toque. Un fallo no detiene a las demás."""
    results: dict[str, str] = {}
    for source in SOURCES:
        if not is_configured(source):
            continue
        async with factory() as session:
            if not await is_due(session, source, now):
                continue
            try:
                await run_source(session, source, settings.auto_sync_days, "schedule")
                results[source] = "ok"
            except Exception as exc:  # noqa: BLE001 - queda registrado en sync_run
                log.warning("Sincronización de %s falló: %s", source, exc)
                results[source] = "failed"
    return results


async def auto_sync_loop(factory: async_sessionmaker[AsyncSession]) -> None:
    """Tarea de fondo: revisa periódicamente qué fuentes tocan. Se cancela al apagar el backend."""
    await asyncio.sleep(settings.auto_sync_start_delay_seconds)
    while True:
        try:
            done = await run_due(factory)
            if done:
                log.info("Sincronización automática: %s", done)
        except Exception:
            log.exception("Fallo inesperado en la sincronización automática")
        await asyncio.sleep(settings.auto_sync_check_seconds)


async def sync_status(session: AsyncSession) -> dict[str, Any]:
    latest = await last_runs(session)
    now = datetime.now(UTC)
    rows = []
    for source in SOURCES:
        run = latest.get(source)
        started = _aware(run.started_at) if run else None
        rows.append(
            {
                "source": source,
                "label": LABELS[source],
                "configured": is_configured(source),
                "status": run.status if run else None,
                "triggered_by": run.triggered_by if run else None,
                "started_at": started,
                "finished_at": _aware(run.finished_at) if run else None,
                "error": run.error if run else None,
                "next_due_at": (started + interval()) if (started and settings.auto_sync_enabled) else None,
                "overdue": bool(started and now - started > interval() * 2),
            }
        )
    return {
        "auto_enabled": settings.auto_sync_enabled,
        "interval_minutes": settings.auto_sync_interval_minutes,
        "days": settings.auto_sync_days,
        "sources": rows,
    }
