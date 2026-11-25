"""Endpoints de solo lectura de TikTok Creator (la cuenta de creador propia). El análisis vive en
`tiktok_creator_analytics`; los datos los deja la fuente `tiktok_creator` de `sync_jobs`.

Todos exigen `profit.view` y no escriben nada, ni aquí ni en TikTok. No hay endpoint de colaboraciones: las
colaboraciones dirigidas exigen un `shop_id` que la API no expone en ninguna otra consulta.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import tiktok_creator_analytics as analytics
from app.auth import require
from app.db import get_session

router = APIRouter(prefix="/api/tiktok-creator")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=365)]
Sort = Literal["score", "commission", "units", "price", "newest"]
MAX_LIMIT = 200


@router.get("/status", dependencies=VIEW)
async def status(session: SessionDep) -> dict:
    """¿Configurado, conectado o hay que reautorizar? Vencimiento de tokens, últimas cargas y cobertura del catálogo."""
    return await analytics.status(session)


@router.get("/profile", dependencies=VIEW)
async def profile(session: SessionDep) -> dict:
    """Perfil de la cuenta de creador y sus permisos explicados en español."""
    return await analytics.profile(session)


@router.get("/opportunities", dependencies=VIEW)
async def opportunities(
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=80)] = None,
    category: Annotated[str | None, Query(max_length=80)] = None,
    min_commission: Annotated[float | None, Query(ge=0, le=100, description="Porcentaje mínimo de comisión")] = None,
    min_units: Annotated[int | None, Query(ge=0)] = None,
    sort: Sort = "score",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = analytics.DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_suspect: bool = False,
) -> dict:
    """Catálogo de afiliados con puntaje de oportunidad (comisión x demanda x disponibilidad x afinidad)."""
    return await analytics.opportunities(
        session, q=q, category=category, min_commission=min_commission, min_units=min_units,
        sort=sort, limit=limit, offset=offset, include_suspect=include_suspect,
    )


@router.get("/opportunities/summary", dependencies=VIEW)
async def opportunities_summary(session: SessionDep) -> dict:
    """Categorías, comisión promedio, productos nuevos, cambios de precio/comisión y los 10 mejores."""
    return await analytics.summary(session)


@router.get("/orders", dependencies=VIEW)
async def orders(session: SessionDep, days: Days = 90) -> dict:
    """Pedidos de afiliado propios con sus comisiones (vacío si no hay)."""
    return await analytics.orders(session, days)


@router.get("/showcase", dependencies=VIEW)
async def showcase(session: SessionDep) -> dict:
    """Vitrina propia: productos con estado de revisión e inventario."""
    return await analytics.showcase(session)
