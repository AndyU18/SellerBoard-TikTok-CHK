"""Publicidad (TikTok Ads): gasto contra ventas del canal, ROAS, TACOS y bandera de sin gasto."""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ads_api import ads_overview, router
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    Account,
    AdSpendDaily,
    GoldDailyProfit,
    Product,
    SalesOrder,
    SyncRun,
)

TODAY = date(2026, 11, 20)


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    yield m
    await engine.dispose()


async def _seed(m, with_spend: bool) -> None:
    async with m() as s:
        acc = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        prod = Product(sku="SKU1", name="Producto")
        s.add_all([acc, prod])
        await s.flush()
        for day, sales in ((date(2026, 11, 9), 100), (date(2026, 11, 10), 200)):
            s.add(SalesOrder(account_id=acc.id, external_order_id=f"o{day}", order_date=day))
            s.add(
                GoldDailyProfit(
                    profit_date=day,
                    channel_code="tiktok_shop",
                    account_id=acc.id,
                    product_id=prod.id,
                    orders=1,
                    units=1,
                    gross_sales=Decimal(sales),
                    promo=Decimal(0),
                    platform_fees=Decimal(10),
                    shipping_cost=Decimal(0),
                    refund_amount=Decimal(0),
                    ad_spend=Decimal(0),
                    cogs=Decimal(30),
                    allocated_expenses=Decimal(0),
                )
            )
        if with_spend:
            s.add(AdSpendDaily(account_id=acc.id, spend_date=date(2026, 11, 9), spend=Decimal(25)))
            s.add(AdSpendDaily(account_id=acc.id, spend_date=date(2026, 11, 10), spend=Decimal(25)))
            s.add(
                SyncRun(
                    job_type="tiktok_ads",
                    status="success",
                    finished_at=datetime(2026, 11, 19, 12, tzinfo=UTC),
                )
            )
        await s.commit()


@pytest.mark.asyncio
async def test_overview_with_spend(maker):
    await _seed(maker, True)
    async with maker() as s:
        o = await ads_overview(s, days=30, today=TODAY)
    k = o["kpis"]
    assert o["has_spend"] and o["last_sync"] is not None
    assert k["spend"] == 50 and k["sales"] == 300 and k["orders"] == 2
    assert k["roas"] == pytest.approx(6) and k["tacos"] == pytest.approx(50 / 300)
    assert k["spend_per_order"] == 25
    assert k["profit_before_ads"] == 300 - 20 - 60 and k["profit_after_ads"] == 220 - 50
    assert [d["date"] for d in o["daily"]] == ["2026-11-09", "2026-11-10"]
    assert o["daily"][0]["roas"] == pytest.approx(4)
    assert o["monthly"][0]["month"] == "2026-11" and o["monthly"][0]["spend"] == 50
    assert o["by_account"] == [{"account": "tiktok_shop_us", "spend": 50}]
    assert o["by_weekday"][date(2026, 11, 9).weekday()] == 25


@pytest.mark.asyncio
async def test_overview_without_spend(maker):
    await _seed(maker, False)
    async with maker() as s:
        o = await ads_overview(s, days=30, today=TODAY)
    assert not o["has_spend"] and o["last_sync"] is None
    assert o["kpis"]["roas"] is None and o["kpis"]["tacos"] == 0 and o["by_account"] == []


@pytest.mark.asyncio
async def test_endpoint(maker):
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/ads/overview?days=30")
        assert res.status_code == 200, res.text
        assert res.json()["has_spend"] is False
        assert (await client.get("/api/ads/overview?days=3")).status_code == 422
