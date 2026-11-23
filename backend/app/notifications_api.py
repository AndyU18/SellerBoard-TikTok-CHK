"""Endpoints del resumen por correo, Slack o webhook.

La lógica vive en `digest` (datos y formato) y `notify` (canales y registro); aquí solo se validan
permisos y parámetros. Ninguna respuesta incluye secretos ni URLs: solo qué canales están configurados.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.config import settings
from app.db import get_session
from app.digest import Period, build_digest, render_html, render_text
from app.models import NotificationLog
from app.notify import configured_channels, deliver

router = APIRouter(prefix="/api")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LOG_LIMIT = 20


@router.get("/notifications/status", dependencies=[Depends(require("settings.view"))])
async def notifications_status(session: SessionDep) -> dict[str, Any]:
    rows = (
        await session.scalars(select(NotificationLog).order_by(NotificationLog.id.desc()).limit(LOG_LIMIT))
    ).all()
    return {
        "enabled": settings.digest_enabled,
        "daily_hour": settings.digest_daily_hour,
        "weekly_weekday": settings.digest_weekly_weekday,
        "timezone": settings.report_timezone,
        "channels": configured_channels(),
        "email_recipients": len(settings.smtp_to),
        "log": [
            {
                "id": r.id,
                "kind": r.kind,
                "channel": r.channel,
                "status": r.status,
                "detail": r.detail,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


@router.get("/digest/preview", dependencies=[Depends(require("profit.view"))])
async def digest_preview(
    session: SessionDep, period: Annotated[Period, Query()] = "daily"
) -> dict[str, Any]:
    """Vista previa del resumen. No envía nada."""
    digest = await build_digest(session, period)
    return {"text": render_text(digest), "html": render_html(digest), "digest": digest}


@router.post("/notifications/test", dependencies=[Depends(require("sync.run", writes=True))])
async def notifications_test(session: SessionDep) -> dict[str, Any]:
    """Entrega un resumen de prueba a los canales configurados (acción explícita del usuario)."""
    if not any(configured_channels().values()):
        raise HTTPException(
            409,
            detail={
                "problems": [
                    "No hay ningún canal configurado: define las variables de correo, Slack o webhook y reinicia el backend."
                ]
            },
        )
    digest = await build_digest(session, "daily")
    results = await deliver(session, "test", digest)
    await session.commit()
    return {"results": results}
