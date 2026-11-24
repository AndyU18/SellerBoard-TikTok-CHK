"""Artículos del pedido del Dashboard: filtros, búsqueda, paginación, costo y ganancia (datos sintéticos)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.chartdata_pedidos import router
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    Account,
    CostComponentType,
    OrderLine,
    Product,
    ProductCostHistory,
    SalesOrder,
)


@pytest_asyncio.fixture
async def client():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        tt = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        wm = Account(channel_code="walmart", name="walmart_us")
        p1 = Product(sku="SKU1", name="Lámpara")
        p2 = Product(sku="SKU2", name="Taza")
        comp = CostComponentType(code="unit", name="Unitario")
        s.add_all([tt, wm, p1, p2, comp])
        await s.flush()
        s.add(
            ProductCostHistory(
                product_id=p1.id,
                component_type_id=comp.id,
                amount=Decimal(5),
                valid_from=date(2026, 1, 1),
            )
        )
        for acc, ext, day, prod, units, gross in (
            (tt, "T-100", date(2026, 11, 1), p1, 2, 50),
            (tt, "T-200", date(2026, 11, 2), p2, 1, 20),
            (wm, "W-300", date(2026, 11, 3), p1, 1, 30),
        ):
            order = SalesOrder(account_id=acc.id, external_order_id=ext, order_date=day)
            s.add(order)
            await s.flush()
            s.add(
                OrderLine(
                    order_id=order.id,
                    account_id=acc.id,
                    order_date=day,
                    product_id=prod.id,
                    channel_sku=prod.sku,
                    units=units,
                    gross_sales=Decimal(gross),
                    promo=Decimal(2),
                    platform_fees=Decimal(3),
                    shipping_cost=Decimal(4),
                    refund_amount=Decimal(0),
                )
            )
        await s.commit()

    app = FastAPI()
    app.include_router(router)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    await engine.dispose()


@pytest.mark.asyncio
async def test_lines_newest_first_with_cost_and_profit(client):
    data = (await client.get("/api/chartdata/order-lines")).json()
    assert data["total"] == 3 and [r["order"] for r in data["rows"]] == [
        "W-300",
        "T-200",
        "T-100",
    ]
    first = data["rows"][2]  # T-100: 2 unidades a costo 5
    assert first["cogs"] == 10 and first["net_profit"] == 50 - 2 - 3 - 4 - 0 - 10
    assert data["rows"][1]["cogs"] == 0  # SKU2 sin costo
    assert data["summary"]["units"] == 4 and data["summary"]["gross_sales"] == 100
    assert data["summary"]["net_profit"] == pytest.approx(
        sum(r["net_profit"] for r in data["rows"])
    )


@pytest.mark.asyncio
async def test_filters_search_and_paging(client):
    tiktok = (
        await client.get(
            "/api/chartdata/order-lines", params={"channel": "tiktok_shop"}
        )
    ).json()
    assert tiktok["total"] == 2
    store = (
        await client.get("/api/chartdata/order-lines", params={"account": "walmart_us"})
    ).json()
    assert [r["order"] for r in store["rows"]] == ["W-300"]
    dated = (
        await client.get(
            "/api/chartdata/order-lines",
            params={"start": "2026-11-02", "end": "2026-11-02"},
        )
    ).json()
    assert [r["order"] for r in dated["rows"]] == ["T-200"]
    by_order = (
        await client.get("/api/chartdata/order-lines", params={"q": "t-1"})
    ).json()
    assert [r["order"] for r in by_order["rows"]] == ["T-100"]
    by_sku = (
        await client.get("/api/chartdata/order-lines", params={"q": "sku1"})
    ).json()
    assert by_sku["total"] == 2 and by_sku["summary"]["units"] == 3
    # El comodín % se trata como texto, no como patrón.
    assert (await client.get("/api/chartdata/order-lines", params={"q": "%"})).json()[
        "total"
    ] == 0
    page = (
        await client.get("/api/chartdata/order-lines", params={"limit": 1, "offset": 1})
    ).json()
    assert page["total"] == 3 and [r["order"] for r in page["rows"]] == ["T-200"]
    assert page["summary"]["lines"] == 3
    assert (
        await client.get("/api/chartdata/order-lines", params={"limit": 0})
    ).status_code == 422
