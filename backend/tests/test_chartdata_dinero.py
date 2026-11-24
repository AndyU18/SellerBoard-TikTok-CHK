"""Datos extra de las gráficas de dinero: LIVE, creativos por semana y entrega del embudo (datos sintéticos)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.chartdata_dinero import router
from app.db import create_dev_schema, get_session, make_engine
from app.main import app
from app.models import Account, AffiliateOrder, LiveSession, Shipment

# Mientras el lead no lo registre en main.py, el test lo monta por su cuenta.
if not any(getattr(r, "path", "") == "/api/chartdata/live-sessions" for r in app.routes):
    app.include_router(router)


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        acc = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        s.add(acc)
        await s.flush()
        s.add_all(
            [
                LiveSession(
                    account_id=acc.id,
                    external_id="L1",
                    username="ana",
                    start_at=datetime(2026, 11, 10, 18, 0, tzinfo=UTC),
                    end_at=datetime(2026, 11, 10, 19, 0, tzinfo=UTC),
                    gmv=Decimal(120),
                    orders=3,
                ),
                LiveSession(
                    account_id=acc.id,
                    external_id="L2",
                    username="bo",
                    start_at=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
                    gmv=Decimal(0),
                ),
                AffiliateOrder(
                    account_id=acc.id,
                    external_order_id="O1",
                    sku_id="s1",
                    order_date=date(2026, 11, 3),
                    creator_username="ana",
                    content_type="VIDEO",
                    content_id="V1",
                    commission_base=Decimal(40),
                ),
                AffiliateOrder(
                    account_id=acc.id,
                    external_order_id="O2",
                    sku_id="s1",
                    order_date=date(2026, 11, 11),
                    creator_username="ana",
                    content_type="VIDEO",
                    content_id="V1",
                    commission_base=Decimal(60),
                ),
                Shipment(
                    account_id=acc.id,
                    external_order_id="O1",
                    order_date=date(2026, 11, 3),
                    delivered_at=datetime(2026, 11, 6, tzinfo=UTC),
                    collected_at=datetime(2026, 11, 4, tzinfo=UTC),
                ),
                Shipment(account_id=acc.id, external_order_id="O2", order_date=date(2026, 11, 11)),
                Shipment(account_id=acc.id, external_order_id="S1", order_date=date(2026, 11, 11), is_sample=True),
            ]
        )
        await s.commit()
    yield m
    await engine.dispose()


@pytest.mark.asyncio
async def test_chartdata_dinero(maker):
    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            live = (await client.get("/api/chartdata/live-sessions")).json()
            assert [s["external_id"] for s in live["sessions"]] == ["L2", "L1"]
            assert live["sessions"][1]["duration_min"] == 60

            late = (await client.get("/api/chartdata/live-sessions?start=2026-11-05")).json()
            assert [s["external_id"] for s in late["sessions"]] == ["L1"]

            weekly = (await client.get("/api/chartdata/creative-weekly")).json()
            assert weekly["weeks"] == ["2026-11-02", "2026-11-09"]
            assert weekly["rows"][0]["gmv"] == 100
            assert weekly["rows"][0]["weeks"]["2026-11-09"] == {"gmv": 60.0, "orders": 1}

            funnel = (await client.get("/api/chartdata/funnel-delivery")).json()
            assert funnel == {"orders": 2, "shipped": 1, "delivered": 1}

            assert (await client.get("/api/chartdata/creative-weekly?limit=0")).status_code == 422
    finally:
        app.dependency_overrides.clear()
