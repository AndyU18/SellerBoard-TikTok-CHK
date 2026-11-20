import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, get_session, make_engine
from app.main import app

ORDERS = (
    b"date,channel,account,order_id,sku,units,sales,fees,shipping\n"
    b"2026-10-05,amazon,amazon_1,A-1,GRN-100,10,500.00,150.00,0\n"
    b"2026-11-02,tiktok_shop,tt_1,T-1,GRN-100,5,150.00,40.00,25.00\n"
    b"2026-11-03,walmart,wm_1,W-1,TOR-003,4,100.00,12.00,0\n"
)
ADS = b"date,channel,account,sku,spend\n2026-11-02,tiktok,tt_1,GRN-100,30\n"


@pytest_asyncio.fixture
async def client():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def _upload(client, path, name, data):
    return await client.post(path, files={"file": (name, data, "text/csv")})


async def _setup(client):
    assert (await _upload(client, "/api/import/orders", "o.csv", ORDERS)).status_code == 200
    assert (await _upload(client, "/api/import/ad-spend", "a.csv", ADS)).status_code == 200
    for p in (await client.get("/api/products")).json():
        r = await client.put(f"/api/products/{p['id']}/cogs", json={"cogs_unit": 20})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_empty_database_returns_empty_profit(client):
    data = (await client.get("/api/profit")).json()
    assert data["rows"] == [] and data["totals"] is None


@pytest.mark.asyncio
async def test_profit_by_channel_with_negative_tiktok(client):
    await _setup(client)
    data = (await client.get("/api/profit?group_by=channel")).json()
    rows = {r["channel"]: r for r in data["rows"]}
    assert rows["amazon"]["net_profit"] == 500 - 150 - 200  # 150
    # TikTok: 150 - 40 - 25 - 100 (cogs) - 30 (ads) = -45
    assert rows["tiktok_shop"]["net_profit"] == -45.0
    assert rows["tiktok_shop"]["unit_profit"] == -9.0
    assert rows["walmart"]["net_profit"] == 100 - 12 - 80


@pytest.mark.asyncio
async def test_profit_by_product_and_channel_product(client):
    await _setup(client)
    data = (await client.get("/api/profit?group_by=product")).json()
    prod = {r["sku"]: r for r in data["rows"]}
    assert prod["GRN-100"]["net_profit"] == 150 + (-45)
    assert prod["GRN-100"]["units"] == 15
    cp = (await client.get("/api/profit?group_by=channel_product")).json()["rows"]
    assert {(r["channel"], r["sku"]) for r in cp} == {
        ("amazon", "GRN-100"),
        ("tiktok_shop", "GRN-100"),
        ("walmart", "TOR-003"),
    }


@pytest.mark.asyncio
async def test_date_and_channel_filters(client):
    await _setup(client)
    data = (await client.get("/api/profit?start=2026-11-01&end=2026-11-30")).json()
    assert {r["channel"] for r in data["rows"]} == {"tiktok_shop", "walmart"}
    only = (await client.get("/api/profit?channel=amazon")).json()
    assert [r["channel"] for r in only["rows"]] == ["amazon"]


@pytest.mark.asyncio
async def test_timeseries_by_month(client):
    await _setup(client)
    rows = (await client.get("/api/profit/timeseries?interval=month")).json()["rows"]
    assert {(r["period"], r["channel"]) for r in rows} == {
        ("2026-10-01", "amazon"),
        ("2026-11-01", "tiktok_shop"),
        ("2026-11-01", "walmart"),
    }


@pytest.mark.asyncio
async def test_totals_match_sum_of_rows(client):
    await _setup(client)
    data = (await client.get("/api/profit?group_by=channel")).json()
    assert data["totals"]["net_profit"] == pytest.approx(sum(r["net_profit"] for r in data["rows"]))


@pytest.mark.asyncio
async def test_invalid_upload_returns_422_with_problems(client):
    bad = b"date,channel,account,order_id,sku,units,sales\nnope,ebay,a,1,X,1,1\n"
    r = await _upload(client, "/api/import/orders", "o.csv", bad)
    assert r.status_code == 422
    assert any("Fecha inválida" in p for p in r.json()["detail"]["problems"])


@pytest.mark.asyncio
async def test_negative_cogs_rejected_and_unknown_product_404(client):
    await _setup(client)
    pid = (await client.get("/api/products")).json()[0]["id"]
    bad = await client.put(f"/api/products/{pid}/cogs", json={"cogs_unit": -1})
    assert bad.status_code == 422
    missing = await client.put("/api/products/9999/cogs", json={"cogs_unit": 1})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_bulk_cogs_import_updates_creates_and_validates(client):
    await _upload(client, "/api/import/orders", "o.csv", ORDERS)
    sku = (await client.get("/api/products")).json()[0]["sku"]
    csv = f"sku,cost\n{sku},$12.50\nNEW-1,3\n".encode()
    r = await _upload(client, "/api/import/cogs", "c.csv", csv)
    assert r.status_code == 200 and r.json()["products_created"] == 1
    costs = {p["sku"]: p["cogs_unit"] for p in (await client.get("/api/products")).json()}
    assert costs[sku] == 12.5 and costs["NEW-1"] == 3
    bad = await _upload(client, "/api/import/cogs", "c.csv", b"sku,cost\nA,-1\n")
    assert bad.status_code == 422
    missing = await _upload(client, "/api/import/cogs", "c.csv", b"sku\nA\n")
    assert missing.status_code == 422
