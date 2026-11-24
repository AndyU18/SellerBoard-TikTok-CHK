"""Datos de solo lectura para las gráficas de operación: devoluciones en el tiempo y envío por canal / rango.

Complementa a reorder.py, returns_cost.py y shipping_profit.py con series que esas APIs no entregan:
tendencia de devoluciones (semana o mes), matriz producto x período y costo de envío por canal y por rango
de costo por orden. No escribe nada. El peso del paquete no está en los datos, por eso el "rango" de envío
es de costo por orden.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.models import (
    Account,
    Channel,
    OrderLine,
    Product,
    ReturnCase,
    ReturnCaseLine,
    SalesOrder,
    ShippingLabelCost,
)

router = APIRouter(prefix="/api/chartdata")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]

# Rangos de costo de envío por orden: (límite superior exclusivo, etiqueta).
BANDS: tuple[tuple[float, str], ...] = (
    (3, "Menos de $3"),
    (5, "$3 a $5"),
    (8, "$5 a $8"),
    (12, "$8 a $12"),
    (float("inf"), "$12 o más"),
)
MAX_MATRIX_ROWS = 15


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def _granularity(start: date | None, end: date | None) -> str:
    """Semanas para períodos cortos (hasta ~4 meses), meses para los largos."""
    if start and end and (end - start).days <= 120:
        return "week"
    return "month"


def _bucket(day: date, granularity: str) -> str:
    if granularity == "week":
        return (day - timedelta(days=day.weekday())).isoformat()
    return day.strftime("%Y-%m")


def _keys_between(start: date, end: date, granularity: str) -> list[str]:
    out: list[str] = []
    cur = start
    while cur <= end:
        key = _bucket(cur, granularity)
        if key not in out:
            out.append(key)
        cur += timedelta(days=1)
    return out


async def _returned_lines(
    session: AsyncSession, start: date | None, end: date | None, channel: Channel | None, account: str | None
) -> list[tuple[ReturnCase, ReturnCaseLine]]:
    query = (
        select(ReturnCase, ReturnCaseLine)
        .join(Account, ReturnCase.account_id == Account.id)
        .join(ReturnCaseLine, ReturnCaseLine.case_id == ReturnCase.id)
        .where(ReturnCase.kind == "return")
    )
    if start:
        query = query.where(ReturnCase.case_date >= start)
    if end:
        query = query.where(ReturnCase.case_date <= end)
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)
    rows = (await session.execute(query)).all()
    # Las solicitudes que el comprador retiró no cuentan.
    return [(c, line) for c, line in rows if not (c.status or "").endswith("_CANCEL")]


async def _units_sold(
    session: AsyncSession, start: date | None, end: date | None, channel: Channel | None, account: str | None
) -> list[tuple[date, int]]:
    query = (
        select(OrderLine.order_date, func.sum(OrderLine.units))
        .join(Account, OrderLine.account_id == Account.id)
        .group_by(OrderLine.order_date)
    )
    if start:
        query = query.where(OrderLine.order_date >= start)
    if end:
        query = query.where(OrderLine.order_date <= end)
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)
    return [(d, int(n or 0)) for d, n in (await session.execute(query)).all()]


async def returns_trend(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Devoluciones, unidades devueltas, vendidas y tasa por semana (o mes)."""
    gran = _granularity(start, end)
    buckets: dict[str, dict[str, Any]] = {}

    def slot(key: str) -> dict[str, Any]:
        return buckets.setdefault(
            key, {"period": key, "_cases": set(), "returned_units": 0, "units_sold": 0, "refund": 0.0}
        )

    for case, line in await _returned_lines(session, start, end, channel, account):
        b = slot(_bucket(case.case_date, gran))
        b["_cases"].add(case.id)
        b["returned_units"] += line.units
        b["refund"] += _f(line.refund_total)
    for day, units in await _units_sold(session, start, end, channel, account):
        slot(_bucket(day, gran))["units_sold"] += units

    rows = []
    for key in sorted(buckets):
        b = buckets[key]
        rows.append(
            {
                "period": key,
                "returns": len(b["_cases"]),
                "returned_units": b["returned_units"],
                "units_sold": b["units_sold"],
                "return_rate": _ratio(b["returned_units"], b["units_sold"]),
                "refund": b["refund"],
            }
        )
    return {"granularity": gran, "rows": rows}


async def returns_matrix(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Unidades devueltas por producto y período (los 15 productos con más devoluciones)."""
    gran = _granularity(start, end)
    pairs = await _returned_lines(session, start, end, channel, account)
    if start and end:
        periods = _keys_between(start, end, gran)
    else:
        periods = sorted({_bucket(c.case_date, gran) for c, _ in pairs})
    index = {p: i for i, p in enumerate(periods)}

    skus: dict[str, dict[str, Any]] = {}
    for case, line in pairs:
        i = index.get(_bucket(case.case_date, gran))
        if i is None:
            continue
        s = skus.setdefault(
            line.sku,
            {"sku": line.sku, "product_name": line.product_name, "values": [0] * len(periods), "total": 0},
        )
        s["values"][i] += line.units
        s["total"] += line.units
    rows = sorted(skus.values(), key=lambda r: -r["total"])[:MAX_MATRIX_ROWS]
    return {"granularity": gran, "periods": periods, "rows": rows}


async def shipping_breakdown(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Costo de envío por canal y por rango de costo por orden (línea + etiqueta de la orden)."""
    query = (
        select(
            OrderLine.order_id,
            SalesOrder.external_order_id,
            Account.channel_code,
            OrderLine.gross_sales,
            OrderLine.promo,
            OrderLine.shipping_cost,
        )
        .join(SalesOrder, OrderLine.order_id == SalesOrder.id)
        .join(Account, OrderLine.account_id == Account.id)
        .join(Product, OrderLine.product_id == Product.id)
    )
    if start:
        query = query.where(OrderLine.order_date >= start)
    if end:
        query = query.where(OrderLine.order_date <= end)
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)

    labels: dict[str, float] = defaultdict(float)
    for oid, amount in (
        await session.execute(select(ShippingLabelCost.external_order_id, ShippingLabelCost.amount))
    ).all():
        labels[oid] += _f(amount)

    orders: dict[int, dict[str, Any]] = {}
    for order_id, ext_id, ch, gross, promo, ship in (await session.execute(query)).all():
        o = orders.setdefault(
            order_id, {"ext": ext_id, "channel": ch, "net": 0.0, "shipping": 0.0}
        )
        o["net"] += _f(gross) - _f(promo)
        o["shipping"] += _f(ship)
    for o in orders.values():
        o["shipping"] += labels.get(o["ext"], 0.0)

    def blank() -> dict[str, float]:
        return {"orders": 0, "shipping": 0.0, "net_sales": 0.0}

    by_channel: dict[str, dict[str, float]] = defaultdict(blank)
    by_band: list[dict[str, float]] = [blank() for _ in BANDS]
    for o in orders.values():
        for bucket in (by_channel[o["channel"]], by_band[next(i for i, (hi, _) in enumerate(BANDS) if o["shipping"] < hi)]):
            bucket["orders"] += 1
            bucket["shipping"] += o["shipping"]
            bucket["net_sales"] += o["net"]

    def finish(key: str, b: dict[str, float]) -> dict[str, Any]:
        return {
            "key": key,
            "orders": int(b["orders"]),
            "shipping": b["shipping"],
            "net_sales": b["net_sales"],
            "shipping_per_order": _ratio(b["shipping"], b["orders"]),
            "shipping_share": _ratio(b["shipping"], b["net_sales"]),
        }

    return {
        "by_channel": sorted((finish(k, v) for k, v in by_channel.items()), key=lambda r: -r["shipping"]),
        "by_band": [finish(label, b) for (_, label), b in zip(BANDS, by_band, strict=True)],
    }


@router.get("/returns-trend", dependencies=VIEW)
async def returns_trend_endpoint(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Devoluciones y tasa de devolución por semana o mes."""
    return await returns_trend(session, start=start, end=end, channel=channel, account=account)


@router.get("/returns-matrix", dependencies=VIEW)
async def returns_matrix_endpoint(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Unidades devueltas por producto y período."""
    return await returns_matrix(session, start=start, end=end, channel=channel, account=account)


@router.get("/shipping-breakdown", dependencies=VIEW)
async def shipping_breakdown_endpoint(
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Costo de envío por canal y por rango de costo por orden."""
    return await shipping_breakdown(session, start=start, end=end, channel=channel, account=account)
