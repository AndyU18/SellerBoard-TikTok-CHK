"""Endpoint de solo lectura: rentabilidad real por creador, video y LIVE."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.creator_profit import creator_profit_report
from app.db import get_session

router = APIRouter(prefix="/api/creators")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]


@router.get("/profit", dependencies=VIEW)
async def profit(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    level: Literal["creator", "video", "live"] = "creator",
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Utilidad real por entidad: ventas menos promo, comisiones, envío, reembolsos, COGS y muestras."""
    return await creator_profit_report(session, start=start, end=end, level=level, q=q, limit=limit, offset=offset)
