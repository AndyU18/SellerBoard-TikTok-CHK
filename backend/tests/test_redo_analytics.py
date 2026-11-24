"""Apartado Redo: guarda el libro, lo clasifica y calcula costos, cobertura y recargas (datos sintéticos)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.redo import RedoClient, RedoError, RedoSettings, sync_redo
from app.db import create_dev_schema, get_session, make_engine
from app.main import app
from app.models import Account, OrderLine, Product, RedoCharge, SalesOrder
from app.redo_analytics import redo_billing, redo_charges, redo_costs, redo_overview
from app.redo_ops import upsert_bills, upsert_charges, upsert_topups

TODAY = date(2026, 11, 20)


def _charge(cid, subtype, amount, day, order=None, note=None):
    return {
        "id": cid,
        "subtype": subtype,
        "type": "ShippingLabel",
        "orderName": order,
        "amount": {"amount": str(amount), "currency": "USD"},
        "note": note,
        "createdAt": f"{day}T12:00:00.000Z",
        "orderDate": f"{day}T08:00:00.000Z",
    }


CHARGES = [
    _charge("c1", "ChargeLabel", 10, "2026-11-10", "A1"),
    _charge("c2", "Surcharge", 3, "2026-11-12", "A1", "Shipping Charge Correction Ground"),
    _charge("c3", "ChargeLabel", 20, "2026-11-11", "A2"),
    _charge("c4", "VoidLabel", -20, "2026-11-11", "A2"),
    _charge("c5", "ChargeLabel", 12, "2026-11-11", "A2"),
    _charge("c6", "ChargeLabel", 9, "2026-11-13", "#2500"),
    _charge("c7", "ChargeLabel", 8, "2026-11-13", "577000000000000001"),
    _charge("c8", "CardProcessingFee", 15, "2026-11-14"),
    _charge("c9", "ReduceBalance", 100, "2026-11-14"),
]


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        await upsert_charges(s, CHARGES)
        await upsert_bills(
            s,
            [
                {
                    "id": "b1",
                    "type": "SHIPPING",
                    "status": "OPEN",
                    "periodStart": "2026-11-01T00:00:00.000Z",
                    "periodEnd": "2026-11-30T23:59:59.999Z",
                    "chargeTotal": {"amount": "500"},
                    "openingBalance": {"amount": "-50"},
                    "closingBalance": {"amount": "0"},
                }
            ],
        )
        await upsert_topups(
            s,
            [
                {"id": "t1", "amount": {"amount": "1000"}, "status": "SUCCESS", "createdAt": "2026-11-05T10:00:00Z"},
                {"id": "t2", "amount": {"amount": "1000"}, "status": "SUCCESS", "createdAt": "2026-11-15T10:00:00Z"},
                {"id": "t3", "amount": {"amount": "500"}, "status": "FAILED", "createdAt": "2026-11-16T10:00:00Z"},
            ],
        )
        acc = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        prod = Product(sku="SKU1", name="Producto")
        s.add_all([acc, prod])
        await s.flush()
        order = SalesOrder(account_id=acc.id, external_order_id="A1", order_date=date(2026, 11, 9), ship_state="Florida")
        s.add(order)
        await s.flush()
        s.add(
            OrderLine(
                order_id=order.id,
                account_id=acc.id,
                order_date=date(2026, 11, 9),
                product_id=prod.id,
                channel_sku="SKU1",
                units=2,
                gross_sales=Decimal(60),
                promo=Decimal(10),
            )
        )
        await s.commit()
    yield m
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_is_idempotent(maker):
    async with maker() as s:
        await upsert_charges(s, CHARGES)
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(RedoCharge)) == len(CHARGES)
        row = await s.get(RedoCharge, "c2")
    assert row.subtype == "Surcharge" and row.amount == Decimal(3) and row.order_name == "A1"


@pytest.mark.asyncio
async def test_overview_classifies_costs_and_coverage(maker):
    async with maker() as s:
        o = await redo_overview(s, days=30, today=TODAY)
    k = o["kpis"]
    assert k["labels"] == 59 and k["surcharges"] == 3 and k["voids"] == -20
    assert k["card_fees"] == 15 and k["adjustments"] == 100  # el ajuste de saldo no es costo
    assert k["net_cost"] == 59 + 3 - 20 + 15
    assert k["orders"] == 4 and k["relabel_orders"] == 1 and k["surcharge_orders"] == 1
    assert k["surcharge_rate"] == pytest.approx(3 / 59) and k["void_rate"] == pytest.approx(20 / 59)
    assert k["unassigned_cost"] == 15
    assert o["surcharge_reasons"][0]["reason"] == "Shipping Charge Correction Ground"
    cover = {c["origin"]: c for c in o["coverage"]}
    assert cover["tiktok_shop"]["orders"] == 1 and cover["shopify"]["orders"] == 1
    assert cover["tiktok_pendiente"]["orders"] == 1 and cover["otro"]["orders"] == 1
    assert o["by_month"][-1]["month"] == "2026-11" and o["by_month"][-1]["orders"] == 4


@pytest.mark.asyncio
async def test_costs_by_channel_state_and_relabels(maker):
    async with maker() as s:
        c = await redo_costs(s, days=30, today=TODAY)
    assert c["matched_orders"] == 1 and c["unmatched_orders"] == 3
    ch = c["by_channel"][0]
    assert ch["key"] == "tiktok_shop" and ch["cost"] == 13 and ch["share_of_sales"] == pytest.approx(13 / 50)
    assert c["by_state"][0]["key"] == "Florida" and c["by_state"][0]["cost_per_unit"] == pytest.approx(6.5)
    assert [r["order"] for r in c["relabeled_orders"]] == ["A2"]
    assert sum(b["orders"] for b in c["distribution"]) == 4  # A2 queda en 12 (neto de la anulación)


@pytest.mark.asyncio
async def test_billing_topups_and_burn(maker):
    async with maker() as s:
        b = await redo_billing(s, today=TODAY)
    assert b["topups"]["count"] == 2 and b["topups"]["total"] == 2000 and b["topups"]["failed"] == 1
    assert b["topups"]["avg_days_between"] == pytest.approx(10)
    assert b["bills"][0]["charge_total"] == 500 and b["bills"][0]["opening_balance"] == -50
    assert b["burn"]["spend_30d"] == 59 + 3 - 20
    assert b["burn"]["days_per_topup"] == pytest.approx(1000 / (42 / 30))
    assert b["by_month"][-1]["month"] == "2026-11"


@pytest.mark.asyncio
async def test_charges_filters_and_paging(maker):
    async with maker() as s:
        surcharges = await redo_charges(s, subtype="Surcharge")
        search = await redo_charges(s, q="a2", limit=2)
    assert surcharges["total"] == 1 and surcharges["rows"][0]["label"] == "Correcciones del carrier"
    assert search["total"] == 3 and len(search["rows"]) == 2
    assert search["rows"][0]["created_at"] >= search["rows"][1]["created_at"]
    # El resumen cubre todo el filtro, no solo la página devuelta (2 filas de 3).
    assert sum(s["count"] for s in search["by_subtype"]) == 3
    assert sum(d["amount"] for d in search["by_day"]) == pytest.approx(search["amount"])
    assert surcharges["by_subtype"] == [
        {"subtype": "Surcharge", "label": "Correcciones del carrier", "count": 1, "amount": 3.0, "is_cost": True}
    ]
    assert surcharges["by_day"] == [{"date": "2026-11-12", "amount": 3.0}]


class FakeRedo:
    def iter_charges(self):
        return iter(CHARGES)

    def iter_bills(self):
        return iter([])

    def iter_topups(self):
        return iter([{"id": "t9", "amount": {"amount": "5"}, "status": "SUCCESS", "createdAt": "2026-11-01T00:00:00Z"}])


@pytest.mark.asyncio
async def test_sync_redo_stores_ledger(maker):
    async with maker() as s:
        _, stats = await sync_redo(s, FakeRedo())
        await s.commit()
    assert stats.ledger_charges == len(CHARGES) and stats.topups == 1


@pytest.mark.asyncio
async def test_redo_endpoints(maker):
    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("overview", "costs", "billing", "charges"):
                res = await client.get(f"/api/redo/{path}")
                assert res.status_code == 200, (path, res.text)
            assert (await client.get("/api/redo/overview?days=3")).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_client_is_read_only():
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    client = RedoClient(RedoSettings(store_id="s", api_key="k"), http)
    with pytest.raises(RedoError):
        client.query("mutation { x }")
