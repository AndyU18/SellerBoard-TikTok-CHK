"""Datos de la pantalla Productos: velocidad, beneficio por unidad y serie semanal (datos sintéticos)."""

from datetime import UTC, datetime, timedelta

import httpx
import polars as pl
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.chartdata_productos import WEEKS, build_products_overview, router
from app.db import create_dev_schema, get_session, make_engine
from app.gold import refresh_daily_profit
from app.importers.persist import import_orders

TODAY = datetime.now(UTC).date()


def test_build_products_overview_empty():
    out = build_products_overview(pl.DataFrame(), None, TODAY)
    assert out["rows"] == [] and out["weeks"] == WEEKS


@pytest_asyncio.fixture
async def client():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    rows = [
        {
            "channel": ch,
            "account": f"{ch}_1",
            "product_name": "Producto A" if sku == "A" else None,
            "date": TODAY - timedelta(days=i),
            "order_id": f"{ch}-{sku}-{i}",
            "sku": sku,
            "units": 3,
            "gross_sales": 30.0,
            "promo": 0.0,
            "platform_fees": 0.0,
            "shipping_cost": 0.0,
            "refund_amount": 0.0,
        }
        for ch, sku in (("walmart", "A"), ("tiktok_shop", "A"), ("walmart", "B"))
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
async def test_products_overview_endpoint(client):
    res = await client.get("/api/chartdata/products-overview")
    assert res.status_code == 200, res.text
    body = res.json()
    by_sku = {r["sku"]: r for r in body["rows"]}
    a, b = by_sku["A"], by_sku["B"]
    assert a["units"] == 60 and a["gross_sales"] == 600.0
    assert a["channels"] == ["tiktok_shop", "walmart"] and b["channels"] == ["walmart"]
    assert a["product_name"] == "Producto A"
    assert a["units_per_day"] == pytest.approx(60 / body["velocity_days"])
    assert len(a["weekly_units"]) == WEEKS and sum(a["weekly_units"]) == 60
    assert a["weekly_units"][-1] > 0 and a["weekly_units"][0] == 0
    assert a["avg_price"] == pytest.approx(10.0)
    assert a["unit_profit"] == pytest.approx(a["net_profit"] / 60, abs=0.01)


@pytest.mark.asyncio
async def test_products_overview_filters(client):
    res = await client.get("/api/chartdata/products-overview?channel=tiktok_shop")
    assert [r["sku"] for r in res.json()["rows"]] == ["A"]
    start = (TODAY - timedelta(days=4)).isoformat()
    res = await client.get(f"/api/chartdata/products-overview?start={start}&channel=walmart")
    rows = {r["sku"]: r for r in res.json()["rows"]}
    assert rows["B"]["units"] == 15 and rows["B"]["units_per_day"] == pytest.approx(30 / 30)
    assert (await client.get("/api/chartdata/products-overview?channel=nope")).status_code == 422
