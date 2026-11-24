"""Utilidad por orden: cálculo, etiqueta Redo en el envío, causa de pérdida, filtros y resumen (datos sintéticos)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.costs import product_cost_component_id
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    Account,
    Channel,
    OrderLine,
    Product,
    ProductCostHistory,
    SalesOrder,
    ShippingLabelCost,
)
from app.order_profit import order_profit_report
from app.order_profit_api import router

DAY = date(2026, 11, 10)


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        tiktok = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        walmart = Account(channel_code="walmart", name="walmart_1")
        p1 = Product(sku="SKU1", name="Uno")
        p2 = Product(sku="SKU2", name="Dos")
        s.add_all([tiktok, walmart, p1, p2])
        await s.flush()
        comp_id = await product_cost_component_id(
            s
        )  # el esquema de desarrollo ya trae el componente
        s.add_all(
            [
                ProductCostHistory(
                    product_id=p1.id,
                    component_type_id=comp_id,
                    amount=Decimal(5),
                    valid_from=date(2026, 1, 1),
                ),
                ProductCostHistory(
                    product_id=p2.id,
                    component_type_id=comp_id,
                    amount=Decimal(20),
                    valid_from=date(2026, 1, 1),
                ),
            ]
        )

        async def order(acc, ext, day, lines, state=None):
            o = SalesOrder(
                account_id=acc.id,
                external_order_id=ext,
                order_date=day,
                ship_state=state,
            )
            s.add(o)
            await s.flush()
            for prod, units, gross, promo, fees, ship, refund in lines:
                s.add(
                    OrderLine(
                        order_id=o.id,
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

        # A1 (dos líneas): neto 90, comisiones 9, envío 3, COGS 10 + 20 -> utilidad 48.
        await order(
            tiktok,
            "A1",
            DAY,
            [(p1, 2, 60, 10, 5, 3, 0), (p2, 1, 40, 0, 4, 0, 0)],
            "Florida",
        )
        # A2: el envío (2 de la línea + 10 de etiqueta Redo) manda -> 15 - 1 - 12 - 5 = -3.
        await order(tiktok, "A2", DAY, [(p1, 1, 15, 0, 1, 2, 0)])
        # A3 (Walmart): el costo del producto manda -> 22 - 3 - 20 = -1.
        await order(walmart, "A3", DAY, [(p2, 1, 22, 0, 3, 0, 0)])
        # A4: reembolso total -> 20 - 2 - 20 - 5 = -7, otra fecha.
        await order(tiktok, "A4", date(2026, 11, 1), [(p1, 1, 20, 0, 2, 0, 20)])
        # A5: el id lleva % y _ para probar el escape -> 25 - 2 - 5 = 18.
        await order(tiktok, "X_50%OFF", DAY, [(p1, 1, 25, 0, 2, 0, 0)])
        s.add(
            ShippingLabelCost(source="redo", external_order_id="A2", amount=Decimal(10))
        )
        await s.commit()
    yield m
    await engine.dispose()


def _by_order(report):
    return {r["order"]: r for r in report["rows"]}


@pytest.mark.asyncio
async def test_profit_calculation(maker):
    async with maker() as s:
        rep = await order_profit_report(s, limit=50)
    rows = _by_order(rep)
    a1 = rows["A1"]
    assert a1["units"] == 3 and a1["gross_sales"] == 100 and a1["promo"] == 10
    assert (
        a1["fees"] == 9
        and a1["shipping"] == 3
        and a1["cogs"] == 30
        and a1["net_sales"] == 90
    )
    assert a1["profit"] == pytest.approx(48) and a1["margin"] == pytest.approx(48 / 90)
    assert (
        a1["state"] == "Florida"
        and a1["channel"] == "tiktok_shop"
        and a1["driver"] is None
    )
    assert sorted(a1["skus"]) == ["SKU1", "SKU2"]
    assert rows["X_50%OFF"]["profit"] == pytest.approx(18)


@pytest.mark.asyncio
async def test_redo_label_adds_to_shipping(maker):
    async with maker() as s:
        rep = await order_profit_report(s, limit=50)
    a2 = _by_order(rep)["A2"]
    assert a2["label_cost"] == 10 and a2["shipping"] == 12
    assert a2["profit"] == pytest.approx(-3)


@pytest.mark.asyncio
async def test_driver_is_biggest_cost_only_for_losses(maker):
    async with maker() as s:
        rep = await order_profit_report(s, limit=50)
    rows = _by_order(rep)
    assert (
        rows["A2"]["driver"] == "shipping"
        and rows["A2"]["driver_label"] == "El envío se comió el margen"
    )
    assert rows["A3"]["driver"] == "cogs"
    assert rows["A4"]["driver"] == "refunds"
    assert rows["A1"]["driver"] is None and rows["A1"]["driver_label"] is None
    by = {d["key"]: d for d in rep["by_driver"]}
    assert by["shipping"]["orders"] == 1 and by["shipping"]["amount"] == pytest.approx(
        3
    )
    assert by["refunds"]["amount"] == pytest.approx(7)


@pytest.mark.asyncio
async def test_only_loss_sort_and_paging(maker):
    async with maker() as s:
        loss = await order_profit_report(s, only_loss=True, sort="profit_asc", limit=2)
        nxt = await order_profit_report(
            s, only_loss=True, sort="profit_asc", limit=2, offset=2
        )
        best = await order_profit_report(s, sort="profit_desc", limit=1)
        old = await order_profit_report(s, sort="date_asc", limit=1)
        faceted = await order_profit_report(s, driver="cogs")
        ranged = await order_profit_report(s, profit_min=0, profit_max=20)
    assert loss["total"] == 3 and [r["order"] for r in loss["rows"]] == ["A4", "A2"]
    assert [r["order"] for r in nxt["rows"]] == ["A3"]
    assert best["rows"][0]["order"] == "A1" and old["rows"][0]["order"] == "A4"
    # Las facetas reducen filas y total, pero el resumen sigue siendo de todo el filtro.
    assert faceted["total"] == 1 and faceted["summary"]["orders"] == 5
    assert [r["order"] for r in ranged["rows"]] == ["X_50%OFF"]
    with pytest.raises(ValueError):
        await order_profit_report(s, sort="nope")


@pytest.mark.asyncio
async def test_search_by_order_or_sku_escapes_wildcards(maker):
    async with maker() as s:
        by_sku = await order_profit_report(s, q="sku2")
        by_order = await order_profit_report(s, q="a2")
        percent = await order_profit_report(s, q="%")
        underscore = await order_profit_report(s, q="_")
        none = await order_profit_report(s, q="zzz")
    assert sorted(r["order"] for r in by_sku["rows"]) == ["A1", "A3"]
    # Buscar por SKU conserva todas las líneas de la orden (A1 sigue valiendo 48).
    assert _by_order(by_sku)["A1"]["profit"] == pytest.approx(48)
    assert [r["order"] for r in by_order["rows"]] == ["A2"]
    assert [r["order"] for r in percent["rows"]] == ["X_50%OFF"]
    assert [r["order"] for r in underscore["rows"]] == ["X_50%OFF"]
    assert (
        none["total"] == 0
        and none["summary"]["orders"] == 0
        and none["summary"]["avg_profit"] is None
    )


@pytest.mark.asyncio
async def test_filters_by_date_channel_account(maker):
    async with maker() as s:
        recent = await order_profit_report(s, start=date(2026, 11, 5))
        walmart = await order_profit_report(s, channel=Channel.walmart)
        store = await order_profit_report(
            s, account="tiktok_shop_us", end=date(2026, 11, 5)
        )
    assert recent["summary"]["orders"] == 4
    assert [r["order"] for r in walmart["rows"]] == ["A3"]
    assert [r["order"] for r in store["rows"]] == ["A4"]


@pytest.mark.asyncio
async def test_summary_covers_whole_filter(maker):
    async with maker() as s:
        rep = await order_profit_report(s, limit=1)
    sm = rep["summary"]
    assert len(rep["rows"]) == 1 and rep["total"] == 5
    assert (
        sm["orders"] == 5
        and sm["loss_orders"] == 3
        and sm["loss_pct"] == pytest.approx(0.6)
    )
    assert sm["loss_total"] == pytest.approx(11) and sm[
        "profit_total"
    ] == pytest.approx(55)
    assert sm["avg_profit"] == pytest.approx(11)
    hist = {b["key"]: b["orders"] for b in rep["histogram"]}
    assert hist == {
        "lt_m10": 0,
        "m10_0": 3,
        "0_5": 0,
        "5_10": 0,
        "10_20": 1,
        "gt_20": 1,
    }
    assert rep["histogram"][0]["label"] == "< -$10"
    ch = {c["channel"]: c for c in rep["by_channel"]}
    assert ch["tiktok_shop"]["orders"] == 4 and ch["tiktok_shop"]["loss_orders"] == 2
    assert ch["walmart"]["profit"] == pytest.approx(-1)


@pytest.mark.asyncio
async def test_endpoint(maker):
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()  # el router aún no está en main.py: se prueba montado aparte
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.get("/api/orders/profit?limit=2&only_loss=true")
            assert res.status_code == 200, res.text
            assert res.json()["total"] == 3
            assert (await client.get("/api/orders/profit?limit=500")).status_code == 422
            assert (await client.get("/api/orders/profit?sort=bad")).status_code == 422
    finally:
        app.dependency_overrides.clear()
