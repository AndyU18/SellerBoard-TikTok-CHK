"""Endpoint de solo lectura de la política de muestras."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.sample_policy import sample_policy_report

router = APIRouter(prefix="/api/samples")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]

MAX_SAMPLES_LIMIT = 20
MAX_DAYS_LIMIT = 365


@router.get("/policy", dependencies=VIEW)
async def policy(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    max_samples_without_sale: Annotated[int, Query(ge=0, le=MAX_SAMPLES_LIMIT)] = 2,
    max_days_to_first_sale: Annotated[int, Query(ge=1, le=MAX_DAYS_LIMIT)] = 30,
) -> dict:
    """Puntuación y recomendación por creador, y la simulación de la regla sobre el historial."""
    return await sample_policy_report(
        session,
        start=start,
        end=end,
        max_samples_without_sale=max_samples_without_sale,
        max_days_to_first_sale=max_days_to_first_sale,
    )
