"""Datos para las gráficas de Promociones: línea de tiempo diaria y antes/durante/después de cada promoción.

Solo lectura. Reutiliza la carga y la utilidad por línea de `promo_uplift` (mismo umbral de "día con promoción").
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.gold import load_gold_frame
from app.models import Channel
from app.profit import add_line_profit
from app.promo_uplift import DEFAULT_PROMO_THRESHOLD

router = APIRouter(prefix="/api/chartdata")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
WINDOW_DAYS = 7  # días que se miran antes y después de cada promoción


def _rate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {
        "days": n,
        "units_per_day": sum(r["units"] for r in rows) / n if n else None,
        "sales_per_day": sum(r["net_sales"] for r in rows) / n if n else None,
    }


def build_timeline(series: list[dict[str, Any]], threshold: float, window: int = WINDOW_DAYS) -> dict[str, Any]:
    """Marca los días con promoción y agrupa las rachas seguidas en períodos con su antes/durante/después.

    `series` es diaria y sin huecos: {date, units, net_sales, depth}. Los días "antes" y "después" excluyen
    otros días en promoción para no mezclar efectos.
    """
    flags = [d["depth"] >= threshold for d in series]
    periods: list[dict[str, Any]] = []
    i = 0
    while i < len(series):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(series) and flags[j + 1]:
            j += 1
        during = series[i : j + 1]
        lo = max(0, i - window)
        before = [d for k, d in enumerate(series[lo:i], lo) if not flags[k]]
        after = [d for k, d in enumerate(series[j + 1 : j + 1 + window], j + 1) if not flags[k]]
        base = [r for r in (_rate(before)["units_per_day"], _rate(after)["units_per_day"]) if r is not None]
        baseline = sum(base) / len(base) if base else None
        d_units = _rate(during)["units_per_day"] or 0.0
        periods.append(
            {
                "start": series[i]["date"],
                "end": series[j]["date"],
                "days": len(during),
                "avg_depth": sum(d["depth"] for d in during) / len(during),
                "before": _rate(before),
                "during": _rate(during),
                "after": _rate(after),
                # Unidades por día durante la promoción frente al promedio de los días vecinos sin promoción.
                "uplift": (d_units / baseline - 1) if baseline else None,
            }
        )
        i = j + 1
    return {
        "series": [{**d, "promo": f} for d, f in zip(series, flags, strict=True)],
        "periods": periods,
    }


async def promo_timeline(
    session: AsyncSession,
    *,
    days: int = 90,
    promo_threshold: float = DEFAULT_PROMO_THRESHOLD,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)
    df = await load_gold_frame(session, start=start, end=today, channel=channel, account=account)
    by_day: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if df.height:
        for r in add_line_profit(df).iter_rows(named=True):
            d = by_day[r["date"]]
            d["units"] += r["units"]
            d["gross"] += r["gross_sales"]
            d["promo"] += r["promo"]
    series = []
    for k in range(days):
        day = start + timedelta(days=k)
        d = by_day.get(day, {})
        gross = d.get("gross", 0.0)
        series.append(
            {
                "date": day.isoformat(),
                "units": d.get("units", 0.0),
                "net_sales": gross - d.get("promo", 0.0),
                "depth": (d.get("promo", 0.0) / gross) if gross > 0 else 0.0,
            }
        )
    return {"days": days, "promo_threshold": promo_threshold, **build_timeline(series, promo_threshold)}


@router.get("/promo-timeline", dependencies=VIEW)
async def promo_timeline_endpoint(
    session: SessionDep,
    days: Annotated[int, Query(ge=7, le=365)] = 90,
    promo_threshold: Annotated[float, Query(ge=0.01, le=0.9)] = DEFAULT_PROMO_THRESHOLD,
    channel: Channel | None = None,
    account: str | None = None,
) -> dict:
    """Ventas, unidades y descuento por día, con los períodos de promoción y su antes/durante/después."""
    return await promo_timeline(
        session, days=days, promo_threshold=promo_threshold, channel=channel, account=account
    )
