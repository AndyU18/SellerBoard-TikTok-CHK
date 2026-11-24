"""Endpoints de solo lectura del desglose de liquidaciones de TikTok Shop."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.settlement_breakdown import settlement_breakdown_report, settlement_waterfall

router = APIRouter(prefix="/api/settlements")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]


@router.get("/breakdown", dependencies=VIEW)
async def breakdown(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    account: Annotated[str | None, Query(max_length=80)] = None,
) -> dict:
    """Comisiones y demás conceptos por concepto, mes y liquidación, contra el período anterior."""
    end = end or date.today()
    start = start or end - timedelta(days=89)
    if start > end:
        raise HTTPException(422, "start debe ser anterior a end")
    return await settlement_breakdown_report(session, start=start, end=end, account=account)


@router.get("/{settlement_id}/waterfall", dependencies=VIEW)
async def waterfall(settlement_id: int, session: SessionDep) -> dict:
    """Cascada de un depósito: ventas -> cada concepto -> depósito."""
    out = await settlement_waterfall(session, settlement_id)
    if out is None:
        raise HTTPException(404, "Liquidación no encontrada")
    return out
