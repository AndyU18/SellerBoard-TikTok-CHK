"""Reportes que leen la capa plata directamente: promociones, dayparting y flujo de caja.

La capa oro solo guarda utilidad por día/producto; estos reportes necesitan datos que no están
ahí (subsidios, hora de la orden, liquidaciones), así que consultan sales.* y, cuando hace falta la
utilidad, la cruzan con la capa oro.
"""

from __future__ import annotations

from datetime import UTC, date
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gold import load_gold_frame
from app.models import Account, Channel, OrderLine, Product, SalesOrder, Settlement
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
