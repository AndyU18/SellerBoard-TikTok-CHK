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

from app.costs import CostHistory, load_cost_history
from app.gold import load_gold_frame
from app.models import (
    Account,
    AffiliateOrder,
    Channel,
    LiveSession,
    OrderLine,
    Product,
    ReturnCase,
    ReturnCaseLine,
    SalesOrder,
    SampleApplication,
    SampleLine,
    Settlement,
    Shipment,
    ShippingLabelCost,
    TrafficDaily,
    VideoPerformance,
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


# ---------------------------------------------------------------- muestras a creadores
NO_CREATOR = "(sin creador identificado)"


async def samples_report(
    session: AsyncSession, start: date | None = None, end: date | None = None
) -> dict:
    """ROI de las muestras: costo de lo enviado vs utilidad de las ventas del creador después.

    - Costo de la muestra = costo del producto + etiqueta (Redo) + envío de plataforma si la orden
      entró a ventas.
    - Utilidad del creador = utilidad neta de sus órdenes de afiliado desde su primera muestra. La
      comisión del afiliado ya viene dentro de las comisiones de esa utilidad (no se resta otra vez).
    - Las órdenes de afiliado que aún no se liquidan no tienen utilidad calculable: se cuentan aparte.
    """
    costs = await load_cost_history(session)
    product_ids = {sku: pid for sku, pid in (await session.execute(select(Product.sku, Product.id))).all()}

    # --- muestras enviadas
    line_q = select(SampleLine)
    if start:
        line_q = line_q.where(SampleLine.order_date >= start)
    if end:
        line_q = line_q.where(SampleLine.order_date <= end)
    sample_lines = (await session.scalars(line_q)).all()
    order_ids = sorted({line.external_order_id for line in sample_lines})

    creator_of: dict[str, str] = {}
    for order_id, username in (
        await session.execute(
            select(SampleApplication.external_order_id, SampleApplication.creator_username).where(
                SampleApplication.external_order_id.is_not(None)
            )
        )
    ).all():
        creator_of[order_id] = username or NO_CREATOR

    labels = await _label_costs(session, order_ids)
    in_sales = await _order_lines_by_external(session, order_ids)

    orders: dict[str, dict] = {}
    for line in sample_lines:
        unit_cost = costs.unit_cost(product_ids[line.sku], line.order_date) if line.sku in product_ids else 0.0
        o = orders.setdefault(
            line.external_order_id,
            {
                "creator": creator_of.get(line.external_order_id, NO_CREATOR),
                "date": line.order_date,
                "units": 0,
                "cogs": 0.0,
            },
        )
        o["units"] += int(line.units)
        o["cogs"] += float(line.units) * float(unit_cost)
    for oid, o in orders.items():
        o["shipping"] = labels.get(oid, 0.0) + sum(_f(r.shipping_cost) for r in in_sales.get(oid, []))
        o["cost"] = o["cogs"] + o["shipping"]
        o["in_profit"] = oid in in_sales

    # --- ventas del creador (afiliados)
    aff_q = select(AffiliateOrder)
    if start:
        aff_q = aff_q.where(AffiliateOrder.order_date >= start)
    if end:
        aff_q = aff_q.where(AffiliateOrder.order_date <= end)
    affiliate = (await session.scalars(aff_q)).all()
    aff_orders = sorted({a.external_order_id for a in affiliate})
    profits = await _profit_by_order(session, aff_orders, costs)

    def order_profit(oid: str) -> float | None:
        return profits.get(oid)

    creators: dict[str, dict] = {}
    for oid, o in orders.items():
        c = creators.setdefault(o["creator"], _blank_creator(o["creator"]))
        c["sample_orders"] += 1
        c["sample_units"] += o["units"]
        c["sample_cost"] += o["cost"]
        c["first_sample"] = min(c["first_sample"], o["date"]) if c["first_sample"] else o["date"]
        if not o["in_profit"]:
            c["cost_not_in_profit"] += o["cost"]

    seen: dict[str, set[str]] = {}
    for a in affiliate:
        c = creators.get(a.creator_username or "")
        if c is None or c["first_sample"] is None or a.order_date < c["first_sample"]:
            continue
        c["affiliate_units"] += int(a.units)
        c["affiliate_gmv"] += _f(a.commission_base)
        c["commission"] += _f(a.commission)
        orders_seen = seen.setdefault(c["creator"], set())
        if a.external_order_id not in orders_seen:
            orders_seen.add(a.external_order_id)
            profit = order_profit(a.external_order_id)
            if profit is None:
                c["orders_pending"] += 1
            else:
                c["affiliate_profit"] += profit
    for name, c in creators.items():
        c["affiliate_orders"] = len(seen.get(name, set()))
        c["net"] = c["affiliate_profit"] - c["sample_cost"]
        c["roi"] = _ratio(c["net"], c["sample_cost"])
        c["first_sample"] = c["first_sample"].isoformat() if c["first_sample"] else None

    rows = sorted(creators.values(), key=lambda c: c["net"], reverse=True)
    total_cost = sum(c["sample_cost"] for c in rows)
    total_profit = sum(c["affiliate_profit"] for c in rows)
    with_sales = sum(1 for c in rows if c["affiliate_orders"] > 0)

    statuses: dict[str, int] = {}
    for (status,) in (await session.execute(select(SampleApplication.status))).all():
        statuses[status or "(sin estado)"] = statuses.get(status or "(sin estado)", 0) + 1

    return {
        "summary": {
            "sample_orders": len(orders),
            "sample_units": sum(o["units"] for o in orders.values()),
            "creators": len(rows),
            "creators_with_sales": with_sales,
            "creators_without_sales": len(rows) - with_sales,
            "sample_cost": total_cost,
            "cost_not_in_profit": sum(c["cost_not_in_profit"] for c in rows),
            "affiliate_profit": total_profit,
            "net": total_profit - total_cost,
            "roi": _ratio(total_profit - total_cost, total_cost),
            "orders_pending": sum(c["orders_pending"] for c in rows),
        },
        "creators": rows,
        "applications_by_status": sorted(
            ({"status": k, "applications": v} for k, v in statuses.items()),
            key=lambda r: r["applications"],
            reverse=True,
        ),
    }


async def _profit_by_order(
    session: AsyncSession, order_ids: list[str], costs: CostHistory
) -> dict[str, float]:
    """Utilidad neta de cada orden que ya está en ventas (las que no, no aparecen en el resultado).

    Misma fórmula que la capa oro: ventas - promo - comisiones - envío - reembolsos - costo, y la
    etiqueta de envío (Redo) de la orden.
    """
    lines = await _order_lines_by_external(session, order_ids)
    labels = await _label_costs(session, list(lines))
    out: dict[str, float] = {}
    for oid, rows in lines.items():
        total = 0.0
        for r in rows:
            cogs = float(r.units) * float(costs.unit_cost(r.product_id, r.order_date))
            total += (
                _f(r.gross_sales) - _f(r.promo) - _f(r.platform_fees) - _f(r.shipping_cost)
                - _f(r.refund_amount) - cogs
            )
        out[oid] = total - labels.get(oid, 0.0)
    return out


def _blank_creator(name: str) -> dict:
    return {
        "creator": name,
        "sample_orders": 0,
        "sample_units": 0,
        "sample_cost": 0.0,
        "cost_not_in_profit": 0.0,
        "first_sample": None,
        "affiliate_orders": 0,
        "affiliate_units": 0,
        "affiliate_gmv": 0.0,
        "commission": 0.0,
        "affiliate_profit": 0.0,
        "orders_pending": 0,
    }


async def _label_costs(session: AsyncSession, order_ids: list[str]) -> dict[str, float]:
    """Costo de etiquetas por orden (todas las fuentes, p. ej. Redo)."""
    out: dict[str, float] = {}
    for i in range(0, len(order_ids), 500):
        part = order_ids[i : i + 500]
        for oid, amount in (
            await session.execute(
                select(ShippingLabelCost.external_order_id, func.sum(ShippingLabelCost.amount))
                .where(ShippingLabelCost.external_order_id.in_(part))
                .group_by(ShippingLabelCost.external_order_id)
            )
        ).all():
            out[oid] = _f(amount)
    return out


async def _order_lines_by_external(
    session: AsyncSession, order_ids: list[str]
) -> dict[str, list[OrderLine]]:
    """Líneas de venta (capa plata) de las órdenes dadas, por id externo de la orden."""
    out: dict[str, list[OrderLine]] = {}
    for i in range(0, len(order_ids), 500):
        part = order_ids[i : i + 500]
        for external_id, line in (
            await session.execute(
                select(SalesOrder.external_order_id, OrderLine)
                .join(OrderLine, OrderLine.order_id == SalesOrder.id)
                .where(SalesOrder.external_order_id.in_(part))
            )
        ).all():
            out.setdefault(external_id, []).append(line)
    return out


# ---------------------------------------------------------------- embudo de tráfico
FUNNEL_SOURCES = ("VIDEO", "LIVE", "PRODUCT_CARD")


async def funnel_report(
    session: AsyncSession, start: date | None = None, end: date | None = None
) -> dict:
    """Embudo: impresiones -> vistas de producto -> compradores/órdenes, por origen y por día.

    Las vistas únicas no se pueden sumar entre días: "visitors" es la suma de los visitantes
    promedio diarios (visitante-días), útil para comparar, no como personas únicas.
    """
    query = select(TrafficDaily)
    if start:
        query = query.where(TrafficDaily.day >= start)
    if end:
        query = query.where(TrafficDaily.day <= end)
    rows = (await session.scalars(query.order_by(TrafficDaily.day))).all()

    def blank() -> dict:
        return {"impressions": 0, "page_views": 0, "visitors": 0, "buyers": 0, "orders": 0, "units": 0, "gmv": 0.0}

    totals = {"ALL": blank(), **{s: blank() for s in FUNNEL_SOURCES}}
    daily: dict[date, dict] = {}
    for r in rows:
        t = totals.get(r.source)
        if t is None:
            continue
        for k in ("impressions", "page_views", "visitors", "buyers", "orders", "units"):
            t[k] += int(getattr(r, k) or 0)
        t["gmv"] += _f(r.gmv)
        if r.source == "ALL":
            daily[r.day] = {
                "day": r.day,
                "impressions": int(r.impressions or 0),
                "page_views": int(r.page_views or 0),
                "orders": int(r.orders or 0),
                "buyers": int(r.buyers or 0),
                "gmv": _f(r.gmv),
            }

    def rates(t: dict) -> dict:
        return {
            **t,
            "ctr": _ratio(t["page_views"], t["impressions"]),
            "conversion": _ratio(t["buyers"], t["page_views"]),
        }

    all_t = totals["ALL"]
    series = []
    for d in daily.values():
        series.append({**d, "ctr": _ratio(d["page_views"], d["impressions"]), "conversion": _ratio(d["buyers"], d["page_views"])})
    gmv_sources = sum(totals[s]["gmv"] for s in FUNNEL_SOURCES)
    return {
        "days": len(daily),
        "totals": {**rates(all_t), "aov": _ratio(all_t["gmv"], all_t["orders"])},
        "by_source": [
            {"source": s, **rates(totals[s]), "gmv_share": _ratio(totals[s]["gmv"], gmv_sources)}
            for s in FUNNEL_SOURCES
        ],
        "daily": series,
    }


# ---------------------------------------------------------------- LIVE
async def live_report(
    session: AsyncSession, start: date | None = None, end: date | None = None
) -> dict:
    """Sesiones LIVE: ventas, duración, ventas por hora y los creadores que más venden."""
    sessions = []
    for s in (await session.scalars(select(LiveSession))).all():
        started = _aware(s.start_at)
        if started is None:
            continue
        day = started.date()
        if (start and day < start) or (end and day > end):
            continue
        ended = _aware(s.end_at)
        minutes = (ended - started).total_seconds() / 60 if ended and ended > started else None
        sessions.append(
            {
                "external_id": s.external_id,
                "username": s.username,
                "title": s.title,
                "start_at": started.isoformat(),
                "duration_min": minutes,
                "gmv": _f(s.gmv),
                "orders": int(s.orders),
                "customers": int(s.customers),
                "units": int(s.units),
                "products_added": int(s.products_added),
                "click_to_order_rate": _f(s.click_to_order_rate) if s.click_to_order_rate is not None else None,
                "gmv_per_hour": _ratio(_f(s.gmv), minutes / 60) if minutes else None,
            }
        )
    with_sales = [s for s in sessions if s["gmv"] > 0]
    total_gmv = sum(s["gmv"] for s in sessions)
    durations = [s["duration_min"] for s in sessions if s["duration_min"]]
    hours = sum(durations) / 60

    creators: dict[str, dict] = {}
    for s in sessions:
        c = creators.setdefault(
            s["username"] or "(sin usuario)",
            {"username": s["username"] or "(sin usuario)", "sessions": 0, "gmv": 0.0, "orders": 0},
        )
        c["sessions"] += 1
        c["gmv"] += s["gmv"]
        c["orders"] += s["orders"]
    ranked = sorted(sessions, key=lambda s: s["gmv"], reverse=True)
    return {
        "summary": {
            "sessions": len(sessions),
            "sessions_with_sales": len(with_sales),
            "gmv": total_gmv,
            "orders": sum(s["orders"] for s in sessions),
            "customers": sum(s["customers"] for s in sessions),
            "units": sum(s["units"] for s in sessions),
            "avg_gmv_per_session": _ratio(total_gmv, len(sessions)),
            "avg_duration_min": sum(durations) / len(durations) if durations else None,
            "gmv_per_hour": _ratio(total_gmv, hours),
            "top5_share": _ratio(sum(s["gmv"] for s in ranked[:5]), total_gmv),
        },
        "top_sessions": ranked[:30],
        "by_creator": sorted(creators.values(), key=lambda c: c["gmv"], reverse=True)[:15],
    }


# ---------------------------------------------------------------- creativos (videos virales)
CREATIVES_LIMIT = 200


async def creatives_report(
    session: AsyncSession, start: date | None = None, end: date | None = None
) -> dict:
    """Ventas y utilidad por contenido de creador (video, LIVE, enlace, tienda).

    La utilidad de una orden se reparte entre los contenidos que la originaron según su base de
    comisión. La comisión de afiliado ya va dentro de las comisiones de esa utilidad.
    Las órdenes aún sin liquidar no tienen utilidad calculable y se cuentan aparte.
    """
    costs = await load_cost_history(session)
    query = select(AffiliateOrder)
    if start:
        query = query.where(AffiliateOrder.order_date >= start)
    if end:
        query = query.where(AffiliateOrder.order_date <= end)
    rows = (await session.scalars(query)).all()
    profits = await _profit_by_order(session, sorted({r.external_order_id for r in rows}), costs)
    sampled = {
        u
        for (u,) in (
            await session.execute(
                select(SampleApplication.creator_username).where(
                    SampleApplication.external_order_id.is_not(None)
                )
            )
        ).all()
        if u
    }

    base_by_order: dict[str, float] = {}
    lines_by_order: dict[str, int] = {}
    for r in rows:
        base_by_order[r.external_order_id] = base_by_order.get(r.external_order_id, 0.0) + _f(r.commission_base)
        lines_by_order[r.external_order_id] = lines_by_order.get(r.external_order_id, 0) + 1

    contents: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r.content_type or "(sin tipo)", r.content_id or "(sin contenido)")
        c = contents.setdefault(
            key,
            {
                "content_type": key[0],
                "content_id": key[1],
                "creator": r.creator_username,
                "orders": set(),
                "pending": set(),
                "units": 0,
                "gmv": 0.0,
                "commission": 0.0,
                "profit": 0.0,
                "first_order": r.order_date,
                "last_order": r.order_date,
                "skus": {},
            },
        )
        oid = r.external_order_id
        c["orders"].add(oid)
        c["units"] += int(r.units)
        c["gmv"] += _f(r.commission_base)
        c["commission"] += _f(r.commission)
        c["first_order"] = min(c["first_order"], r.order_date)
        c["last_order"] = max(c["last_order"], r.order_date)
        if r.sku:
            c["skus"][r.sku] = c["skus"].get(r.sku, 0) + int(r.units)
        if oid in profits:
            total_base = base_by_order[oid]
            share = _f(r.commission_base) / total_base if total_base > 0 else 1 / lines_by_order[oid]
            c["profit"] += profits[oid] * share
        else:
            c["pending"].add(oid)

    perf = {v.video_id: v for v in (await session.scalars(select(VideoPerformance))).all()}
    result = []
    for c in contents.values():
        is_video = c["content_type"] == "VIDEO" and c["creator"] and c["content_id"].isdigit()
        views = perf.get(c["content_id"])
        result.append(
            {
                "views": views.views if views else None,
                "ctr": _f(views.click_through_rate) if views and views.click_through_rate is not None else None,
                "content_type": c["content_type"],
                "content_id": c["content_id"],
                "creator": c["creator"],
                "url": f"https://www.tiktok.com/@{c['creator']}/video/{c['content_id']}" if is_video else None,
                "orders": len(c["orders"]),
                "orders_pending": len(c["pending"]),
                "units": c["units"],
                "gmv": c["gmv"],
                "commission": c["commission"],
                "profit": c["profit"],
                "margin": _ratio(c["profit"], c["gmv"]),
                "first_order": c["first_order"],
                "last_order": c["last_order"],
                "top_sku": max(c["skus"], key=c["skus"].get) if c["skus"] else None,  # type: ignore[arg-type]
                "sampled": c["creator"] in sampled,
            }
        )
    result.sort(key=lambda r: r["gmv"], reverse=True)

    by_type: dict[str, dict] = {}
    for r in result:
        t = by_type.setdefault(
            r["content_type"],
            {"content_type": r["content_type"], "contents": 0, "orders": 0, "gmv": 0.0, "profit": 0.0},
        )
        t["contents"] += 1
        t["orders"] += r["orders"]
        t["gmv"] += r["gmv"]
        t["profit"] += r["profit"]

    total_gmv = sum(r["gmv"] for r in result)
    return {
        "summary": {
            "contents": len(result),
            "creators": len({r["creator"] for r in result}),
            "orders": len({r.external_order_id for r in rows}),
            "gmv": total_gmv,
            "commission": sum(r["commission"] for r in result),
            "profit": sum(r["profit"] for r in result),
            "top10_share": _ratio(sum(r["gmv"] for r in result[:10]), total_gmv),
            "losing_contents": sum(1 for r in result if r["profit"] < 0),
            "orders_pending": len({r.external_order_id for r in rows if r.external_order_id not in profits}),
        },
        "by_type": sorted(by_type.values(), key=lambda t: t["gmv"], reverse=True),
        "contents": result[:CREATIVES_LIMIT],
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
