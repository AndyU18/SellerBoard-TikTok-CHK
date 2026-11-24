"""Endpoint de solo lectura del simulador "¿y si...?": la economía unitaria real por SKU y canal."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.models import Channel
from app.simulator import simulator_baseline

router = APIRouter(prefix="/api/simulator")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]


@router.get("/baseline", dependencies=VIEW)
async def baseline(
    session: SessionDep,
    days: Annotated[int, Query(ge=7, le=365)] = 90,
    channel: Channel | None = None,
    account: Annotated[str | None, Query(max_length=80)] = None,
) -> dict:
    """Precio, promo, comisión, envío, reembolsos y COGS por unidad de cada SKU en los últimos N días."""
    return await simulator_baseline(session, days=days, channel=channel, account=account)
