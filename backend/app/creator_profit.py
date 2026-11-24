"""Rentabilidad real por creador, video y LIVE.

utilidad = ventas - promo - comisión de la plataforma - comisión del afiliado - envío - reembolsos
           - costo del producto (COGS) - costo de las muestras enviadas al creador

Enlaces con los datos disponibles:
  * orden de afiliado -> creador: `AffiliateOrder.creator_username`.
  * orden de afiliado -> video / LIVE: `AffiliateOrder.content_id` (= `VideoPerformance.video_id` /
    `LiveSession.external_id`) con `content_type` VIDEO / LIVE. Otros tipos (SHOP, LINKSHARE) no tienen
    entidad y solo cuentan en el nivel creador.
  * orden de afiliado -> líneas de venta: mismo id externo de orden (`SalesOrder` / `OrderLine`), de ahí
    salen ventas, promo, comisiones, envío, reembolsos y COGS (`app.costs`). Si la orden aún no está
    en ventas se cuenta como pendiente y no suma dinero.
  * muestras -> creador: `SampleApplication.external_order_id` enlaza la orden de muestra con su creador.
    La muestra NO se puede ligar a un video ni a un LIVE, por eso su costo solo existe en el nivel creador.

La comisión del afiliado ya viene dentro de `platform_fees` de la línea; aquí se separa para no
contarla dos veces. Si una orden trae varias entidades se reparte por su base de comisión.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import load_cost_history
from app.models import (
    AffiliateOrder,
    LiveSession,
    OrderLine,
    Product,
    SampleApplication,
    SampleLine,
    VideoPerformance,
)
from app.reports import NO_CREATOR, _label_costs, _order_lines_by_external

LEVELS = ("creator", "video", "live")
CONTENT_TYPE = {"video": "VIDEO", "live": "LIVE"}

# Umbrales del veredicto (sobre ventas netas = ventas - promo).
MIN_ORDERS_FOR_VERDICT = 3  # con menos órdenes no hay base para decidir: Vigilar
RENEW_MARGIN = 0.15  # margen desde el cual conviene renovar
KEEP_MARGIN = 0.05  # margen desde el cual se mantiene
SAMPLE_GRACE_DAYS = 14  # una muestra sin ventas es "Cortar" solo pasado este plazo

RENEW, KEEP, WATCH, CUT = "Renovar", "Mantener", "Vigilar", "Cortar"
VERDICTS = (RENEW, KEEP, WATCH, CUT)


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def _week(day: date) -> str:
    return (day - timedelta(days=day.weekday())).isoformat()


def verdict_for(
    *,
    orders: int,
    profit: float,
    margin: float | None,
    sample_cost: float,
    first_sample: date | None,
    today: date,
) -> str:
    """Renovar / Mantener / Vigilar / Cortar según utilidad, margen y volumen de órdenes."""
    if orders == 0:
        waited = first_sample is not None and (today - first_sample).days >= SAMPLE_GRACE_DAYS
        return CUT if (sample_cost > 0 and waited) else WATCH
    if orders < MIN_ORDERS_FOR_VERDICT:
        return WATCH
    if profit <= 0:
        return CUT
    if margin is not None and margin >= RENEW_MARGIN:
        return RENEW
    if margin is not None and margin >= KEEP_MARGIN:
        return KEEP
    return WATCH


def _blank(key: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": key,
        "creator": None,
        "orders": set(),
        "pending": set(),
        "units": 0,
        "sales": 0.0,
        "promo": 0.0,
        "platform_fee": 0.0,
        "commission": 0.0,
        "shipping": 0.0,
        "refunds": 0.0,
        "cogs": 0.0,
        "sample_cost": 0.0,
        "first_sample": None,
        "order_profit": {},  # orden -> (fecha, utilidad antes de muestras) para el payback
        "weeks": defaultdict(lambda: [0.0, 0.0]),  # semana -> [ventas, utilidad]
    }


async def creator_profit_report(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    level: str = "creator",
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    today: date | None = None,
) -> dict[str, Any]:
    if level not in LEVELS:
        raise ValueError(f"level debe ser uno de {LEVELS}")
    today = today or datetime.now(UTC).date()
    costs = await load_cost_history(session)

    # ---- órdenes de afiliado
    aff_q = select(AffiliateOrder)
    if start:
        aff_q = aff_q.where(AffiliateOrder.order_date >= start)
    if end:
        aff_q = aff_q.where(AffiliateOrder.order_date <= end)
    affiliate = list((await session.scalars(aff_q)).all())
    order_ids = sorted({a.external_order_id for a in affiliate})
    lines = await _order_lines_by_external(session, order_ids)
    labels = await _label_costs(session, list(lines))

    # totales por orden (de las líneas de venta)
    totals: dict[str, dict[str, float]] = {}
    for oid, rows in lines.items():
        t = dict.fromkeys(("gross", "promo", "fees", "shipping", "refunds", "cogs"), 0.0)
        for r in rows:
            t["gross"] += _f(r.gross_sales)
            t["promo"] += _f(r.promo)
            t["fees"] += _f(r.platform_fees)
            t["shipping"] += _f(r.shipping_cost)
            t["refunds"] += _f(r.refund_amount)
            t["cogs"] += float(r.units) * float(costs.unit_cost(r.product_id, r.order_date))
        t["shipping"] += labels.get(oid, 0.0)
        t["units"] = float(sum(r.units for r in rows))
        t["date"] = min(r.order_date for r in rows)  # type: ignore[assignment]
        totals[oid] = t

    # ---- entidad de cada fila de afiliado (None = no pertenece a este nivel)
    def entity_of(a: AffiliateOrder) -> str | None:
        if level == "creator":
            return a.creator_username or None
        if (a.content_type or "").upper() != CONTENT_TYPE[level]:
            return None
        return a.content_id or None

    # repartir cada orden entre sus entidades según la base de comisión
    shares: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for a in affiliate:
        key = entity_of(a)
        if key is None:
            continue
        s = shares[a.external_order_id].setdefault(
            key, {"base": 0.0, "commission": 0.0, "units": 0, "creator": a.creator_username, "day": a.order_date}
        )
        s["base"] += _f(a.commission_base)
        s["commission"] += _f(a.commission)
        s["units"] += int(a.units)

    ents: dict[str, dict[str, Any]] = {}
    for oid, by_key in shares.items():
        base_total = sum(s["base"] for s in by_key.values())
        for key, s in by_key.items():
            e = ents.setdefault(key, _blank(key))
            if s["creator"] and not e["creator"]:
                e["creator"] = s["creator"]
            e["orders"].add(oid)
            e["units"] += s["units"]
            t = totals.get(oid)
            if t is None:
                e["pending"].add(oid)
                continue
            frac = s["base"] / base_total if base_total > 0 else 1 / len(by_key)
            order_commission = sum(x["commission"] for x in by_key.values())
            commission = s["commission"]
            gross, promo = t["gross"] * frac, t["promo"] * frac
            platform_fee = (max(t["fees"], order_commission) - order_commission) * frac
            ship, refunds, cogs = t["shipping"] * frac, t["refunds"] * frac, t["cogs"] * frac
            e["sales"] += gross
            e["promo"] += promo
            e["commission"] += commission
            e["platform_fee"] += platform_fee
            e["shipping"] += ship
            e["refunds"] += refunds
            e["cogs"] += cogs
            op = gross - promo - platform_fee - commission - ship - refunds - cogs
            e["order_profit"][oid] = (s["day"], op)
            w = e["weeks"][_week(s["day"])]
            w[0] += gross
            w[1] += op

    # ---- muestras (solo nivel creador)
    sample_total = 0.0
    sold_creators: set[str] = set()
    sample_by_creator: dict[str, dict[str, Any]] = {}
    sl_q = select(SampleLine)
    if start:
        sl_q = sl_q.where(SampleLine.order_date >= start)
    if end:
        sl_q = sl_q.where(SampleLine.order_date <= end)
    sample_lines = list((await session.scalars(sl_q)).all())
    sample_order_ids = sorted({sl.external_order_id for sl in sample_lines})
    creator_of: dict[str, str] = {}
    for oid, username in (
        await session.execute(
            select(SampleApplication.external_order_id, SampleApplication.creator_username).where(
                SampleApplication.external_order_id.is_not(None)
            )
        )
    ).all():
        creator_of[oid] = username or NO_CREATOR
    product_ids = dict((await session.execute(select(Product.sku, Product.id))).all())
    s_labels = await _label_costs(session, sample_order_ids)
    s_sales = await _order_lines_by_external(session, sample_order_ids)
    sample_orders: dict[str, dict[str, Any]] = {}
    for sl in sample_lines:
        o = sample_orders.setdefault(
            sl.external_order_id,
            {"creator": creator_of.get(sl.external_order_id, NO_CREATOR), "day": sl.order_date, "cost": 0.0},
        )
        pid = product_ids.get(sl.sku)
        o["cost"] += float(sl.units) * (costs.unit_cost(pid, sl.order_date) if pid is not None else 0.0)
    for oid, o in sample_orders.items():
        o["cost"] += s_labels.get(oid, 0.0) + sum(_f(r.shipping_cost) for r in s_sales.get(oid, []))
        sample_total += o["cost"]
        c = sample_by_creator.setdefault(o["creator"], {"cost": 0.0, "first": o["day"], "weeks": defaultdict(float)})
        c["cost"] += o["cost"]
        c["first"] = min(c["first"], o["day"])
        c["weeks"][_week(o["day"])] += o["cost"]

    # nivel creador: todos los creadores con muestra, hayan vendido o no
    if level == "creator":
        for name, c in sample_by_creator.items():
            if name == NO_CREATOR:
                continue
            e = ents.setdefault(name, _blank(name))
            e["creator"] = name
            e["sample_cost"] = c["cost"]
            e["first_sample"] = c["first"]
            for wk, cost in c["weeks"].items():
                e["weeks"][wk][1] -= cost
    # creadores que aparecen en afiliados y tienen muestra incluso con otro nivel: para el resumen global
    creators_sold_global = {a.creator_username for a in affiliate if a.creator_username}
    sold_creators = {n for n in sample_by_creator if n in creators_sold_global}
    never = {n: c for n, c in sample_by_creator.items() if n != NO_CREATOR and n not in sold_creators}

    # ---- metadatos de video / LIVE
    if level == "video" and ents:
        for vid, username, title in (
            await session.execute(
                select(VideoPerformance.video_id, VideoPerformance.username, VideoPerformance.title).where(
                    VideoPerformance.video_id.in_(list(ents))
                )
            )
        ).all():
            e = ents[vid]
            e["label"] = (title or "").strip() or f"Video {vid}"
            e["creator"] = e["creator"] or username
            e["known"] = True
    if level == "live" and ents:
        for lid, username, title, start_at in (
            await session.execute(
                select(LiveSession.external_id, LiveSession.username, LiveSession.title, LiveSession.start_at).where(
                    LiveSession.external_id.in_(list(ents))
                )
            )
        ).all():
            e = ents[lid]
            day = start_at.date().isoformat() if start_at else ""
            e["label"] = ((title or "").strip() or f"LIVE {lid}") + (f" ({day})" if day else "")
            e["creator"] = e["creator"] or username
            e["known"] = True
    if level != "creator":
        for e in ents.values():
            if not e.get("known"):
                e["label"] = f"{'Video' if level == 'video' else 'LIVE'} {e['key']}"

    # ---- métricas finales
    rows: list[dict[str, Any]] = []
    for e in ents.values():
        net = e["sales"] - e["promo"]
        before = net - e["platform_fee"] - e["commission"] - e["shipping"] - e["refunds"] - e["cogs"]
        profit = before - e["sample_cost"]
        margin = _ratio(profit, net)
        payback = None
        if e["sample_cost"] > 0:
            running = 0.0
            for n, (_day, op) in enumerate(sorted(e["order_profit"].values(), key=lambda x: x[0]), 1):
                running += op
                if running >= e["sample_cost"]:
                    payback = n
                    break
        weeks = sorted(e["weeks"].items())
        rows.append(
            {
                "key": e["key"],
                "label": e["label"],
                "creator": e["creator"],
                "orders": len(e["orders"]),
                "orders_pending": len(e["pending"]),
                "units": e["units"],
                "sales": e["sales"],
                "promo": e["promo"],
                "platform_fee": e["platform_fee"],
                "commission": e["commission"],
                "shipping": e["shipping"],
                "refunds": e["refunds"],
                "cogs": e["cogs"],
                "sample_cost": e["sample_cost"],
                "profit_before_samples": before,
                "profit": profit,
                "margin": margin,
                "roi": _ratio(profit, e["sample_cost"]),
                "payback_orders": payback,
                "verdict": verdict_for(
                    orders=len(e["orders"]),
                    profit=profit,
                    margin=margin,
                    sample_cost=e["sample_cost"],
                    first_sample=e["first_sample"],
                    today=today,
                ),
                "series": [{"week": w, "sales": v[0], "profit": v[1]} for w, v in weeks],
            }
        )
    rows.sort(key=lambda r: -r["profit"])

    # ---- resumen (global de afiliados, no depende del nivel)
    sales_q = select(func.coalesce(func.sum(OrderLine.gross_sales), 0))
    if start:
        sales_q = sales_q.where(OrderLine.order_date >= start)
    if end:
        sales_q = sales_q.where(OrderLine.order_date <= end)
    all_sales = _f(await session.scalar(sales_q))
    aff_gross = aff_profit = 0.0
    for oid in order_ids:
        t = totals.get(oid)
        if t is None:
            continue
        aff_gross += t["gross"]
        aff_profit += (
            t["gross"] - t["promo"] - max(t["fees"], 0.0) - t["shipping"] - t["refunds"] - t["cogs"]
        )
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    summary = {
        "affiliate_orders": len(order_ids),
        "affiliate_orders_pending": sum(1 for o in order_ids if o not in totals),
        "affiliate_sales": aff_gross,
        "affiliate_profit_before_samples": aff_profit,
        "sample_cost": sample_total,
        "affiliate_profit": aff_profit - sample_total,
        "affiliate_share_of_sales": _ratio(aff_gross, all_sales),
        "total_sales": all_sales,
        "never_sold_creators": len(never),
        "never_sold_cost": sum(c["cost"] for c in never.values()),
        "verdict_counts": counts,
        "entities": len(rows),
    }
    notes: list[str] = []
    if level != "creator":
        notes.append("Las muestras no se pueden ligar a un video ni a un LIVE: su costo solo aparece por creador.")
        unknown = sum(1 for e in ents.values() if not e.get("known"))
        if unknown:
            notes.append(f"{unknown} sin ficha de rendimiento (se muestran por id).")

    needle = (q or "").strip().lower()
    if needle:
        rows = [r for r in rows if needle in f"{r['label']} {r['key']} {r['creator'] or ''}".lower()]
    page = rows[offset : offset + limit]
    weeks_all = sorted({s["week"] for r in rows for s in r["series"]})
    return {
        "level": level,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "thresholds": {
            "min_orders": MIN_ORDERS_FOR_VERDICT,
            "renew_margin": RENEW_MARGIN,
            "keep_margin": KEEP_MARGIN,
            "sample_grace_days": SAMPLE_GRACE_DAYS,
        },
        "summary": summary,
        "weeks": weeks_all,
        "total": len(rows),
        "rows": page,
        "notes": notes,
    }
