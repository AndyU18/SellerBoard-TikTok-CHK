"""Endpoints de solo lectura del mercado de creadores y las colaboraciones del vendedor de TikTok Shop.

El análisis vive en `tiktok_creator_market_analytics`; los datos los deja la fuente `tiktok_creator_market` de
`sync_jobs`. Todos exigen `profit.view` y no escriben nada, ni aquí ni en TikTok. Este router no se registra en
`main.py` desde este módulo: quien lo integre lo monta con `app.include_router(router)`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import tiktok_creator_market_analytics as analytics
from app.auth import require
from app.db import get_session

router = APIRouter(prefix="/api/tiktok-creators")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
MAX_LIMIT = 200
MAX_SHORTLIST = 50


@router.get("/status", dependencies=VIEW)
async def status(session: SessionDep) -> dict:
    """Rutas y versiones disponibles, lo que no se pudo conseguir, cobertura de datos y última sincronización."""
    return await analytics.status(session)


@router.get("/marketplace", dependencies=VIEW)
async def marketplace(
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=80, description="Texto en el usuario o apodo")] = None,
    category: Annotated[str | None, Query(max_length=80, description="Id o nombre de la categoría de primer nivel")] = None,
    min_followers: Annotated[int | None, Query(ge=0)] = None,
    sort: analytics.Sort = "score",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = analytics.DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Creadores del mercado con puntaje de candidato explicado y anti-recomendación (muestras sin ventas, ya vende)."""
    return await analytics.marketplace(
        session, q=q, category=category, min_followers=min_followers, sort=sort, limit=limit, offset=offset
    )


@router.get("/marketplace/summary", dependencies=VIEW)
async def marketplace_summary(session: SessionDep) -> dict:
    """Distribución por categoría y por tamaño, creadores nuevos y los mejores candidatos."""
    return await analytics.summary(session)


@router.get("/collaborations", dependencies=VIEW)
async def collaborations(
    session: SessionDep,
    kind: analytics.CollabKind = "all",
    status: Annotated[str | None, Query(max_length=40, description="p. ej. NORMAL (abiertas) u ONGOING (dirigidas)")] = None,
) -> dict:
    """Colaboraciones abiertas y dirigidas con su estado, comisión y creadores involucrados."""
    return await analytics.collaborations(session, kind, status)


@router.get("/candidates", dependencies=VIEW)
async def candidates(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=MAX_SHORTLIST)] = analytics.SHORTLIST_SIZE
) -> dict:
    """Shortlist de los mejores creadores nuevos para invitar, cada uno con la razón de su puntaje."""
    return await analytics.candidates(session, limit)
