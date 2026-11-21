"""Promociones/subsidios, dayparting y flujo de caja (liquidaciones)."""

from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.connectors.tiktok_shop_sync import build_order_lines, build_settlements
from app.db import create_dev_schema, get_session, make_engine
from app.gold import refresh_daily_profit
from app.importers.persist import import_orders
from app.main import app
from app.settlements import upsert_settlements

# 2026-11-18 15:30 UTC = miércoles 10:30 en Nueva York (EST, UTC-5)
T0 = int(datetime(2026, 11, 18, 15, 30, tzinfo=UTC).timestamp())


def _order(oid, price, platform="0", create_time=T0):
    return {
        "id": oid,
        "status": "DELIVERED",
        "create_time": create_time,
        "line_items": [
            {
                "seller_sku": "A",
                "product_name": "Prod A",
                "original_price": str(price),
                "platform_discount": platform,
            }
        ],
    }


def _tx(oid, gross, seller_disc):
    return {
        "order_id": oid,
        "gross_sales_amount": str(gross),
        "seller_discount_amount": str(seller_disc),
        "fee_amount": "-5",
        "shipping_cost_amount": "-3",
        "gross_sales_refund_amount": "0",
    }


@pytest_asyncio.fixture
async def env(monkeypatch):
    monkeypatch.setattr(settings, "report_timezone", "America/New_York")
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


# --- mapeo -------------------------------------------------------------------
def test_lines_carry_platform_discount_list_amount_and_order_time():
    df, _ = build_order_lines([_order("1", 100, platform="10")], [_tx("1", 100, -20)])
    row = df.to_dicts()[0]
    assert row["platform_discount"] == pytest.approx(10)
    assert row["list_amount"] == pytest.approx(100)
    assert row["ordered_at"] == datetime.fromtimestamp(T0, tz=UTC)
    assert row["promo"] == pytest.approx(20)  # el descuento del vendedor sigue aparte


def test_statements_become_settlements_with_positive_costs_and_lowercase_status():
    [s] = build_settlements(
        [
            {
                "id": 77,
                "statement_time": T0,
                "payment_time": T0 + 86400,
                "payment_status": "PAID",
                "revenue_amount": "861.81",
                "fee_amount": "-166.2",
                "shipping_cost_amount": "0",
                "adjustment_amount": "0",
                "settlement_amount": "695.61",
            }
        ]
    )
    assert s["external_id"] == "77" and s["status"] == "paid"
    assert s["fees_amount"] == pytest.approx(166.2)
    assert s["net_payout"] == pytest.approx(695.61)
    assert s["payout_date"].isoformat() == "2026-11-19"


# --- endpoints ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_promotions_separates_seller_promo_from_platform_subsidy(env):
    client, maker = env
    df, _ = build_order_lines([_order("1", 100, platform="10")], [_tx("1", 100, -20)])
    async with maker() as s:
        await import_orders(s, df)
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()

    body = (await client.get("/api/promotions")).json()
    row = body["rows"][0]
    assert row["sku"] == "A" and row["channel"] == "tiktok_shop"
    assert row["promo"] == 20 and row["platform_discount"] == 10
    assert row["discount_depth"] == pytest.approx(0.30)
    assert row["subsidy_share"] == pytest.approx(1 / 3)
    assert row["deep_discount"] is True
    assert body["totals"]["platform_discount"] == 10


@pytest.mark.asyncio
async def test_dayparting_buckets_by_local_weekday_and_hour(env):
    client, maker = env
    orders = [_order("1", 50), _order("2", 50), _order("3", 50, create_time=T0 + 3600)]
    txs = [_tx(o["id"], 50, 0) for o in orders]
    df, _ = build_order_lines(orders, txs)
    async with maker() as s:
        await import_orders(s, df)
        await s.commit()

    body = (await client.get("/api/dayparting")).json()
    assert body["timezone"] == "America/New_York"
    assert body["coverage"] == {"with_time": 3, "total": 3}
    cells = {(c["weekday"], c["hour"]): c for c in body["cells"]}
    assert cells[(2, 10)]["orders"] == 2 and cells[(2, 10)]["gross_sales"] == 100
    assert cells[(2, 11)]["orders"] == 1
    assert body["best_hours"][0]["hour"] == 10


@pytest.mark.asyncio
async def test_dayparting_reports_orders_without_time(env):
    client, maker = env
    df, _ = build_order_lines([_order("1", 50)], [_tx("1", 50, 0)])
    async with maker() as s:
        await import_orders(s, df.drop("ordered_at"))  # como un CSV: sin hora
        await s.commit()
    body = (await client.get("/api/dayparting")).json()
    assert body["coverage"] == {"with_time": 0, "total": 1} and body["cells"] == []


@pytest.mark.asyncio
async def test_cashflow_splits_received_and_pending_and_upsert_is_idempotent(env):
    client, maker = env
    rows = build_settlements(
        [
            {
                "id": 1,
                "statement_time": T0,
                "payment_time": T0 + 86400,
                "payment_status": "PAID",
                "revenue_amount": "100",
                "fee_amount": "-10",
                "settlement_amount": "90",
            },
            {
                "id": 2,
                "statement_time": T0 + 7 * 86400,
                "payment_status": "PROCESSING",
                "revenue_amount": "50",
                "fee_amount": "-5",
                "settlement_amount": "45",
            },
        ]
    )
    async with maker() as s:
        await upsert_settlements(s, "tiktok_shop", "tiktok_shop_us", rows)
        await s.commit()
    body = (await client.get("/api/cashflow")).json()
    assert body["summary"]["received"] == 90 and body["summary"]["pending"] == 45

    async with maker() as s:  # la liquidación 2 se paga en la siguiente sincronización
        rows[1]["status"], rows[1]["payout_date"] = "paid", rows[1]["statement_date"]
        await upsert_settlements(s, "tiktok_shop", "tiktok_shop_us", rows)
        await s.commit()
    body = (await client.get("/api/cashflow")).json()
    assert body["summary"]["statements"] == 2  # no se duplicó
    assert body["summary"]["received"] == 135 and body["summary"]["pending"] == 0
    assert body["by_month"][0]["month"] == "2026-11"
