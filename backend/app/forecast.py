"""Pronóstico de ventas y utilidad, y seguimiento de metas mensuales.

Método (deliberadamente simple y explicable):

  * **Nivel**: promedio móvil exponencial de los últimos días (pesa más lo reciente).
  * **Día de la semana**: factor por día calculado en la ventana (solo si hay 3 semanas o más).
  * **Temporada**: multiplicador manual (p. ej. 1.5 para Black Friday) que el usuario decide.
  * **Rango**: ±1.28 desviaciones estándar del total (aprox. percentiles 10 y 90).

No conoce promociones futuras ni agotamientos de inventario: es una proyección del ritmo actual.
"""

from __future__ import annotations

import calendar
import math
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import polars as pl
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.economics import load_unit_economics
from app.gold import load_gold_frame
from app.models import Channel, Goal
from app.profit import add_line_profit

METRICS = ("gross_sales", "net_profit", "units")
EWMA_ALPHA = 0.2
MIN_DAYS_FOR_WEEKDAY = 21
Z_BAND = 1.28
TOP_SKUS = 15
BASELINE_DAYS = 28


def ewma(values: list[float], alpha: float = EWMA_ALPHA) -> float:
    if not values:
        return 0.0
    level = values[0]
    for v in values[1:]:
        level = alpha * v + (1 - alpha) * level
    return level


def weekday_index(dates: list[date], values: list[float]) -> dict[int, float]:
    """Factor por día de la semana (0 = lunes). 1.0 si no hay datos suficientes."""
    mean = sum(values) / len(values) if values else 0.0
    if len(values) < MIN_DAYS_FOR_WEEKDAY or mean <= 0:
        return dict.fromkeys(range(7), 1.0)
    by_day: dict[int, list[float]] = defaultdict(list)
    for d, v in zip(dates, values, strict=True):
        by_day[d.weekday()].append(v)
    return {
        w: min(1.5, max(0.5, (sum(by_day[w]) / len(by_day[w])) / mean)) if by_day[w] else 1.0 for w in range(7)
    }


def project(
    dates: list[date], values: list[float], horizon: list[date], multiplier: float = 1.0, floor_zero: bool = True
) -> tuple[list[float], float]:
    """Valores proyectados para los días de `horizon` y la desviación estándar diaria observada."""
    level = ewma(values)
    index = weekday_index(dates, values)
    out = [level * index[d.weekday()] * multiplier for d in horizon]
    if floor_zero:
        out = [max(0.0, v) for v in out]
    return out, (statistics.pstdev(values) if len(values) > 1 else 0.0)


def daily_totals(df: pl.DataFrame, start: date, end: date) -> tuple[list[date], dict[str, list[float]]]:
    """Totales por día entre `start` y `end` (ambos incluidos), sin huecos."""
    days = [start + timedelta(days=i) for i in range(max(0, (end - start).days + 1))]
    sums: dict[str, dict[date, float]] = {m: defaultdict(float) for m in METRICS}
    if df.height:
        for r in add_line_profit(df).iter_rows(named=True):
            sums["gross_sales"][r["date"]] += r["gross_sales"]
            sums["net_profit"][r["date"]] += r["net_profit"]
            sums["units"][r["date"]] += r["units"]
    return days, {m: [sums[m].get(d, 0.0) for d in days] for m in METRICS}


async def forecast_report(
    session: AsyncSession,
    *,
    horizon_days: int = 30,
    lookback_days: int = 28,
    multiplier: float = 1.0,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    start = today - timedelta(days=lookback_days - 1)
    df = await load_gold_frame(session, start=start, end=today, channel=channel, account=account)
    days, series = daily_totals(df, start, today)
    horizon = [today + timedelta(days=i) for i in range(1, horizon_days + 1)]

    totals: dict[str, Any] = {}
    projected: dict[str, list[float]] = {}
    for m in METRICS:
        values, sd = project(days, series[m], horizon, multiplier, floor_zero=m != "net_profit")
        projected[m] = values
        total = sum(values)
        band = Z_BAND * sd * math.sqrt(horizon_days)
        # Lo que se vendería en el horizonte si el promedio diario de la ventana siguiera igual.
        pace = sum(series[m]) / len(series[m]) * horizon_days if series[m] else 0.0
        totals[m] = {
            "forecast": total,
            "low": total - band if m == "net_profit" else max(0.0, total - band),
            "high": total + band,
            "recent_pace": pace,
            "change": (total / pace - 1) if pace > 0 else None,
        }

    history = [
        {"date": d.isoformat(), **{m: series[m][i] for m in METRICS}, "forecast": False} for i, d in enumerate(days)
    ]
    future = [
        {"date": d.isoformat(), **{m: projected[m][i] for m in METRICS}, "forecast": True}
        for i, d in enumerate(horizon)
    ]

    economics = await load_unit_economics(session, days=lookback_days, today=today, channel=channel, account=account)
    skus = sorted(
        (
            {
                "channel": e.channel,
                "sku": e.sku,
                "product_name": e.product_name,
                "daily_units": e.units / lookback_days,
                "forecast_units": e.units / lookback_days * horizon_days * multiplier,
                "forecast_sales": (e.net_sales / lookback_days) * horizon_days * multiplier,
                "forecast_profit": (e.net_profit / lookback_days) * horizon_days * multiplier,
                "unit_profit": e.unit_profit,
            }
            for e in economics
        ),
        key=lambda r: -r["forecast_sales"],
    )[:TOP_SKUS]
    return {
        "params": {"horizon_days": horizon_days, "lookback_days": lookback_days, "multiplier": multiplier},
        "weekday_adjusted": len(days) >= MIN_DAYS_FOR_WEEKDAY,
        "totals": totals,
        "series": history + future,
        "by_sku": skus,
    }


# ---------------------------------------------------------------------------- metas


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


async def list_goals(session: AsyncSession, month: date | None = None) -> list[Goal]:
    query = select(Goal).order_by(Goal.month.desc(), Goal.metric, Goal.channel_code)
    if month:
        query = query.where(Goal.month == month_start(month))
    return list((await session.scalars(query)).all())


async def upsert_goal(
    session: AsyncSession, month: date, metric: str, target: float, channel_code: str | None = None
) -> Goal:
    if metric not in METRICS:
        raise ValueError(f"Métrica desconocida: {metric}")
    if target < 0:
        raise ValueError("La meta no puede ser negativa")
    first = month_start(month)
    conditions = [Goal.month == first, Goal.metric == metric]
    conditions.append(Goal.channel_code.is_(None) if channel_code is None else Goal.channel_code == channel_code)
    found = await session.scalar(select(Goal).where(*conditions))
    if found is None:
        found = Goal(month=first, metric=metric, channel_code=channel_code, target=Decimal(str(target)))
        session.add(found)
    else:
        found.target = Decimal(str(target))
    await session.flush()
    return found


async def delete_goal(session: AsyncSession, goal_id: int) -> bool:
    result = await session.execute(delete(Goal).where(Goal.id == goal_id))
    return bool(result.rowcount)


def classify_progress(projected: float, target: float) -> str:
    if target <= 0:
        return "no_target"
    ratio = projected / target
    return "on_track" if ratio >= 1.0 else "at_risk" if ratio >= 0.9 else "behind"


async def goal_progress(
    session: AsyncSession, month: date | None = None, today: date | None = None, multiplier: float = 1.0
) -> dict[str, Any]:
    """Avance de las metas del mes: lo logrado, la proyección al cierre y el ritmo que falta."""
    today = today or datetime.now(UTC).date()
    first = month_start(month or today)
    last = month_end(first)
    month_days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    remaining = [d for d in month_days if d > today]
    goals = await list_goals(session, first)

    rows = []
    for g in goals:
        channel = Channel(g.channel_code) if g.channel_code else None
        upto = min(today, last)
        actual = 0.0
        if upto >= first:
            df = await load_gold_frame(session, start=first, end=upto, channel=channel)
            _, mtd = daily_totals(df, first, upto)
            actual = sum(mtd[g.metric])
        expected_rest = 0.0
        if remaining:
            # El nivel sale de las últimas 4 semanas, no solo de lo que va del mes.
            base_start = today - timedelta(days=BASELINE_DAYS - 1)
            base_df = await load_gold_frame(session, start=base_start, end=today, channel=channel)
            base_days, base = daily_totals(base_df, base_start, today)
            values, _ = project(base_days, base[g.metric], remaining, multiplier, floor_zero=g.metric != "net_profit")
            expected_rest = sum(values)
        target = float(g.target)
        projected = actual + expected_rest
        rows.append(
            {
                "id": g.id,
                "month": first.isoformat(),
                "metric": g.metric,
                "channel": g.channel_code,
                "target": target,
                "actual": actual,
                "projected": projected,
                "attainment": (actual / target) if target > 0 else None,
                "projected_attainment": (projected / target) if target > 0 else None,
                "days_left": len(remaining),
                "required_per_day": ((target - actual) / len(remaining)) if remaining else None,
                "status": classify_progress(projected, target),
            }
        )
    return {"month": first.isoformat(), "days_left": len(remaining), "goals": rows}
