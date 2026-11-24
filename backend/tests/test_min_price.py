"""Precio mínimo con envío real: fórmula, piso por estado, pérdida estimada, bordes, proveedor y endpoint (sintético)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import action_center
from app.action_center import ActionItem
from app.costs import product_cost_component_id
from app.db import create_dev_schema, get_session, make_engine
from app.min_price import (
    MAX_ACTIONS,
    load_sale_lines,
    min_price_report,
    price_floor_actions,
    solve_min_price,
)
from app.min_price_api import router
from app.models import (
    Account,
    OrderLine,
    Product,
    ProductCostHistory,
    SalesOrder,
    ShippingLabelCost,
)

TODAY = datetime.now(UTC).date()  # el proveedor del Centro de acciones usa la fecha real
SOLD = TODAY - timedelta(days=5)
COST = Decimal(10)


async def _order(
    s: AsyncSession,
    acc: Account,
    ext_id: str,
    state: str | None,
    lines: list[tuple[Product, int, str, str]],  # (producto, unidades, venta bruta, comisión)
    label: str | None = None,
    day: date = SOLD,
) -> None:
    order = SalesOrder(account_id=acc.id, external_order_id=ext_id, order_date=day, ship_state=state)
    s.add(order)
    await s.flush()
    for prod, units, gross, fees in lines:
        s.add(
            OrderLine(
                order_id=order.id,
                account_id=acc.id,
                order_date=day,
                product_id=prod.id,
                channel_sku=prod.sku,
                units=units,
                gross_sales=Decimal(gross),
                platform_fees=Decimal(fees),
            )
        )
    if label is not None:
        s.add(ShippingLabelCost(source="redo", external_order_id=ext_id, amount=Decimal(label)))


async def _product(s: AsyncSession, sku: str, cost: Decimal | None) -> Product:
    prod = Product(sku=sku, name=f"Producto {sku}")
    s.add(prod)
    await s.flush()
    if cost is not None:
        s.add(
            ProductCostHistory(
                product_id=prod.id,
                component_type_id=await product_cost_component_id(s),
                amount=cost,
                valid_from=date(2026, 1, 1),
            )
        )
    return prod


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        acc = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        s.add(acc)
        await s.flush()
        near = await _product(s, "NEAR", COST)  # etiquetas baratas a Florida, caras a Maine
        nocost = await _product(s, "NOCOST", None)
        nolabel = await _product(s, "NOLABEL", COST)
        # NEAR: comisión 10 % de la venta neta. Florida a $30 con etiqueta de $5; Maine a $26 con etiqueta de $15.
        for i in (1, 2):
            await _order(s, acc, f"FL{i}", "Florida", [(near, 1, "30", "3")], label="5")
            await _order(s, acc, f"ME{i}", "Maine", [(near, 1, "26", "2.6")], label="15")
        for i in range(3):
            await _order(s, acc, f"NC{i}", "Ohio", [(nocost, 1, "12", "1.2")], label="6")
            await _order(s, acc, f"NL{i}", "Ohio", [(nolabel, 1, "12", "0")])
        await s.commit()
    yield m
    await engine.dispose()


def _row(report: dict, sku: str) -> dict:
    return next(r for r in report["rows"] if r["sku"] == sku)


def test_formula_closed_form():
    # (COGS 10 + envío 10 + reembolso 0) / (1 - 0.10 - 0) = 22.22; con margen 20 % sube a 28.57.
    base = solve_min_price(cogs=10, shipping=10, refund=0, fee_rate=0.10, margin=0.0)
    assert base == pytest.approx(20 / 0.9)
    assert solve_min_price(cogs=10, shipping=10, refund=0, fee_rate=0.10, margin=0.2) == pytest.approx(20 / 0.7)
    assert solve_min_price(cogs=10, shipping=10, refund=2, fee_rate=0.10, margin=0.0) == pytest.approx(22 / 0.9)


def test_formula_unreachable_when_fees_and_margin_eat_the_price():
    assert solve_min_price(cogs=10, shipping=5, refund=0, fee_rate=0.10, margin=0.9) is None


@pytest.mark.asyncio
async def test_floor_uses_real_label_cost(maker):
    async with maker() as s:
        report = await min_price_report(s, days=30, today=TODAY)
    near = _row(report, "NEAR")
    # Envío real medio: (5 + 5 + 15 + 15) / 4 = 10 por unidad.
    assert near["shipping_basis"] == "real" and near["labeled_units"] == 4
    assert near["shipping_unit"] == pytest.approx(10.0)
    assert near["fee_rate"] == pytest.approx(0.10)
    assert near["cogs_unit"] == pytest.approx(10.0)
    assert near["min_price"] == pytest.approx(20 / 0.9)
    assert near["realized_price"] == pytest.approx(28.0)
    assert near["gap"] == pytest.approx(28.0 - 20 / 0.9)


@pytest.mark.asyncio
async def test_floor_by_state_and_losing_states(maker):
    async with maker() as s:
        near = _row(await min_price_report(s, days=30, today=TODAY), "NEAR")
    by_state = {r["state"]: r for r in near["states"]}
    assert by_state["Florida"]["min_price"] == pytest.approx(15 / 0.9)
    assert by_state["Maine"]["min_price"] == pytest.approx(25 / 0.9)
    assert by_state["Maine"]["shipping_basis"] == "state"
    assert near["states"][0]["state"] == "Maine"  # los estados lejanos suben el piso: van primero
    assert near["max_state_min_price"] == pytest.approx(25 / 0.9)
    assert near["loss_states"] == ["Maine"]  # a Florida se vende sobre su piso


@pytest.mark.asyncio
async def test_estimated_loss_uses_each_sale_state_floor(maker):
    async with maker() as s:
        report = await min_price_report(s, days=30, today=TODAY)
    near = _row(report, "NEAR")
    # El precio medio ($28) supera el piso medio ($22.2), pero las 2 ventas a Maine ($26) quedan bajo $27.78.
    assert near["status"] == "below"
    assert near["below_units"] == 2 and near["below_pct"] == pytest.approx(0.5)
    assert near["estimated_loss"] == pytest.approx(2 * (25 / 0.9 - 26))
    assert report["summary"]["below_skus"] == 1
    assert report["summary"]["estimated_loss"] == pytest.approx(near["estimated_loss"])


@pytest.mark.asyncio
async def test_sku_without_cost_is_not_judged(maker):
    async with maker() as s:
        report = await min_price_report(s, days=30, today=TODAY)
    row = _row(report, "NOCOST")
    assert row["status"] == "no_cost" and row["estimated_loss"] == 0 and row["below_units"] == 0
    assert report["summary"]["skus_no_cost"] == 1


@pytest.mark.asyncio
async def test_sku_without_label_has_no_real_shipping(maker):
    async with maker() as s:
        report = await min_price_report(s, days=30, today=TODAY)
    row = _row(report, "NOLABEL")
    assert row["shipping_basis"] == "none" and row["labeled_units"] == 0 and row["shipping_unit"] == 0
    assert row["min_price"] == pytest.approx(10.0)  # solo COGS: sin etiqueta no se inventa un envío
    assert row["status"] == "ok"
    assert report["summary"]["skus_real_shipping"] == 2  # NEAR y NOCOST tienen etiqueta
    assert report["summary"]["skus_estimated_shipping"] == 1
    assert report["summary"]["label_coverage"] == pytest.approx(7 / 10)  # 10 unidades, 7 con etiqueta


@pytest.mark.asyncio
async def test_high_target_margin_raises_floor_or_is_unreachable(maker):
    async with maker() as s:
        high = await min_price_report(s, days=30, target_margin=0.5, today=TODAY)
        impossible = await min_price_report(s, days=30, target_margin=0.9, today=TODAY)
        with pytest.raises(ValueError):
            await min_price_report(s, days=30, target_margin=0.95, today=TODAY)
    assert _row(high, "NEAR")["min_price"] == pytest.approx(20 / 0.4)
    # Con 90 % de margen y 10 % de comisión no sobra nada para costos: no hay piso alcanzable.
    row = _row(impossible, "NEAR")
    assert row["min_price"] is None and row["status"] == "unreachable" and row["estimated_loss"] == 0


@pytest.mark.asyncio
async def test_label_is_split_by_gross_between_order_lines(maker):
    async with maker() as s:
        acc = await s.scalar(select(Account))
        a = await _product(s, "SPLIT-A", COST)
        b = await _product(s, "SPLIT-B", COST)
        await _order(s, acc, "MULTI", "Texas", [(a, 1, "30", "0"), (b, 1, "10", "0")], label="8")
        await s.commit()
        lines = await load_sale_lines(s, start=SOLD, end=TODAY)
    split = {ln.sku: ln.label for ln in lines if ln.sku.startswith("SPLIT")}
    assert split == {"SPLIT-A": pytest.approx(6.0), "SPLIT-B": pytest.approx(2.0)}


@pytest.mark.asyncio
async def test_voided_label_counts_as_no_label(maker):
    async with maker() as s:
        acc = await s.scalar(select(Account))
        prod = await _product(s, "VOIDED", COST)
        await _order(s, acc, "V1", "Utah", [(prod, 1, "30", "0")], label="-4")
        await s.commit()
        lines = await load_sale_lines(s, start=SOLD, end=TODAY)
    assert next(ln for ln in lines if ln.sku == "VOIDED").label is None


@pytest.mark.asyncio
async def test_empty_window(maker):
    async with maker() as s:
        report = await min_price_report(s, days=7, today=TODAY - timedelta(days=200))
    assert report["rows"] == [] and report["summary"]["below_skus"] == 0


@pytest.mark.asyncio
async def test_action_provider(maker):
    async with maker() as s:
        items = await price_floor_actions(s)
    assert [i.key for i in items] == ["price_floor:NEAR"]
    item = items[0]
    assert isinstance(item, ActionItem) and item.kind == "price_floor" and item.target == "pricing"
    loss = 2 * (25 / 0.9 - 26)
    assert item.impact_usd == pytest.approx(loss * 30 / 90)
    assert "$28.00" in item.detail and "$22.22" in item.detail  # precio actual vs mínimo sugerido
    assert "Maine" in item.detail
    assert "price_floor" in action_center.providers()
    assert len(items) <= MAX_ACTIONS


@pytest.mark.asyncio
async def test_endpoint(maker):
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()  # el router aún no está en main.py: se prueba montado aparte
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/pricing/min-price?days=30&target_margin=0.1&channel=tiktok_shop")
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["target_margin"] == 0.1 and {r["sku"] for r in body["rows"]} == {"NEAR", "NOCOST", "NOLABEL"}
            assert (await client.get("/api/pricing/min-price?days=3")).status_code == 422
            assert (await client.get("/api/pricing/min-price?target_margin=0.95")).status_code == 422
            assert (await client.get("/api/pricing/min-price?channel=bad")).status_code == 422
    finally:
        app.dependency_overrides.clear()
