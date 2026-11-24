"""Endpoint de solo lectura de la utilidad por orden (ver app/order_profit.py)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.models import Channel
from app.order_profit import order_profit_report

router = APIRouter(prefix="/api/orders")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]

Sort = Literal[
    "profit_asc", "profit_desc", "sales_asc", "sales_desc", "date_asc", "date_desc"
]
Driver = Literal["cogs", "fees", "shipping", "refunds", "promo"]


@router.get("/profit", dependencies=VIEW)
async def order_profit(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    only_loss: bool = False,
    sort: Sort = "profit_asc",
    limit: Annotated[int, Query(ge=1, le=200)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    driver: Driver | None = None,
    profit_min: float | None = None,
    profit_max: float | None = None,
) -> dict:
    """Utilidad orden por orden (sin publicidad), con resumen de todo el filtro y causa principal de pérdida."""
    return await order_profit_report(
        session,
        start=start,
        end=end,
        channel=channel,
        account=account,
        q=q,
        only_loss=only_loss,
        sort=sort,
        limit=limit,
        offset=offset,
        driver=driver,
        profit_min=profit_min,
        profit_max=profit_max,
    )
