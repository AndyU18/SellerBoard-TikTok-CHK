"""Dinero atorado en liquidaciones: cubetas, monto estimado, diferencias, proveedor y endpoint (datos sintéticos)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import settlement_aging as sa
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    Account,
    AffiliateOrder,
    OrderLine,
    Product,
    SalesOrder,
    Settlement,
    SettlementFee,
    Shipment,
)
from app.settlement_aging_api import router

TODAY = date(2026, 11, 24)
# Ticket promedio 100 y comisión efectiva 20% => cada orden de TikTok pendiente se estima en $80.
EXPECTED_ORDER_USD = 80.0


def _dt(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T12:00:00+00:00")


def _shipment(acc_id, order_id, status, *, delivered=None, collected=None, sample=False):
    return Shipment(
        account_id=acc_id,
        external_order_id=order_id,
        order_date=date(2026, 7, 1),
        status=status,
        is_sample=sample,
        delivered_at=_dt(delivered) if delivered else None,
        collected_at=_dt(collected) if collected else None,
    )


def _affiliate(acc_id, order_id, status, base):
    return AffiliateOrder(
        account_id=acc_id,
        external_order_id=order_id,
        sku_id="sku",
        order_date=date(2026, 10, 1),
        commission_base=Decimal(base),
        settlement_status=status,
        creator_username="creador",
    )


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        acc = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        prod = Product(sku="SKU1", name="Producto")
        s.add_all([acc, prod])
        await s.flush()

        for oid in ("S1", "S2"):  # liquidadas: ticket promedio 100
            order = SalesOrder(account_id=acc.id, external_order_id=oid, order_date=date(2026, 11, 1))
            s.add(order)
            await s.flush()
            s.add(
                OrderLine(
                    order_id=order.id,
                    account_id=acc.id,
                    order_date=date(2026, 11, 1),
                    product_id=prod.id,
                    channel_sku="SKU1",
                    units=1,
                    gross_sales=Decimal(100),
                )
            )

        ok = Settlement(
            account_id=acc.id, external_id="ST-OK", statement_date=date(2026, 11, 2), status="paid",
            gross_amount=Decimal(200), fees_amount=Decimal(40), net_payout=Decimal(160),
        )
        short = Settlement(
            account_id=acc.id, external_id="ST-SHORT", statement_date=date(2026, 11, 3), status="paid",
            adjustment_amount=Decimal(-50), net_payout=Decimal(-50),
        )
        tiny = Settlement(  # diferencia bajo la tolerancia: redondeo
            account_id=acc.id, external_id="ST-TINY", statement_date=date(2026, 11, 4), status="paid",
            net_payout=Decimal("10.20"),
        )
        s.add_all([ok, short, tiny])
        await s.flush()
        for settlement, concept, kind, amount in (
            (ok, "gross_sales", "revenue", 200),
            (ok, "platform_fee", "fee", -40),
            (ok, "sales_tax", "memo", 15),  # informativo: no cuenta
            (tiny, "gross_sales", "revenue", 10),
        ):
            s.add(
                SettlementFee(
                    settlement_id=settlement.id, account_id=acc.id, statement_id=settlement.external_id,
                    transaction_id=f"{concept}-{settlement.id}", concept=concept, kind=kind, amount=Decimal(amount),
                )
            )

        s.add_all(
            [
                _shipment(acc.id, "P1", "DELIVERED", delivered="2026-11-20"),  # 4 días
                _shipment(acc.id, "P2", "DELIVERED", delivered="2026-11-05"),  # 19
                _shipment(acc.id, "P3", "COMPLETED", delivered="2026-08-01"),  # 115
                _shipment(acc.id, "P4", "IN_TRANSIT", collected="2026-11-10"),  # 14, desde el envío
                _shipment(acc.id, "S1", "COMPLETED", delivered="2026-10-01"),  # ya liquidada
                _shipment(acc.id, "SMP", "DELIVERED", delivered="2026-10-01", sample=True),  # muestra
                _shipment(acc.id, "C1", "CANCELLED"),
                _shipment(acc.id, "W1", "AWAITING_SHIPMENT"),
                _shipment(acc.id, "A1", "DELIVERED", delivered="2026-10-20"),  # 35, afiliado
                _shipment(acc.id, "A4", "CANCELLED"),
                _affiliate(acc.id, "A1", "To-SETTLE", 50),
                _affiliate(acc.id, "A2", "SETTLED", 70),
                _affiliate(acc.id, "A3", "To-SETTLE", 30),  # sin envío registrado: se ancla a la fecha de la orden
                _affiliate(acc.id, "S2", "To-SETTLE", 99),  # estado viejo: ya liquidada
                _affiliate(acc.id, "A4", "To-SETTLE", 60),  # paquete cancelado
            ]
        )
        await s.commit()
    yield m
    await engine.dispose()


def test_bucket_boundaries_and_overdue():
    assert [sa.bucket_for(d)[1] for d in (0, 7, 8, 14, 15, 30, 31, 60, 61, 400)] == [
        "0-7", "0-7", "8-14", "8-14", "15-30", "15-30", "31-60", "31-60", "60+", "60+",
    ]
    assert sa.bucket_for(-3)[1] == "0-7"  # fecha a futuro: cuenta como recién enviada
    assert not sa.is_overdue(sa.EXPECTED_SETTLEMENT_DAYS) and sa.is_overdue(sa.EXPECTED_SETTLEMENT_DAYS + 1)


@pytest.mark.asyncio
async def test_report_buckets_and_estimates(maker):
    async with maker() as s:
        r = await sa.settlement_aging_report(s, today=TODAY)
    assert r["assumptions"]["fee_rate"] == pytest.approx(0.2)
    assert r["assumptions"]["avg_order_usd"] == 100.0 and r["assumptions"]["sample_orders"] == 2

    by_key = {b["key"]: b for b in r["buckets"]}
    # P1-P4 son de TikTok; A1 y A3 de afiliados (A1 no se cuenta también como TikTok). Fuera: S1/S2 (ya
    # liquidadas), A2 (SETTLED), A4 (paquete cancelado), la muestra, la cancelada y la que aún no se envía.
    assert [(k, b["orders"]) for k, b in by_key.items()] == [
        ("0_7", 1), ("8_14", 1), ("15_30", 1), ("31_60", 2), ("60_plus", 1),
    ]
    assert by_key["0_7"]["tiktok_shop_usd"] == EXPECTED_ORDER_USD
    assert by_key["8_14"]["tiktok_shop_orders"] == 1  # P4: ancla en el envío
    assert by_key["31_60"]["affiliate_orders"] == 2 and by_key["31_60"]["affiliate_usd"] == 64.0  # (50 + 30) * 0.8
    assert by_key["31_60"]["tiktok_shop_orders"] == 0
    assert [b["overdue"] for b in r["buckets"]] == [False, True, True, True, True]

    t = r["totals"]
    assert t["orders"] == 6  # P1-P4, A1 y A3 (afiliado pendiente sin envío registrado)
    assert t["oldest_days"] == 115
    origins = {o["origin"]: o for o in r["by_origin"]}
    assert origins["affiliate"]["orders"] == 2 and origins["tiktok_shop"]["orders"] == 4
    assert t["pending_usd"] == pytest.approx(4 * EXPECTED_ORDER_USD + 40 + 24)
    assert t["overdue_orders"] == t["orders"] - by_key["0_7"]["orders"]


@pytest.mark.asyncio
async def test_oldest_orders_sorted_and_limited(maker):
    async with maker() as s:
        r = await sa.settlement_aging_report(s, today=TODAY, oldest_limit=3)
    oldest = r["oldest_orders"]
    assert oldest[0]["order_id"] == "P3" and len(oldest) == 3
    assert oldest[0]["age_days"] == 115 and oldest[0]["bucket"] == "60+" and oldest[0]["overdue"] is True
    assert oldest[0]["anchor_kind"] == "entrega"
    assert [o["age_days"] for o in oldest] == sorted((o["age_days"] for o in oldest), reverse=True)


@pytest.mark.asyncio
async def test_differences_ignore_memo_and_rounding(maker):
    async with maker() as s:
        r = await sa.settlement_aging_report(s, today=TODAY)
    diffs = r["differences"]
    assert [d["external_id"] for d in diffs] == ["ST-SHORT"]  # ST-TINY (0.20) es redondeo; ST-OK cuadra
    assert r["totals"]["difference_count"] == 1 and r["totals"]["difference_usd"] == -50
    short = diffs[0]
    assert short["deposit_usd"] == -50 and short["transactions_usd"] == 0 and short["difference_usd"] == -50
    assert short["adjustment_usd"] == -50 and short["concepts"] == []  # no se puede saber qué concepto falta


@pytest.mark.asyncio
async def test_empty_database_has_no_estimates():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        r = await sa.settlement_aging_report(s, today=TODAY)
    await engine.dispose()
    assert r["assumptions"]["fee_rate"] is None and r["assumptions"]["avg_order_usd"] is None
    assert r["totals"]["orders"] == 0 and r["differences"] == [] and r["oldest_orders"] == []
    assert all(b["orders"] == 0 for b in r["buckets"])


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 24, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_provider_emits_overdue_buckets_and_large_differences(maker, monkeypatch):
    monkeypatch.setattr(sa, "datetime", _FrozenDatetime)
    async with maker() as s:
        items = await sa.settlement_aging_actions(s)
    bucket_keys = {i.key for i in items if ":bucket_" in i.key}
    assert bucket_keys == {f"settlement_aging:bucket_{k}" for k in ("8_14", "15_30", "31_60", "60_plus")}
    assert len([i for i in items if ":diff_" in i.key]) == 1
    assert all(i.kind == "settlement_aging" and i.target == "settlements" for i in items)
    diff = next(i for i in items if ":diff_" in i.key)
    assert diff.impact_usd == 50.0  # |−50|; la diferencia de redondeo no genera acción
    bucket = next(i for i in items if i.key.endswith("bucket_31_60"))
    assert bucket.impact_usd == 64.0 and bucket.meta["orders"] == 2


def test_provider_is_registered():
    from app.action_center import providers

    assert sa.settlement_aging_actions is providers()["settlement_aging"].fn


@pytest.mark.asyncio
async def test_endpoint(maker):
    app = FastAPI()
    app.include_router(router)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/settlements/aging?limit=2")
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body["oldest_orders"]) == 2 and len(body["buckets"]) == 5
        assert body["expected_days"] == sa.EXPECTED_SETTLEMENT_DAYS
        assert (await client.get("/api/settlements/aging?limit=0")).status_code == 422
