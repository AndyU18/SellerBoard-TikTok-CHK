"""Artículos del pedido para el Dashboard: una fila por línea de orden, paginada y con búsqueda (solo lectura)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.costs import load_cost_history
from app.db import get_session
from app.models import Account, Channel, OrderLine, Product, SalesOrder

router = APIRouter(prefix="/api/chartdata")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/order-lines", dependencies=VIEW)
async def order_lines(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Líneas de pedido (más recientes primero) con costo y ganancia por línea.

    La ganancia de la línea es ventas - promo - comisiones - envío - reembolso - costo del producto
    (la publicidad no se reparte por línea). `summary` cubre todo el filtro, no solo la página.
    """
    filters = []
    if start:
        filters.append(OrderLine.order_date >= start)
    if end:
        filters.append(OrderLine.order_date <= end)
    if channel:
        filters.append(Account.channel_code == channel.value)
    if account:
        filters.append(Account.name == account)
    if q and q.strip():
        pattern = _like(q.strip())
        filters.append(
            or_(
                SalesOrder.external_order_id.ilike(pattern, escape="\\"),
                Product.sku.ilike(pattern, escape="\\"),
                OrderLine.channel_sku.ilike(pattern, escape="\\"),
                Product.name.ilike(pattern, escape="\\"),
            )
        )

    def base(*columns: Any):
        return (
            select(*columns)
            .select_from(OrderLine)
            .join(SalesOrder, OrderLine.order_id == SalesOrder.id)
            .join(Account, OrderLine.account_id == Account.id)
            .join(Product, OrderLine.product_id == Product.id)
            .where(*filters)
        )

    total = await session.scalar(base(func.count(OrderLine.id))) or 0
    page = (
        await session.execute(
            base(
                OrderLine.id,
                SalesOrder.external_order_id,
                OrderLine.order_date,
                Account.channel_code,
                Account.name,
                Product.id,
                Product.sku,
                Product.name,
                OrderLine.units,
                OrderLine.gross_sales,
                OrderLine.promo,
                OrderLine.platform_fees,
                OrderLine.shipping_cost,
                OrderLine.refund_amount,
            )
            .order_by(OrderLine.order_date.desc(), OrderLine.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    # Totales de todo el filtro (el costo depende de la fecha, así que se suma por línea).
    summary = {"lines": total, "units": 0, "gross_sales": 0.0, "net_profit": 0.0}
    costs = await load_cost_history(session)
    for pid, day, units, gross, promo, fees, ship, refund in (
        await session.execute(
            base(
                OrderLine.product_id,
                OrderLine.order_date,
                OrderLine.units,
                OrderLine.gross_sales,
                OrderLine.promo,
                OrderLine.platform_fees,
                OrderLine.shipping_cost,
                OrderLine.refund_amount,
            )
        )
    ).all():
        cogs = units * costs.unit_cost(pid, day)
        summary["units"] += units
        summary["gross_sales"] += _f(gross)
        summary["net_profit"] += (
            _f(gross) - _f(promo) - _f(fees) - _f(ship) - _f(refund) - cogs
        )

    rows = []
    for (
        line_id,
        ext,
        day,
        ch,
        acc,
        pid,
        sku,
        name,
        units,
        gross,
        promo,
        fees,
        ship,
        refund,
    ) in page:
        cogs = units * costs.unit_cost(pid, day)
        rows.append(
            {
                "id": line_id,
                "order": ext,
                "date": day.isoformat(),
                "channel": ch,
                "account": acc,
                "sku": sku,
                "product_name": name,
                "units": units,
                "gross_sales": _f(gross),
                "promo": _f(promo),
                "platform_fees": _f(fees),
                "shipping_cost": _f(ship),
                "refund_amount": _f(refund),
                "cogs": cogs,
                "net_profit": _f(gross)
                - _f(promo)
                - _f(fees)
                - _f(ship)
                - _f(refund)
                - cogs,
            }
        )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "summary": summary,
        "rows": rows,
    }
