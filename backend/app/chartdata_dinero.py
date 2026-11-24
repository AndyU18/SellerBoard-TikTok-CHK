"""Datos extra para las gráficas de dinero (LIVE, creativos, embudo): solo lectura.

Reutiliza los mismos filtros de canal y tienda que `reports.py`; no escribe nada.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.models import AffiliateOrder, Channel, LiveSession, Shipment
from app.reports import _aware, _f, _scope_ids

router = APIRouter(prefix="/api/chartdata")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]


@router.get("/live-sessions", dependencies=VIEW)
async def live_sessions(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 600,
) -> dict:
    """Todas las sesiones LIVE del período en orden cronológico (hora en UTC), para series y mapa de calor."""
    query = select(LiveSession)
    ids = await _scope_ids(session, channel, account)
    if ids is not None:
        query = query.where(LiveSession.account_id.in_(ids))
    rows = []
    for s in (await session.scalars(query)).all():
        started = _aware(s.start_at)
        if started is None:
            continue
        if (start and started.date() < start) or (end and started.date() > end):
            continue
        ended = _aware(s.end_at)
        minutes = (ended - started).total_seconds() / 60 if ended and ended > started else None
        rows.append(
            {
                "external_id": s.external_id,
                "username": s.username,
                "start_at": started.isoformat(),
                "gmv": _f(s.gmv),
                "orders": int(s.orders),
                "customers": int(s.customers),
                "units": int(s.units),
                "duration_min": minutes,
            }
        )
    rows.sort(key=lambda r: r["start_at"])
    return {"sessions": rows[-limit:], "timezone": "UTC"}


def _week(day: date) -> str:
    """Lunes de la semana de `day` (clave de la semana)."""
    return (day - timedelta(days=day.weekday())).isoformat()


@router.get("/creative-weekly", dependencies=VIEW)
async def creative_weekly(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 15,
) -> dict:
    """Ventas (base de comisión) y órdenes por semana de los `limit` contenidos de creador con más ventas."""
    query = select(AffiliateOrder).where(AffiliateOrder.content_id.is_not(None))
    ids = await _scope_ids(session, channel, account)
    if ids is not None:
        query = query.where(AffiliateOrder.account_id.in_(ids))
    if start:
        query = query.where(AffiliateOrder.order_date >= start)
    if end:
        query = query.where(AffiliateOrder.order_date <= end)
    contents: dict[tuple[str, str], dict] = {}
    weeks: set[str] = set()
    orders_seen: set[tuple[str, str, str]] = set()
    for a in (await session.scalars(query)).all():
        key = (a.content_type or "", a.content_id or "")
        week = _week(a.order_date)
        weeks.add(week)
        c = contents.setdefault(
            key,
            {"content_type": key[0], "content_id": key[1], "creator": a.creator_username, "gmv": 0.0, "weeks": {}},
        )
        cell = c["weeks"].setdefault(week, {"gmv": 0.0, "orders": 0})
        cell["gmv"] += _f(a.commission_base)
        c["gmv"] += _f(a.commission_base)
        order_key = (*key, a.external_order_id)
        if order_key not in orders_seen:
            orders_seen.add(order_key)
            cell["orders"] += 1
    ranked = sorted(contents.values(), key=lambda c: c["gmv"], reverse=True)[:limit]
    return {"weeks": sorted(weeks), "rows": ranked}


@router.get("/funnel-delivery", dependencies=VIEW)
async def funnel_delivery(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Órdenes con envío registrado y cuántas ya se entregaron (sin muestras): últimas etapas del embudo."""
    query = select(func.count(), func.count(Shipment.delivered_at), func.count(Shipment.collected_at)).where(
        Shipment.is_sample.is_(False)
    )
    ids = await _scope_ids(session, channel, account)
    if ids is not None:
        query = query.where(Shipment.account_id.in_(ids))
    if start:
        query = query.where(Shipment.order_date >= start)
    if end:
        query = query.where(Shipment.order_date <= end)
    total, delivered, shipped = (await session.execute(query)).one()
    return {"orders": int(total), "shipped": int(shipped), "delivered": int(delivered)}
