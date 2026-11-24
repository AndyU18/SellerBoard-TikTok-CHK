"""Rentabilidad por creador, video y LIVE con datos sintéticos."""

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.creator_profit import CUT, RENEW, WATCH, creator_profit_report, verdict_for
from app.db import create_dev_schema, make_engine
from app.models import (
    Account,
    AffiliateOrder,
    CostComponentType,
    OrderLine,
    Product,
    ProductCostHistory,
    SalesOrder,
    SampleApplication,
    SampleLine,
    VideoPerformance,
)

TODAY = date(2026, 11, 20)


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        acc = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        prod = Product(sku="P1", name="Producto")
        s.add_all([acc, prod])
        await s.flush()
        comp_id = await s.scalar(select(CostComponentType.id).where(CostComponentType.code == "product_cost"))
        s.add(ProductCostHistory(product_id=prod.id, component_type_id=comp_id, amount=Decimal(10), valid_from=date(2000, 1, 1)))
        for i, day in enumerate([date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 10)], 1):
            oid = f"O{i}"
            order = SalesOrder(account_id=acc.id, external_order_id=oid, order_date=day)
            s.add(order)
            await s.flush()
            s.add(
                OrderLine(
                    order_id=order.id, account_id=acc.id, order_date=day, product_id=prod.id,
                    channel_sku="P1", units=1, gross_sales=Decimal(100), platform_fees=Decimal(15),
                )
            )
            s.add(
                AffiliateOrder(
                    account_id=acc.id, external_order_id=oid, sku_id="1", order_date=day,
                    creator_username="ana", content_type="VIDEO", content_id="V1", units=1,
                    commission_base=Decimal(100), commission=Decimal(10),
                )
            )
        # orden de afiliado aún sin liquidar en ventas
        s.add(
            AffiliateOrder(
                account_id=acc.id, external_order_id="O9", sku_id="1", order_date=date(2026, 11, 11),
                creator_username="ana", content_type="LIVE", content_id="L1", units=1,
                commission_base=Decimal(50), commission=Decimal(5),
            )
        )
        s.add(VideoPerformance(account_id=acc.id, video_id="V1", username="ana", title="Reseña", gmv=Decimal(300)))
        for creator, oid in (("ana", "S1"), ("bob", "S2")):
            s.add(SampleLine(account_id=acc.id, external_order_id=oid, order_date=date(2026, 10, 20), sku="P1", units=1))
            s.add(SampleApplication(account_id=acc.id, external_id=f"app-{oid}", creator_username=creator, external_order_id=oid))
        await s.commit()
    yield m
    await engine.dispose()


@pytest.mark.asyncio
async def test_creator_level_true_profit(maker):
    async with maker() as s:
        r = await creator_profit_report(s, level="creator", today=TODAY)
    rows = {x["key"]: x for x in r["rows"]}
    ana = rows["ana"]
    assert ana["orders"] == 4 and ana["orders_pending"] == 1
    assert ana["sales"] == 300 and ana["commission"] == 30 and ana["platform_fee"] == 15
    assert ana["cogs"] == 30 and ana["sample_cost"] == 10
    assert ana["profit_before_samples"] == 225 and ana["profit"] == 215
    assert ana["margin"] == pytest.approx(215 / 300) and ana["roi"] == pytest.approx(21.5)
    assert ana["payback_orders"] == 1 and ana["verdict"] == RENEW
    assert ana["series"][0]["week"] == "2026-10-19"  # la muestra pesa en su semana
    bob = rows["bob"]
    assert bob["orders"] == 0 and bob["profit"] == -10 and bob["verdict"] == CUT
    assert r["summary"]["never_sold_creators"] == 1 and r["summary"]["never_sold_cost"] == 10
    assert r["summary"]["affiliate_share_of_sales"] == pytest.approx(1.0)
    assert r["summary"]["verdict_counts"][RENEW] == 1


@pytest.mark.asyncio
async def test_video_and_live_levels_and_search(maker):
    async with maker() as s:
        v = await creator_profit_report(s, level="video", today=TODAY)
        live = await creator_profit_report(s, level="live", today=TODAY)
        none = await creator_profit_report(s, level="creator", q="zzz", today=TODAY)
    assert len(v["rows"]) == 1 and v["rows"][0]["label"] == "Reseña" and v["rows"][0]["sample_cost"] == 0
    assert v["rows"][0]["profit"] == 225 and v["notes"]
    assert live["rows"][0]["key"] == "L1" and live["rows"][0]["orders_pending"] == 1
    assert live["rows"][0]["verdict"] == WATCH and none["rows"] == [] and none["total"] == 0


def test_verdict_thresholds():
    kw = {"sample_cost": 0.0, "first_sample": None, "today": TODAY}
    assert verdict_for(orders=2, profit=500, margin=0.5, **kw) == WATCH
    assert verdict_for(orders=5, profit=-1, margin=-0.1, **kw) == CUT
    assert verdict_for(orders=5, profit=50, margin=0.10, **kw) == "Mantener"
    assert verdict_for(orders=5, profit=5, margin=0.01, **kw) == WATCH


@pytest.mark.asyncio
async def test_bad_level_raises(maker):
    async with maker() as s:
        with pytest.raises(ValueError):
            await creator_profit_report(s, level="x")
