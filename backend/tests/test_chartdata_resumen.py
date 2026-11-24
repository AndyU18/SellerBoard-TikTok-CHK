"""Serie mensual por producto para el resumen ejecutivo (datos sintéticos)."""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.chartdata_resumen import router
from app.db import create_dev_schema, get_session, make_engine
from app.gold import refresh_daily_profit
from app.main import app

ORDERS = (
    b"date,channel,account,order_id,sku,units,sales,fees,shipping\n"
    b"2026-10-05,amazon,amazon_1,A-1,GRN-100,10,500.00,150.00,0\n"
    b"2026-10-05,amazon,amazon_1,A-2,TOR-003,2,60.00,18.00,0\n"
    b"2026-11-02,tiktok_shop,tt_1,T-1,GRN-100,5,150.00,40.00,25.00\n"
)


@pytest_asyncio.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    if not any(getattr(r, 'path', '').startswith('/api/chartdata/product-monthly') for r in app.routes):
        app.include_router(router)  # main.py lo registra; aquí basta con que exista
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_product_monthly(env):
    client, maker = env
    res = await client.post("/api/import/orders", files={"file": ("o.csv", ORDERS, "text/csv")})
    assert res.status_code == 200, res.text
    async with maker() as s:
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()

    body = (await client.get("/api/chartdata/product-monthly")).json()
    assert body["months"] == ["2026-10-01", "2026-11-01"]
    grn = {r["period"]: r for r in body["rows"] if r["sku"] == "GRN-100"}
    assert grn["2026-10-01"]["units"] == 10 and grn["2026-10-01"]["gross_sales"] == 500
    assert grn["2026-11-01"]["units"] == 5

    only_one = (await client.get("/api/chartdata/product-monthly?limit=1")).json()
    assert {r["sku"] for r in only_one["rows"]} == {"GRN-100"}
    amazon = (await client.get("/api/chartdata/product-monthly?channel=amazon")).json()
    assert amazon["months"] == ["2026-10-01"]
    assert (await client.get("/api/chartdata/product-monthly?limit=0")).status_code == 422


@pytest.mark.asyncio
async def test_product_monthly_empty(env):
    client, _ = env
    assert (await client.get("/api/chartdata/product-monthly")).json() == {"months": [], "rows": []}


@pytest.mark.asyncio
async def test_versus_series(env):
    client, maker = env
    await client.post("/api/import/orders", files={"file": ("o.csv", ORDERS, "text/csv")})
    async with maker() as s:
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()

    body = (await client.get("/api/chartdata/versus-series?group_by=channel")).json()
    amazon = {r["period"]: r for r in body["rows"] if r["channel"] == "amazon"}
    assert amazon["2026-10-01"]["gross_sales"] == 560
    daily = (await client.get("/api/chartdata/versus-series?group_by=product&interval=day")).json()
    assert {r["sku"] for r in daily["rows"]} == {"GRN-100", "TOR-003"}
    assert (await client.get("/api/chartdata/versus-series?group_by=nope")).status_code == 422
