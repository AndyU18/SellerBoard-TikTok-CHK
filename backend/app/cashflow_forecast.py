"""Flujo de caja proyectado: cuánto dinero entra en los próximos días y cuánta utilidad aún no es caja.

Con los estados de cuenta de la plataforma (`sales.settlement`):

  * **Rezago de pago**: días entre el cierre del estado de cuenta y el depósito (mediana, de los pagados).
  * **Ya liquidado y por cobrar**: estados de cuenta en proceso, en su fecha de pago (o cierre + rezago).
  * **Ritmo futuro**: lo liquidado por día en la ventana reciente, que se paga con el mismo rezago.

Solo cubre canales que informan liquidaciones (hoy TikTok Shop). Es una estimación con el ritmo
reciente: no conoce promociones, devoluciones futuras ni cambios de temporada.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gold import load_gold_frame
from app.models import Account, Channel, Settlement
from app.profit import overall

DEFAULT_HORIZON_DAYS = 14
DEFAULT_LOOKBACK_DAYS = 28
DEFAULT_LAG_DAYS = 7  # si aún no hay pagos de los que medir el rezago


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def payment_lag_days(rows: list[Settlement]) -> float | None:
    """Mediana de días entre el cierre y el depósito, solo de pagos ya hechos."""
    lags = [
        (s.payout_date - s.statement_date).days
        for s in rows
        if s.status == "paid" and s.payout_date and s.statement_date and s.payout_date >= s.statement_date
    ]
    return float(statistics.median(lags)) if lags else None


def expected_payout(s: Settlement, lag: int, today: date) -> date:
    """Fecha estimada de depósito de un estado de cuenta pendiente (nunca antes de mañana)."""
    base = s.payout_date or ((s.statement_date or today) + timedelta(days=lag))
    return max(base, today + timedelta(days=1))


async def cash_projection(
    session: AsyncSession,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    end = today + timedelta(days=horizon_days)

    query = select(Account.channel_code, Settlement).join(Account, Settlement.account_id == Account.id)
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)
    pairs = (await session.execute(query)).all()
    settlements = [s for _, s in pairs]
    covered = sorted({code for code, _ in pairs})

    measured = payment_lag_days(settlements)
    lag = round(measured) if measured is not None else DEFAULT_LAG_DAYS

    # Estados de cuenta ya cerrados que aún no se depositan.
    known: dict[date, float] = defaultdict(float)
    pending_total = 0.0
    for s in settlements:
        if s.status in ("failed", "paid"):
            continue
        pending_total += _f(s.net_payout)
        when = expected_payout(s, lag, today)
        if when <= end:
            known[when] += _f(s.net_payout)

    # Ritmo reciente de cierre de estados de cuenta, por día natural.
    window_start = today - timedelta(days=lookback_days - 1)
    recent = sum(
        _f(s.net_payout)
        for s in settlements
        if s.status != "failed" and s.statement_date and window_start <= s.statement_date <= today
    )
    daily_rate = max(recent, 0.0) / lookback_days

    # Un estado de cuenta que se cierra el día `d` (futuro) se deposita en d + rezago.
    projected: dict[date, float] = defaultdict(float)
    for offset in range(1, horizon_days + 1):
        payout = today + timedelta(days=offset + lag)
        if payout <= end:
            projected[payout] += daily_rate

    series, running = [], 0.0
    for offset in range(1, horizon_days + 1):
        day = today + timedelta(days=offset)
        k, p = known.get(day, 0.0), projected.get(day, 0.0)
        running += k + p
        series.append({"date": day.isoformat(), "known": k, "projected": p, "cumulative": running})

    # Utilidad contable vs dinero que realmente llegó, últimos 30 días, solo en canales con liquidaciones.
    month_start = today - timedelta(days=29)
    profit_30d = 0.0
    for code in covered:
        row = overall(
            await load_gold_frame(session, start=month_start, end=today, channel=Channel(code), account=account)
        )
        profit_30d += row["net_profit"] if row else 0.0
    received_30d = sum(
        _f(s.net_payout)
        for s in settlements
        if s.status == "paid" and s.payout_date and month_start <= s.payout_date <= today
    )

    return {
        "params": {"horizon_days": horizon_days, "lookback_days": lookback_days},
        "channels_covered": covered,
        "lag_days": lag,
        "lag_measured": measured is not None,
        "daily_rate": daily_rate,
        "summary": {
            "pending_total": pending_total,
            "known_in_horizon": sum(known.values()),
            "projected_in_horizon": sum(projected.values()),
            "expected_in_horizon": sum(known.values()) + sum(projected.values()),
            "received_30d": received_30d,
            "profit_30d": profit_30d,
            "cash_gap_30d": profit_30d - received_30d,
        },
        "series": series,
        "note": None if covered else "Ningún canal informa liquidaciones todavía: no hay base para proyectar.",
    }
