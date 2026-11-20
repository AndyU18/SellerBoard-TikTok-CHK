from datetime import date

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.costs import CostHistory
from app.db import create_dev_schema, get_session, make_engine
from app.main import app

ORDERS = (
    b"date,channel,account,order_id,sku,units,sales\n"
    b"2026-01-10,amazon,a1,1,X,2,100.00\n"
    b"2026-06-10,amazon,a1,2,X,2,100.00\n"
)


def test_unit_cost_uses_latest_vigencia_not_after_the_day():
    h = CostHistory([(1, 1, date(2026, 1, 1), 10.0), (1, 1, date(2026, 5, 1), 12.0)])
    assert h.unit_cost(1, date(2025, 12, 31)) == 0.0  # antes de cualquier vigencia
    assert h.unit_cost(1, date(2026, 3, 1)) == 10.0
    assert h.unit_cost(1, date(2026, 5, 1)) == 12.0  # la vigencia rige desde su fecha


def test_unit_cost_sums_components_and_unknown_product_is_zero():
    h = CostHistory([(1, 1, date(2026, 1, 1), 10.0), (1, 2, date(2026, 1, 1), 2.5)])
    assert h.unit_cost(1, date(2026, 2, 1)) == 12.5
    assert h.unit_cost(99, date(2026, 2, 1)) == 0.0
    assert h.has_cost(1) and not h.has_cost(99)


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
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_cost_change_applies_from_its_date_and_keeps_past_profit(client):
    files = {"file": ("o.csv", ORDERS, "text/csv")}
    assert (await client.post("/api/import/orders", files=files)).status_code == 200
    pid = (await client.get("/api/products")).json()[0]["id"]

    # Primer costo sin fecha: rige desde siempre. Segundo, desde mayo.
    assert (
        await client.put(f"/api/products/{pid}/cogs", json={"cogs_unit": 10})
    ).status_code == 200
    r = await client.put(
        f"/api/products/{pid}/cogs", json={"cogs_unit": 20, "valid_from": "2026-05-01"}
    )
    assert r.status_code == 200

    jan = (
        await client.get("/api/profit?group_by=product&start=2026-01-01&end=2026-01-31")
    ).json()
    jun = (
        await client.get("/api/profit?group_by=product&start=2026-06-01&end=2026-06-30")
    ).json()
    assert jan["totals"]["cogs"] == 20.0  # 2 u x 10 (el costo viejo no se reescribe)
    assert jun["totals"]["cogs"] == 40.0  # 2 u x 20
    assert (await client.get("/api/products")).json()[0]["cogs_unit"] == 20.0


@pytest.mark.asyncio
async def test_same_cost_does_not_create_redundant_vigencia(client):
    files = {"file": ("o.csv", ORDERS, "text/csv")}
    await client.post("/api/import/orders", files=files)
    pid = (await client.get("/api/products")).json()[0]["id"]
    await client.put(f"/api/products/{pid}/cogs", json={"cogs_unit": 10})
    csv = b"sku,cost\nX,10\n"
    r = await client.post(
        "/api/import/cogs", files={"file": ("c.csv", csv, "text/csv")}
    )
    assert r.json()["updated"] == 0 and r.json()["products_created"] == 0


@pytest.mark.asyncio
async def test_cogs_csv_accepts_valid_from_and_rejects_bad_date(client):
    await client.post(
        "/api/import/orders", files={"file": ("o.csv", ORDERS, "text/csv")}
    )
    good = b"sku,cost,valid_from\nX,15,2026-03-01\n"
    r = await client.post(
        "/api/import/cogs", files={"file": ("c.csv", good, "text/csv")}
    )
    assert r.status_code == 200
    bad = b"sku,cost,valid_from\nX,15,no-es-fecha\n"
    r = await client.post(
        "/api/import/cogs", files={"file": ("c.csv", bad, "text/csv")}
    )
    assert r.status_code == 422
