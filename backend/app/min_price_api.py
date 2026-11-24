"""Endpoint de solo lectura del precio mínimo con envío real (lógica en `app.min_price`).

Cuelga de /api/pricing junto a /floor y /parity (analytics_api.py): la ruta propia es /min-price, sin choques.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.min_price import DEFAULT_DAYS, MAX_TARGET_MARGIN, min_price_report
from app.models import Channel

router = APIRouter(prefix="/api/pricing")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]


@router.get("/min-price", dependencies=VIEW)
async def min_price(
    session: SessionDep,
    days: Annotated[int, Query(ge=7, le=365)] = DEFAULT_DAYS,
    target_margin: Annotated[float, Query(ge=0, le=MAX_TARGET_MARGIN)] = 0.0,
    channel: Channel | None = None,
    account: Annotated[str | None, Query(max_length=80)] = None,
) -> dict:
    """Precio mínimo por SKU y canal con el costo real de las etiquetas de Redo, y pérdida por venta bajo el piso."""
    return await min_price_report(
        session, days=days, target_margin=target_margin, channel=channel, account=account or None
    )
