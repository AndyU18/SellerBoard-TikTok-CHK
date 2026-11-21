"""Reportes que leen la capa plata directamente: promociones, dayparting y flujo de caja.

La capa oro solo guarda utilidad por día/producto; estos reportes necesitan datos que no están
ahí (subsidios, hora de la orden, liquidaciones), así que consultan sales.* y, cuando hace falta la
utilidad, la cruzan con la capa oro.
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gold import load_gold_frame
from app.models import (
    Account,
    Channel,
    OrderLine,
    Product,
    ReturnCase,
    ReturnCaseLine,
    SalesOrder,
    Settlement,
    Shipment,
)
from app.profit import summarize

DEEP_DISCOUNT = 0.30  # descuento total (vendedor + plataforma) desde el cual se marca "agresivo"


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


# ---------------------------------------------------------------- promociones y subsidios
async def promotions_report(
    session: AsyncSession,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
) -> dict:
    """Descuentos del vendedor vs subsidio de la plataforma, por canal y producto.

    - promo: lo paga el vendedor y ya resta de la utilidad.
    - platform_discount: lo paga la plataforma; no resta utilidad (es dinero que no se pierde).
    - discount_depth: (promo + subsidio) / venta a precio de lista; None si no se conoce la lista.
    """
    query = (
        select(
            Account.channel_code,
            Product.sku,
            Product.name,
            func.sum(OrderLine.units),
            func.sum(OrderLine.gross_sales),
            func.sum(OrderLine.promo),
            func.sum(OrderLine.platform_discount),
            func.sum(OrderLine.list_amount),
        )
        .join(Account, OrderLine.account_id == Account.id)
        .join(Product, OrderLine.product_id == Product.id)
        .group_by(Account.channel_code, Product.sku, Product.name)
    )
    if start:
        query = query.where(OrderLine.order_date >= start)
    if end:
        query = query.where(OrderLine.order_date <= end)
    if channel:
        query = query.where(Account.channel_code == channel.value)
    data = [
        {
            "channel": ch,
            "sku": sku,
            "product_name": name,
            "units": int(units or 0),
            "gross_sales": _f(gross),
            "promo": _f(promo),
            "platform_discount": _f(platform),
            "list_amount": _f(listed),
        }
        for ch, sku, name, units, gross, promo, platform, listed in (await session.execute(query)).all()
    ]
    if not data:
        return {"rows": [], "totals": None}

    gold = await load_gold_frame(session, start=start, end=end, channel=channel)
    profit = (
        summarize(gold, ["channel", "sku"]).select("channel", "sku", "net_profit", "margin")
        if gold.height
        else pl.DataFrame(
            schema={"channel": pl.Utf8, "sku": pl.Utf8, "net_profit": pl.Float64, "margin": pl.Float64}
        )
    )
    frame = pl.DataFrame(data).join(profit, on=["channel", "sku"], how="left")
    rows = []
    for r in frame.sort("promo", descending=True).to_dicts():
        seller, platform = r["promo"], r["platform_discount"]
        depth = _ratio(seller + platform, r["list_amount"])
        rows.append(
            {
                **r,
                "promo_rate": _ratio(seller, r["gross_sales"]),
                "discount_depth": depth,
                "subsidy_share": _ratio(platform, seller + platform),
                "loses_money": r["net_profit"] is not None and r["net_profit"] < 0 and seller > 0,
                "deep_discount": depth is not None and depth >= DEEP_DISCOUNT,
            }
        )
    seller = sum(r["promo"] for r in rows)
    platform = sum(r["platform_discount"] for r in rows)
    listed = sum(r["list_amount"] for r in rows)
    gross = sum(r["gross_sales"] for r in rows)
    totals = {
        "units": sum(r["units"] for r in rows),
        "gross_sales": gross,
        "promo": seller,
        "platform_discount": platform,
        "list_amount": listed,
        "promo_rate": _ratio(seller, gross),
        "discount_depth": _ratio(seller + platform, listed),
        "subsidy_share": _ratio(platform, seller + platform),
    }
    return {"rows": rows, "totals": totals}


# ---------------------------------------------------------------- dayparting
async def dayparting_report(
    session: AsyncSession,
    tz_name: str,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
) -> dict:
    """Órdenes, unidades y ventas por día de la semana y hora (en la zona horaria del reporte)."""
    tz = ZoneInfo(tz_name)
    base = (
        select(
            SalesOrder.id,
            SalesOrder.ordered_at,
            func.sum(OrderLine.units),
            func.sum(OrderLine.gross_sales),
        )
        .join(OrderLine, OrderLine.order_id == SalesOrder.id)
        .join(Account, SalesOrder.account_id == Account.id)
        .group_by(SalesOrder.id, SalesOrder.ordered_at)
    )
    if start:
        base = base.where(SalesOrder.order_date >= start)
    if end:
        base = base.where(SalesOrder.order_date <= end)
    if channel:
        base = base.where(Account.channel_code == channel.value)

    cells: dict[tuple[int, int], dict[str, float]] = {}
    total = with_time = 0
    for _, ordered_at, units, gross in (await session.execute(base)).all():
        total += 1
        if ordered_at is None:
            continue
        with_time += 1
        if ordered_at.tzinfo is None:  # SQLite devuelve fechas sin zona: se guardaron en UTC
            ordered_at = ordered_at.replace(tzinfo=UTC)
        local = ordered_at.astimezone(tz)
        cell = cells.setdefault(
            (local.weekday(), local.hour), {"orders": 0, "units": 0, "gross_sales": 0.0}
        )
        cell["orders"] += 1
        cell["units"] += int(units or 0)
        cell["gross_sales"] += _f(gross)

    by_hour: dict[int, dict[str, float]] = {}
    for (_, hour), c in cells.items():
        h = by_hour.setdefault(hour, {"orders": 0, "units": 0, "gross_sales": 0.0})
        for k in h:
            h[k] += c[k]
    best = sorted(by_hour.items(), key=lambda kv: kv[1]["orders"], reverse=True)[:3]
    return {
        "timezone": tz_name,
        "coverage": {"with_time": with_time, "total": total},
        "cells": [
            {"weekday": wd, "hour": hr, **{k: round(v, 2) for k, v in c.items()}}
            for (wd, hr), c in sorted(cells.items())
        ],
        "best_hours": [{"hour": hr, **{k: round(v, 2) for k, v in c.items()}} for hr, c in best],
    }


# ---------------------------------------------------------------- devoluciones (RMA)
OPEN_RETURN_STATUSES = {
    "RETURN_OR_REFUND_REQUEST_PENDING",
    "AWAITING_BUYER_SHIP",
    "BUYER_SHIPPED_ITEM",
}


def _is_withdrawn(status: str | None) -> bool:
    """El comprador retiró la solicitud: no cuenta como devolución."""
    return bool(status) and status.endswith("_CANCEL")  # type: ignore[union-attr]


async def returns_report(
    session: AsyncSession,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
) -> dict:
    """Devoluciones y cancelaciones: motivos, productos con más devoluciones y tasa de devolución."""
    query = (
        select(ReturnCase, ReturnCaseLine)
        .join(Account, ReturnCase.account_id == Account.id)
        .outerjoin(ReturnCaseLine, ReturnCaseLine.case_id == ReturnCase.id)
    )
    if start:
        query = query.where(ReturnCase.case_date >= start)
    if end:
        query = query.where(ReturnCase.case_date <= end)
    if channel:
        query = query.where(Account.channel_code == channel.value)

    cases: dict[int, ReturnCase] = {}
    lines: list[tuple[ReturnCase, ReturnCaseLine]] = []
    for case, line in (await session.execute(query)).all():
        cases[case.id] = case
        if line is not None:
            lines.append((case, line))
    valid = [c for c in cases.values() if not _is_withdrawn(c.status)]
    returns = [c for c in valid if c.kind == "return"]
    cancels = [c for c in valid if c.kind == "cancellation"]

    sold_q = (
        select(Product.sku, func.sum(OrderLine.units))
        .join(OrderLine, OrderLine.product_id == Product.id)
        .join(Account, OrderLine.account_id == Account.id)
        .group_by(Product.sku)
    )
    if start:
        sold_q = sold_q.where(OrderLine.order_date >= start)
    if end:
        sold_q = sold_q.where(OrderLine.order_date <= end)
    if channel:
        sold_q = sold_q.where(Account.channel_code == channel.value)
    sold = {sku: int(units or 0) for sku, units in (await session.execute(sold_q)).all()}

    reasons: dict[tuple[str, str], dict] = {}
    for c in valid:
        r = reasons.setdefault(
            (c.kind, c.reason or "(sin motivo)"),
            {"kind": c.kind, "reason": c.reason or "(sin motivo)", "cases": 0, "refund_total": 0.0},
        )
        r["cases"] += 1
        r["refund_total"] += _f(c.refund_total)

    products: dict[str, dict] = {}
    for case, line in lines:
        if _is_withdrawn(case.status):
            continue
        p = products.setdefault(
            line.sku,
            {
                "sku": line.sku,
                "product_name": line.product_name,
                "returned_units": 0,
                "returned_refund": 0.0,
                "cancelled_units": 0,
            },
        )
        if case.kind == "return":
            p["returned_units"] += int(line.units)
            p["returned_refund"] += _f(line.refund_total)
        else:
            p["cancelled_units"] += int(line.units)
    product_rows = []
    for p in products.values():
        units_sold = sold.get(p["sku"], 0)
        product_rows.append(
            {
                **p,
                "units_sold": units_sold,
                "return_rate": _ratio(p["returned_units"], units_sold),
            }
        )
    product_rows.sort(key=lambda r: (r["returned_units"], r["cancelled_units"]), reverse=True)

    returned_units = sum(p["returned_units"] for p in product_rows)
    total_sold = sum(sold.values())
    seller_cancels = sum(1 for c in cancels if c.initiated_by == "seller")
    recent = sorted(cases.values(), key=lambda c: (c.case_date, c.external_id), reverse=True)[:30]
    return {
        "summary": {
            "returns": len(returns),
            "returns_open": sum(1 for c in returns if c.status in OPEN_RETURN_STATUSES),
            "return_refund": sum(_f(c.refund_total) for c in returns),
            "returned_units": returned_units,
            "units_sold": total_sold,
            "return_rate": _ratio(returned_units, total_sold),
            "cancellations": len(cancels),
            "cancel_refund": sum(_f(c.refund_total) for c in cancels),
            "seller_cancellations": seller_cancels,
        },
        "by_reason": sorted(reasons.values(), key=lambda r: r["cases"], reverse=True),
        "by_product": product_rows,
        "recent": [
            {
                "external_id": c.external_id,
                "kind": c.kind,
                "case_type": c.case_type,
                "status": c.status,
                "reason": c.reason,
                "initiated_by": c.initiated_by,
                "external_order_id": c.external_order_id,
                "case_date": c.case_date,
                "refund_total": _f(c.refund_total),
            }
            for c in recent
        ],
    }


# ---------------------------------------------------------------- logística
def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:  # SQLite devuelve fechas sin zona (UTC)
        return value.replace(tzinfo=UTC)
    return value


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


async def logistics_report(
    session: AsyncSession,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    now: datetime | None = None,
) -> dict:
    """Tiempos de envío: manejo (pago -> listo), tránsito (recolección -> entrega) y puntualidad."""
    now = now or datetime.now(UTC)
    query = select(Shipment, Account.channel_code).join(Account, Shipment.account_id == Account.id)
    if start:
        query = query.where(Shipment.order_date >= start)
    if end:
        query = query.where(Shipment.order_date <= end)
    if channel:
        query = query.where(Account.channel_code == channel.value)

    statuses: dict[str, int] = {}
    handling: list[float] = []
    transit: list[float] = []
    on_time = late = overdue_now = samples = cancelled = 0
    carriers: dict[str, dict[str, list[float] | int]] = {}
    total = 0
    for s, _ in (await session.execute(query)).all():
        total += 1
        statuses[s.status or "(sin estado)"] = statuses.get(s.status or "(sin estado)", 0) + 1
        samples += int(bool(s.is_sample))
        if s.status == "CANCELLED":
            cancelled += 1
            continue
        ordered, rts, collected = _aware(s.ordered_at), _aware(s.rts_at), _aware(s.collected_at)
        delivered, due = _aware(s.delivered_at), _aware(s.due_at)
        if ordered and rts:
            handling.append((rts - ordered).total_seconds() / 3600)
        if rts and due:
            if rts <= due:
                on_time += 1
            else:
                late += 1
        if s.status == "AWAITING_SHIPMENT" and due and due < now:
            overdue_now += 1
        c = carriers.setdefault(s.carrier or "(sin transportista)", {"shipments": 0, "days": []})
        c["shipments"] += 1  # type: ignore[operator]
        if collected and delivered and delivered >= collected:
            days = (delivered - collected).total_seconds() / 86400
            transit.append(days)
            c["days"].append(days)  # type: ignore[union-attr]

    return {
        "summary": {
            "shipments": total,
            "cancelled": cancelled,
            "sample_orders": samples,
            "handling_hours_avg": _avg(handling),
            "handling_hours_median": _median(handling),
            "transit_days_avg": _avg(transit),
            "transit_days_median": _median(transit),
            "shipped_on_time": on_time,
            "shipped_late": late,
            "on_time_rate": _ratio(on_time, on_time + late),
            "overdue_now": overdue_now,
        },
        "by_status": sorted(
            ({"status": k, "orders": v} for k, v in statuses.items()),
            key=lambda r: r["orders"],
            reverse=True,
        ),
        "by_carrier": sorted(
            (
                {
                    "carrier": name,
                    "shipments": int(c["shipments"]),  # type: ignore[arg-type]
                    "delivered": len(c["days"]),  # type: ignore[arg-type]
                    "transit_days_avg": _avg(c["days"]),  # type: ignore[arg-type]
                }
                for name, c in carriers.items()
            ),
            key=lambda r: r["shipments"],
            reverse=True,
        ),
    }


# ---------------------------------------------------------------- flujo de caja
async def cashflow_report(
    session: AsyncSession, start: date | None = None, end: date | None = None
) -> dict:
    """Liquidaciones cobradas y por cobrar. Una por estado de cuenta de la plataforma."""
    query = (
        select(Account.channel_code, Settlement)
        .join(Account, Settlement.account_id == Account.id)
        .order_by(Settlement.statement_date.desc())
    )
    if start:
        query = query.where(Settlement.statement_date >= start)
    if end:
        query = query.where(Settlement.statement_date <= end)

    rows = [
        {
            "channel": channel,
            "external_id": s.external_id,
            "statement_date": s.statement_date,
            "payout_date": s.payout_date,
            "status": s.status,
            "gross_amount": _f(s.gross_amount),
            "fees_amount": _f(s.fees_amount),
            "shipping_amount": _f(s.shipping_amount),
            "adjustment_amount": _f(s.adjustment_amount),
            "net_payout": _f(s.net_payout),
        }
        for channel, s in (await session.execute(query)).all()
    ]

    def total_of(*statuses: str | None) -> float:
        return sum(r["net_payout"] for r in rows if r["status"] in statuses)

    months: dict[str, dict[str, float]] = {}
    for r in rows:
        paid = r["status"] == "paid"
        when = r["payout_date"] if paid and r["payout_date"] else r["statement_date"]
        if when is None or r["status"] == "failed":
            continue
        m = months.setdefault(when.strftime("%Y-%m"), {"received": 0.0, "pending": 0.0})
        m["received" if paid else "pending"] += r["net_payout"]

    return {
        "summary": {
            "received": total_of("paid"),
            "pending": total_of("processing", None),
            "failed": total_of("failed"),
            "statements": len(rows),
        },
        "by_month": [{"month": k, **v} for k, v in sorted(months.items())],
        "rows": rows,
    }
