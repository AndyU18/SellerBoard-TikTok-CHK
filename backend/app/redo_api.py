"""Endpoints de solo lectura del apartado Redo: resumen, costos, facturación y libro de cargos."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.redo_analytics import redo_billing, redo_charges, redo_costs, redo_overview

router = APIRouter(prefix="/api/redo")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=365)]


@router.get("/overview", dependencies=VIEW)
async def overview(session: SessionDep, days: Days = 90) -> dict:
    """Gasto en etiquetas, correcciones, anulaciones, comisiones y cobertura contra las ventas."""
    return await redo_overview(session, days=days)


@router.get("/costs", dependencies=VIEW)
async def costs(session: SessionDep, days: Days = 90) -> dict:
    """Costo por orden: por canal, por estado de destino, distribución y órdenes más caras."""
    return await redo_costs(session, days=days)


@router.get("/billing", dependencies=VIEW)
async def billing(session: SessionDep) -> dict:
    """Facturas, recargas de saldo y cuánto dura cada recarga al ritmo actual de gasto."""
    return await redo_billing(session)


@router.get("/charges", dependencies=VIEW)
async def charges(
    session: SessionDep,
    subtype: str | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    start: date | None = None,
    end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Libro de cargos de Redo, con filtros por tipo, orden/nota y fechas."""
    return await redo_charges(session, subtype=subtype, q=q, start=start, end=end, limit=limit, offset=offset)
