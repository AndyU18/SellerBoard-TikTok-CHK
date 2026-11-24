"""Datos de las gráficas de operación: tendencia de devoluciones, matriz por producto y envío por canal (sintéticos)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.chartdata_operacion import returns_matrix, returns_trend, shipping_breakdown
from app.chartdata_operacion import router as chartdata_router
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    Account,
    OrderLine,
    Product,
    ReturnCase,
    ReturnCaseLine,
    SalesOrder,
    ShippingLabelCost,
)

START, END = date(2026, 10, 1), date(2026, 11, 20)


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        acc = Account(channel_code="tiktok_shop", name="tt")
        prod = Product(sku="SKU1", name="Producto")
        s.add_all([acc, prod])
        await s.flush()
        for ext, day, gross, ship in (("A1", date(2026, 10, 5), 50, 2), ("A2", date(2026, 11, 9), 40, 9)):
            order = SalesOrder(account_id=acc.id, external_order_id=ext, order_date=day)
            s.add(order)
            await s.flush()
            s.add(
                OrderLine(
                    order_id=order.id,
                    account_id=acc.id,
                    order_date=day,
                    product_id=prod.id,
                    channel_sku="SKU1",
                    units=5,
                    gross_sales=Decimal(gross),
                    shipping_cost=Decimal(ship),
                )
            )
        s.add(ShippingLabelCost(source="redo", external_order_id="A1", amount=Decimal(4)))
        for ext, day, status in (("r1", date(2026, 10, 6), "COMPLETE"), ("r2", date(2026, 11, 10), "X_CANCEL")):
            case = ReturnCase(
                account_id=acc.id, external_id=ext, kind="return", status=status, external_order_id="A1", case_date=day
            )
            s.add(case)
            await s.flush()
            s.add(ReturnCaseLine(case_id=case.id, sku="SKU1", product_name="Producto", units=2, refund_total=Decimal(20)))
        await s.commit()
    yield m
    await engine.dispose()


@pytest.mark.asyncio
async def test_returns_trend_by_week_ignores_withdrawn(maker):
    async with maker() as s:
        t = await returns_trend(s, start=START, end=END)
    assert t["granularity"] == "week"
    rows = {r["period"]: r for r in t["rows"]}
    assert rows["2026-10-05"]["returns"] == 1 and rows["2026-10-05"]["returned_units"] == 2
    assert rows["2026-10-05"]["return_rate"] == pytest.approx(2 / 5)
    assert sum(r["returns"] for r in t["rows"]) == 1  # la solicitud retirada no cuenta


@pytest.mark.asyncio
async def test_returns_matrix_by_product(maker):
    async with maker() as s:
        m = await returns_matrix(s, start=START, end=END)
    assert m["rows"][0]["sku"] == "SKU1" and m["rows"][0]["total"] == 2
    assert len(m["rows"][0]["values"]) == len(m["periods"])


@pytest.mark.asyncio
async def test_shipping_breakdown_by_channel_and_band(maker):
    async with maker() as s:
        b = await shipping_breakdown(s, start=START, end=END)
    ch = b["by_channel"][0]
    assert ch["key"] == "tiktok_shop" and ch["orders"] == 2 and ch["shipping"] == pytest.approx(15)
    bands = {r["key"]: r for r in b["by_band"]}
    assert bands["$5 a $8"]["orders"] == 1  # A1: 2 + 4 de etiqueta
    assert bands["$8 a $12"]["orders"] == 1  # A2: 9
    assert sum(r["orders"] for r in b["by_band"]) == 2


@pytest.mark.asyncio
async def test_endpoints(maker):
    async def override():
        async with maker() as s:
            yield s

    api = FastAPI()
    api.include_router(chartdata_router)
    api.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
        for path in ("returns-trend", "returns-matrix", "shipping-breakdown"):
            res = await client.get(f"/api/chartdata/{path}?start=2026-10-01&end=2026-11-20")
            assert res.status_code == 200, (path, res.text)
        assert (await client.get("/api/chartdata/shipping-breakdown?channel=nope")).status_code == 422
