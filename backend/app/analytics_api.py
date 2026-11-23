"""Endpoints de análisis avanzado: precios, aranceles, reposición, caja proyectada, conciliación,
costo de devoluciones, rentabilidad del envío, efecto de promociones, creadores, pronóstico y metas,
y estado de resultados con gastos.

Mismo patrón que `api.py`: la lógica vive en módulos de dominio (`pricing`, `landed`, `pnl`...) y aquí
solo se validan parámetros, permisos y errores.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.cashflow_forecast import cash_projection
from app.config import settings
from app.creators import creator_decisions_report
from app.db import get_session
from app.forecast import (
    delete_goal,
    forecast_report,
    goal_progress,
    list_goals,
    upsert_goal,
)
from app.gold import refresh_daily_profit
from app.landed import LandedFileError, import_landed_workbook, tariff_report
from app.models import Channel
from app.pnl import (
    ExpenseError,
    create_expense,
    delete_expense,
    list_categories,
    list_expenses,
    pnl_csv,
    pnl_report,
)
from app.pricing import parity_report, price_floor_report
from app.promo_uplift import promo_effect_report
from app.reconciliation import reconciliation_report
from app.reorder import reorder_report
from app.returns_cost import returns_cost_report
from app.shipping_profit import shipping_profitability_report

router = APIRouter(prefix="/api")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=365)]


# ---------------------------------------------------------------------------- precios y aranceles


@router.get("/pricing/floor", dependencies=VIEW)
async def pricing_floor(
    session: SessionDep,
    days: Days = 60,
    target_margin: Annotated[float, Query(ge=0, le=0.9)] = 0.15,
    min_units: Annotated[int, Query(ge=1, le=100)] = 3,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Precio de equilibrio, precio meta, descuento máximo y precio de lista sugerido por SKU y canal."""
    return await price_floor_report(
        session, days=days, target_margin=target_margin, min_units=min_units, channel=channel, account=account
    )


@router.get("/pricing/parity", dependencies=VIEW)
async def pricing_parity(
    session: SessionDep,
    days: Days = 60,
    threshold: Annotated[float, Query(ge=0.01, le=1)] = 0.10,
) -> dict:
    """Mismo producto en varios canales: brecha de precio y de utilidad por unidad."""
    return await parity_report(session, days=days, threshold=threshold)


@router.get("/tariffs", dependencies=VIEW)
async def tariffs(
    session: SessionDep,
    extra_duty: Annotated[float, Query(ge=0, le=2)] = 0.0,
    days: Days = 60,
) -> dict:
    """Costo desglosado por contenedor y efecto en el margen de subir el arancel `extra_duty` (0.10 = +10 %)."""
    return await tariff_report(session, extra_duty=extra_duty, days=days)


@router.post("/import/landed-costs", dependencies=[Depends(require("costs.edit", writes=True))])
async def upload_landed_costs(session: SessionDep, file: UploadFile, apply_costs: bool = True) -> dict:
    """Excel de landed cost (reporte por contenedor o lista plana): guarda recepciones y actualiza el COGS."""
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "Archivo demasiado grande")
    try:
        result = await import_landed_workbook(session, data, apply_costs=apply_costs)
    except LandedFileError as exc:
        raise HTTPException(422, {"problems": [str(exc)]}) from exc
    if apply_costs and result.get("cost_changes"):
        # Un costo nuevo cambia la utilidad de todo el historial.
        await refresh_daily_profit(session, None, None, reason="import_landed_costs")
    await session.commit()
    return result


# ---------------------------------------------------------------------------- operación y caja


@router.get("/reorder", dependencies=VIEW)
async def reorder(
    session: SessionDep,
    lead_time_days: Annotated[int, Query(ge=1, le=365)] = 60,
    safety_days: Annotated[int, Query(ge=0, le=180)] = 14,
    cover_days: Annotated[int, Query(ge=7, le=365)] = 60,
    demand_multiplier: Annotated[float, Query(ge=0.1, le=10)] = 1.0,
    days: Annotated[int, Query(ge=7, le=90)] = 30,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Punto de reorden, cantidad a pedir, días para agotarse y capital parado en inventario."""
    return await reorder_report(
        session,
        lead_time_days=lead_time_days,
        safety_days=safety_days,
        cover_days=cover_days,
        demand_multiplier=demand_multiplier,
        days=days,
        channel=channel,
        account=account,
    )


@router.get("/cashflow/projection", dependencies=VIEW)
async def cashflow_projection(
    session: SessionDep,
    horizon_days: Annotated[int, Query(ge=7, le=90)] = 14,
    lookback_days: Annotated[int, Query(ge=7, le=90)] = 28,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Dinero que se espera recibir en los próximos días, con el rezago de pago medido."""
    return await cash_projection(
        session, horizon_days=horizon_days, lookback_days=lookback_days, channel=channel, account=account
    )


@router.get("/reconciliation", dependencies=VIEW)
async def reconciliation(session: SessionDep) -> dict:
    """Etiquetas sin orden, comisiones anormales y pagos fallidos o atorados."""
    return await reconciliation_report(session)


@router.get("/returns/cost", dependencies=VIEW)
async def returns_cost(
    session: SessionDep,
    days: Days = 90,
    default_loss_rate: Annotated[float, Query(ge=0, le=1)] = 0.5,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Costo real de las devoluciones: envío perdido y producto que no se puede revender."""
    return await returns_cost_report(
        session, days=days, default_loss_rate=default_loss_rate, channel=channel, account=account
    )


@router.get("/shipping-profitability", dependencies=VIEW)
async def shipping_profitability(
    session: SessionDep,
    days: Days = 90,
    threshold: Annotated[float, Query(ge=0.01, le=1)] = 0.20,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Envío como porcentaje del precio, por estado de destino y por SKU."""
    return await shipping_profitability_report(
        session, days=days, threshold=threshold, channel=channel, account=account
    )


@router.get("/promotions/effect", dependencies=VIEW)
async def promotions_effect(
    session: SessionDep,
    days: Days = 90,
    promo_threshold: Annotated[float, Query(ge=0.01, le=0.9)] = 0.05,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """¿Las promociones aumentaron las ventas y la utilidad? Días con promoción contra días sin ella."""
    return await promo_effect_report(
        session, days=days, promo_threshold=promo_threshold, channel=channel, account=account
    )


@router.get("/creators/decisions", dependencies=VIEW)
async def creators_decisions(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    wait_days: Annotated[int, Query(ge=1, le=90)] = 14,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Qué creadores repetir, esperar, observar o dejar de enviar, y qué contenido vende más."""
    return await creator_decisions_report(
        session, start=start, end=end, wait_days=wait_days, channel=channel, account=account
    )


# ---------------------------------------------------------------------------- pronóstico y metas


@router.get("/forecast", dependencies=VIEW)
async def forecast(
    session: SessionDep,
    horizon_days: Annotated[int, Query(ge=7, le=90)] = 30,
    lookback_days: Annotated[int, Query(ge=14, le=90)] = 28,
    multiplier: Annotated[float, Query(ge=0.1, le=10)] = 1.0,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Ventas, unidades y utilidad esperadas con el ritmo reciente (y un multiplicador de temporada)."""
    return await forecast_report(
        session,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        multiplier=multiplier,
        channel=channel,
        account=account,
    )


class GoalIn(BaseModel):
    month: date
    metric: Literal["gross_sales", "net_profit", "units"]
    target: float = Field(ge=0)
    channel: Channel | None = None


@router.get("/goals", dependencies=VIEW)
async def goals(
    session: SessionDep, month: date | None = None, multiplier: Annotated[float, Query(ge=0.1, le=10)] = 1.0
) -> dict:
    """Avance de las metas del mes con la proyección al cierre."""
    return await goal_progress(session, month, multiplier=multiplier)


@router.put("/goals", dependencies=[Depends(require("expenses.edit", writes=True))])
async def put_goal(session: SessionDep, body: GoalIn) -> dict:
    try:
        goal = await upsert_goal(
            session, body.month, body.metric, body.target, body.channel.value if body.channel else None
        )
    except ValueError as exc:
        raise HTTPException(422, {"problems": [str(exc)]}) from exc
    await session.commit()
    return {"id": goal.id}


@router.delete("/goals/{goal_id}", dependencies=[Depends(require("expenses.edit", writes=True))])
async def remove_goal(session: SessionDep, goal_id: int) -> dict:
    if not await delete_goal(session, goal_id):
        raise HTTPException(404, "La meta no existe")
    await session.commit()
    return {"deleted": goal_id}


@router.get("/goals/all", dependencies=VIEW)
async def all_goals(session: SessionDep) -> list[dict]:
    return [
        {"id": g.id, "month": g.month.isoformat(), "metric": g.metric, "channel": g.channel_code, "target": float(g.target)}
        for g in await list_goals(session)
    ]


# ---------------------------------------------------------------------------- estado de resultados y gastos


@router.get("/pnl", dependencies=VIEW)
async def pnl(
    session: SessionDep, start: date | None = None, end: date | None = None, channel: Channel | None = None
) -> dict:
    """Estado de resultados mensual por canal, con gastos indirectos prorrateados."""
    return await pnl_report(session, start=start, end=end, channel=channel)


@router.get("/pnl.csv", dependencies=VIEW)
async def pnl_download(
    session: SessionDep, start: date | None = None, end: date | None = None, channel: Channel | None = None
) -> Response:
    report = await pnl_report(session, start=start, end=end, channel=channel)
    return Response(
        pnl_csv(report),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="estado_de_resultados.csv"'},
    )


class ExpenseIn(BaseModel):
    category_id: int
    description: str = Field(min_length=1, max_length=200)
    amount: float = Field(ge=0)
    start_date: date
    recurrence: Literal["one_time", "monthly", "annual"] = "one_time"
    end_date: date | None = None
    channel: Channel | None = None


@router.get("/expense-categories", dependencies=VIEW)
async def expense_categories(session: SessionDep) -> list[dict]:
    return [{"id": c.id, "code": c.code, "name": c.name} for c in await list_categories(session)]


@router.get("/expenses", dependencies=VIEW)
async def expenses(session: SessionDep) -> list[dict]:
    return await list_expenses(session)


@router.post("/expenses", dependencies=[Depends(require("expenses.edit", writes=True))])
async def add_expense(session: SessionDep, body: ExpenseIn) -> dict:
    try:
        expense = await create_expense(
            session,
            category_id=body.category_id,
            description=body.description,
            amount=body.amount,
            start_date=body.start_date,
            recurrence=body.recurrence,
            end_date=body.end_date,
            channel=body.channel.value if body.channel else None,
        )
    except ExpenseError as exc:
        raise HTTPException(422, {"problems": [str(exc)]}) from exc
    await session.commit()
    return {"id": expense.id}


@router.delete("/expenses/{expense_id}", dependencies=[Depends(require("expenses.edit", writes=True))])
async def remove_expense(session: SessionDep, expense_id: int) -> dict:
    if not await delete_expense(session, expense_id):
        raise HTTPException(404, "El gasto no existe")
    await session.commit()
    return {"deleted": expense_id}
