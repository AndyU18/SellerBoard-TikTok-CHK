"""Decisiones sobre creadores: a quién repetirle muestra y a quién dejar de enviarle.

Parte del ROI de las muestras (`reports.samples_report`) y añade, por creador:

  * **acción** según reglas simples y explícitas:
      repeat = ya dejó utilidad y tiene al menos 2 órdenes   -> enviar más
      wait   = aún no vende, pero la muestra es reciente      -> esperar
      cut    = sin ventas después del plazo de espera         -> dejar de enviar
      keep   = vende pero todavía no cubre la muestra         -> seguir observando
  * **costo por orden** y **días de la muestra a la primera venta**.

Y un resumen del tipo de contenido (video, LIVE, vitrina...) que más vende y paga.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AffiliateOrder, Channel
from app.reports import NO_CREATOR, _scope_ids, samples_report

DEFAULT_WAIT_DAYS = 14  # una muestra necesita este tiempo para llegar, grabarse y publicarse
REPEAT_MIN_ORDERS = 2
ACTION_ORDER = ("repeat", "keep", "wait", "cut")


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def decide(creator: dict[str, Any], days_since_sample: int, wait_days: int) -> str:
    """Acción para un creador con los datos de `samples_report`."""
    orders, net = creator["affiliate_orders"], creator["net"]
    if orders == 0:
        return "wait" if days_since_sample < wait_days else "cut"
    if net > 0 and orders >= REPEAT_MIN_ORDERS:
        return "repeat"
    return "keep"


async def creator_decisions_report(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    wait_days: int = DEFAULT_WAIT_DAYS,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    samples = await samples_report(session, start, end, channel, account)
    creators = [c for c in samples["creators"] if c["creator"] != NO_CREATOR]

    ids = await _scope_ids(session, channel, account)
    aff_q = select(AffiliateOrder)
    if start:
        aff_q = aff_q.where(AffiliateOrder.order_date >= start)
    if end:
        aff_q = aff_q.where(AffiliateOrder.order_date <= end)
    if ids is not None:
        aff_q = aff_q.where(AffiliateOrder.account_id.in_(ids))
    affiliate = (await session.scalars(aff_q)).all()

    by_creator: dict[str, list[AffiliateOrder]] = defaultdict(list)
    for a in affiliate:
        if a.creator_username:
            by_creator[a.creator_username].append(a)

    rows: list[dict[str, Any]] = []
    lags: list[int] = []
    for c in creators:
        first_sample = date.fromisoformat(c["first_sample"]) if c["first_sample"] else None
        sales = sorted(a.order_date for a in by_creator.get(c["creator"], []) if first_sample and a.order_date >= first_sample)
        lag = (sales[0] - first_sample).days if (sales and first_sample) else None
        if lag is not None:
            lags.append(lag)
        days_since = (today - first_sample).days if first_sample else 0
        action = decide(c, days_since, wait_days)
        orders = c["affiliate_orders"]
        rows.append(
            {
                "creator": c["creator"],
                "action": action,
                "samples": c["sample_orders"],
                "sample_cost": c["sample_cost"],
                "orders": orders,
                "gmv": c["affiliate_gmv"],
                "commission": c["commission"],
                "profit": c["affiliate_profit"],
                "net": c["net"],
                "roi": c["roi"],
                "orders_pending": c["orders_pending"],
                "cost_per_order": (c["sample_cost"] / orders) if orders else None,
                "days_to_first_sale": lag,
                "days_since_sample": days_since,
                "first_sample": c["first_sample"],
            }
        )
    rows.sort(key=lambda r: (ACTION_ORDER.index(r["action"]), -r["net"]))

    summary: dict[str, Any] = {"creators": len(rows)}
    for action in ACTION_ORDER:
        group = [r for r in rows if r["action"] == action]
        summary[action] = {
            "creators": len(group),
            "sample_cost": sum(r["sample_cost"] for r in group),
            "net": sum(r["net"] for r in group),
        }
    with_sales = sum(1 for r in rows if r["orders"] > 0)
    summary.update(
        {
            "conversion": (with_sales / len(rows)) if rows else None,
            "median_days_to_first_sale": float(statistics.median(lags)) if lags else None,
            "avoidable_cost": summary["cut"]["sample_cost"],
        }
    )

    content: dict[str, dict[str, Any]] = {}
    for a in affiliate:
        kind = a.content_type or "(sin tipo)"
        m = content.setdefault(kind, {"content_type": kind, "orders": set(), "units": 0, "gmv": 0.0, "commission": 0.0, "creators": set()})
        m["orders"].add(a.external_order_id)
        m["units"] += int(a.units)
        m["gmv"] += _f(a.commission_base)
        m["commission"] += _f(a.commission)
        if a.creator_username:
            m["creators"].add(a.creator_username)
    content_rows = sorted(
        (
            {
                "content_type": m["content_type"],
                "orders": len(m["orders"]),
                "units": m["units"],
                "gmv": m["gmv"],
                "commission": m["commission"],
                "commission_rate": (m["commission"] / m["gmv"]) if m["gmv"] > 0 else None,
                "creators": len(m["creators"]),
            }
            for m in content.values()
        ),
        key=lambda r: -r["gmv"],
    )
    return {"wait_days": wait_days, "summary": summary, "creators": rows, "by_content": content_rows}
