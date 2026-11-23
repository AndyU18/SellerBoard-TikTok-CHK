"""Efecto de las promociones: ¿descontar hizo vender más y dejó más utilidad?

Por (canal, SKU), con la serie diaria completa (los días sin ventas cuentan como cero) se compara:

  * **días con promoción** (descuento del vendedor >= `promo_threshold` del precio de lista)
  * **días sin promoción**

en unidades por día y utilidad por día. También se agrupan los días por profundidad del descuento y
se mide el índice de unidades (unidades del día / promedio normal del mismo SKU).

Es una comparación observacional: no separa el efecto del descuento de la temporada, de la
publicidad ni de los creadores. Úsala como señal, no como prueba.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.gold import load_gold_frame
from app.models import Channel
from app.profit import add_line_profit

DEFAULT_PROMO_THRESHOLD = 0.05
MIN_DAYS_EACH_SIDE = 3
BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("sin promo", 0.0, 0.05),
    ("5-15 %", 0.05, 0.15),
    ("15-30 %", 0.15, 0.30),
    ("30 % o más", 0.30, 10.0),
)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _bucket(depth: float) -> str:
    return next(name for name, lo, hi in BUCKETS if lo <= depth < hi)


def daily_series(days: dict[date, dict[str, float]], first: date, last: date) -> list[dict[str, Any]]:
    """Serie diaria sin huecos entre la primera venta del SKU y `last`."""
    out, day = [], first
    while day <= last:
        d = days.get(day, {})
        gross = d.get("gross", 0.0)
        out.append(
            {
                "date": day,
                "units": d.get("units", 0.0),
                "profit": d.get("profit", 0.0),
                "net_sales": gross - d.get("promo", 0.0),
                "depth": (d.get("promo", 0.0) / gross) if gross > 0 else 0.0,
            }
        )
        day += timedelta(days=1)
    return out


def _empty_summary() -> dict[str, int]:
    return {"skus": 0, "worth_it": 0, "not_worth": 0, "insufficient": 0}


def compare_sides(series: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    """Días con promoción contra días sin promoción de una serie diaria."""
    promo = [d for d in series if d["depth"] >= threshold]
    normal = [d for d in series if d["depth"] < threshold]
    u_promo, u_normal = _mean([d["units"] for d in promo]), _mean([d["units"] for d in normal])
    p_promo, p_normal = _mean([d["profit"] for d in promo]), _mean([d["profit"] for d in normal])
    enough = len(promo) >= MIN_DAYS_EACH_SIDE and len(normal) >= MIN_DAYS_EACH_SIDE
    delta = p_promo - p_normal if enough else None
    return {
        "promo_days": len(promo),
        "normal_days": len(normal),
        "avg_depth": _mean([d["depth"] for d in promo]),
        "units_per_day_promo": u_promo,
        "units_per_day_normal": u_normal,
        "uplift": (u_promo / u_normal - 1) if (enough and u_normal > 0) else None,
        "profit_per_day_promo": p_promo,
        "profit_per_day_normal": p_normal,
        "profit_delta_per_day": delta,
        "verdict": "insufficient" if not enough else ("worth_it" if delta > 0 else "not_worth"),  # type: ignore[operator]
    }


async def promo_effect_report(
    session: AsyncSession,
    *,
    days: int = 90,
    promo_threshold: float = DEFAULT_PROMO_THRESHOLD,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    df = await load_gold_frame(
        session, start=today - timedelta(days=days - 1), end=today, channel=channel, account=account
    )
    if not df.height:
        return {"days": days, "promo_threshold": promo_threshold, "summary": _empty_summary(), "rows": [], "buckets": []}

    grouped: dict[tuple[str, str], dict[date, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    names: dict[str, str | None] = {}
    for r in add_line_profit(df).iter_rows(named=True):
        d = grouped[(r["channel"], r["sku"])][r["date"]]
        d["units"] += r["units"]
        d["gross"] += r["gross_sales"]
        d["promo"] += r["promo"]
        d["profit"] += r["net_profit"]
        names.setdefault(r["sku"], r["product_name"])

    rows: list[dict[str, Any]] = []
    stats: dict[tuple[str, str], list[tuple[float, float, float]]] = defaultdict(list)  # índice, utilidad, ventas netas
    for (ch, sku), by_day in grouped.items():
        series = daily_series(by_day, min(by_day), today)
        mean_units = _mean([d["units"] for d in series])
        for d in series:
            if mean_units > 0 and d["units"] > 0:
                stats[(ch, _bucket(d["depth"]))].append((d["units"] / mean_units, d["profit"], d["net_sales"]))
        rows.append({"channel": ch, "sku": sku, "product_name": names.get(sku), **compare_sides(series, promo_threshold)})

    order = {"not_worth": 0, "worth_it": 1, "insufficient": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], r["profit_delta_per_day"] or 0, r["sku"]))

    buckets = []
    for ch in sorted({c for c, _ in stats}):
        for name, _lo, _hi in BUCKETS:
            sample = stats.get((ch, name), [])
            if not sample:
                continue
            net = sum(s[2] for s in sample)
            buckets.append(
                {
                    "channel": ch,
                    "bucket": name,
                    "sku_days": len(sample),
                    "units_index": _mean([s[0] for s in sample]),
                    "profit_per_sku_day": _mean([s[1] for s in sample]),
                    "margin": (sum(s[1] for s in sample) / net) if net > 0 else None,
                }
            )
    return {
        "days": days,
        "promo_threshold": promo_threshold,
        "summary": {
            "skus": len(rows),
            "worth_it": sum(1 for r in rows if r["verdict"] == "worth_it"),
            "not_worth": sum(1 for r in rows if r["verdict"] == "not_worth"),
            "insufficient": sum(1 for r in rows if r["verdict"] == "insufficient"),
        },
        "rows": rows,
        "buckets": buckets,
    }
