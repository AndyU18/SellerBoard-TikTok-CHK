import httpx
import polars as pl
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, get_session, make_engine
from app.main import app
from app.profit import abc_classes


def _products(values: dict[str, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "sku": list(values),
            "net_profit": list(values.values()),
            "gross_sales": [v * 2 for v in values.values()],
        }
    )


def test_abc_splits_at_80_and_95_percent_and_isolates_losses():
    # total positivo = 100: P1 60 (acum 60), P2 25 (85), P3 10 (95), P4 5 (100)
    out = abc_classes(
        _products({"P2": 25, "P1": 60, "P4": 5, "P3": 10, "L": -8}), "profit"
    )
    cls = dict(zip(out["sku"], out["abc_class"], strict=True))
    assert cls == {"P1": "A", "P2": "A", "P3": "B", "P4": "C", "L": "loss"}
    assert out["sku"].to_list()[0] == "P1"  # ordenado de mayor a menor
    p2 = out.filter(pl.col("sku") == "P2").row(0, named=True)
    assert p2["share"] == 0.25 and p2["cumulative_share"] == 0.85
    assert (
        out.filter(pl.col("sku") == "L")["share"][0] is None
    )  # la pérdida no pesa en el total


def test_abc_single_product_is_a_and_sales_metric_has_no_loss_class():
    assert abc_classes(_products({"X": 10}), "profit")["abc_class"].to_list() == ["A"]
    out = abc_classes(_products({"X": 10, "Y": -3}), "sales")
    assert dict(zip(out["sku"], out["abc_class"], strict=True)) == {"X": "A", "Y": "C"}


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


ORDERS = (
    b"date,channel,account,order_id,sku,units,sales\n"
    b"2026-11-01,tiktok_shop,tt1,T-1,BIG,1,90\n"
    b"2026-11-01,tiktok_shop,tt1,T-2,SMALL,1,10\n"
)


@pytest.mark.asyncio
async def test_pareto_endpoint_and_cost_history(client):
    r = await client.post(
        "/api/import/orders", files={"file": ("o.csv", ORDERS, "text/csv")}
    )
    assert r.status_code == 200, r.text

    pareto = (await client.get("/api/profit/pareto?metric=sales")).json()
    assert [row["sku"] for row in pareto["rows"]] == ["BIG", "SMALL"]
    assert (
        pareto["rows"][0]["abc_class"] == "A"
        and pareto["summary"]["A"]["products"] == 1
    )

    products = {p["sku"]: p for p in (await client.get("/api/products")).json()}
    assert products["BIG"]["has_cost"] is False

    pid = products["BIG"]["id"]
    assert (
        await client.put(f"/api/products/{pid}/cogs", json={"cogs_unit": 30})
    ).status_code == 200
    assert (
        await client.put(
            f"/api/products/{pid}/cogs",
            json={"cogs_unit": 35, "valid_from": "2026-11-10"},
        )
    ).status_code == 200

    history = (await client.get(f"/api/products/{pid}/cogs-history")).json()
    assert [h["amount"] for h in history] == [35.0, 30.0]  # más reciente primero
    assert (
        history[0]["valid_from"] == "2026-11-10"
        and history[0]["component"] == "product_cost"
    )
    assert {p["sku"]: p for p in (await client.get("/api/products")).json()}["BIG"][
        "has_cost"
    ] is True
