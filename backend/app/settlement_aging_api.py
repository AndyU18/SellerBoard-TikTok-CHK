"""Endpoint de solo lectura de la antigüedad del dinero pendiente de liquidar.

Comparte el prefijo /api/settlements con settlement_api.py: la ruta literal "/aging" no choca con
"/{settlement_id}/waterfall" (distinto número de segmentos).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.settlement_aging import DEFAULT_OLDEST_LIMIT, settlement_aging_report

router = APIRouter(prefix="/api/settlements")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]


@router.get("/aging", dependencies=VIEW)
async def aging(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = DEFAULT_OLDEST_LIMIT,
) -> dict:
    """Cubetas de antigüedad del dinero sin liquidar, órdenes más viejas y liquidaciones con diferencia."""
    return await settlement_aging_report(session, today=datetime.now(UTC).date(), oldest_limit=limit)
