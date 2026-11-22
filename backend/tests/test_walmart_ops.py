"""Walmart: devoluciones, desempeño del vendedor e inventario."""

from datetime import UTC, date, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.walmart import (
    build_listing_stock,
    build_order_lines,
    build_performance,
    build_return_cases,
)
from app.db import create_dev_schema, get_session, make_engine
from app.importers.persist import import_orders
from app.main import app
from app.ops_data import (
    upsert_listing_stock,
    upsert_return_cases,
    upsert_seller_performance,
)

NOW_MS = int(datetime.now(UTC).timestamp() * 1000)


def _return(rid, order, status, reason="INCORRECT_ITEM", price=39.99, qty=1, sku="A"):
    return {
        "returnOrderId": rid,
        "customerOrderId": order,
        "returnOrderDate": "2026-11-10T15:00:00.000Z",
        "refundMode": "FIRST_SCAN",
        "totalRefundAmount": {"currencyAmount": price * qty + 3.0, "currencyUnit": "USD"},
        "returnOrderLines": [
            {
                "returnReason": reason,
                "item": {"sku": sku, "productName": f"Prod {sku}"},
                "quantity": {"unitOfMeasure": "EA", "measurementValue": float(qty)},
                "charges": [
                    {"chargeName": "ItemPrice", "chargePerUnit": {"currencyAmount": price, "currencyUnit": "USD"}},
                    {"chargeName": "Shipping", "chargePerUnit": {"currencyAmount": 5.0, "currencyUnit": "USD"}},
                ],
                "status": status,
                "statusTime": "2026-11-12T10:00:00.000Z",
            }
        ],
    }


def _order(oid, sku, qty, price):
    return {
        "customerOrderId": oid,
        "orderDate": NOW_MS,
        "orderLines": {
            "orderLine": [
                {
                    "item": {"sku": sku, "productName": f"Prod {sku}"},
                    "orderLineQuantity": {"amount": str(qty)},
                    "charges": {"charge": [{"chargeType": "PRODUCT", "chargeAmount": {"amount": price}}]},
                    "orderLineStatuses": {"orderLineStatus": [{"status": "Delivered"}]},
                }
            ]
        },
    }


def test_build_return_cases_uses_item_price_and_marks_withdrawn():
    cases = build_return_cases(
        [_return("R1", "O1", "COMPLETED", qty=2), _return("R2", "O2", "CANCELLED", reason="DAMAGED")]
    )
    first, second = cases
    assert first["kind"] == "return" and first["external_order_id"] == "O1"
    assert first["refund_total"] == pytest.approx(79.98)  # precio x unidades, sin impuesto ni envío
    assert first["lines"][0]["units"] == 2
    assert first["case_date"] == date(2026, 11, 10)
    assert second["status"] == "BUYER_CANCEL"  # el reporte lo excluye como retirada


def test_build_return_cases_skips_rows_without_date_or_lines():
    broken = {"returnOrderId": "R9", "customerOrderId": "O9", "returnOrderLines": []}
    assert build_return_cases([broken]) == []


def test_completed_returns_reduce_profit_but_pending_ones_do_not():
    cases = build_return_cases(
        [_return("R1", "O1", "COMPLETED", qty=2), _return("R2", "O2", "INITIATED", sku="B")]
    )
    frame, _ = build_order_lines(
        [_order("O1", "A", 3, "120.00"), _order("O2", "B", 1, "40.00")], [], returns=cases
    )
    refunds = {row["order_id"]: row["refund_amount"] for row in frame.iter_rows(named=True)}
    assert refunds["O1"] == pytest.approx(79.98)  # devolución completada
    assert refunds["O2"] == 0  # iniciada: aún no hay reembolso


def test_build_performance_one_row_per_window():
    payload = {
        "30DAY": {"otd": 83.0, "ots": 98.8, "vtr": 95.5, "refundRateSellerFault": 2.0},
        "7DAY": {"otd": 80.0},
        "junk": "x",
    }
    rows = build_performance(payload, today=date(2026, 11, 22))
    assert [r["window_days"] for r in rows] == [7, 30]
    assert rows[1]["otd"] == 83.0 and rows[1]["snapshot_date"] == date(2026, 11, 22)
    assert rows[1]["refund_rate_seller_fault"] == 2.0


def test_build_listing_stock_sums_nodes_and_keeps_inventory_only_skus():
    items = [
        {
            "sku": "A",
            "productName": "Prod A",
            "price": {"amount": 29.99},
            "publishedStatus": "PUBLISHED",
            "unpublishedReasons": {"reason": ["x", "y"]},
        }
    ]
    inventories = [
        {
            "sku": "A",
            "nodes": [
                {"availToSellQty": {"amount": 3}, "reservedQty": {"amount": 1}, "inputQty": {"amount": 5}},
                {"availToSellQty": {"amount": 4}, "reservedQty": {"amount": 0}, "inputQty": {"amount": 4}},
            ],
        },
        {"sku": "B", "nodes": [{"availToSellQty": {"amount": 9}}]},
    ]
    rows = {r["sku"]: r for r in build_listing_stock(items, inventories)}
    assert rows["A"]["avail_to_sell"] == 7 and rows["A"]["reserved"] == 1
    assert rows["A"]["unpublished_reasons"] == "x; y"
    assert rows["B"]["avail_to_sell"] == 9 and rows["B"]["price"] is None


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
async def test_endpoints_return_walmart_data(env):
    client, maker = env
    frame, _ = build_order_lines([_order("O1", "A", 30, "300.00"), _order("O2", "B", 5, "50.00")], [])
    async with maker() as s:
        await import_orders(s, frame)
        await upsert_return_cases(s, "walmart", "walmart_1", build_return_cases([_return("R1", "O1", "COMPLETED")]))
        await upsert_seller_performance(
            s, "walmart", "walmart_1", build_performance({"30DAY": {"otd": 83.0, "ots": 99.0}}, date(2026, 11, 22))
        )
        stock = build_listing_stock(
            [
                {"sku": "A", "productName": "Prod A", "price": {"amount": 10}, "publishedStatus": "PUBLISHED"},
                {"sku": "B", "productName": "Prod B", "price": {"amount": 10}, "publishedStatus": "UNPUBLISHED"},
            ],
            [{"sku": "A", "nodes": [{"availToSellQty": {"amount": 0}}]}],
        )
        await upsert_listing_stock(s, "walmart", "walmart_1", stock)
        await s.commit()

    inv = (await client.get("/api/inventory", params={"channel": "walmart"})).json()
    by_sku = {r["sku"]: r for r in inv["rows"]}
    assert by_sku["A"]["status"] == "out_of_stock"  # se vende y no hay existencias
    assert by_sku["B"]["status"] == "unpublished"
    assert inv["summary"]["out_of_stock"] == 1
    assert inv["rows"][0]["sku"] == "A"  # lo más urgente primero

    perf = (await client.get("/api/seller-performance", params={"channel": "walmart"})).json()
    assert perf["accounts"][0]["windows"][0]["otd"] == 83.0
    assert perf["history"][0]["snapshot_date"] == "2026-11-22"

    ret = (await client.get("/api/returns", params={"channel": "walmart"})).json()
    assert ret["summary"]["returns"] == 1

    other = (await client.get("/api/inventory", params={"channel": "amazon"})).json()
    assert other["rows"] == []
