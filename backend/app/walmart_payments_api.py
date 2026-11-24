"""Endpoint de solo lectura de pagos y comisiones de Walmart (reporte de conciliación).

Router delgado: la lógica vive en `app.walmart_payments`. Solo define `/payments`; las demás rutas
`/api/walmart/*` están en `app.walmart_api`. Aún no está registrado en `main.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.walmart_payments import walmart_payments_report

router = APIRouter(prefix="/api/walmart")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=365)]
DEFAULT_DAYS = 180


@router.get("/payments", dependencies=VIEW)
async def payments(session: SessionDep, days: Days = DEFAULT_DAYS) -> dict[str, Any]:
    """Ventas, comisión real vs estimada, ajustes y neto esperado por mes, con lo que falta guardar."""
    return await walmart_payments_report(session, days=days, today=date.today())
