"""Devoluciones/cancelaciones (RMA) y logística (envíos)."""

from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.tiktok_shop_ops import (
    build_return_cases,
    build_shipments,
    sku_by_order_line,
)
from app.connectors.tiktok_shop_sync import build_order_lines
from app.db import create_dev_schema, get_session, make_engine
from app.importers.persist import import_orders
from app.main import app
from app.ops_data import upsert_return_cases, upsert_shipments

H = 3600
T0 = int(datetime(2026, 11, 18, 15, 0, tzinfo=UTC).timestamp())


def _order(oid, status="DELIVERED", rts=2 * H, collected=5 * H, delivered=3 * 86400, due=24 * H, carrier="UPS"):
    return {
        "id": oid,
        "status": status,
        "create_time": T0,
        "rts_time": T0 + rts if rts is not None else None,
        "collection_time": T0 + collected if collected is not None else None,
        "delivery_time": T0 + delivered if delivered is not None else None,
        "shipping_due_time": T0 + due,
        "shipping_type": "SELLER",
        "is_sample_order": oid == "S1",
        "line_items": [
            {
                "id": f"L-{oid}",
                "seller_sku": "A",
                "product_name": "Prod A",
                "original_price": "50",
                "shipping_provider_name": carrier,
                "tracking_number": "1Z",
            }
        ],
    }


RETURN = {
    "return_id": "R1",
    "order_id": "O1",
    "create_time": T0,
    "update_time": T0 + H,
    "return_type": "RETURN_AND_REFUND",
    "return_status": "RETURN_OR_REFUND_REQUEST_COMPLETE",
    "return_reason": "wrong_product",
    "return_reason_text": "Wrong item was sent",
    "role": "BUYER",
    "refund_amount": {"refund_total": "40"},
    "return_line_items": [{"seller_sku": "A", "product_name": "Prod A", "refund_amount": {"refund_total": "40"}}],
}
WITHDRAWN = {**RETURN, "return_id": "R2", "return_status": "RETURN_OR_REFUND_REQUEST_CANCEL"}
CANCEL = {
    "cancel_id": "C1",
    "order_id": "O2",
    "create_time": T0,
    "cancel_type": "CANCEL",
    "role": "SELLER",
    "cancel_reason": "seller_cancel_reason_out_of_stock",
    "cancel_reason_text": "Out of stock",
    "refund_amount": {"refund_total": "25"},
    "cancel_line_items": [{"order_line_item_id": "L-O2", "refund_amount": {"refund_total": "25"}}],
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


def test_cases_map_returns_and_cancellations_with_sku_lookup():
    cases = {c["external_id"]: c for c in build_return_cases([RETURN], [CANCEL], sku_by_order_line([_order("O2")]))}
    r, c = cases["R1"], cases["C1"]
    assert r["kind"] == "return" and r["reason"] == "Wrong item was sent" and r["initiated_by"] == "buyer"
    assert r["refund_total"] == 40 and r["lines"][0]["sku"] == "A"
    assert c["kind"] == "cancellation" and c["initiated_by"] == "seller"
    assert c["lines"][0]["sku"] == "A"  # resuelto por el id de la línea de orden


def test_shipments_skip_unpaid_and_flag_samples():
    rows = build_shipments([_order("O1"), _order("S1"), _order("U", status="UNPAID")])
    assert [r["external_order_id"] for r in rows] == ["O1", "S1"]
    assert rows[1]["is_sample"] is True and rows[0]["carrier"] == "UPS"
    assert rows[0]["rts_at"] - rows[0]["ordered_at"] == datetime.fromtimestamp(2 * H, UTC) - datetime.fromtimestamp(0, UTC)


@pytest.mark.asyncio
async def test_returns_endpoint_rates_reasons_and_ignores_withdrawn(env):
    client, maker = env
    orders = [_order("O1"), _order("O2"), _order("O3"), _order("O4")]
    txs = [
        {
            "order_id": o["id"],
            "gross_sales_amount": "50",
            "seller_discount_amount": "0",
            "fee_amount": "-5",
            "shipping_cost_amount": "-3",
            "gross_sales_refund_amount": "0",
        }
        for o in orders
    ]
    df, _ = build_order_lines(orders, txs)
    async with maker() as s:
        await import_orders(s, df)
        cases = build_return_cases([RETURN, WITHDRAWN], [CANCEL], sku_by_order_line(orders))
        assert await upsert_return_cases(s, "tiktok_shop", "tiktok_shop_us", cases) == 3
        await upsert_return_cases(s, "tiktok_shop", "tiktok_shop_us", cases)  # idempotente
        await s.commit()

    body = (await client.get("/api/returns")).json()
    sm = body["summary"]
    assert sm["returns"] == 1 and sm["return_refund"] == 40  # la retirada no cuenta
    assert sm["returned_units"] == 1 and sm["units_sold"] == 4 and sm["return_rate"] == 0.25
    assert sm["cancellations"] == 1 and sm["seller_cancellations"] == 1
    assert {r["reason"] for r in body["by_reason"]} == {"Wrong item was sent", "Out of stock"}
    prod = body["by_product"][0]
    assert prod["sku"] == "A" and prod["returned_units"] == 1 and prod["cancelled_units"] == 1
    assert len(body["recent"]) == 3  # las lineas no se duplicaron al reimportar


@pytest.mark.asyncio
async def test_logistics_endpoint_handling_transit_and_late(env):
    client, maker = env
    rows = build_shipments(
        [
            _order("O1"),  # listo en 2 h (a tiempo), tránsito 3 d - 5 h
            _order("O2", rts=30 * H, collected=31 * H, delivered=2 * 86400 + 31 * H),  # tarde: venció a las 24 h
            _order("O3", status="AWAITING_SHIPMENT", rts=None, collected=None, delivered=None),  # vencida
            _order("O4", status="CANCELLED"),
        ]
    )
    async with maker() as s:
        await upsert_shipments(s, "tiktok_shop", "tiktok_shop_us", rows)
        await s.commit()

    sm = (await client.get("/api/logistics")).json()["summary"]
    assert sm["shipments"] == 4 and sm["cancelled"] == 1
    assert sm["shipped_on_time"] == 1 and sm["shipped_late"] == 1 and sm["on_time_rate"] == 0.5
    assert sm["overdue_now"] == 1  # AWAITING_SHIPMENT con fecha límite pasada
    assert sm["handling_hours_avg"] == pytest.approx((2 + 30) / 2)
    assert sm["transit_days_avg"] is not None
    carriers = (await client.get("/api/logistics")).json()["by_carrier"]
    assert carriers[0]["carrier"] == "UPS" and carriers[0]["delivered"] == 2
