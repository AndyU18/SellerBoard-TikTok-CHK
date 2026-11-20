from datetime import date

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, get_session, make_engine
from app.gold import refresh_daily_profit
from app.main import app
from app.models import GoldDailyProfit
from app.profit import overall
from app.queries import load_profit_frame

ORDERS = (
    b"date,channel,account,order_id,sku,units,sales,fees,shipping\n"
    b"2026-10-05,amazon,amazon_1,A-1,GRN-100,10,500.00,150.00,0\n"
    b"2026-10-05,amazon,amazon_1,A-2,TOR-003,2,60.00,18.00,0\n"
    b"2026-11-02,tiktok_shop,tt_1,T-1,GRN-100,5,150.00,40.00,25.00\n"
    b"2026-11-03,walmart,wm_1,W-1,TOR-003,4,100.00,12.00,0\n"
)
ADS = (
    b"date,channel,account,sku,spend\n"
    b"2026-11-02,tiktok,tt_1,GRN-100,30\n"
    b"2026-11-02,tiktok,tt_1,,10\n"  # publicidad sin SKU
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

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def _post(client, path, data, name="f.csv"):
    r = await client.post(path, files={"file": (name, data, "text/csv")})
    assert r.status_code == 200, r.text
    return r


async def _load(client):
    await _post(client, "/api/import/orders", ORDERS)
    await _post(client, "/api/import/ad-spend", ADS)
    for p in (await client.get("/api/products")).json():
        await client.put(f"/api/products/{p['id']}/cogs", json={"cogs_unit": 20})


@pytest.mark.asyncio
async def test_gold_totals_match_silver_engine(env):
    client, maker = env
    await _load(client)
    async with maker() as s:
        silver = overall(await load_profit_frame(s))
    gold = (await client.get("/api/profit?group_by=channel")).json()["totals"]
    for key in ("units", "gross_sales", "platform_fees", "ad_spend", "cogs", "net_profit"):
        assert gold[key] == pytest.approx(silver[key]), key


@pytest.mark.asyncio
async def test_net_profit_is_generated_by_the_database(env):
    client, maker = env
    await _load(client)
    async with maker() as s:
        rows = (await s.scalars(select(GoldDailyProfit))).all()
    assert rows
    for r in rows:
        expected = (
            r.gross_sales - r.promo - r.platform_fees - r.shipping_cost - r.refund_amount
            - r.ad_spend - r.cogs - r.allocated_expenses
        )  # fmt: skip
        assert r.net_profit == expected


@pytest.mark.asyncio
async def test_ads_without_sku_land_in_unassigned_bucket(env):
    client, _ = env
    await _load(client)
    rows = (await client.get("/api/profit?group_by=product")).json()["rows"]
    assert any(r["sku"] == "(sin asignar)" and r["ad_spend"] == 10.0 for r in rows)


@pytest.mark.asyncio
async def test_partial_refresh_rewrites_only_its_range(env):
    client, maker = env
    await _load(client)
    in_october = GoldDailyProfit.profit_date < date(2026, 11, 1)
    async with maker() as s:
        before = await s.scalar(select(func.count()).select_from(GoldDailyProfit).where(in_october))
        await refresh_daily_profit(s, date(2026, 11, 1), date(2026, 11, 30), reason="test")
        await s.commit()
        after = await s.scalar(select(func.count()).select_from(GoldDailyProfit).where(in_october))
    assert before == after > 0  # octubre intacto
    totals = (await client.get("/api/profit")).json()["totals"]
    assert totals["gross_sales"] == 810.0  # nada se duplicó


@pytest.mark.asyncio
async def test_order_moved_to_another_day_leaves_no_stale_gold_row(env):
    client, _ = env
    await _load(client)
    moved = (
        b"date,channel,account,order_id,sku,units,sales,fees\n"
        b"2026-12-15,amazon,amazon_1,A-1,GRN-100,10,500.00,150.00\n"  # antes: 2026-10-05
    )
    await _post(client, "/api/import/orders", moved)
    oct_ = (await client.get("/api/profit?start=2026-10-01&end=2026-10-31")).json()["totals"]
    dec = (await client.get("/api/profit?start=2026-12-01&end=2026-12-31")).json()["totals"]
    assert oct_["gross_sales"] == 60.0  # solo queda la orden A-2
    assert dec["gross_sales"] == 500.0


@pytest.mark.asyncio
async def test_retroactive_cost_change_updates_gold_from_its_date(env):
    client, _ = env
    await _load(client)
    products = (await client.get("/api/products")).json()
    pid = next(p["id"] for p in products if p["sku"] == "GRN-100")
    await client.put(
        f"/api/products/{pid}/cogs", json={"cogs_unit": 50, "valid_from": "2026-11-01"}
    )
    oct_ = (await client.get("/api/profit?group_by=product&start=2026-10-01&end=2026-10-31")).json()
    nov = (await client.get("/api/profit?group_by=product&start=2026-11-01&end=2026-11-30")).json()

    def grn(data):
        return next(r for r in data["rows"] if r["sku"] == "GRN-100")

    assert grn(oct_)["cogs"] == 200.0  # 10 u x 20: octubre no se reescribe
    assert grn(nov)["cogs"] == 250.0  # 5 u x 50


@pytest.mark.asyncio
async def test_manual_refresh_and_status_endpoints(env):
    client, _ = env
    assert (await client.get("/api/gold/status")).json() == {"last_refresh": None}
    await _load(client)
    r = await client.post("/api/gold/refresh")
    assert r.status_code == 200 and r.json()["rows_written"] > 0
    status = (await client.get("/api/gold/status")).json()["last_refresh"]
    assert status["reason"] == "manual" and status["rows_written"] == r.json()["rows_written"]
