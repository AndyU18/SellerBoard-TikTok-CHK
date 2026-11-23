"""Gestor de SKU (claves, duplicados, fusión, alias) y pantalla de calidad de datos."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import polars as pl
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.costs import set_product_cost
from app.data_quality import data_quality_report, score_of
from app.db import create_dev_schema, get_session, make_engine
from app.gold import refresh_daily_profit
from app.importers.persist import import_ad_spend, import_cogs, import_orders
from app.models import (
    AdSpendDaily,
    ChannelListing,
    GoldDailyProfit,
    OrderLine,
    Product,
    ProductCostHistory,
    ReturnCase,
    ReturnCaseLine,
    SampleLine,
    SkuAlias,
)
from app.sku_api import router
from app.sku_manager import (
    SkuError,
    add_alias,
    duplicate_groups,
    merge_products,
    sku_key,
)

TODAY = datetime.now(UTC).date()


@pytest_asyncio.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    # App propia con solo este router: no depende de que main.py ya lo incluya.
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker
    await engine.dispose()


def frame(rows: list[dict]) -> pl.DataFrame:
    base = {
        "channel": "walmart",
        "account": "walmart_1",
        "product_name": None,
        "units": 1,
        "gross_sales": 0.0,
        "promo": 0.0,
        "platform_fees": 0.0,
        "shipping_cost": 0.0,
        "refund_amount": 0.0,
    }
    return pl.DataFrame([{**base, **r} for r in rows])


async def pid_of(session, sku: str) -> int:
    return await session.scalar(select(Product.id).where(Product.sku == sku))


async def set_costs(session, costs: dict[str, float]) -> None:
    for sku, amount in costs.items():
        await set_product_cost(session, await pid_of(session, sku), Decimal(str(amount)), None)


def line(order_id: str, sku: str, **kw) -> dict:
    return {"date": TODAY, "order_id": order_id, "sku": sku, "gross_sales": 20.0, "platform_fees": 3.0, **kw}


# ------------------------------------------------------------------------------ claves


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("XMAS8027", "XMAS-8027"),
        ("xmas-8027", "XMAS-8027"),
        ("XMAS-7702-1PK", "XMAS-7702"),
        ("GDL-187E", "GDL-187"),
        (" TOR 003 ", "TOR-003"),
    ],
)
def test_sku_key_unites_variants(a, b):
    assert sku_key(a) == sku_key(b)


def test_sku_key_keeps_different_products_apart():
    assert sku_key("XMAS-8027") != sku_key("XMAS-8028")
    assert sku_key("ABCE") == "ABCE"  # la E solo se quita tras un dígito
    assert sku_key("---") == "---"  # sin alfanuméricos: se conserva


# ------------------------------------------------------------------------------ duplicados


async def seed_duplicates(maker) -> None:
    df = frame(
        [
            line("O1", "XMAS8027", units=2, gross_sales=40.0),
            line("O1", "XMAS-8027", units=1, gross_sales=20.0),  # misma orden en ambos productos
            line("O2", "XMAS8027", units=3, gross_sales=60.0, date=TODAY - timedelta(days=1)),
            line("O3", "SOLO-1"),
            {**line("T1", "XMAS-8027"), "channel": "tiktok_shop", "account": "tt_1"},
        ]
    )
    async with maker() as s:
        await import_orders(s, df)
        await set_costs(s, {"XMAS-8027": 8.0, "XMAS8027": 8.0})
        await s.commit()


@pytest.mark.asyncio
async def test_duplicate_groups_and_suggestion(env):
    _, maker = env
    await seed_duplicates(maker)
    async with maker() as s:
        report = await duplicate_groups(s, days=30)
    assert report["count"] == 1  # SOLO-1 no tiene pareja
    group = report["groups"][0]
    assert {m["sku"] for m in group["members"]} == {"XMAS8027", "XMAS-8027"}
    assert group["total_units"] == 7 and group["total_sales"] == pytest.approx(140.0)
    # Ambos con costo: gana el de más ventas (XMAS8027: 100 vs 40)... salvo el formato con guion en empate.
    suggested = next(m for m in group["members"] if m["suggested"])
    assert suggested["sku"] == group["suggested_sku"] == "XMAS8027"
    hyphen = next(m for m in group["members"] if m["sku"] == "XMAS-8027")
    assert hyphen["channels"] == ["tiktok_shop", "walmart"] and hyphen["has_cost"] and hyphen["listings"] == 2


@pytest.mark.asyncio
async def test_suggestion_prefers_product_with_cost(env):
    _, maker = env
    async with maker() as s:
        await import_orders(s, frame([line("O1", "AB1", gross_sales=100.0), line("O2", "AB-1", gross_sales=10.0)]))
        await set_costs(s, {"AB-1": 3.0})
        await s.commit()
        report = await duplicate_groups(s)
    assert report["groups"][0]["suggested_sku"] == "AB-1"


# ------------------------------------------------------------------------------ fusión


@pytest.mark.asyncio
async def test_merge_moves_everything_without_losing_data(env):
    _, maker = env
    await seed_duplicates(maker)
    async with maker() as s:
        walmart = await s.scalar(select(ChannelListing.account_id).limit(1))
        s.add(AdSpendDaily(account_id=walmart, product_id=await pid_of(s, "XMAS8027"), spend_date=TODAY, spend=Decimal(4)))
        case = ReturnCase(account_id=walmart, external_id="R1", kind="return", external_order_id="O2", case_date=TODAY)
        s.add(case)
        await s.flush()
        s.add(ReturnCaseLine(case_id=case.id, sku="XMAS8027", units=1, refund_total=Decimal(20)))
        s.add(SampleLine(account_id=walmart, external_order_id="S1", order_date=TODAY, sku="XMAS8027", units=2))
        s.add(SampleLine(account_id=walmart, external_order_id="S1", order_date=TODAY, sku="XMAS-8027", units=1))
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()

    async with maker() as s:
        before_units = await s.scalar(select(func.sum(OrderLine.units)))
        before_sales = await s.scalar(select(func.sum(OrderLine.gross_sales)))
        source_id = await pid_of(s, "XMAS8027")
        stats = await merge_products(s, "XMAS8027", "XMAS-8027", "duplicado")
        await s.commit()

    assert stats["lines_moved"] == 1 and stats["lines_merged"] == 1  # O2 se mueve, O1 se suma
    assert stats["listings_moved"] == 1 and stats["ad_rows_moved"] == 1
    assert stats["cost_rows_dropped"] == 1 and stats["cost_rows_moved"] == 0  # el destino conserva su costo
    assert stats["returns_updated"] == 1 and stats["samples_merged"] == 1 and stats["gold_rows_deleted"] > 0

    async with maker() as s:
        target = await pid_of(s, "XMAS-8027")
        assert await pid_of(s, "XMAS8027") is None  # el origen ya no existe
        assert await s.scalar(select(func.sum(OrderLine.units))) == before_units  # nada se pierde
        assert await s.scalar(select(func.sum(OrderLine.gross_sales))) == before_sales
        o1 = (
            await s.scalars(select(OrderLine).where(OrderLine.product_id == target, OrderLine.gross_sales == Decimal(60)))
        ).all()
        assert len(o1) == 2 and {x.units for x in o1} == {3}  # O1 (40 + 20 sumadas) y O2 (60)
        assert await s.scalar(select(func.count(OrderLine.id)).where(OrderLine.product_id != target)) == 1  # SOLO-1
        assert await s.scalar(select(func.count(ChannelListing.id)).where(ChannelListing.product_id == target)) == 3
        assert await s.scalar(select(AdSpendDaily.product_id)) == target
        assert await s.scalar(select(ReturnCaseLine.sku)) == "XMAS-8027"
        sample = (await s.scalars(select(SampleLine))).one()
        assert sample.sku == "XMAS-8027" and sample.units == 3
        assert await s.scalar(select(func.count(ProductCostHistory.id)).where(ProductCostHistory.product_id == target)) == 1
        alias = (await s.scalars(select(SkuAlias))).one()
        assert alias.alias == "XMAS8027" and alias.product_id == target and alias.reason == "duplicado"
        assert await s.scalar(select(func.count(GoldDailyProfit.id)).where(GoldDailyProfit.product_id == source_id)) == 0


@pytest.mark.asyncio
async def test_merge_copies_cost_when_target_has_none_and_repoints_aliases(env):
    _, maker = env
    async with maker() as s:
        await import_orders(s, frame([line("O1", "AB1"), line("O2", "AB-1")]))
        await set_costs(s, {"AB1": 5.0})
        await add_alias(s, "AB1-OLD", "AB1")
        await s.commit()
    async with maker() as s:
        stats = await merge_products(s, "AB1", "AB-1")
        await s.commit()
    assert stats["cost_rows_moved"] == 1 and stats["aliases_repointed"] == 1
    async with maker() as s:
        target = await pid_of(s, "AB-1")
        assert await s.scalar(select(func.count(ProductCostHistory.id)).where(ProductCostHistory.product_id == target)) == 1
        aliases = {a.alias: a.product_id for a in (await s.scalars(select(SkuAlias))).all()}
        assert aliases == {"AB1-OLD": target, "AB1": target}


@pytest.mark.asyncio
async def test_merge_validation(env):
    _, maker = env
    async with maker() as s:
        await import_orders(s, frame([line("O1", "A-1")]))
        await s.commit()
        for src, dst in [("A-1", "A-1"), ("", "A-1"), ("NOPE", "A-1"), ("A-1", "NOPE")]:
            with pytest.raises(SkuError) as exc:
                await merge_products(s, src, dst)
            assert exc.value.problems
        both = await _problems(s, "X", "Y")
        assert len(both) == 2  # origen y destino inexistentes


async def _problems(session, src, dst) -> list[str]:
    with pytest.raises(SkuError) as exc:
        await merge_products(session, src, dst)
    return exc.value.problems


@pytest.mark.asyncio
async def test_import_with_alias_does_not_create_product(env):
    _, maker = env
    await seed_duplicates(maker)
    async with maker() as s:
        await merge_products(s, "XMAS8027", "XMAS-8027")
        await s.commit()
    async with maker() as s:
        products_before = await s.scalar(select(func.count(Product.id)))
        result = await import_orders(s, frame([line("N1", "XMAS8027", units=4, gross_sales=80.0)]))
        ads = await import_ad_spend(
            s, pl.DataFrame([{"channel": "walmart", "account": "walmart_1", "date": TODAY, "sku": "XMAS8027", "amount": 2.0}])
        )
        cogs = await import_cogs(
            s, pl.DataFrame([{"sku": "XMAS8027", "product_name": None, "cogs_unit": 9.5, "valid_from": TODAY}])
        )
        await s.commit()
        assert result.products_created == 0 and ads.products_created == 0 and cogs.products_created == 0
        assert await s.scalar(select(func.count(Product.id))) == products_before
        assert await pid_of(s, "XMAS8027") is None
        target = await pid_of(s, "XMAS-8027")
        new_line = (await s.scalars(select(OrderLine).where(OrderLine.units == 4))).one()
        assert new_line.product_id == target and new_line.units == 4
        assert await s.scalar(select(AdSpendDaily.product_id).where(AdSpendDaily.spend == Decimal(2))) == target
        assert cogs.updated == 1  # el costo se aplicó al maestro


@pytest.mark.asyncio
async def test_add_alias_rules(env):
    _, maker = env
    async with maker() as s:
        await import_orders(s, frame([line("O1", "A-1"), line("O2", "B-1")]))
        row = await add_alias(s, " A1 ", "A-1", "SKU de Walmart")
        assert row["alias"] == "A1" and row["sku"] == "A-1"
        for alias, target in [("A1", "A-1"), ("B-1", "A-1"), ("A-1", "A-1"), ("ZZ", "NOPE"), ("", "A-1")]:
            with pytest.raises(SkuError):
                await add_alias(s, alias, target)


# ------------------------------------------------------------------------------ calidad de datos


@pytest.mark.asyncio
async def test_quality_report_on_clean_database(env):
    _, maker = env
    async with maker() as s:
        report = await data_quality_report(s, TODAY)
    assert report["score"] == 100 and report["summary"]["ok"] == report["summary"]["total"]
    assert all(c["severity"] == "ok" and c["count"] == 0 for c in report["checks"])


@pytest.mark.asyncio
async def test_quality_report_detects_problems(env):
    _, maker = env
    df = frame(
        [
            line("O1", "NOCOST-1", gross_sales=200.0, product_name="Sin costo"),
            line("O2", "DUP1", platform_fees=0.0),  # comisión cero
            line("O3", "DUP-1"),
            line("O4", "CON-1"),
        ]
    )
    async with maker() as s:
        await import_orders(s, df)
        await set_costs(s, {"DUP1": 1.0, "DUP-1": 1.0, "CON-1": 1.0})
        walmart = await s.scalar(select(ChannelListing.account_id).limit(1))
        case = ReturnCase(account_id=walmart, external_id="R1", kind="return", external_order_id="O1", case_date=TODAY)
        s.add(case)
        await s.flush()
        s.add(ReturnCaseLine(case_id=case.id, sku="FANTASMA", units=1, refund_total=Decimal(12)))
        await s.commit()
    async with maker() as s:
        report = await data_quality_report(s, TODAY)

    checks = {c["id"]: c for c in report["checks"]}
    assert checks["missing_cost"]["severity"] == "critical" and checks["missing_cost"]["count"] == 1
    assert checks["missing_cost"]["impact"] == pytest.approx(200.0)
    assert checks["missing_cost"]["action"]["page"] == "costs"
    assert checks["duplicate_skus"]["count"] == 1 and checks["duplicate_skus"]["action"]["page"] == "skus"
    assert checks["nameless_products"]["count"] == 3 and checks["nameless_products"]["severity"] == "info"
    assert checks["zero_fee_lines"]["count"] == 1
    assert checks["return_unknown_skus"]["count"] == 1 and checks["return_unknown_skus"]["impact"] == pytest.approx(12.0)
    assert checks["orders_without_state"]["count"] == 4
    assert report["score"] == score_of(report["checks"]) < 100
    severities = [c["severity"] for c in report["checks"]]
    assert severities == sorted(severities, key=["critical", "warning", "info", "ok"].index)  # graves primero


def test_score_formula_is_clamped():
    crit = [{"severity": "critical"}] * 6
    assert score_of(crit) == 0
    assert score_of([{"severity": "warning"}, {"severity": "info"}, {"severity": "ok"}]) == 90


# ------------------------------------------------------------------------------ endpoints


@pytest.mark.asyncio
async def test_endpoints_duplicates_merge_aliases_and_quality(env):
    client, maker = env
    await seed_duplicates(maker)

    groups = (await client.get("/api/skus/duplicates")).json()
    assert groups["count"] == 1

    bad = await client.post("/api/skus/merge", json={"source_sku": "XMAS8027", "target_sku": "XMAS8027"})
    assert bad.status_code == 422 and bad.json()["detail"]["problems"]
    missing = await client.post("/api/skus/merge", json={"source_sku": "NOPE", "target_sku": "XMAS8027"})
    assert missing.status_code == 422

    ok = await client.post("/api/skus/merge", json={"source_sku": "XMAS8027", "target_sku": "XMAS-8027"})
    assert ok.status_code == 200 and ok.json()["lines_merged"] == 1 and ok.json()["gold_rows"] > 0
    assert (await client.get("/api/skus/duplicates")).json()["count"] == 0

    aliases = (await client.get("/api/skus/aliases")).json()
    assert [a["alias"] for a in aliases] == ["XMAS8027"]
    created = await client.post("/api/skus/aliases", json={"alias": "XMAS 8027 B", "target_sku": "XMAS-8027"})
    assert created.status_code == 200
    dup = await client.post("/api/skus/aliases", json={"alias": "XMAS 8027 B", "target_sku": "XMAS-8027"})
    assert dup.status_code == 422
    assert (await client.delete(f"/api/skus/aliases/{created.json()['id']}")).status_code == 200
    assert (await client.delete("/api/skus/aliases/9999")).status_code == 404

    quality = await client.get("/api/data-quality")
    assert quality.status_code == 200 and {"score", "summary", "checks"} <= quality.json().keys()
