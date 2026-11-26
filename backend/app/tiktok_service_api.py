"""Endpoints de solo lectura del servicio al cliente de TikTok Shop. El análisis vive en `tiktok_service_analytics`.

Todos exigen `profit.view` y devuelven únicamente cifras y metadatos: ningún texto de mensaje, nombre ni id de
comprador existe en la base, así que no puede salir por aquí. No se envía, cierra ni cambia nada en TikTok.
Este router no se registra en `main.py`: lo hace quien integra el módulo.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import tiktok_service_analytics as analytics
from app.auth import require
from app.db import get_session

router = APIRouter(prefix="/api/tiktok-service")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=90)]
StatusFilter = Literal["waiting_seller", "waiting_buyer", "closed"]
# Debe coincidir con `CATEGORIES` del conector (lo verifica una prueba).
CategoryFilter = Literal["complaint", "return_refund", "shipping_tracking", "order_status", "product_question", "other"]
MaxWaitHours = Annotated[float, Query(ge=0, le=24 * 90)]


@router.get("/overview", dependencies=VIEW)
async def overview(session: SessionDep, days: Days = 30) -> dict:
    """Abiertas, esperando al vendedor, no leídos, respuesta media/mediana/p90, % en 1 h y 24 h, volumen y categorías."""
    return await analytics.overview(session, days)


@router.get("/conversations", dependencies=VIEW)
async def conversations(
    session: SessionDep,
    status: StatusFilter | None = None,
    category: CategoryFilter | None = None,
    min_wait_hours: MaxWaitHours | None = None,
    limit: Annotated[int, Query(ge=1, le=analytics.MAX_LIST_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Conversaciones (solo metadatos: id opaco, fechas, estado, categoría, espera en horas, no leídos y conteos)."""
    return await analytics.conversations(session, status, category, min_wait_hours, limit, offset)


@router.get("/response-times", dependencies=VIEW)
async def response_times(session: SessionDep, days: Days = 30) -> dict:
    """Primera respuesta por rangos, por hora del día y por día de la semana (zona CHK_REPORT_TIMEZONE)."""
    return await analytics.response_times(session, days)


@router.get("/categories", dependencies=VIEW)
async def categories(session: SessionDep, days: Days = 30) -> dict:
    """Volumen, tiempo de respuesta y no resueltas por categoría."""
    return await analytics.categories(session, days)


@router.get("/backlog", dependencies=VIEW)
async def backlog(session: SessionDep) -> dict:
    """Conversaciones sin responder por antigüedad: <1 h, 1-6 h, 6-24 h, 1-3 d y >3 d."""
    return await analytics.backlog(session)


@router.get("/agents", dependencies=VIEW)
async def agents(session: SessionDep) -> dict:
    """¿Aceptan chats los agentes?, carga actual y métricas oficiales de TikTok de los últimos 7 días."""
    return await analytics.agents(session)
