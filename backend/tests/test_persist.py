from decimal import Decimal

import polars as pl
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, make_engine
from app.importers.canonical import normalize_ad_spend, normalize_orders, read_table
from app.importers.persist import import_ad_spend, import_orders
from app.models import Account, AdSpendDaily, OrderLine, Product, SalesOrder
from app.profit import profit_by_channel

ORDERS = (
    b"date,channel,account,order_id,sku,units,sales,fees\n"
    b"2026-11-18,amazon,amazon_1,111-1,GRN-100,2,100.00,15.00\n"
    b"2026-11-18,amazon,amazon_2,111-1,GRN-100,1,50.00,7.50\n"
    b"2026-11-18,tiktok_shop,tt_1,TT-9,GRN-100,1,30.00,4.50\n"
)


@pytest_asyncio.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def _count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
async def test_import_creates_accounts_and_one_master_product(session):
    df = normalize_orders(read_table(ORDERS, "o.csv"))
    result = await import_orders(session, df)
    assert result.inserted == 3 and result.updated == 0
    assert result.accounts_created == 3
    assert await _count(session, Account) == 3
    assert await _count(session, Product) == 1  # mismo SKU en 3 cuentas -> 1 producto


@pytest.mark.asyncio
async def test_reimport_is_idempotent(session):
    df = normalize_orders(read_table(ORDERS, "o.csv"))
    await import_orders(session, df)
    again = await import_orders(session, df)
    assert again.inserted == 0 and again.updated == 3
    assert await _count(session, OrderLine) == 3
    assert await _count(session, SalesOrder) == 3  # una cabecera por cuenta+orden


@pytest.mark.asyncio
async def test_duplicate_rows_in_file_are_summed(session):
    csv = (
        b"date,channel,account,order_id,sku,units,sales\n"
        b"2026-11-18,amazon,a1,1,X,1,10.00\n"
        b"2026-11-18,amazon,a1,1,X,2,20.00\n"
    )
    await import_orders(session, normalize_orders(read_table(csv, "o.csv")))
    line = await session.scalar(select(OrderLine))
    assert line.units == 3 and line.gross_sales == Decimal("30.00")


@pytest.mark.asyncio
async def test_ad_spend_reimport_replaces_range(session):
    ads = b"date,channel,account,spend\n2026-11-18,tiktok,tt_1,10\n2026-11-19,tiktok,tt_1,20\n"
    df = normalize_ad_spend(read_table(ads, "a.csv"))
    await import_ad_spend(session, df)
    await import_ad_spend(session, df)
    assert await _count(session, AdSpendDaily) == 2
    total = await session.scalar(select(func.sum(AdSpendDaily.spend)))
    assert Decimal(total) == Decimal("30.00")


def test_normalized_orders_feed_profit_engine():
    df = normalize_orders(read_table(ORDERS, "o.csv")).with_columns(
        pl.lit(0.0).alias("ad_spend"), pl.lit(5.0).alias("cogs_unit")
    )
    out = {r["channel"]: r for r in profit_by_channel(df).to_dicts()}
    # Amazon: ventas 150 - fees 22.5 - cogs (3 u x 5 = 15) = 112.5
    assert out["amazon"]["net_profit"] == 112.5
    assert out["tiktok_shop"]["net_profit"] == 30.0 - 4.5 - 5.0
