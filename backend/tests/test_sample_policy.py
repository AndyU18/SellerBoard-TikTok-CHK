"""Política de muestras con datos sintéticos (sqlite en memoria).

Creadores del fixture (muestra = $10 de COGS; cada orden de afiliado deja $75 antes de muestras):
  star   2 muestras (1 sep, 15 sep) y 6 órdenes en 4 semanas desde el 3 sep   -> Seguir enviando
  slow   3 muestras (1, 15 y 25 ago) y su primera venta el 5 sep (2 órdenes)   -> Enviar con tope (falso positivo)
  ghost  3 muestras (1, 10 y 12 oct), nunca vendió                              -> Cortar
  stuck  2 muestras (1 y 5 nov), nunca vendió, aún en plazo                     -> Pausar
  fresh  1 muestra (15 nov), nunca vendió, aún en plazo                         -> Enviar con tope
"""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import sample_policy as sp
from app.db import create_dev_schema, get_session, make_engine
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
)
from app.sample_policy_api import router

TODAY = date(2026, 11, 20)

SAMPLES = {
    "star": [date(2026, 9, 1), date(2026, 9, 15)],
    "slow": [date(2026, 8, 1), date(2026, 8, 15), date(2026, 8, 25)],
    "ghost": [date(2026, 10, 1), date(2026, 10, 10), date(2026, 10, 12)],
    "stuck": [date(2026, 11, 1), date(2026, 11, 5)],
    "fresh": [date(2026, 11, 15)],
}
SALES = {
    "star": [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 11), date(2026, 9, 18), date(2026, 9, 25), date(2026, 9, 26)],
    "slow": [date(2026, 9, 5), date(2026, 9, 6)],
}


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
        n = 0
        for creator, days in SAMPLES.items():
            for day in days:
                n += 1
                oid = f"S{n}"
                s.add(SampleLine(account_id=acc.id, external_order_id=oid, order_date=day, sku="P1", units=1))
                s.add(SampleApplication(account_id=acc.id, external_id=f"app-{oid}", creator_username=creator, external_order_id=oid))
        for creator, days in SALES.items():
            for day in days:
                n += 1
                oid = f"O{n}"
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
                        creator_username=creator, content_type="VIDEO", content_id="V1", units=1,
                        commission_base=Decimal(100), commission=Decimal(10),
                    )
                )
        await s.commit()
    yield m
    await engine.dispose()


async def _report(maker, **kw):
    async with maker() as s:
        return await sp.sample_policy_report(s, today=TODAY, **kw)


def _by_creator(report):
    return {r["creator"]: r for r in report["rows"]}


def test_weights_add_up_to_100():
    assert sum(sp.SCORE_WEIGHTS.values()) == pytest.approx(100)


def test_component_scales():
    assert sp.speed_value(None) == 0 and sp.speed_value(sp.FAST_SALE_DAYS) == 1 and sp.speed_value(sp.SLOW_SALE_DAYS) == 0
    assert sp.conversion_value(0, 3) == 0 and sp.conversion_value(100, 1) == 1 and sp.conversion_value(1, 0) == 0
    assert sp.profit_value(-5, 10) == 0 and sp.profit_value(30, 10) == 1 and sp.profit_value(5, 0) == 1
    assert sp.consistency_value(2) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_score_is_explained_by_components(maker):
    rows = _by_creator(await _report(maker))
    star = rows["star"]
    assert star["score"] == pytest.approx(86)  # 0.6*35 + 30 + 20 + 15
    parts = {p["key"]: p for p in star["score_parts"]}
    assert parts["conversion"]["points"] == pytest.approx(21) and parts["profit"]["points"] == pytest.approx(30)
    assert sum(p["points"] for p in star["score_parts"]) == pytest.approx(star["score"])
    assert star["profit_before_samples"] == 450 and star["sample_cost"] == 20 and star["profit"] == 430
    assert star["days_to_first_sale"] == 2 and star["content_type"] == "Video" and star["active_weeks"] == 4
    assert star["sample_conversion"] == pytest.approx(3)
    ghost = rows["ghost"]
    assert ghost["score"] == 0 and ghost["sample_conversion"] == 0 and ghost["content_type"] is None
    assert ghost["days_to_first_sale"] is None and ghost["sample_cost"] == 30


@pytest.mark.asyncio
async def test_recommendations_and_budget(maker):
    rows = _by_creator(await _report(maker))
    assert {c: r["recommendation"] for c, r in rows.items()} == {
        "star": sp.SEND,
        "slow": sp.CAP,
        "ghost": sp.CUT,
        "stuck": sp.PAUSE,
        "fresh": sp.CAP,
    }
    assert rows["star"]["suggested_samples"] == 5 and rows["star"]["suggested_budget_usd"] == 50
    assert rows["slow"]["suggested_samples"] == 1 and rows["slow"]["suggested_budget_usd"] == 10
    assert rows["ghost"]["suggested_samples"] == 0 and rows["stuck"]["suggested_budget_usd"] == 0
    assert "tope" in rows["stuck"]["reason"] and "días" in rows["ghost"]["reason"]


def test_recommend_sells_at_a_loss_is_paused():
    rule = sp.PolicyRule(2, 30)
    rec, _ = sp.recommend(score=90, orders=10, samples=1, profit_before_samples=-1, days_since_first_sample=5, rule=rule)
    assert rec == sp.PAUSE


def test_blocked_samples_replays_cap_and_time():
    history = sp.CreatorHistory(
        sample_days=(date(2026, 1, 1), date(2026, 1, 20), date(2026, 2, 15)), first_sale=None, active_weeks=0, content_type=None
    )
    assert sp.blocked_samples(history, sp.PolicyRule(10, 30)) == [date(2026, 2, 15)]  # solo por plazo
    assert sp.blocked_samples(history, sp.PolicyRule(1, 365)) == [date(2026, 1, 20), date(2026, 2, 15)]  # solo por tope
    sold = sp.CreatorHistory(history.sample_days, date(2026, 1, 10), 1, None)
    assert sp.blocked_samples(sold, sp.PolicyRule(0, 1)) == [date(2026, 1, 1)]  # tras vender nada se bloquea
    assert sp.blocked_samples(sp.CreatorHistory((), None, 0, None), sp.PolicyRule(0, 0)) == []


@pytest.mark.asyncio
async def test_simulation_savings_and_false_positives(maker):
    report = await _report(maker)
    sim = report["simulation"]
    # ghost: la 3.ª muestra; slow: la 3.ª muestra (vendió el 5 sep, después de la muestra bloqueada del 25 ago)
    assert sim["savings"] == 20 and sim["samples_saved"] == 2 and sim["creators_stopped"] == 2
    assert sim["false_positives"] == 1 and sim["lost_sales"] == 200 and sim["lost_profit"] == 150
    assert sim["net_benefit"] == -130
    summary = report["summary"]
    assert summary["estimated_savings"] == 20 and summary["false_positives"] == 1
    assert summary["creators"] == 5 and summary["to_pause"] == 2
    assert summary["recommendation_counts"] == {sp.SEND: 1, sp.CAP: 2, sp.PAUSE: 1, sp.CUT: 1}
    assert summary["never_sold_creators"] == 3 and summary["never_sold_cost"] == 60
    assert summary["profit_before_samples"] == 600 and summary["profit_after_samples"] == 600 - 110  # 11 muestras de $10
    assert summary["profit_with_policy"] == summary["profit_after_samples"] + 20 - 150


@pytest.mark.asyncio
async def test_simulation_responds_to_query_parameters(maker):
    strict = (await _report(maker, max_samples_without_sale=0))["simulation"]
    lax = (await _report(maker, max_samples_without_sale=20, max_days_to_first_sale=365))["simulation"]
    assert strict["savings"] == 100 and strict["false_positives"] == 2
    assert lax["savings"] == 0 and lax["false_positives"] == 0


@pytest.mark.asyncio
async def test_savings_curve_is_monotonic(maker):
    curve = (await _report(maker))["curve"]
    assert [p["cap"] for p in curve] == [0, 1, 2, 3]
    assert [p["savings"] for p in curve] == [100, 50, 20, 0]
    savings = [p["savings"] for p in curve]
    assert savings == sorted(savings, reverse=True)


@pytest.mark.asyncio
async def test_histogram_counts_every_creator(maker):
    hist = (await _report(maker))["histogram"]
    assert len(hist) == sp.HISTOGRAM_BINS and sum(b["count"] for b in hist) == 5
    assert hist[8]["count"] == 1  # star: 86
    assert sp.score_histogram([100.0])[-1]["count"] == 1


@pytest.mark.asyncio
async def test_date_range_limits_history(maker):
    report = await _report(maker, start=date(2026, 10, 1))
    assert set(_by_creator(report)) == {"ghost", "stuck", "fresh"}


@pytest.mark.asyncio
async def test_negative_parameters_raise(maker):
    async with maker() as s:
        with pytest.raises(ValueError):
            await sp.sample_policy_report(s, max_samples_without_sale=-1)
        with pytest.raises(ValueError):
            await sp.sample_policy_report(s, max_days_to_first_sale=-1)


@pytest.mark.asyncio
async def test_action_provider_individual_actions(maker, monkeypatch):
    monkeypatch.setattr(sp, "_today", lambda: TODAY)
    async with maker() as s:
        items = await sp.sample_policy_actions(s)
    by_key = {i.key: i for i in items}
    assert set(by_key) == {"sample_policy:creator:ghost", "sample_policy:creator:stuck"}
    pace = 30 / (TODAY - date(2026, 8, 1)).days
    ghost = by_key["sample_policy:creator:ghost"]
    assert ghost.impact_usd == pytest.approx(30 * pace, abs=0.01) and ghost.target == "creatorProfit"
    assert ghost.kind == "sample_policy" and ghost.entity == "ghost" and "ritmo" in ghost.impact_basis


@pytest.mark.asyncio
async def test_action_provider_groups_the_rest_without_double_counting(maker, monkeypatch):
    monkeypatch.setattr(sp, "_today", lambda: TODAY)
    monkeypatch.setattr(sp, "TOP_CREATOR_ACTIONS", 1)
    async with maker() as s:
        items = await sp.sample_policy_actions(s)
    by_key = {i.key: i for i in items}
    assert set(by_key) == {"sample_policy:creator:ghost", "sample_policy:pause_cut"}
    group = by_key["sample_policy:pause_cut"]
    pace = 30 / (TODAY - date(2026, 8, 1)).days
    assert group.title == "Pausar/Cortar muestras a 2 creadores"
    assert group.impact_usd == pytest.approx(20 * pace, abs=0.01)  # solo "stuck": ghost ya tiene su acción
    assert "dos veces" in group.impact_basis and group.target == "creatorProfit"


@pytest.mark.asyncio
async def test_action_provider_is_empty_without_history():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        assert await sp.sample_policy_actions(s) == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_endpoint_and_parameter_limits(maker):
    app = FastAPI()
    app.include_router(router)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get("/api/samples/policy?max_samples_without_sale=3&max_days_to_first_sale=45")
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["params"] == {"max_samples_without_sale": 3, "max_days_to_first_sale": 45}
        assert {"summary", "simulation", "curve", "histogram", "rows", "notes"} <= set(body)
        assert (await client.get("/api/samples/policy?max_samples_without_sale=-1")).status_code == 422
        assert (await client.get("/api/samples/policy?max_samples_without_sale=21")).status_code == 422
        assert (await client.get("/api/samples/policy?max_days_to_first_sale=0")).status_code == 422
        assert (await client.post("/api/samples/policy")).status_code == 405
