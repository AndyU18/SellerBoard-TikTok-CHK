"""Base del simulador: economía unitaria real por SKU y canal (datos sintéticos)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.costs import set_product_cost
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    Account,
    AdSpendDaily,
    Channel,
    OrderLine,
    Product,
    SalesOrder,
    ShippingLabelCost,
)
from app.simulator import simulator_baseline
from app.simulator_api import router

TODAY = date(2026, 11, 20)


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        tt = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        wm = Account(channel_code="walmart", name="walmart_us")
        a, b, c = Product(sku="A", name="Producto A"), Product(sku="B", name="Producto B"), Product(sku="C", name="C")
        s.add_all([tt, wm, a, b, c])
        await s.flush()
        await set_product_cost(s, a.id, Decimal(10))
        await set_product_cost(s, c.id, Decimal(5))  # C no tiene ventas; B no tiene costo

        async def sell(acc, ext, prod, units, gross, promo, fees, ship, refund, day=date(2026, 11, 10)):
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
                    promo=Decimal(promo),
                    platform_fees=Decimal(fees),
                    shipping_cost=Decimal(ship),
                    refund_amount=Decimal(refund),
                )
            )

        await sell(tt, "T1", a, 2, 100, 10, 9, 4, 0)
        await sell(tt, "T2", a, 2, 100, 10, 9, 0, 20)  # la etiqueta de Redo se suma al envío
        await sell(wm, "W1", a, 1, 60, 0, 9, 6, 0)
        await sell(tt, "T3", b, 1, 40, 0, 4, 2, 0)
        await sell(tt, "OLD", a, 5, 500, 0, 50, 0, 0, day=date(2026, 1, 1))  # fuera de la ventana
        s.add(ShippingLabelCost(source="redo", external_order_id="T2", amount=Decimal(8)))
        s.add(AdSpendDaily(account_id=tt.id, product_id=a.id, spend_date=date(2026, 11, 12), spend=Decimal(12)))
        s.add(AdSpendDaily(account_id=tt.id, product_id=None, spend_date=date(2026, 11, 12), spend=Decimal(99)))
        await s.commit()
    yield m
    await engine.dispose()


@pytest.mark.asyncio
async def test_baseline_unit_economics(maker):
    async with maker() as s:
        res = await simulator_baseline(s, days=30, today=TODAY)
    by = {(r["sku"], r["channel"]): r for r in res["rows"]}
    a = by[("A", "tiktok_shop")]
    assert a["units"] == 4 and a["units_per_day"] == pytest.approx(4 / 30)
    assert a["avg_price"] == pytest.approx(50) and a["promo_per_unit"] == pytest.approx(5)
    assert a["fee_per_unit"] == pytest.approx(4.5) and a["fee_pct_of_net"] == pytest.approx(18 / 180)
    assert a["shipping_per_unit"] == pytest.approx((4 + 8) / 4)  # línea + etiqueta de Redo
    assert a["refund_per_unit"] == pytest.approx(5) and a["refund_rate"] == pytest.approx(20 / 180)
    assert a["cogs_per_unit"] == 10 and a["has_cost"] is True
    # utilidad = 180 neto - 18 comisiones - 12 envío - 20 reembolsos - 40 costo
    assert a["profit"] == pytest.approx(90) and a["profit_per_unit"] == pytest.approx(22.5)
    assert a["monthly_profit"] == pytest.approx(90)  # 30 días = un mes
    assert a["ad_spend"] == 12 and a["profit_after_ads"] == pytest.approx(78)
    assert a["profit"] == pytest.approx(90)  # la publicidad no entra en la base
    assert ("A", "walmart") in by and len(res["rows"]) == 3  # una fila por SKU y canal; C no aparece


@pytest.mark.asyncio
async def test_sku_without_cost_and_totals(maker):
    async with maker() as s:
        res = await simulator_baseline(s, days=30, today=TODAY)
    b = next(r for r in res["rows"] if r["sku"] == "B")
    assert b["has_cost"] is False and b["cogs_per_unit"] == 0 and b["profit_per_unit"] == pytest.approx(34)
    assert res["skus_without_cost"] == 1
    t = res["totals"]
    assert t["units"] == 6 and t["gross_sales"] == 300 and t["ad_spend"] == 12  # el gasto sin SKU no se asigna
    assert t["profit"] == pytest.approx(sum(r["profit"] for r in res["rows"]))


@pytest.mark.asyncio
async def test_filters_and_empty(maker):
    async with maker() as s:
        wm = await simulator_baseline(s, days=30, channel=Channel.walmart, today=TODAY)
        acc = await simulator_baseline(s, days=30, account="tiktok_shop_us", today=TODAY)
        empty = await simulator_baseline(s, days=30, today=date(2030, 1, 1))
    assert [r["sku"] for r in wm["rows"]] == ["A"] and wm["totals"]["units"] == 1
    assert {r["channel"] for r in acc["rows"]} == {"tiktok_shop"}
    assert empty["rows"] == [] and empty["skus"] == 0
    assert empty["totals"]["units"] == 0 and empty["totals"]["profit_per_unit"] is None
    assert empty["totals"]["monthly_profit"] == 0


@pytest.mark.asyncio
async def test_endpoint(maker):
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/simulator/baseline?days=30")
        assert res.status_code == 200, res.text
        assert "rows" in res.json() and "totals" in res.json()
        assert (await client.get("/api/simulator/baseline?days=3")).status_code == 422
        assert (await client.get("/api/simulator/baseline?channel=nope")).status_code == 422
