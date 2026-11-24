"""Todo lo que se puede calcular con el libro de Redo: costo de etiquetas, correcciones, anulaciones,
cobertura contra las ventas, costo por canal/estado/orden, facturas y recargas de saldo.

Clasificación de los cargos (subtype de Redo):
  * costo:       ChargeLabel (etiqueta), Surcharge (corrección del carrier), VoidLabel (anulación, crédito),
                 ClaimCommission (comisión por reclamo), ClaimPayout (reclamo pagado, crédito), CardProcessingFee
  * no es costo: ReduceBalance, Refund, PromotionalCredit... son ajustes de saldo y se muestran aparte.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.persist import _chunks
from app.models import (
    Account,
    OrderLine,
    Product,
    RedoBill,
    RedoCharge,
    RedoTopup,
    SalesOrder,
)

CLASS_BY_SUBTYPE = {
    "ChargeLabel": "labels",
    "Surcharge": "surcharges",
    "VoidLabel": "voids",
    "ClaimCommission": "claim_fees",
    "ClaimPayout": "claim_payouts",
    "CardProcessingFee": "card_fees",
}
COST_CLASSES = set(CLASS_BY_SUBTYPE.values())
SUBTYPE_NAMES = {
    "ChargeLabel": "Etiquetas",
    "Surcharge": "Correcciones del carrier",
    "VoidLabel": "Etiquetas anuladas (crédito)",
    "ClaimCommission": "Comisión por reclamos",
    "ClaimPayout": "Reclamos pagados (crédito)",
    "CardProcessingFee": "Comisión de tarjeta (recargas)",
    "ReduceBalance": "Ajuste de saldo",
    "Refund": "Reembolso de saldo",
    "PromotionalCredit": "Crédito promocional",
}
ORIGIN_NAMES = {
    "tiktok_shop": "TikTok Shop",
    "walmart": "Walmart",
    "tiktok_pendiente": "TikTok (aún sin sincronizar)",
    "shopify": "Shopify (sin integrar)",
    "otro": "Otro / sin orden",
}
BUCKETS = [(0, 8, "Menos de $8"), (8, 12, "$8 a $12"), (12, 16, "$12 a $16"), (16, 25, "$16 a $25"), (25, 1e9, "$25 o más")]
NO_STATE = "(sin dato)"
LONG_AGO = date(2000, 1, 1)
# Un SKU se marca para revisar medidas con al menos estas órdenes enviadas y esta tasa de corrección.
FLAG_MIN_ORDERS = 5
FLAG_MIN_RATE = 0.15
TOP_REASONS_PER_SKU = 3
FLAG_SUGGESTION = "Revisa el peso y las medidas declaradas de este producto en la plataforma"


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _day(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _is_cost(subtype: str) -> bool:
    return subtype in CLASS_BY_SUBTYPE


def _reason(subtype: str, note: str | None) -> str:
    text = (note or "").strip()
    if subtype == "ClaimCommission":
        return "Comisión por reclamo"
    return text[:80] if text else "Sin detalle"


def _origin(order_name: str | None, channel: str | None) -> str:
    if channel in ("tiktok_shop", "walmart"):
        return channel  # type: ignore[return-value]
    if not order_name:
        return "otro"
    if order_name.startswith("#"):
        return "shopify"
    if order_name.isdigit() and len(order_name) >= 17:
        return "tiktok_pendiente"
    return "otro"


async def _load(session: AsyncSession, start: date, end: date) -> list[RedoCharge]:
    rows = (await session.scalars(select(RedoCharge).order_by(RedoCharge.created_at))).all()
    return [r for r in rows if start <= _day(r.created_at) <= end]


async def _orders_info(session: AsyncSession, names: list[str]) -> dict[str, dict[str, Any]]:
    """Canal, estado de destino, venta neta y unidades de las órdenes de Redo que existen en las ventas."""
    info: dict[str, dict[str, Any]] = {}
    ids: dict[int, str] = {}
    for part in _chunks(names):
        for oid, ext, state, channel in (
            await session.execute(
                select(SalesOrder.id, SalesOrder.external_order_id, SalesOrder.ship_state, Account.channel_code)
                .join(Account, SalesOrder.account_id == Account.id)
                .where(SalesOrder.external_order_id.in_(part))
            )
        ).all():
            ids[oid] = ext
            info[ext] = {"channel": channel, "state": state, "net_sales": 0.0, "units": 0}
    for part in _chunks(list(ids)):
        for oid, units, gross, promo in (
            await session.execute(
                select(OrderLine.order_id, OrderLine.units, OrderLine.gross_sales, OrderLine.promo).where(
                    OrderLine.order_id.in_(part)
                )
            )
        ).all():
            rec = info[ids[oid]]
            rec["net_sales"] += _f(gross) - _f(promo)
            rec["units"] += units
    return info


def _per_order(charges: list[RedoCharge]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in charges:
        if not c.order_name or not _is_cost(c.subtype):
            continue
        o = out.setdefault(
            c.order_name,
            {"cost": 0.0, "labels": 0, "voids": 0, "surcharge": 0.0, "date": _day(c.created_at)},
        )
        o["cost"] += _f(c.amount)
        o["date"] = min(o["date"], _day(c.created_at))
        if c.subtype == "ChargeLabel":
            o["labels"] += 1
        elif c.subtype == "VoidLabel":
            o["voids"] += 1
        elif c.subtype == "Surcharge":
            o["surcharge"] += _f(c.amount)
    return out


def _window(days: int, today: date | None) -> tuple[date, date]:
    end = today or datetime.now(UTC).date()
    return end - timedelta(days=days - 1), end


async def redo_overview(session: AsyncSession, *, days: int = 90, today: date | None = None) -> dict[str, Any]:
    start, end = _window(days, today)
    everything = await _load(session, LONG_AGO, end)
    charges = [c for c in everything if _day(c.created_at) >= start]

    totals: dict[str, float] = defaultdict(float)
    by_subtype: dict[str, dict[str, Any]] = {}
    reasons: dict[str, dict[str, Any]] = {}
    by_day: dict[date, float] = defaultdict(float)
    for c in charges:
        amount = _f(c.amount)
        totals[CLASS_BY_SUBTYPE.get(c.subtype, "adjustments")] += amount
        s = by_subtype.setdefault(
            c.subtype,
            {
                "subtype": c.subtype,
                "label": SUBTYPE_NAMES.get(c.subtype, c.subtype),
                "count": 0,
                "amount": 0.0,
                "is_cost": _is_cost(c.subtype),
            },
        )
        s["count"] += 1
        s["amount"] += amount
        if c.subtype == "Surcharge":
            reason = _reason(c.subtype, c.note)
            r = reasons.setdefault(reason, {"reason": reason, "count": 0, "amount": 0.0})
            r["count"] += 1
            r["amount"] += amount
        if _is_cost(c.subtype):
            by_day[_day(c.created_at)] += amount

    per_order = _per_order(charges)
    orders = len(per_order)
    labels = totals["labels"]
    net_cost = sum(totals[k] for k in COST_CLASSES)
    orders_cost = sum(o["cost"] for o in per_order.values())
    surcharge_orders = sum(1 for o in per_order.values() if o["surcharge"] > 0)

    months: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    month_orders: dict[str, set[str]] = defaultdict(set)
    for c in everything:
        cls = CLASS_BY_SUBTYPE.get(c.subtype)
        if not cls:
            continue
        key = c.created_at.strftime("%Y-%m")
        months[key][cls] += _f(c.amount)
        months[key]["net"] += _f(c.amount)
        if c.order_name and c.subtype == "ChargeLabel":
            month_orders[key].add(c.order_name)
    month_rows = [
        {
            "month": m,
            "labels": v["labels"],
            "surcharges": v["surcharges"],
            "voids": v["voids"],
            "fees": v["card_fees"] + v["claim_fees"],
            "net": v["net"],
            "orders": len(month_orders[m]),
            "avg_per_order": v["net"] / len(month_orders[m]) if month_orders[m] else None,
        }
        for m, v in sorted(months.items())
    ]

    info = await _orders_info(session, list(per_order))
    origin: dict[str, dict[str, Any]] = {}
    for name, o in per_order.items():
        key = _origin(name, (info.get(name) or {}).get("channel"))
        b = origin.setdefault(key, {"origin": key, "label": ORIGIN_NAMES[key], "orders": 0, "amount": 0.0})
        b["orders"] += 1
        b["amount"] += o["cost"]
    unassigned = sum(_f(c.amount) for c in charges if not c.order_name and _is_cost(c.subtype))

    return {
        "days": days,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "last_charge": max((c.created_at for c in everything), default=None),
        "kpis": {
            "net_cost": net_cost,
            "labels": labels,
            "surcharges": totals["surcharges"],
            "voids": totals["voids"],
            "card_fees": totals["card_fees"],
            "claim_fees": totals["claim_fees"],
            "claim_payouts": totals["claim_payouts"],
            "adjustments": totals["adjustments"],
            "orders": orders,
            "avg_cost_per_order": orders_cost / orders if orders else None,
            "median_cost_per_order": median([o["cost"] for o in per_order.values()]) if per_order else None,
            "surcharge_rate": totals["surcharges"] / labels if labels else None,
            "surcharge_orders": surcharge_orders,
            "surcharge_orders_pct": surcharge_orders / orders if orders else None,
            "relabel_orders": sum(1 for o in per_order.values() if o["labels"] >= 2),
            "void_rate": -totals["voids"] / labels if labels else None,
            "unassigned_cost": unassigned,
        },
        "by_subtype": sorted(by_subtype.values(), key=lambda r: -abs(r["amount"])),
        "surcharge_reasons": sorted(reasons.values(), key=lambda r: -r["amount"])[:12],
        "by_month": month_rows,
        "by_day": [{"date": d.isoformat(), "net": round(a, 2)} for d, a in sorted(by_day.items())],
        "coverage": sorted(origin.values(), key=lambda r: -r["amount"]),
    }


async def redo_costs(session: AsyncSession, *, days: int = 90, today: date | None = None) -> dict[str, Any]:
    """Costo por orden contra lo vendido: por canal, por estado de destino, distribución y órdenes más caras."""
    start, end = _window(days, today)
    per_order = _per_order(await _load(session, start, end))
    info = await _orders_info(session, list(per_order))

    channels: dict[str, dict[str, Any]] = {}
    states: dict[str, dict[str, Any]] = {}
    matched = 0
    for name, o in per_order.items():
        meta = info.get(name)
        if not meta:
            continue
        matched += 1
        for table, key in ((channels, meta["channel"]), (states, meta["state"] or NO_STATE)):
            b = table.setdefault(key, {"key": key, "orders": 0, "cost": 0.0, "net_sales": 0.0, "units": 0})
            b["orders"] += 1
            b["cost"] += o["cost"]
            b["net_sales"] += meta["net_sales"]
            b["units"] += meta["units"]

    def finish(b: dict[str, Any]) -> dict[str, Any]:
        return {
            **b,
            "avg_cost": b["cost"] / b["orders"] if b["orders"] else None,
            "cost_per_unit": b["cost"] / b["units"] if b["units"] else None,
            "share_of_sales": b["cost"] / b["net_sales"] if b["net_sales"] > 0 else None,
        }

    positive = [o["cost"] for o in per_order.values() if o["cost"] > 0]

    def order_row(name: str, o: dict[str, Any]) -> dict[str, Any]:
        meta = info.get(name)
        return {
            "order": name,
            "channel": _origin(name, meta["channel"] if meta else None),
            "state": meta["state"] if meta else None,
            "date": o["date"].isoformat(),
            "cost": round(o["cost"], 2),
            "labels": o["labels"],
            "voids": o["voids"],
            "surcharge": round(o["surcharge"], 2),
            "net_sales": round(meta["net_sales"], 2) if meta else None,
        }

    top = sorted(per_order.items(), key=lambda kv: -kv[1]["cost"])[:25]
    relabels = sorted(((n, o) for n, o in per_order.items() if o["labels"] >= 2), key=lambda kv: -kv[1]["cost"])[:25]
    return {
        "days": days,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "matched_orders": matched,
        "unmatched_orders": len(per_order) - matched,
        "by_channel": sorted((finish(b) for b in channels.values()), key=lambda r: -r["cost"]),
        "by_state": sorted((finish(b) for b in states.values()), key=lambda r: -r["cost"]),
        "distribution": [
            {"bucket": label, "orders": sum(1 for v in positive if lo <= v < hi)} for lo, hi, label in BUCKETS
        ],
        "top_orders": [order_row(n, o) for n, o in top],
        "relabeled_orders": [order_row(n, o) for n, o in relabels],
    }


async def _order_skus(session: AsyncSession, names: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """Por orden de Redo: SKU -> {name, gross} según las líneas de venta (varias cuentas con el mismo id se suman)."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for part in _chunks(names):
        for ext, sku, pname, gross in (
            await session.execute(
                select(SalesOrder.external_order_id, Product.sku, Product.name, OrderLine.gross_sales)
                .join(OrderLine, OrderLine.order_id == SalesOrder.id)
                .join(Product, Product.id == OrderLine.product_id)
                .where(SalesOrder.external_order_id.in_(part))
            )
        ).all():
            rec = out.setdefault(ext, {}).setdefault(sku, {"name": pname, "gross": 0.0})
            rec["gross"] += _f(gross)
    return out


def _shares(skus: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Parte de cada SKU en una orden según su venta bruta (si no hay venta, partes iguales)."""
    total = sum(s["gross"] for s in skus.values())
    if total > 0:
        return {sku: s["gross"] / total for sku, s in skus.items()}
    return {sku: 1 / len(skus) for sku in skus}


async def redo_corrections_by_sku(
    session: AsyncSession, *, days: int = 180, today: date | None = None
) -> dict[str, Any]:
    """Correcciones del carrier (Surcharge) por SKU: marca los productos con peso o medidas mal declarados.

    Solo cuentan las órdenes con etiqueta (ChargeLabel) en la ventana. El monto de una orden con varios SKU
    se reparte según la venta bruta de cada línea. Las correcciones de órdenes que no están en las ventas de la
    app (o que no tienen etiqueta en la ventana) van a `unattributed`.
    """
    start, end = _window(days, today)
    charges = await _load(session, start, end)
    labeled = {c.order_name for c in charges if c.order_name and c.subtype == "ChargeLabel"}
    label_cost: dict[str, float] = defaultdict(float)
    surcharges: dict[str, list[RedoCharge]] = defaultdict(list)
    for c in charges:
        if not c.order_name:
            continue
        if c.subtype == "ChargeLabel":
            label_cost[c.order_name] += _f(c.amount)
        elif c.subtype == "Surcharge":
            surcharges[c.order_name].append(c)

    order_skus = await _order_skus(session, sorted(labeled | set(surcharges)))
    rows: dict[str, dict[str, Any]] = {}
    sku_reasons: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    reasons: dict[str, dict[str, Any]] = {}
    total = 0.0
    total_count = 0
    unattr_amount = 0.0
    unattr_count = 0

    for name, rows_c in surcharges.items():
        amount = sum(_f(c.amount) for c in rows_c)
        total += amount
        total_count += len(rows_c)
        for c in rows_c:
            r = reasons.setdefault(_reason("Surcharge", c.note), {"reason": _reason("Surcharge", c.note), "count": 0, "amount": 0.0})
            r["count"] += 1
            r["amount"] += _f(c.amount)
        if name not in labeled or name not in order_skus:
            unattr_amount += amount
            unattr_count += len(rows_c)

    for name in labeled:
        skus = order_skus.get(name)
        if not skus:
            continue
        shares = _shares(skus)
        order_charges = surcharges.get(name, [])
        amount = sum(_f(c.amount) for c in order_charges)
        for sku, meta in skus.items():
            rec = rows.setdefault(
                sku,
                {"sku": sku, "name": meta["name"], "orders": 0, "orders_with_correction": 0, "correction_amount": 0.0, "label_cost": 0.0},
            )
            rec["orders"] += 1
            rec["label_cost"] += label_cost[name] * shares[sku]
            if order_charges:
                rec["orders_with_correction"] += 1
                rec["correction_amount"] += amount * shares[sku]
                for c in order_charges:
                    bucket = sku_reasons[sku][_reason("Surcharge", c.note)]
                    bucket[0] += 1
                    bucket[1] += _f(c.amount) * shares[sku]

    out_rows = []
    for sku, rec in rows.items():
        rate = rec["orders_with_correction"] / rec["orders"]
        flag = rec["orders"] >= FLAG_MIN_ORDERS and rate >= FLAG_MIN_RATE
        top = sorted(sku_reasons[sku].items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:TOP_REASONS_PER_SKU]
        out_rows.append(
            {
                "sku": sku,
                "name": rec["name"],
                "shipped_orders": rec["orders"],
                "orders_with_correction": rec["orders_with_correction"],
                "correction_rate": rate,
                "correction_amount": round(rec["correction_amount"], 2),
                "avg_correction": rec["correction_amount"] / rec["orders_with_correction"]
                if rec["orders_with_correction"]
                else None,
                "label_cost": round(rec["label_cost"], 2),
                "correction_pct_of_labels": rec["correction_amount"] / rec["label_cost"] if rec["label_cost"] > 0 else None,
                "top_reasons": [{"reason": r, "count": int(v[0]), "amount": round(v[1], 2)} for r, v in top],
                "flag": flag,
                "suggestion": FLAG_SUGGESTION if flag else None,
            }
        )
    out_rows.sort(key=lambda r: (-r["correction_amount"], r["sku"]))
    flagged = [r for r in out_rows if r["flag"]]
    return {
        "days": days,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "thresholds": {"min_orders": FLAG_MIN_ORDERS, "min_rate": FLAG_MIN_RATE},
        "summary": {
            "total_corrections": round(total, 2),
            "correction_count": total_count,
            "flagged_skus": len(flagged),
            "flagged_amount": round(sum(r["correction_amount"] for r in flagged), 2),
            "label_cost": round(sum(label_cost.values()), 2),
            "pct_of_labels": total / sum(label_cost.values()) if sum(label_cost.values()) > 0 else None,
            "reasons": sorted(
                ({**r, "amount": round(r["amount"], 2)} for r in reasons.values()), key=lambda r: -r["amount"]
            )[:12],
            "unattributed": {"amount": round(unattr_amount, 2), "count": unattr_count},
        },
        "skus": out_rows,
    }


async def redo_billing(session: AsyncSession, *, today: date | None = None) -> dict[str, Any]:
    end = today or datetime.now(UTC).date()
    bills = (await session.scalars(select(RedoBill).order_by(RedoBill.period_start.desc()))).all()
    topups = (await session.scalars(select(RedoTopup).order_by(RedoTopup.created_at))).all()
    ok = [t for t in topups if (t.status or "").upper() == "SUCCESS"]
    failed = [t for t in topups if (t.status or "").upper() not in ("SUCCESS", "")]

    everything = await _load(session, LONG_AGO, end)
    spend = [c for c in everything if _is_cost(c.subtype) and c.subtype != "CardProcessingFee"]
    start30 = end - timedelta(days=29)
    spend30 = sum(_f(c.amount) for c in spend if _day(c.created_at) >= start30)
    daily_burn = spend30 / 30

    gaps = [
        (later.created_at - earlier.created_at).total_seconds() / 86400
        for earlier, later in pairwise(ok)
    ]
    avg_topup = sum(_f(t.amount) for t in ok) / len(ok) if ok else None
    topup_month: dict[str, float] = defaultdict(float)
    for t in ok:
        topup_month[t.created_at.strftime("%Y-%m")] += _f(t.amount)
    charge_month: dict[str, float] = defaultdict(float)
    for c in spend:
        charge_month[c.created_at.strftime("%Y-%m")] += _f(c.amount)
    return {
        "bills": [
            {
                "id": b.id,
                "type": b.type,
                "status": b.status,
                "period_start": b.period_start,
                "period_end": b.period_end,
                "charge_total": _f(b.charge_total),
                "opening_balance": _f(b.opening_balance),
                "closing_balance": _f(b.closing_balance),
            }
            for b in bills
        ],
        "topups": {
            "count": len(ok),
            "total": sum(_f(t.amount) for t in ok),
            "average": avg_topup,
            "last_at": ok[-1].created_at if ok else None,
            "last_amount": _f(ok[-1].amount) if ok else None,
            "avg_days_between": sum(gaps) / len(gaps) if gaps else None,
            "failed": len(failed),
            "card_fees": sum(_f(c.amount) for c in everything if c.subtype == "CardProcessingFee"),
            "recent": [
                {"created_at": t.created_at, "amount": _f(t.amount), "status": t.status} for t in reversed(topups[-12:])
            ],
        },
        "burn": {
            "spend_30d": spend30,
            "daily": daily_burn,
            "days_per_topup": avg_topup / daily_burn if avg_topup and daily_burn > 0 else None,
        },
        "by_month": [
            {
                "month": m,
                "charges": round(charge_month.get(m, 0.0), 2),
                "topups": round(topup_month.get(m, 0.0), 2),
                "gap": round(charge_month.get(m, 0.0) - topup_month.get(m, 0.0), 2),
            }
            for m in sorted(set(charge_month) | set(topup_month))
        ],
    }


async def redo_charges(
    session: AsyncSession,
    *,
    subtype: str | None = None,
    q: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    rows = await _load(session, start or LONG_AGO, end or date(2100, 1, 1))
    if subtype:
        rows = [r for r in rows if r.subtype == subtype]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in (r.order_name or "").lower() or needle in (r.note or "").lower()]
    rows.sort(key=lambda r: r.created_at, reverse=True)
    sub_totals: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    day_totals: dict[date, float] = defaultdict(float)
    for r in rows:
        sub_totals[r.subtype][0] += 1
        sub_totals[r.subtype][1] += _f(r.amount)
        day_totals[_day(r.created_at)] += _f(r.amount)
    return {
        "total": len(rows),
        "amount": sum(_f(r.amount) for r in rows),
        # Resumen de TODO el filtro (no solo de la página), para las gráficas de la pantalla.
        "by_subtype": [
            {
                "subtype": k,
                "label": SUBTYPE_NAMES.get(k, k),
                "count": int(v[0]),
                "amount": round(v[1], 2),
                "is_cost": _is_cost(k),
            }
            for k, v in sorted(sub_totals.items(), key=lambda kv: -abs(kv[1][1]))
        ],
        "by_day": [{"date": d.isoformat(), "amount": round(a, 2)} for d, a in sorted(day_totals.items())],
        "subtypes": [{"value": k, "label": v} for k, v in SUBTYPE_NAMES.items()],
        "rows": [
            {
                "id": r.id,
                "created_at": r.created_at,
                "order_date": r.order_date,
                "order": r.order_name,
                "subtype": r.subtype,
                "label": SUBTYPE_NAMES.get(r.subtype, r.subtype),
                "amount": _f(r.amount),
                "note": r.note,
                "is_cost": _is_cost(r.subtype),
            }
            for r in rows[offset : offset + limit]
        ],
    }
