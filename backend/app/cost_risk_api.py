"""Endpoint de solo lectura de la utilidad en riesgo por costo faltante (ver app/cost_risk.py)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.cost_risk import cost_risk_report
from app.db import get_session
from app.models import Channel

router = APIRouter(prefix="/api/costs")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=365)]


@router.get("/risk", dependencies=VIEW)
async def cost_risk(
    session: SessionDep,
    days: Days = 90,
    channel: Channel | None = None,
    account: Annotated[str | None, Query(max_length=80)] = None,
) -> dict:
    """SKU sin costo cargado, su utilidad en riesgo y el orden sugerido para cargarlos."""
    return await cost_risk_report(session, days=days, channel=channel, account=account)
