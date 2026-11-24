"""Utilidad en riesgo por costo faltante: SKU con y sin costo, mediana de respaldo, resumen, proveedor y API."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import cost_risk as cr
from app.action_center import collect
from app.cost_risk_api import router
from app.costs import set_product_cost
from app.db import create_dev_schema, get_session, make_engine
from app.models import Account, OrderLine, Product, SalesOrder

TODAY = date(2026, 11, 20)
DAY = date(2026, 11, 10)


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        s.add_all(
            [
                Account(channel_code="tiktok_shop", name="tiktok_shop_us"),
                Account(channel_code="walmart", name="walmart_1"),
            ]
        )
        await s.commit()
    yield m
    await engine.dispose()


async def _sell(s, sku, *, units, gross, cost=None, account="tiktok_shop_us", fees=0, day=DAY):
    """Crea el producto (con costo si se da) y una orden de una línea."""
    prod = await s.scalar(select(Product).where(Product.sku == sku))
    if prod is None:
        prod = Product(sku=sku, name=f"Producto {sku}")
        s.add(prod)
        await s.flush()
        if cost is not None:
            await set_product_cost(s, prod.id, Decimal(str(cost)))
    acc = await s.scalar(select(Account).where(Account.name == account))
    order_row = SalesOrder(account_id=acc.id, external_order_id=f"{sku}-{units}-{gross}", order_date=day)
    s.add(order_row)
    await s.flush()
    s.add(
        OrderLine(
            order_id=order_row.id,
            account_id=acc.id,
            order_date=day,
            product_id=prod.id,
            channel_sku=sku,
            units=units,
            gross_sales=Decimal(gross),
            platform_fees=Decimal(fees),
        )
    )
    await s.commit()


async def _seed_costed(s):
    """Tres SKU con costo: razones COGS/venta de 0.2, 0.3 y 0.5 (mediana 0.3)."""
    await _sell(s, "A", units=10, gross=100, cost=2)  # cogs 20 / 100
    await _sell(s, "B", units=10, gross=100, cost=3)  # 30 / 100
    await _sell(s, "C", units=10, gross=100, cost=5)  # 50 / 100


@pytest.mark.asyncio
async def test_missing_sku_estimated_with_median_ratio(maker):
    async with maker() as s:
        await _seed_costed(s)
        await _sell(s, "X", units=5, gross=200, fees=20)
        report = await cr.cost_risk_report(s, days=30, today=TODAY)
    summary, (row,) = report["summary"], report["skus"]
    assert summary["cost_ratio_source"] == "median" and summary["cost_ratio"] == 0.3
    assert row["sku"] == "X" and row["reported_profit"] == 180.0  # 200 - 20, costo 0
    assert row["est_cogs"] == 60.0 and row["est_unit_cost"] == 12.0
    assert row["adjusted_profit"] == 120.0 and row["profit_at_risk"] == 60.0
    assert row["priority"] == 1 and row["cumulative_sales_share"] == 1.0


@pytest.mark.asyncio
async def test_fallback_ratio_when_few_costed_skus(maker):
    async with maker() as s:
        await _sell(s, "A", units=10, gross=100, cost=2)  # una sola muestra: no alcanza
        await _sell(s, "X", units=1, gross=100)
        report = await cr.cost_risk_report(s, days=30, today=TODAY)
    assert report["summary"]["cost_ratio_source"] == "fallback"
    assert report["summary"]["cost_ratio"] == cr.FALLBACK_COST_RATIO
    assert report["skus"][0]["est_cogs"] == pytest.approx(100 * cr.FALLBACK_COST_RATIO)


@pytest.mark.asyncio
async def test_summary_shares_and_load_order(maker):
    async with maker() as s:
        await _seed_costed(s)  # ventas 300, utilidad 300 - 100 = 200
        await _sell(s, "BIG", units=10, gross=500)
        await _sell(s, "MID", units=10, gross=300)
        await _sell(s, "LOW", units=10, gross=200)
        report = await cr.cost_risk_report(s, days=30, today=TODAY)
    sm, rows = report["summary"], report["skus"]
    assert [r["sku"] for r in rows] == ["BIG", "MID", "LOW"]
    assert [r["priority"] for r in rows] == [1, 2, 3]
    assert [r["cumulative_sales_share"] for r in rows] == [0.5, 0.8, 1.0]
    assert sm["missing_skus"] == 3 and sm["net_sales_total"] == 1300.0
    assert sm["pct_sales_missing"] == pytest.approx(1000 / 1300)
    assert sm["reported_profit_total"] == 1200.0 and sm["reported_profit_missing"] == 1000.0
    assert sm["profit_at_risk"] == 300.0 and sm["adjusted_profit_total"] == 900.0
    assert sm["pct_profit_missing"] == pytest.approx(1000 / 1200)
    assert sm["overstated_pct"] == pytest.approx(0.25)
    assert sm["coverage"] == {"5": 1.0, "10": 1.0}


@pytest.mark.asyncio
async def test_all_skus_have_cost(maker):
    async with maker() as s:
        await _seed_costed(s)
        report = await cr.cost_risk_report(s, days=30, today=TODAY)
        actions = await cr.missing_cost_actions(s)
    sm = report["summary"]
    assert report["skus"] == [] and actions == []
    assert sm["missing_skus"] == 0 and sm["profit_at_risk"] == 0
    assert sm["pct_sales_missing"] == 0 and sm["coverage"] == {"5": None, "10": None}
    assert sm["adjusted_profit_total"] == sm["reported_profit_total"]


@pytest.mark.asyncio
async def test_no_sku_has_cost_uses_fallback(maker):
    async with maker() as s:
        await _sell(s, "X", units=1, gross=100)
        await _sell(s, "Y", units=1, gross=50)
        report = await cr.cost_risk_report(s, days=30, today=TODAY)
    sm = report["summary"]
    assert sm["cost_ratio_source"] == "fallback" and sm["cost_ratio_samples"] == 0
    assert sm["pct_sales_missing"] == 1.0 and sm["missing_skus"] == 2


@pytest.mark.asyncio
async def test_empty_window_has_no_shares(maker):
    async with maker() as s:
        report = await cr.cost_risk_report(s, days=7, today=TODAY)
    assert report["skus"] == []
    assert report["summary"]["pct_sales_missing"] is None
    assert report["summary"]["overstated_pct"] is None


@pytest.mark.asyncio
async def test_window_channel_and_account_filters(maker):
    async with maker() as s:
        await _sell(s, "X", units=1, gross=100, account="tiktok_shop_us")
        await _sell(s, "W", units=1, gross=100, account="walmart_1")
        by_channel = await cr.cost_risk_report(s, days=30, channel=cr.Channel.walmart, today=TODAY)
        by_account = await cr.cost_risk_report(s, days=30, account="tiktok_shop_us", today=TODAY)
        too_short = await cr.cost_risk_report(s, days=1, today=TODAY)  # la venta fue el 10/11
        with pytest.raises(ValueError):
            await cr.cost_risk_report(s, days=0, today=TODAY)
    assert [r["sku"] for r in by_channel["skus"]] == ["W"]
    assert [r["sku"] for r in by_account["skus"]] == ["X"]
    assert too_short["skus"] == []


@pytest.mark.asyncio
async def test_provider_ranks_by_risk_and_caps_at_top(maker):
    async with maker() as s:
        for i in range(cr.PROVIDER_TOP + 3):
            await _sell(s, f"S{i:02d}", units=1, gross=100 + i, day=date.today())
        items, errors = await collect(s, {"missing_costs"})
    assert errors == [] and len(items) == cr.PROVIDER_TOP
    assert all(i.target == "costs" and i.key == f"missing_costs:{i.entity}" for i in items)
    top = max(items, key=lambda i: i.impact_usd)
    assert top.entity == f"S{cr.PROVIDER_TOP + 2:02d}"
    expected = (100 + cr.PROVIDER_TOP + 2) * cr.FALLBACK_COST_RATIO * cr.PROJECTION_DAYS / cr.PROVIDER_DAYS
    assert top.impact_usd == pytest.approx(expected, abs=0.01)


@pytest.mark.asyncio
async def test_endpoint(maker):
    async with maker() as s:
        await _sell(s, "X", units=5, gross=200, day=date.today())

    app = FastAPI()
    app.include_router(router)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/costs/risk", params={"days": 365, "account": "tiktok_shop_us"})
        bad = await client.get("/api/costs/risk", params={"days": 1})
        bad_channel = await client.get("/api/costs/risk", params={"channel": "nope"})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"summary", "skus"}
    assert [r["sku"] for r in body["skus"]] == ["X"]
    assert bad.status_code == 422 and bad_channel.status_code == 422
