"""Desglose de liquidaciones de TikTok Shop: conceptos, guardado idempotente y reportes (datos sintéticos)."""

from datetime import UTC, date, datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import create_dev_schema, get_session, make_engine
from app.models import SettlementFee
from app.settlement_api import router
from app.settlement_breakdown import settlement_breakdown_report, settlement_waterfall
from app.settlement_fees import (
    build_settlement_fees,
    transaction_concepts,
    upsert_settlement_fees,
)
from app.settlements import upsert_settlements


def _ts(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())


def _tx(tid, statement, day, gross, referral, affiliate, other=0.0, shipping=0.0, refund=0.0, disc=0.0):
    fee = referral + affiliate + other
    return {
        "id": tid,
        "statement_id": statement,
        "order_id": f"o{tid}",
        "order_create_time": _ts(day),
        "gross_sales_amount": str(gross),
        "gross_sales_refund_amount": str(refund),
        "seller_discount_amount": str(disc),
        "referral_fee_amount": str(referral),
        "affiliate_commission_amount": str(affiliate),
        "fee_amount": str(fee),
        "shipping_cost_amount": str(shipping),
        "sales_tax_amount": "-1.0",
        "platform_discount_amount": "-5.0",
    }


def _statement(sid, day, net):
    return {
        "external_id": sid,
        "statement_date": date.fromisoformat(day),
        "payout_date": date.fromisoformat(day),
        "status": "paid",
        "net_payout": net,
    }


# Septiembre (período anterior) y octubre (período actual).
TXS = [
    _tx("1", "s1", "2026-09-10", 100, -3, -2),
    _tx("2", "s1", "2026-09-11", 100, -3, -2),
    _tx("3", "s2", "2026-10-10", 100, -3, -8, other=-4, shipping=-10, refund=-20, disc=-10),
    _tx("4", "s2", "2026-10-11", 100, -3, -8, other=-4),
]
STATEMENTS = [
    _statement("s1", "2026-09-15", 190),
    _statement("s2", "2026-10-15", 100 * 2 - 20 - 10 - 30 - 10 + 5),  # 5 de ajuste sin orden
]


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        await upsert_settlements(s, "tiktok_shop", "tiktok_shop_us", STATEMENTS)
        await upsert_settlement_fees(s, "tiktok_shop", "tiktok_shop_us", build_settlement_fees(TXS))
        await s.commit()
    yield m
    await engine.dispose()


def test_transaction_concepts_split_known_fees_and_residual():
    c = transaction_concepts(TXS[2])
    assert c["referral_fee"] == -3 and c["affiliate_commission"] == -8
    assert c["other_fees"] == -4  # fee_amount - componentes conocidos
    assert c["sales_tax"] == -1 and c["platform_discount"] == -5
    assert "transaction_fee" not in c  # los ceros se omiten


@pytest.mark.asyncio
async def test_upsert_is_idempotent(maker):
    async with maker() as s:
        before = await s.scalar(select(func.count()).select_from(SettlementFee))
        await upsert_settlement_fees(s, "tiktok_shop", "tiktok_shop_us", build_settlement_fees(TXS))
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(SettlementFee)) == before


@pytest.mark.asyncio
async def test_report_by_concept_month_and_change(maker):
    async with maker() as s:
        r = await settlement_breakdown_report(s, start=date(2026, 10, 1), end=date(2026, 10, 31))
    assert r["summary"]["sales"] == 200
    assert r["summary"]["fees"] == 30  # 3+8+4 por 2 órdenes
    assert r["summary"]["fee_pct"] == 15
    assert r["summary"]["biggest_concept"]["code"] == "affiliate_commission"
    aff = next(c for c in r["by_concept"] if c["code"] == "affiliate_commission")
    assert aff["cost"] == 16 and aff["pct_of_sales"] == 8
    # Contra el período anterior (31 días antes: septiembre) la comisión de afiliado creció.
    assert aff["previous_cost"] == 4 and aff["change_pct"] == 300
    assert r["summary"]["previous_fee_pct"] == 5
    assert r["summary"]["fee_pct_change_points"] == 10
    # Los informativos no entran en los totales pero sí en la lista.
    tax = next(c for c in r["by_concept"] if c["code"] == "sales_tax")
    assert tax["kind"] == "memo"
    assert r["by_concept"][-1]["kind"] == "memo" or r["by_concept"][-2]["kind"] == "memo"
    assert [m["month"] for m in r["by_month"]] == ["2026-10"]


@pytest.mark.asyncio
async def test_report_months_and_statements(maker):
    async with maker() as s:
        r = await settlement_breakdown_report(s, start=date(2026, 9, 1), end=date(2026, 10, 31))
    assert [m["month"] for m in r["by_month"]] == ["2026-09", "2026-10"]
    assert r["by_month"][0]["fee_pct"] == 5
    assert r["by_month"][1]["concepts"]["affiliate_commission"] == 16
    assert [x["external_id"] for x in r["statements"]] == ["s2", "s1"]  # más reciente primero


@pytest.mark.asyncio
async def test_empty_period_has_no_division_errors(maker):
    async with maker() as s:
        r = await settlement_breakdown_report(s, start=date(2025, 1, 1), end=date(2025, 1, 31))
    assert r["summary"]["statements"] == 0 and r["summary"]["fee_pct"] == 0
    assert r["summary"]["biggest_concept"] is None and r["by_concept"] == []


@pytest.mark.asyncio
async def test_waterfall_reconciles_to_payout(maker):
    async with maker() as s:
        r = await settlement_breakdown_report(s, start=date(2026, 10, 1), end=date(2026, 10, 31))
        w = await settlement_waterfall(s, r["statements"][0]["id"])
        assert await settlement_waterfall(s, 9999) is None
    assert w is not None
    codes = [x["code"] for x in w["steps"]]
    assert codes[0] == "gross_sales" and "sales_tax" not in codes
    assert codes[-1] == "other_adjustments"  # los 5 sin orden
    assert round(sum(x["amount"] for x in w["steps"]), 2) == w["net_payout"]


@pytest.mark.asyncio
async def test_api_read_only_and_validation(maker):
    app = FastAPI()
    app.include_router(router)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get("/api/settlements/breakdown?start=2026-10-01&end=2026-10-31")
        assert ok.status_code == 200 and ok.json()["summary"]["sales"] == 200
        assert (await client.get("/api/settlements/breakdown?start=2026-11-01&end=2026-10-01")).status_code == 422
        assert (await client.get("/api/settlements/9999/waterfall")).status_code == 404
        assert (await client.post("/api/settlements/breakdown")).status_code == 405


@pytest.mark.parametrize("bad", [None])
def test_build_rows_skip_bad_dates(bad):
    rows = build_settlement_fees([{**TXS[0], "order_create_time": bad}])
    assert rows and rows[0]["fee_date"] is None
