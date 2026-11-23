"""Costo real de las devoluciones: lo que pierdes aunque el reembolso devuelva el dinero.

Cuando un cliente devuelve, el reembolso deshace el ingreso, pero tú ya pagaste la etiqueta de ida y,
si el producto no se puede revender, también pierdes su costo. Por devolución:

    envío perdido    = costo de la etiqueta de la orden x (unidades devueltas / unidades de la orden)
    producto perdido = unidades devueltas x costo unitario x tasa de pérdida según el motivo
    costo real       = envío perdido + producto perdido

La tasa de pérdida depende del motivo: dañado, defectuoso o no como se describió = 100 %; arrepentimiento
del cliente o paquete devuelto sin entregar = 0 %; cualquier otro motivo usa `default_loss_rate`.
No incluye la etiqueta de regreso (no está en los datos) ni el tiempo de manejo.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import load_cost_history
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

DEFAULT_LOSS_RATE = 0.5
CHUNK = 500
# Palabras del motivo -> (tasa de pérdida, ¿culpa del vendedor?). Gana la primera que coincida.
REASON_RULES: tuple[tuple[tuple[str, ...], float, bool], ...] = (
    (("DAMAGE", "BROKEN", "CRACK"), 1.0, True),
    (("DEFECT", "NOT_WORKING", "FAULTY", "MALFUNCTION"), 1.0, True),
    (("NOT_AS", "DESCRIB", "DIFFERENT_FROM", "MISSING", "POOR_QUALITY"), 1.0, True),
    (("INCORRECT_ITEM", "WRONG_ITEM", "WRONG_PRODUCT"), 0.0, True),  # error de envío: vuelve y se revende
    (("NO_LONGER_WANTED", "CHANGED_MIND", "UNWANTED", "ORDERED_WRONG", "BUYER_REMORSE", "NO_REASON"), 0.0, False),
    (("RETURN_TO_SENDER", "UNDELIVER", "REFUSED"), 0.0, False),
)


def reason_policy(reason: str | None, default_loss_rate: float) -> tuple[float, bool, bool]:
    """(tasa de pérdida, culpa del vendedor, ¿el motivo es conocido?)."""
    text = (reason or "").upper().replace(" ", "_")
    for words, rate, seller_fault in REASON_RULES:
        if any(w in text for w in words):
            return rate, seller_fault, True
    return default_loss_rate, False, False


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _chunks(items: list[str]) -> list[list[str]]:
    return [items[i : i + CHUNK] for i in range(0, len(items), CHUNK)]


async def _order_economics(session: AsyncSession, order_ids: list[str]) -> tuple[dict[str, float], dict[str, int]]:
    """Costo de etiqueta y unidades por id de orden externo."""
    labels: dict[str, float] = defaultdict(float)
    units: dict[str, int] = defaultdict(int)
    for part in _chunks(order_ids):
        for oid, amount in (
            await session.execute(
                select(ShippingLabelCost.external_order_id, ShippingLabelCost.amount).where(
                    ShippingLabelCost.external_order_id.in_(part)
                )
            )
        ).all():
            labels[oid] += _f(amount)
        for oid, n in (
            await session.execute(
                select(SalesOrder.external_order_id, func.sum(OrderLine.units))
                .join(OrderLine, OrderLine.order_id == SalesOrder.id)
                .where(SalesOrder.external_order_id.in_(part))
                .group_by(SalesOrder.external_order_id)
            )
        ).all():
            units[oid] += int(n or 0)
    return labels, units


async def returns_cost_report(
    session: AsyncSession,
    *,
    days: int = 90,
    default_loss_rate: float = DEFAULT_LOSS_RATE,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)

    query = (
        select(ReturnCase, ReturnCaseLine, Account.channel_code)
        .join(Account, ReturnCase.account_id == Account.id)
        .join(ReturnCaseLine, ReturnCaseLine.case_id == ReturnCase.id)
        .where(ReturnCase.kind == "return", ReturnCase.case_date >= start, ReturnCase.case_date <= today)
    )
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)
    pairs = [
        (case, line, ch)
        for case, line, ch in (await session.execute(query)).all()
        if not (case.status or "").endswith("_CANCEL")  # el comprador retiró la solicitud
    ]

    labels, order_units = await _order_economics(session, sorted({c.external_order_id for c, _, _ in pairs}))
    costs = await load_cost_history(session)
    product_ids = {sku: pid for pid, sku in (await session.execute(select(Product.id, Product.sku))).all()}

    sold_q = (
        select(Product.sku, func.sum(OrderLine.units))
        .join(OrderLine, OrderLine.product_id == Product.id)
        .join(Account, OrderLine.account_id == Account.id)
        .where(OrderLine.order_date >= start, OrderLine.order_date <= today)
        .group_by(Product.sku)
    )
    if channel:
        sold_q = sold_q.where(Account.channel_code == channel.value)
    if account:
        sold_q = sold_q.where(Account.name == account)
    sold = {sku: int(n or 0) for sku, n in (await session.execute(sold_q)).all()}

    skus: dict[str, dict[str, Any]] = {}
    reasons: dict[str, dict[str, Any]] = {}
    cases_seen: set[int] = set()
    for case, line, _ch in pairs:
        loss_rate, seller_fault, known = reason_policy(case.reason, default_loss_rate)
        order_total = order_units.get(case.external_order_id, 0)
        share = min(1.0, line.units / order_total) if order_total else 0.0
        shipping_lost = labels.get(case.external_order_id, 0.0) * share
        pid = product_ids.get(line.sku)
        unit_cost = float(costs.unit_cost(pid, case.case_date)) if pid is not None else 0.0
        product_lost = line.units * unit_cost * loss_rate
        refund = _f(line.refund_total)

        s = skus.setdefault(
            line.sku,
            {
                "sku": line.sku,
                "product_name": line.product_name,
                "returned_units": 0,
                "refund": 0.0,
                "shipping_lost": 0.0,
                "product_lost": 0.0,
                "seller_fault_units": 0,
                "has_cost": unit_cost > 0,
            },
        )
        s["returned_units"] += line.units
        s["refund"] += refund
        s["shipping_lost"] += shipping_lost
        s["product_lost"] += product_lost
        s["seller_fault_units"] += line.units if seller_fault else 0
        s["has_cost"] = s["has_cost"] and unit_cost > 0

        label = case.reason or "(sin motivo)"
        r = reasons.setdefault(
            label,
            {
                "reason": label,
                "cases": 0,
                "units": 0,
                "refund": 0.0,
                "shipping_lost": 0.0,
                "product_lost": 0.0,
                "loss_rate": loss_rate,
                "seller_fault": seller_fault,
                "known_reason": known,
                "_cases": set(),
            },
        )
        r["units"] += line.units
        r["refund"] += refund
        r["shipping_lost"] += shipping_lost
        r["product_lost"] += product_lost
        r["_cases"].add(case.id)
        cases_seen.add(case.id)

    sku_rows = []
    for s in skus.values():
        cost = s["shipping_lost"] + s["product_lost"]
        units_sold = sold.get(s["sku"], 0)
        sku_rows.append(
            {
                **s,
                "real_cost": cost,
                "units_sold": units_sold,
                "return_rate": s["returned_units"] / units_sold if units_sold else None,
                "cost_per_return": cost / s["returned_units"] if s["returned_units"] else 0.0,
            }
        )
    sku_rows.sort(key=lambda r: -r["real_cost"])
    reason_rows = []
    for r in reasons.values():
        r["cases"] = len(r.pop("_cases"))
        reason_rows.append({**r, "real_cost": r["shipping_lost"] + r["product_lost"]})
    reason_rows.sort(key=lambda r: -r["real_cost"])

    total_ship = sum(r["shipping_lost"] for r in sku_rows)
    total_prod = sum(r["product_lost"] for r in sku_rows)
    return {
        "days": days,
        "default_loss_rate": default_loss_rate,
        "summary": {
            "cases": len(cases_seen),
            "units": sum(r["returned_units"] for r in sku_rows),
            "refund": sum(r["refund"] for r in sku_rows),
            "shipping_lost": total_ship,
            "product_lost": total_prod,
            "real_cost": total_ship + total_prod,
            "seller_fault_units": sum(r["seller_fault_units"] for r in sku_rows),
            "without_cost": sum(1 for r in sku_rows if not r["has_cost"]),
        },
        "by_sku": sku_rows,
        "by_reason": reason_rows,
    }
