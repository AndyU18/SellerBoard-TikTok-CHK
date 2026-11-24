"""Desglose de las liquidaciones de TikTok Shop: qué concepto se lleva el dinero y cuál crece.

Lee sales.settlement + sales.settlement_fee. Los costos se devuelven en positivo (`cost`); los
conceptos informativos (impuesto de venta, descuento de plataforma) no entran en los totales
porque no cambian el depósito.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Settlement, SettlementFee
from app.settlement_fees import CONCEPTS

CHANNEL = "tiktok_shop"
DEDUCTIONS = ("refunds", "seller_discount")  # restan ventas pero no son comisión


def _r(value: float, digits: int = 2) -> float:
    return round(value, digits)


def _pct(part: float, whole: float) -> float | None:
    return _r(part / whole * 100) if whole else None


def _change(now: float, before: float) -> float | None:
    return _r((now - before) / abs(before) * 100) if before else None


async def _rows(
    session: AsyncSession, start: date, end: date, account: str | None
) -> list[tuple[Settlement, str, str, float]]:
    stmt = (
        select(Settlement, SettlementFee.concept, SettlementFee.kind, SettlementFee.amount)
        .join(SettlementFee, SettlementFee.settlement_id == Settlement.id)
        .join(Account, Account.id == Settlement.account_id)
        .where(
            Account.channel_code == CHANNEL,
            Settlement.statement_date >= start,
            Settlement.statement_date <= end,
        )
    )
    if account:
        stmt = stmt.where(Account.name == account)
    return [(s, c, k, float(a)) for s, c, k, a in (await session.execute(stmt)).all()]


def _totals(rows: list[tuple[Settlement, str, str, float]]) -> dict[str, float]:
    """Monto firmado por concepto."""
    out: dict[str, float] = defaultdict(float)
    for _s, concept, _k, amount in rows:
        out[concept] += amount
    return out


def _summary(totals: dict[str, float]) -> dict[str, float]:
    sales = totals.get("gross_sales", 0.0)
    fees = -sum(v for c, v in totals.items() if CONCEPTS[c][1] == "fee")
    return {
        "sales": sales,
        "fees": fees,
        "shipping": -totals.get("shipping", 0.0),
        "fee_pct": (fees / sales * 100) if sales else 0.0,
    }


async def settlement_breakdown_report(
    session: AsyncSession, *, start: date, end: date, account: str | None = None
) -> dict[str, Any]:
    """Por concepto, por mes, por liquidación y resumen, con variación contra el período anterior."""
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    rows = await _rows(session, start, end, account)
    prev_rows = await _rows(session, prev_start, prev_end, account)
    totals, prev_totals = _totals(rows), _totals(prev_rows)
    now, before = _summary(totals), _summary(prev_totals)
    sales, prev_sales = now["sales"], before["sales"]

    by_concept = []
    for code, (name, kind) in CONCEPTS.items():
        if code == "gross_sales" or (code not in totals and code not in prev_totals):
            continue
        cost, prev_cost = -totals.get(code, 0.0), -prev_totals.get(code, 0.0)
        by_concept.append(
            {
                "code": code,
                "name": name,
                "kind": kind,
                "amount": _r(totals.get(code, 0.0)),
                "cost": _r(cost),
                "pct_of_sales": _pct(cost, sales),
                "previous_cost": _r(prev_cost),
                "previous_pct_of_sales": _pct(prev_cost, prev_sales),
                "change_pct": _change(cost, prev_cost),
                "change_amount": _r(cost - prev_cost),
            }
        )
    by_concept.sort(key=lambda c: (c["kind"] == "memo", -c["cost"]))

    months: dict[str, dict[str, Any]] = {}
    for s, concept, _kind, amount in rows:
        key = (s.statement_date or start).strftime("%Y-%m")
        m = months.setdefault(key, {"month": key, "sales": 0.0, "fees": 0.0, "concepts": defaultdict(float)})
        if concept == "gross_sales":
            m["sales"] += amount
        elif CONCEPTS[concept][1] != "memo":
            m["concepts"][concept] += -amount
            if CONCEPTS[concept][1] == "fee":
                m["fees"] += -amount
    by_month = [
        {
            "month": m["month"],
            "sales": _r(m["sales"]),
            "fees": _r(m["fees"]),
            "fee_pct": _pct(m["fees"], m["sales"]),
            "concepts": {c: _r(v) for c, v in m["concepts"].items()},
        }
        for _k, m in sorted(months.items())
    ]

    per: dict[int, dict[str, Any]] = {}
    for s, concept, _kind, amount in rows:
        p = per.setdefault(
            s.id,
            {"sales": 0.0, "fees": 0.0, "settlement": s},
        )
        if concept == "gross_sales":
            p["sales"] += amount
        elif CONCEPTS[concept][1] == "fee":
            p["fees"] += -amount
    statements = sorted(
        (
            {
                "id": s.id,
                "external_id": s.external_id,
                "statement_date": s.statement_date.isoformat() if s.statement_date else None,
                "payout_date": s.payout_date.isoformat() if s.payout_date else None,
                "status": s.status,
                "sales": _r(p["sales"]),
                "fees": _r(p["fees"]),
                "fee_pct": _pct(p["fees"], p["sales"]),
                "net_payout": _r(float(s.net_payout)),
            }
            for p in per.values()
            for s in [p["settlement"]]
        ),
        key=lambda x: x["statement_date"] or "",
        reverse=True,
    )

    cost_concepts = [c for c in by_concept if c["kind"] == "fee"]
    biggest = max(cost_concepts, key=lambda c: c["cost"], default=None)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "previous_start": prev_start.isoformat(),
        "previous_end": prev_end.isoformat(),
        "summary": {
            "statements": len(statements),
            "sales": _r(sales),
            "fees": _r(now["fees"]),
            "fee_pct": _r(now["fee_pct"]),
            "shipping": _r(now["shipping"]),
            "net_payout": _r(sum(s["net_payout"] for s in statements)),
            "biggest_concept": (
                {"code": biggest["code"], "name": biggest["name"], "cost": biggest["cost"], "pct_of_sales": biggest["pct_of_sales"]}
                if biggest and biggest["cost"] > 0
                else None
            ),
            "previous_fees": _r(before["fees"]),
            "previous_fee_pct": _r(before["fee_pct"]),
            "fees_change_pct": _change(now["fees"], before["fees"]),
            "fee_pct_change_points": _r(now["fee_pct"] - before["fee_pct"]) if prev_sales else None,
        },
        "by_concept": by_concept,
        "by_month": by_month,
        "statements": statements,
    }


async def settlement_waterfall(session: AsyncSession, settlement_id: int) -> dict[str, Any] | None:
    """Cascada de un depósito: ventas brutas -> cada concepto -> depósito. None si no existe."""
    s = await session.get(Settlement, settlement_id)
    if s is None:
        return None
    fees = (
        await session.execute(
            select(SettlementFee.concept, SettlementFee.amount).where(SettlementFee.settlement_id == s.id)
        )
    ).all()
    totals: dict[str, float] = defaultdict(float)
    for concept, amount in fees:
        totals[concept] += float(amount)
    steps = [
        {"code": code, "name": name, "kind": kind, "amount": _r(totals[code])}
        for code, (name, kind) in CONCEPTS.items()
        if kind != "memo" and abs(totals.get(code, 0.0)) >= 0.005
    ]
    net = float(s.net_payout)
    # Lo que el estado de cuenta suma y no viene en las transacciones (ajustes sin orden, redondeos).
    residual = net - sum(x["amount"] for x in steps)
    if abs(residual) >= 0.01:
        steps.append({"code": "other_adjustments", "name": "Otros ajustes de la liquidación", "kind": "adjustment", "amount": _r(residual)})
    sales = totals.get("gross_sales", 0.0)
    fee_total = -sum(v for c, v in totals.items() if CONCEPTS[c][1] == "fee")
    return {
        "id": s.id,
        "external_id": s.external_id,
        "statement_date": s.statement_date.isoformat() if s.statement_date else None,
        "payout_date": s.payout_date.isoformat() if s.payout_date else None,
        "status": s.status,
        "sales": _r(sales),
        "fees": _r(fee_total),
        "fee_pct": _pct(fee_total, sales),
        "net_payout": _r(net),
        "steps": steps,
    }
