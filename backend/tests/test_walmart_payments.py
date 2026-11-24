"""Pagos y comisiones de Walmart: comisión real vs estimada, ajustes por mes y neto esperado (sintético)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, get_session, make_engine
from app.models import Account, OrderLine, Product, SalesOrder
from app.walmart_payments import is_estimated_fee, walmart_payments_report
from app.walmart_payments_api import router

TODAY = date(2026, 11, 20)

# (orden, día, venta, comisión, reembolso, etiqueta)
LINES = [
    ("W1", date(2026, 11, 10), 100, "15.0000", 0, 0),  # estimada: 15 % exacto
    ("W2", date(2026, 11, 12), 80, "10.4000", 0, 3),  # real (13 %), con etiqueta
    ("W3", date(2026, 10, 5), 33.33, "4.9995", 20, 0),  # estimada con 4 decimales, con reembolso
    ("W3", date(2026, 10, 5), 66.67, "11.2000", 0, 0),  # real, misma orden que una estimada
    ("W4", date(2026, 3, 1), 50, "7.5000", 0, 0),  # fuera de la ventana
]


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        acc = Account(channel_code="walmart", name="walmart_1")
        other = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        prods = [Product(sku=f"SKU{i}", name=f"Producto {i}") for i in range(len(LINES) + 1)]
        s.add_all([acc, other, *prods])
        await s.flush()
        orders: dict[str, SalesOrder] = {}
        for i, (ext, day, gross, fee, refund, label) in enumerate(LINES):
            if ext not in orders:
                orders[ext] = SalesOrder(account_id=acc.id, external_order_id=ext, order_date=day)
                s.add(orders[ext])
                await s.flush()
            s.add(
                OrderLine(
                    order_id=orders[ext].id,
                    account_id=acc.id,
                    order_date=day,
                    product_id=prods[i].id,
                    channel_sku=prods[i].sku,
                    units=1,
                    gross_sales=Decimal(str(gross)),
                    platform_fees=Decimal(fee),
                    refund_amount=Decimal(refund),
                    shipping_cost=Decimal(label),
                )
            )
        tiktok = SalesOrder(account_id=other.id, external_order_id="T1", order_date=date(2026, 11, 10))
        s.add(tiktok)
        await s.flush()
        s.add(
            OrderLine(
                order_id=tiktok.id,
                account_id=other.id,
                order_date=date(2026, 11, 10),
                product_id=prods[-1].id,
                channel_sku="T",
                units=1,
                gross_sales=Decimal(999),
                platform_fees=Decimal(99),
            )
        )
        await s.commit()
    yield m
    await engine.dispose()


def test_is_estimated_fee_matches_only_the_default_rate():
    assert is_estimated_fee(15.0, 100.0)
    assert is_estimated_fee(4.9995, 33.33)
    assert not is_estimated_fee(10.4, 80.0)
    assert not is_estimated_fee(0.0, 0.0)  # sin venta no hay nada que estimar


@pytest.mark.asyncio
async def test_report_splits_actual_and_estimated_fees(maker):
    async with maker() as s:
        r = await walmart_payments_report(s, days=60, today=TODAY)
    k = r["kpis"]
    assert r["window"] == {"start": "2026-09-22", "end": "2026-11-20"}
    assert k["orders"] == 3 and k["sales"] == pytest.approx(280.0)  # sin W4 ni TikTok
    assert k["fees_estimated"] == pytest.approx(15.0 + 4.9995)
    assert k["fees_actual"] == pytest.approx(10.4 + 11.2)
    assert k["fees"] == pytest.approx(41.5995)
    assert k["effective_pct"] == pytest.approx(41.5995 / 280)
    assert k["estimated_orders"] == 2 and k["estimated_orders_pct"] == pytest.approx(2 / 3)
    assert k["estimated_fees_pct"] == pytest.approx(19.9995 / 41.5995)


@pytest.mark.asyncio
async def test_report_adjustments_net_and_months(maker):
    async with maker() as s:
        r = await walmart_payments_report(s, days=60, today=TODAY)
    k = r["kpis"]
    assert k["refunds"] == 20 and k["shipping_labels"] == 3 and k["adjustments"] == 23
    assert k["net_expected"] == pytest.approx(280 - 41.5995 - 23)
    assert {a["key"]: a["amount"] for a in r["by_adjustment"]} == {"refunds": 20, "shipping_labels": 3}
    assert [m["month"] for m in r["by_month"]] == ["2026-10", "2026-11"]
    oct_, nov = r["by_month"]
    assert oct_["orders"] == 1 and oct_["estimated_orders"] == 1 and oct_["refunds"] == 20
    assert nov["orders"] == 2 and nov["shipping_labels"] == 3 and nov["fees_actual"] == pytest.approx(10.4)


@pytest.mark.asyncio
async def test_empty_window_has_no_percentages_and_lists_gaps(maker):
    async with maker() as s:
        r = await walmart_payments_report(s, days=7, today=date(2030, 1, 1))
    assert r["by_month"] == [] and r["by_adjustment"] == []
    assert r["kpis"]["effective_pct"] is None and r["kpis"]["estimated_orders_pct"] is None
    assert any("liquidaciones" in note for note in r["missing"])


@pytest.mark.asyncio
async def test_endpoint_validates_days_and_returns_report(maker):
    app = FastAPI()
    app.include_router(router)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get("/api/walmart/payments?days=365")
        assert ok.status_code == 200 and "kpis" in ok.json()
        assert (await client.get("/api/walmart/payments?days=3")).status_code == 422
