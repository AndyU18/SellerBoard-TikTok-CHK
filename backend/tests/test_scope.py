"""Los reportes se pueden filtrar por canal y por tienda (cuenta)."""

from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.tiktok_shop_ops import build_traffic
from app.connectors.tiktok_shop_sync import build_order_lines, build_settlements
from app.db import create_dev_schema, get_session, make_engine
from app.importers.persist import import_orders
from app.main import app
from app.ops_data import upsert_traffic
from app.settlements import upsert_settlements

T0 = int(datetime(2026, 11, 10, 12, 0, tzinfo=UTC).timestamp())


def _order(oid, price):
    return {
        "id": oid,
        "status": "DELIVERED",
        "create_time": T0,
        "line_items": [{"seller_sku": "A", "product_name": "Prod A", "original_price": str(price)}],
    }


def _tx(oid, gross, promo):
    return {
        "order_id": oid,
        "gross_sales_amount": str(gross),
        "seller_discount_amount": str(-promo),
        "fee_amount": "-5",
        "shipping_cost_amount": "-3",
        "gross_sales_refund_amount": "0",
    }


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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reports_filter_by_channel_and_store(env):
    client, maker = env
    interval = {
        "start_date": "2026-11-10",
        "product_impressions": 100,
        "product_page_views": 10,
        "buyers": 1,
        "orders": 1,
        "units_sold": 1,
        "gmv": {"amount": "50", "currency": "USD"},
    }
    async with maker() as s:
        for account, promo in (("tt_a", 10), ("tt_b", 30)):
            frame, _ = build_order_lines([_order(f"O-{account}", 100)], [_tx(f"O-{account}", 100, promo)], account)
            await import_orders(s, frame)
        await upsert_traffic(s, "tiktok_shop", "tt_a", build_traffic([interval]))
        await upsert_settlements(
            s,
            "tiktok_shop",
            "tt_b",
            build_settlements(
                [{"id": 1, "statement_time": T0, "payment_status": "PAID", "settlement_amount": "90"}]
            ),
        )
        await s.commit()

    def promos(body):
        return sorted(r["promo"] for r in body["rows"])

    assert promos((await client.get("/api/promotions")).json()) == [40]  # agrupa por canal y producto
    assert promos((await client.get("/api/promotions?account=tt_b")).json()) == [30]
    assert promos((await client.get("/api/promotions?channel=tiktok_shop&account=tt_a")).json()) == [10]
    assert (await client.get("/api/promotions?channel=walmart")).json()["rows"] == []

    # Los reportes de datos solo-TikTok también respetan tienda y canal.
    assert (await client.get("/api/funnel?account=tt_a")).json()["days"] == 1
    assert (await client.get("/api/funnel?account=tt_b")).json()["days"] == 0
    assert (await client.get("/api/funnel?channel=amazon")).json()["days"] == 0
    assert (await client.get("/api/cashflow?account=tt_b")).json()["summary"]["received"] == 90
    assert (await client.get("/api/cashflow?account=tt_a")).json()["summary"]["received"] == 0
    assert (await client.get("/api/cashflow?channel=walmart")).json()["summary"]["statements"] == 0
    assert (await client.get("/api/dayparting?account=tt_a")).json()["coverage"]["total"] == 1
    assert (await client.get("/api/returns?channel=walmart")).json()["summary"]["returns"] == 0
