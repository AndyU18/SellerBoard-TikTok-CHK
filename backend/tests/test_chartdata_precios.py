"""Datos de gráficas de Promociones: períodos antes/durante/después y endpoint (datos sintéticos)."""

from datetime import UTC, datetime, timedelta

import httpx
import polars as pl
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.chartdata_precios import build_timeline, router
from app.db import create_dev_schema, get_session, make_engine
from app.gold import refresh_daily_profit
from app.importers.persist import import_orders

TODAY = datetime.now(UTC).date()


def _day(i, units, depth):
    return {"date": f"d{i:02d}", "units": units, "net_sales": units * 10.0, "depth": depth}


def test_build_timeline_groups_runs_and_compares_neighbours():
    series = (
        [_day(i, 4, 0.0) for i in range(5)]
        + [_day(i, 10, 0.2) for i in range(5, 8)]
        + [_day(i, 4, 0.0) for i in range(8, 12)]
    )
    out = build_timeline(series, 0.05)
    assert len(out["series"]) == 12 and sum(1 for d in out["series"] if d["promo"]) == 3
    assert len(out["periods"]) == 1
    p = out["periods"][0]
    assert p["days"] == 3 and p["avg_depth"] == pytest.approx(0.2)
    assert p["before"]["days"] == 5 and p["after"]["days"] == 4
    assert p["during"]["units_per_day"] == pytest.approx(10)
    assert p["uplift"] == pytest.approx(10 / 4 - 1)


def test_build_timeline_without_promotions():
    out = build_timeline([_day(i, 3, 0.0) for i in range(6)], 0.05)
    assert out["periods"] == []


@pytest_asyncio.fixture
async def client():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    rows = [
        {
            "channel": "walmart",
            "account": "walmart_1",
            "product_name": None,
            "date": TODAY - timedelta(days=i),
            "order_id": f"O{i}",
            "sku": "A",
            "units": 5 if i < 3 else 2,
            "gross_sales": 50.0,
            "promo": 10.0 if i < 3 else 0.0,
            "platform_fees": 0.0,
            "shipping_cost": 0.0,
            "refund_amount": 0.0,
        }
        for i in range(10)
    ]
    async with maker() as s:
        await import_orders(s, pl.DataFrame(rows))
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()

    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest.mark.asyncio
async def test_promo_timeline_endpoint(client):
    res = await client.get("/api/chartdata/promo-timeline?days=14")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["series"]) == 14
    assert len(body["periods"]) == 1 and body["periods"][0]["days"] == 3
    assert sum(d["units"] for d in body["series"]) == 3 * 5 + 7 * 2
    assert (await client.get("/api/chartdata/promo-timeline?days=3")).status_code == 422
