"""Conciliación y auditoría de fugas: dinero que sale o no llega sin explicación.

Tres revisiones:
  * **Etiquetas** (Redo): cada cargo se clasifica según la orden a la que pertenece: orden de venta,
    muestra a un creador (su costo ya está en Muestras), envío sin orden de venta o sin ninguna
    coincidencia. Estos últimos son los que hay que investigar.
  * **Comisiones**: líneas con comisión cero y líneas con una tasa muy por encima de la típica de su
    canal, y el cambio de la tasa en las últimas semanas contra las anteriores.
  * **Liquidaciones**: pagos fallidos y estados de cuenta que llevan demasiado sin depositarse.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    OrderLine,
    Product,
    SalesOrder,
    Settlement,
    Shipment,
    ShippingLabelCost,
)

STUCK_PAYMENT_DAYS = 14  # un estado de cuenta sin depositar tras estos días es sospechoso
HIGH_FEE_FACTOR = 2.0  # tasa de comisión vs. la mediana del canal
HIGH_FEE_MIN_EXCESS = 5.0  # dólares de comisión por encima de lo típico para que valga la pena avisar
DRIFT_ALERT = 0.02  # cambio de la tasa de comisión (2 puntos) que se reporta
RECENT_WEEKS = 4
BASELINE_WEEKS = 8
MAX_ROWS = 50


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


async def label_audit(session: AsyncSession) -> dict[str, Any]:
    orders = {x for (x,) in (await session.execute(select(SalesOrder.external_order_id))).all()}
    shipments = {
        oid: bool(is_sample)
        for oid, is_sample in (await session.execute(select(Shipment.external_order_id, Shipment.is_sample))).all()
    }

    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"charges": 0, "orders": 0, "amount": 0.0})
    months: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    unmatched: list[dict[str, Any]] = []
    for row in (await session.scalars(select(ShippingLabelCost))).all():
        oid = row.external_order_id
        if oid in orders:
            kind = "order"
        elif oid in shipments:
            kind = "sample" if shipments[oid] else "shipment_without_order"
        else:
            kind = "unmatched"
        amount = _f(row.amount)
        b = buckets[kind]
        b["charges"] += int(row.charges or 1)
        b["orders"] += 1
        b["amount"] += amount
        when = row.last_charged or row.first_charged
        if when:
            months[when.strftime("%Y-%m")][kind] += amount
        if kind in ("unmatched", "shipment_without_order"):
            unmatched.append(
                {
                    "order_id": oid,
                    "kind": kind,
                    "amount": amount,
                    "charged": when.isoformat() if when else None,
                    "source": row.source,
                }
            )
    unmatched.sort(key=lambda r: -r["amount"])
    kinds = ("order", "sample", "shipment_without_order", "unmatched")
    return {
        "summary": {k: {**buckets[k]} for k in kinds},
        "by_month": [{"month": m, **{k: v.get(k, 0.0) for k in kinds}} for m, v in sorted(months.items())],
        "unmatched_total": sum(r["amount"] for r in unmatched),
        "unmatched_count": len(unmatched),
        "unmatched": unmatched[:MAX_ROWS],
    }


async def fee_audit(session: AsyncSession, today: date) -> dict[str, Any]:
    query = (
        select(
            Account.channel_code,
            Product.sku,
            SalesOrder.external_order_id,
            OrderLine.order_date,
            OrderLine.gross_sales,
            OrderLine.platform_fees,
        )
        .join(Account, OrderLine.account_id == Account.id)
        .join(SalesOrder, OrderLine.order_id == SalesOrder.id)
        .join(Product, OrderLine.product_id == Product.id)
        .where(OrderLine.order_date >= today - timedelta(weeks=RECENT_WEEKS + BASELINE_WEEKS))
    )
    lines = [
        {"channel": ch, "sku": sku, "order_id": oid, "date": day, "gross": _f(gross), "fees": _f(fees)}
        for ch, sku, oid, day, gross, fees in (await session.execute(query)).all()
        if _f(gross) > 0
    ]
    by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        by_channel[line["channel"]].append(line)

    zero_fee: list[dict[str, Any]] = []
    high_fee: list[dict[str, Any]] = []
    channels: list[dict[str, Any]] = []
    recent_start = today - timedelta(weeks=RECENT_WEEKS)

    def rate_of(group: list[dict[str, Any]]) -> float | None:
        gross = sum(i["gross"] for i in group)
        return sum(i["fees"] for i in group) / gross if gross > 0 else None

    for ch, items in sorted(by_channel.items()):
        rates = [i["fees"] / i["gross"] for i in items if i["fees"] > 0]
        median = statistics.median(rates) if rates else 0.0
        for i in items:
            rate = i["fees"] / i["gross"]
            if i["fees"] <= 0:
                zero_fee.append({**i, "date": i["date"].isoformat(), "rate": 0.0})
            elif (
                median > 0
                and rate > median * HIGH_FEE_FACTOR
                and i["fees"] - median * i["gross"] >= HIGH_FEE_MIN_EXCESS
            ):
                high_fee.append({**i, "date": i["date"].isoformat(), "rate": rate, "typical": median})
        r_recent = rate_of([i for i in items if i["date"] > recent_start])
        r_base = rate_of([i for i in items if i["date"] <= recent_start])
        drift = (r_recent - r_base) if (r_recent is not None and r_base is not None) else None
        channels.append(
            {
                "channel": ch,
                "lines": len(items),
                "median_rate": median,
                "recent_rate": r_recent,
                "baseline_rate": r_base,
                "drift": drift,
                "drift_alert": drift is not None and abs(drift) >= DRIFT_ALERT,
            }
        )
    high_fee.sort(key=lambda r: -(r["fees"] - r["typical"] * r["gross"]))
    return {
        "channels": channels,
        "zero_fee_count": len(zero_fee),
        "high_fee_count": len(high_fee),
        "zero_fee": sorted(zero_fee, key=lambda r: -r["gross"])[:MAX_ROWS],
        "high_fee": high_fee[:MAX_ROWS],
    }


async def settlement_audit(session: AsyncSession, today: date) -> dict[str, Any]:
    rows = (
        await session.execute(select(Account.channel_code, Settlement).join(Account, Settlement.account_id == Account.id))
    ).all()
    failed = [(c, s) for c, s in rows if s.status == "failed"]
    stuck = [
        (c, s)
        for c, s in rows
        if s.status not in ("paid", "failed")
        and s.statement_date
        and (today - s.statement_date).days > STUCK_PAYMENT_DAYS
    ]

    def item(c: str, s: Settlement) -> dict[str, Any]:
        return {
            "channel": c,
            "external_id": s.external_id,
            "statement_date": s.statement_date.isoformat() if s.statement_date else None,
            "status": s.status,
            "net_payout": _f(s.net_payout),
        }

    return {
        "failed_count": len(failed),
        "failed_total": sum(_f(s.net_payout) for _, s in failed),
        "stuck_count": len(stuck),
        "stuck_total": sum(_f(s.net_payout) for _, s in stuck),
        "stuck_days": STUCK_PAYMENT_DAYS,
        "failed": [item(c, s) for c, s in failed[:MAX_ROWS]],
        "stuck": [item(c, s) for c, s in sorted(stuck, key=lambda x: x[1].statement_date or today)[:MAX_ROWS]],
    }


async def reconciliation_report(session: AsyncSession, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    labels = await label_audit(session)
    fees = await fee_audit(session, today)
    settlements = await settlement_audit(session, today)
    return {
        "summary": {
            "unassigned_labels_total": labels["unmatched_total"],
            "unassigned_labels_count": labels["unmatched_count"],
            "zero_fee_lines": fees["zero_fee_count"],
            "high_fee_lines": fees["high_fee_count"],
            "fee_drift_channels": sum(1 for c in fees["channels"] if c["drift_alert"]),
            "failed_payments": settlements["failed_total"],
            "stuck_payments": settlements["stuck_total"],
        },
        "labels": labels,
        "fees": fees,
        "settlements": settlements,
    }
