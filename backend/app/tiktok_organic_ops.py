"""Guarda los datos del TikTok orgánico (perfil, videos y su foto diaria de métricas).

Todo es idempotente: la cuenta y las métricas tienen clave (día) / (video, día), y el video su id de TikTok;
sincronizar dos veces el mismo día deja la última lectura en vez de duplicar. Los videos que el dueño borra de
TikTok se conservan (su historial sigue valiendo para el análisis); el historial de métricas se acota.

Importado solo por `app.sync_jobs`. Los upserts hacen `flush`; quien llama decide el `commit`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_organic import OrganicSnapshot
from app.models import (
    TikTokOrganicAccount,
    TikTokOrganicVideo,
    TikTokOrganicVideoMetric,
)

METRIC_HISTORY_DAYS = 365  # fotos de métricas más viejas que esto se borran en cada sincronización


async def upsert_account(session: AsyncSession, row: dict[str, Any], now: datetime) -> None:
    """Una foto por día: sincronizar dos veces el mismo día conserva la última lectura."""
    existing = await session.get(TikTokOrganicAccount, row["snapshot_date"])
    values = {**row, "captured_at": now}
    if existing is None:
        session.add(TikTokOrganicAccount(**values))
    else:
        for name, value in values.items():
            setattr(existing, name, value)
    await session.flush()


async def upsert_videos(session: AsyncSession, rows: list[dict[str, Any]], now: datetime) -> int:
    """Inserta o actualiza los videos por id (una sola consulta para saber cuáles ya existen)."""
    if not rows:
        return 0
    ids = [r["video_id"] for r in rows]
    existing = {
        v.video_id: v
        for v in (await session.scalars(select(TikTokOrganicVideo).where(TikTokOrganicVideo.video_id.in_(ids))))
    }
    for row in rows:
        values = {**row, "updated_at": now}
        found = existing.get(row["video_id"])
        if found is None:
            session.add(TikTokOrganicVideo(**values))
        else:
            for name, value in values.items():
                setattr(found, name, value)
    await session.flush()
    return len(rows)


async def upsert_metrics(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Foto del día por video. Requiere que los videos ya estén guardados (clave foránea)."""
    if not rows:
        return 0
    ids = [r["video_id"] for r in rows]
    day = rows[0]["snapshot_date"]
    existing = {
        m.video_id: m
        for m in (
            await session.scalars(
                select(TikTokOrganicVideoMetric).where(
                    TikTokOrganicVideoMetric.snapshot_date == day, TikTokOrganicVideoMetric.video_id.in_(ids)
                )
            )
        )
    }
    for row in rows:
        found = existing.get(row["video_id"])
        if found is None:
            session.add(TikTokOrganicVideoMetric(**row))
        else:
            for name, value in row.items():
                setattr(found, name, value)
    await session.flush()
    return len(rows)


async def prune_metrics(session: AsyncSession, today: date) -> None:
    """Borra el historial de métricas que pasa del límite."""
    await session.execute(
        delete(TikTokOrganicVideoMetric).where(
            TikTokOrganicVideoMetric.snapshot_date < today - timedelta(days=METRIC_HISTORY_DAYS)
        )
    )


async def save_snapshot(
    session: AsyncSession, snapshot: OrganicSnapshot, today: date, now: datetime
) -> dict[str, int]:
    """Persiste una sincronización completa y devuelve contadores para `sync_run.stats`."""
    if snapshot.account is not None:
        await upsert_account(session, snapshot.account, now)
    videos = await upsert_videos(session, snapshot.videos, now)
    metrics = await upsert_metrics(session, snapshot.metrics)
    await prune_metrics(session, today)
    await session.commit()
    return {"account": int(snapshot.account is not None), "videos": videos, "metrics": metrics}
