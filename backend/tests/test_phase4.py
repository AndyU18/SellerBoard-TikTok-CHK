"""Muestras a creadores: costo de la muestra vs utilidad de las ventas del creador."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.tiktok_shop_ops import (
    build_affiliate_orders,
    build_sample_applications,
    build_sample_lines,
)
from app.connectors.tiktok_shop_sync import build_order_lines
from app.costs import set_product_cost
from app.db import create_dev_schema, get_session, make_engine
from app.importers.persist import import_orders
from app.main import app
from app.models import Product, ShippingLabelCost
from app.ops_data import (
    upsert_affiliate_orders,
    upsert_sample_applications,
    upsert_sample_lines,
)

T0 = int(datetime(2026, 11, 10, 12, 0, tzinfo=UTC).timestamp())
DAY = 86400


def _order(oid, sample=False, status="DELIVERED", units=1, price="100", at=T0):
    return {
        "id": oid,
        "status": status,
        "create_time": at,
        "is_sample_order": sample,
        "line_items": [
            {"id": f"{oid}-{i}", "seller_sku": "A", "product_name": "Prod A", "original_price": price}
            for i in range(units)
        ],
    }


def _app(app_id, username, order_id="0", status="APPROVED"):
    return {
        "id": app_id,
        "status": status,
        "order_id": order_id,
        "commission_rate": "0.20",
        "creator": {"username": username, "follower_count": 1000, "gmv": {"amount": "50", "currency": "USD"}},
        "product": {"title": "Prod A"},
    }


def _aff(oid, username, at=T0 + DAY, actual=None):
    return {
        "id": oid,
        "create_time": at,
        "skus": [
            {
                "sku_id": "s1",
                "seller_sku": "A",
                "quantity": 1,
                "creator_username": username,
                "content_type": "VIDEO",
                "content_id": "v1",
                "commission_rate": "1450",
                "actual_commission_base": {"amount": "100", "currency": "USD"} if actual else {},
                "actual_paid_commission": {"amount": "14.5", "currency": "USD"} if actual else {},
                "estimated_commission_base": {"amount": "99", "currency": "USD"},
                "estimated_paid_commission": {"amount": "14", "currency": "USD"},
                "settlement_status": "SETTLED" if actual else "PENDING",
            }
        ],
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


def test_builders_map_samples_applications_and_commissions():
    lines = build_sample_lines(
        [_order("S1", sample=True, units=2), _order("S2", sample=True, status="CANCELLED"), _order("N")]
    )
    assert lines == [
        {
            "external_order_id": "S1",
            "order_date": datetime.fromtimestamp(T0, UTC).date(),
            "sku": "A",
            "product_name": "Prod A",
            "units": 2,
        }
    ]

    apps = {
        a["external_id"]: a
        for a in build_sample_applications([_app("1", "cr1", "S1"), _app("2", "cr2", status="PENDING")])
    }
    assert apps["1"]["external_order_id"] == "S1" and apps["2"]["external_order_id"] is None  # "0" = sin orden
    assert apps["1"]["commission_rate"] == pytest.approx(0.20) and apps["1"]["creator_gmv"] == 50

    estimated, actual = build_affiliate_orders([_aff("E", "cr1"), _aff("R", "cr1", actual=True)])
    assert estimated["commission"] == 14 and estimated["commission_base"] == 99  # aún estimada
    assert actual["commission"] == 14.5 and actual["commission_base"] == 100  # la real manda sobre la estimada
    assert actual["commission_rate"] == pytest.approx(0.145)


@pytest.mark.asyncio
async def test_samples_roi_by_creator(env):
    client, maker = env
    # Venta atribuida a cr1 (entró a ventas): 100 - 15 comisiones - 5 envío - 10 de costo = 70
    tx = {
        "order_id": "AO1",
        "gross_sales_amount": "100",
        "seller_discount_amount": "0",
        "fee_amount": "-15",
        "shipping_cost_amount": "-5",
        "gross_sales_refund_amount": "0",
    }
    frame, _ = build_order_lines([_order("AO1")], [tx])
    sample_orders = [_order("S1", sample=True, units=2), _order("S2", sample=True)]
    async with maker() as s:
        await import_orders(s, frame)
        product = await s.scalar(select(Product).where(Product.sku == "A"))
        await set_product_cost(s, product.id, Decimal(10), None)
        s.add(ShippingLabelCost(source="redo", external_order_id="S1", amount=Decimal(5)))
        await upsert_sample_lines(s, "tiktok_shop", "tiktok_shop_us", build_sample_lines(sample_orders))
        await upsert_sample_applications(
            s,
            "tiktok_shop",
            "tiktok_shop_us",
            build_sample_applications(
                [_app("1", "cr1", "S1"), _app("2", "cr2", "S2"), _app("3", "cr3", status="PENDING")]
            ),
        )
        affiliate = [_aff("AO1", "cr1", actual=True), _aff("OLD", "cr1", at=T0 - DAY)]  # OLD: antes de la muestra
        await upsert_affiliate_orders(s, "tiktok_shop", "tiktok_shop_us", build_affiliate_orders(affiliate))
        await s.commit()

    body = (await client.get("/api/samples")).json()
    creators = {c["creator"]: c for c in body["creators"]}
    cr1, cr2 = creators["cr1"], creators["cr2"]
    assert cr1["sample_cost"] == pytest.approx(25)  # 2 uds x $10 + $5 de etiqueta
    assert cr1["affiliate_orders"] == 1  # la venta previa a la muestra no cuenta
    assert cr1["affiliate_profit"] == pytest.approx(70)
    assert cr1["net"] == pytest.approx(45) and cr1["roi"] == pytest.approx(1.8)
    assert cr2["sample_cost"] == pytest.approx(10) and cr2["roi"] == pytest.approx(-1)
    sm = body["summary"]
    assert sm["creators"] == 2 and sm["creators_with_sales"] == 1 and sm["creators_without_sales"] == 1
    assert sm["sample_cost"] == pytest.approx(35) and sm["net"] == pytest.approx(70 - 35)
    assert sm["cost_not_in_profit"] == pytest.approx(35)  # ninguna orden de muestra entró a ventas
    assert {r["status"]: r["applications"] for r in body["applications_by_status"]} == {"APPROVED": 2, "PENDING": 1}
