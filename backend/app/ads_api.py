"""Apartado Publicidad (TikTok Ads): gasto diario contra ventas del canal. Solo lectura."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.gold import load_gold_frame
from app.models import Account, AdSpendDaily, Channel, SalesOrder, SyncRun

router = APIRouter(prefix="/api/ads")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=365)]
CHANNEL = Channel.tiktok_shop


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


async def ads_overview(session: AsyncSession, *, days: int = 90, today: date | None = None) -> dict[str, Any]:
    end = today or datetime.now(UTC).date()
    start = end - timedelta(days=days - 1)

    spend_rows = (
        await session.execute(
            select(AdSpendDaily.spend_date, Account.name, func.sum(AdSpendDaily.spend))
            .join(Account, AdSpendDaily.account_id == Account.id)
            .where(
                Account.channel_code == CHANNEL.value,
                AdSpendDaily.spend_date >= start,
                AdSpendDaily.spend_date <= end,
            )
            .group_by(AdSpendDaily.spend_date, Account.name)
        )
    ).all()
    spend_by_day: dict[date, float] = defaultdict(float)
    by_account: dict[str, float] = defaultdict(float)
    for day, name, amount in spend_rows:
        spend_by_day[day] += float(amount or 0)
        by_account[name] += float(amount or 0)

    frame = await load_gold_frame(session, start=start, end=end, channel=CHANNEL)
    sales_by_day: dict[date, float] = defaultdict(float)
    before_ads_by_day: dict[date, float] = defaultdict(float)
    for r in frame.iter_rows(named=True):
        sales_by_day[r["date"]] += r["gross_sales"]
        before_ads_by_day[r["date"]] += (
            r["gross_sales"]
            - r["promo"]
            - r["platform_fees"]
            - r["shipping_cost"]
            - r["refund_amount"]
            - r["cogs"]
            - r["allocated_expenses"]
        )

    orders_by_day: dict[date, int] = defaultdict(int)
    for day, n in (
        await session.execute(
            select(SalesOrder.order_date, func.count(SalesOrder.id))
            .join(Account, SalesOrder.account_id == Account.id)
            .where(Account.channel_code == CHANNEL.value, SalesOrder.order_date >= start, SalesOrder.order_date <= end)
            .group_by(SalesOrder.order_date)
        )
    ).all():
        orders_by_day[day] = int(n)

    daily = []
    for i in range(days):
        d = start + timedelta(days=i)
        spend, sales = spend_by_day.get(d, 0.0), sales_by_day.get(d, 0.0)
        if not (spend or sales or orders_by_day.get(d)):
            continue
        daily.append(
            {
                "date": d.isoformat(),
                "spend": round(spend, 2),
                "sales": round(sales, 2),
                "orders": orders_by_day.get(d, 0),
                "roas": _div(sales, spend),
                "tacos": _div(spend, sales),
            }
        )

    months: dict[str, dict[str, float]] = defaultdict(lambda: {"spend": 0.0, "sales": 0.0, "orders": 0})
    for row in daily:
        m = months[row["date"][:7]]
        m["spend"] += row["spend"]
        m["sales"] += row["sales"]
        m["orders"] += row["orders"]
    monthly = [
        {"month": k, **v, "roas": _div(v["sales"], v["spend"]), "tacos": _div(v["spend"], v["sales"])}
        for k, v in sorted(months.items())
    ]

    weekday = [0.0] * 7
    for d, amount in spend_by_day.items():
        weekday[d.weekday()] += amount

    spend = sum(spend_by_day.values())
    sales = sum(sales_by_day.values())
    orders = sum(orders_by_day.values())
    profit_before = sum(before_ads_by_day.values())

    last_sync = await session.scalar(
        select(func.max(SyncRun.finished_at)).where(
            SyncRun.job_type == "tiktok_ads", SyncRun.status.in_(("success", "partial"))
        )
    )
    if last_sync is not None and last_sync.tzinfo is None:
        last_sync = last_sync.replace(tzinfo=UTC)

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "has_spend": spend > 0,
        "last_sync": last_sync,
        "kpis": {
            "spend": spend,
            "sales": sales,
            "roas": _div(sales, spend),
            "tacos": _div(spend, sales),
            "spend_per_order": _div(spend, orders),
            "orders": orders,
            "profit_before_ads": profit_before,
            "profit_after_ads": profit_before - spend,
        },
        "daily": daily,
        "monthly": monthly,
        "by_account": [{"account": k, "spend": v} for k, v in sorted(by_account.items(), key=lambda x: -x[1])],
        "by_weekday": weekday,
    }


@router.get("/overview", dependencies=VIEW)
async def overview(session: SessionDep, days: Days = 90) -> dict:
    """Gasto de TikTok Ads, ventas del canal, ROAS, TACOS y utilidad después de publicidad."""
    return await ads_overview(session, days=days)
