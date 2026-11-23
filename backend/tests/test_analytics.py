"""Análisis avanzado: precios, aranceles, reposición, caja, conciliación, devoluciones, envío,
promociones, creadores, pronóstico, metas y estado de resultados."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import polars as pl
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import landed
from app.cashflow_forecast import cash_projection, payment_lag_days
from app.costs import set_product_cost
from app.creators import decide
from app.db import create_dev_schema, get_session, make_engine
from app.economics import UnitEconomics, base_sku
from app.forecast import (
    classify_progress,
    ewma,
    goal_progress,
    project,
    upsert_goal,
    weekday_index,
)
from app.gold import refresh_daily_profit
from app.importers.persist import import_orders
from app.main import app
from app.models import (
    Account,
    Expense,
    ExpenseCategory,
    Product,
    ReturnCase,
    ReturnCaseLine,
    Settlement,
    Shipment,
    ShippingLabelCost,
)
from app.pnl import expense_for_month, months_between, pnl_csv, pnl_report
from app.pricing import parity_report, price_floor_report, solve_price
from app.promo_uplift import compare_sides, daily_series
from app.reconciliation import reconciliation_report
from app.reorder import classify, plan_row, reorder_report
from app.returns_cost import reason_policy, returns_cost_report
from app.shipping_profit import shipping_profitability_report

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

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker
    app.dependency_overrides.clear()
    await engine.dispose()


def frame(rows: list[dict]) -> pl.DataFrame:
    """Líneas de orden canónicas. `rows` solo necesita lo que cambia; el resto toma valores por defecto."""
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


async def set_costs(session, costs: dict[str, float]) -> None:
    for sku, amount in costs.items():
        pid = await session.scalar(select(Product.id).where(Product.sku == sku))
        await set_product_cost(session, pid, Decimal(str(amount)), None)


# ------------------------------------------------------------------------------ precios


def _econ(**kw) -> UnitEconomics:
    base = {
        "channel": "walmart",
        "sku": "A",
        "product_name": None,
        "units": 10,
        "gross_sales": 300.0,
        "promo": 0.0,
        "platform_fees": 45.0,
        "shipping_cost": 50.0,
        "refund_amount": 0.0,
        "cogs": 100.0,
        "ad_spend": 0.0,
        "net_profit": 105.0,
    }
    return UnitEconomics(**{**base, **kw})


def test_solve_price_matches_hand_calculation():
    e = _econ(refund_amount=15.0)  # comisión 15 %, reembolsos 5 %, costo 10, envío 5
    assert solve_price(e, 0.0) == pytest.approx(15 / 0.80)
    assert solve_price(e, 0.15) == pytest.approx(15 / 0.65)
    assert solve_price(e, 0.80) is None  # comisión + reembolsos + margen se comen casi todo


def test_base_sku_strips_pack_suffix():
    assert base_sku("XMAS-7702-1PK") == "XMAS-7702" and base_sku("TOR-003") == "TOR-003"


@pytest.mark.asyncio
async def test_price_floor_report_end_to_end(env):
    _, maker = env
    df = frame(
        [{"date": TODAY - timedelta(days=i), "order_id": f"O{i}", "sku": "A", "units": 1, "gross_sales": 30.0,
          "platform_fees": 4.5, "shipping_cost": 5.0} for i in range(10)]
        + [{"date": TODAY, "order_id": "B1", "sku": "B", "units": 5, "gross_sales": 50.0}]
    )
    async with maker() as s:
        await import_orders(s, df)
        await set_costs(s, {"A": 10.0})
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()
    async with maker() as s:
        report = await price_floor_report(s, days=30)
    rows = {r["sku"]: r for r in report["rows"]}
    assert rows["A"]["break_even_price"] == pytest.approx(15 / 0.85)
    assert rows["A"]["status"] == "headroom"  # cobra $30 contra una meta de ~$21
    assert rows["A"]["max_discount_break_even"] == pytest.approx(1 - (15 / 0.85) / 30)
    assert rows["B"]["status"] == "no_cost"  # sin costo el piso no significa nada


@pytest.mark.asyncio
async def test_parity_flags_price_gap_between_channels(env):
    _, maker = env
    rows = []
    for i in range(4):
        rows.append({"date": TODAY - timedelta(days=i), "order_id": f"W{i}", "sku": "X-1PK", "gross_sales": 30.0})
        rows.append({"date": TODAY - timedelta(days=i), "order_id": f"T{i}", "sku": "X", "gross_sales": 36.0,
                     "channel": "tiktok_shop", "account": "tiktok_shop_us"})
    async with maker() as s:
        await import_orders(s, frame(rows))
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()
    async with maker() as s:
        report = await parity_report(s, days=30)
    (row,) = report["rows"]
    assert row["base_sku"] == "X" and row["flag"] == "gap"
    assert row["price_gap"] == pytest.approx(0.2)
    assert row["priciest_channel"] == "tiktok_shop"


# ------------------------------------------------------------------------------ aranceles


def _container_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "Received Date": [datetime(2026, 3, 4), datetime(2026, 3, 4), datetime(2026, 5, 1), datetime(2026, 5, 1)],
            "Container": ["C1", "C1", "C2", "C2"],
            "PO": ["P1", "P1", "P2", None],
            "Item": ["SKU-A", "SKU-A", "SKU-A", "CONTAINER TOTAL"],
            "Description": ["x", "x", "x", None],
            "Qty Received": [60, 40, 100, 100],
            "Expected Rate": [10.0, 10.0, 12.0, None],
            "Unit Duties": [1.0, 1.0, 2.4, None],
            "Unit Freight": [0.5, 0.5, 0.6, None],
        }
    )


def test_parse_container_report_aggregates_and_skips_totals():
    receipts = landed.parse_container_report(_container_frame())
    assert len(receipts) == 2  # las dos filas de C1/P1/SKU-A se juntan; el total se ignora
    c1 = next(r for r in receipts if r["container"] == "C1")
    assert c1["qty"] == 100 and c1["fob_unit"] == pytest.approx(10.0) and c1["duty_unit"] == pytest.approx(1.0)


def test_parse_container_report_requires_columns():
    with pytest.raises(landed.LandedFileError):
        landed.parse_container_report(pl.DataFrame({"Item": ["A"]}))


def test_parse_flat_costs():
    df = pl.DataFrame({"ITEM": ["A", "B", None], "LANDED UNIT COST": [4.5, 0.0, 3.0]})
    assert landed.parse_flat_costs(df) == {"A": 4.5}


def test_match_key_joins_pack_and_ecomm_variants():
    assert landed.match_key("GDL-187E") == landed.match_key("GDL-187") == "GDL-187"
    assert landed.match_key("XMAS-8223-6PK") == "XMAS-8223"


@pytest.mark.asyncio
async def test_receipts_become_cost_history_and_tariff_simulation(env):
    _, maker = env
    async with maker() as s:
        await import_orders(
            s,
            frame([{"date": TODAY - timedelta(days=i), "order_id": f"O{i}", "sku": "SKU-A", "units": 1,
                    "gross_sales": 40.0, "platform_fees": 6.0, "shipping_cost": 5.0} for i in range(5)]),
        )
        receipts = landed.parse_container_report(_container_frame())
        first = await landed.save_receipts(s, receipts)
        again = await landed.save_receipts(s, receipts)
        applied = await landed.apply_receipts_to_costs(s)
        await refresh_daily_profit(s, None, None, reason="test")
        await s.commit()
    assert first["receipts_inserted"] == 2 and again == {"receipts_inserted": 0, "receipts_updated": 2}
    assert applied["products_costed"] == 1

    async with maker() as s:
        report = await landed.tariff_report(s, extra_duty=0.10, days=30)
        costs = await landed.apply_receipts_to_costs(s)
    assert costs["cost_changes"] == 0  # volver a aplicar no cambia nada
    (row,) = report["rows"]
    assert row["fob"] == pytest.approx(12.0)  # el contenedor más reciente
    assert row["delta_unit"] == pytest.approx(1.2)  # +10 puntos sobre el valor de fábrica
    assert row["profit_new"] == pytest.approx(row["profit_now"] - 1.2 * 5)
    assert [m["month"] for m in report["monthly"]] == ["2026-03", "2026-05"]
    assert report["monthly"][0]["duty_rate"] == pytest.approx(0.10)


# ------------------------------------------------------------------------------ reposición


def test_reorder_classification():
    assert classify(avail=0, daily=1, lead=60, safety=14, units_sold=30) == "late"
    assert classify(avail=70, daily=1, lead=60, safety=14, units_sold=30) == "order_now"
    assert classify(avail=100, daily=1, lead=60, safety=14, units_sold=30) == "ok"
    assert classify(avail=400, daily=1, lead=60, safety=14, units_sold=30) == "overstock"
    assert classify(avail=10, daily=0, lead=60, safety=14, units_sold=0) == "dead"
    assert classify(avail=0, daily=0, lead=60, safety=14, units_sold=0) == "no_demand"


def test_reorder_plan_row_quantities():
    row = {"account": "a", "channel": "walmart", "sku": "A", "product_name": None, "published_status": "PUBLISHED",
           "avail_to_sell": 30, "units_sold": 30}
    plan = plan_row(row, unit_cost=5.0, lead=60, safety=14, cover=60, multiplier=1.0, window_days=30, today=TODAY)
    assert plan["daily_units"] == pytest.approx(1.0)
    assert plan["order_qty"] == 134 - 30  # 1 x (60 + 14 + 60) - existencias
    assert plan["status"] == "late" and plan["days_late"] == 30
    assert plan["order_cost"] == pytest.approx(104 * 5.0)


@pytest.mark.asyncio
async def test_reorder_report_uses_stock_and_cost(env):
    _, maker = env
    from app.connectors.walmart import build_listing_stock
    from app.ops_data import upsert_listing_stock

    async with maker() as s:
        await import_orders(
            s, frame([{"date": TODAY - timedelta(days=i), "order_id": f"O{i}", "sku": "A", "gross_sales": 20.0} for i in range(10)])
        )
        await set_costs(s, {"A": 8.0})
        stock = build_listing_stock(
            [{"sku": "A", "productName": "A", "price": {"amount": 20}, "publishedStatus": "PUBLISHED"}],
            [{"sku": "A", "nodes": [{"availToSellQty": {"amount": 5}}]}],
        )
        await upsert_listing_stock(s, "walmart", "walmart_1", stock)
        await s.commit()
    async with maker() as s:
        report = await reorder_report(s, days=30)
    (row,) = report["rows"]
    assert row["unit_cost"] == pytest.approx(8.0) and row["stock_value"] == pytest.approx(40.0)
    assert row["status"] == "late" and report["summary"]["units_to_order"] == row["order_qty"] > 0


# ------------------------------------------------------------------------------ caja


@pytest.mark.asyncio
async def test_cash_projection_uses_measured_lag_and_pending(env):
    _, maker = env
    async with maker() as s:
        acc = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
        s.add(acc)
        await s.flush()
        for i in range(4):  # pagados con 5 días de rezago
            d = TODAY - timedelta(days=20 + i)
            s.add(Settlement(account_id=acc.id, external_id=f"P{i}", statement_date=d,
                             payout_date=d + timedelta(days=5), status="paid", net_payout=Decimal(100)))
        s.add(Settlement(account_id=acc.id, external_id="W1", statement_date=TODAY - timedelta(days=2),
                         status="processing", net_payout=Decimal(250)))
        await s.commit()
    async with maker() as s:
        rows = list((await s.scalars(select(Settlement))).all())
        assert payment_lag_days(rows) == 5.0
        report = await cash_projection(s, horizon_days=14)
    assert report["lag_days"] == 5 and report["lag_measured"]
    assert report["summary"]["pending_total"] == pytest.approx(250.0)
    assert report["summary"]["known_in_horizon"] == pytest.approx(250.0)  # se espera dentro del horizonte
    assert len(report["series"]) == 14 and report["channels_covered"] == ["tiktok_shop"]


@pytest.mark.asyncio
async def test_cash_projection_without_settlements_says_so(env):
    _, maker = env
    async with maker() as s:
        report = await cash_projection(s)
    assert report["note"] and report["summary"]["expected_in_horizon"] == 0


# ------------------------------------------------------------------------------ conciliación


@pytest.mark.asyncio
async def test_reconciliation_classifies_labels_fees_and_payments(env):
    _, maker = env
    df = frame(
        [{"date": TODAY - timedelta(days=i), "order_id": f"O{i}", "sku": "A", "gross_sales": 100.0, "platform_fees": 15.0}
         for i in range(6)]
        + [{"date": TODAY, "order_id": "ZERO", "sku": "A", "gross_sales": 100.0, "platform_fees": 0.0},
           {"date": TODAY, "order_id": "HIGH", "sku": "A", "gross_sales": 100.0, "platform_fees": 60.0}]
    )
    async with maker() as s:
        await import_orders(s, df)
        acc = await s.scalar(select(Account).where(Account.name == "walmart_1"))
        s.add(Shipment(account_id=acc.id, external_order_id="SAMPLE1", order_date=TODAY, is_sample=True))
        for oid, amount in (("O1", 8), ("SAMPLE1", 9), ("GHOST", 12.5)):
            s.add(ShippingLabelCost(source="redo", external_order_id=oid, amount=Decimal(str(amount)), charges=1,
                                    last_charged=TODAY))
        s.add(Settlement(account_id=acc.id, external_id="OLD", statement_date=TODAY - timedelta(days=30),
                         status="processing", net_payout=Decimal(77)))
        s.add(Settlement(account_id=acc.id, external_id="BAD", statement_date=TODAY - timedelta(days=3),
                         status="failed", net_payout=Decimal(40)))
        await s.commit()
    async with maker() as s:
        report = await reconciliation_report(s)
    labels = report["labels"]
    assert labels["summary"]["order"]["amount"] == 8 and labels["summary"]["sample"]["amount"] == 9
    assert labels["unmatched_total"] == 12.5 and labels["unmatched"][0]["order_id"] == "GHOST"
    assert report["fees"]["zero_fee_count"] == 1 and report["fees"]["high_fee_count"] == 1
    assert report["settlements"]["failed_total"] == 40 and report["settlements"]["stuck_total"] == 77


# ------------------------------------------------------------------------------ devoluciones


def test_reason_policy():
    assert reason_policy("DAMAGED_IN_TRANSIT", 0.5) == (1.0, True, True)
    assert reason_policy("NO_LONGER_WANTED", 0.5) == (0.0, False, True)
    assert reason_policy("INCORRECT_ITEM", 0.5) == (0.0, True, True)
    assert reason_policy("SOMETHING_NEW", 0.5) == (0.5, False, False)
    assert reason_policy(None, 0.3)[0] == 0.3


@pytest.mark.asyncio
async def test_returns_cost_counts_lost_shipping_and_product(env):
    _, maker = env
    async with maker() as s:
        await import_orders(s, frame([{"date": TODAY, "order_id": "O1", "sku": "A", "units": 2, "gross_sales": 80.0}]))
        await set_costs(s, {"A": 10.0})
        acc = await s.scalar(select(Account).where(Account.name == "walmart_1"))
        s.add(ShippingLabelCost(source="redo", external_order_id="O1", amount=Decimal(12), charges=1))
        for ext, reason, status in (("R1", "DAMAGED", "COMPLETED"), ("R2", "NO_LONGER_WANTED", "COMPLETED"),
                                    ("R3", "DAMAGED", "BUYER_CANCEL")):
            case = ReturnCase(account_id=acc.id, external_id=ext, kind="return", status=status, reason=reason,
                              external_order_id="O1", case_date=TODAY, refund_total=Decimal(40))
            s.add(case)
            await s.flush()
            s.add(ReturnCaseLine(case_id=case.id, sku="A", units=1, refund_total=Decimal(40)))
        await s.commit()
    async with maker() as s:
        report = await returns_cost_report(s, days=30)
    assert report["summary"]["cases"] == 2  # la solicitud retirada no cuenta
    reasons = {r["reason"]: r for r in report["by_reason"]}
    assert reasons["DAMAGED"]["product_lost"] == pytest.approx(10.0)  # 1 unidad x $10 x 100 %
    assert reasons["NO_LONGER_WANTED"]["product_lost"] == 0.0
    assert reasons["DAMAGED"]["shipping_lost"] == pytest.approx(6.0)  # etiqueta $12 x 1 de 2 unidades
    assert report["by_sku"][0]["real_cost"] == pytest.approx(10.0 + 6.0 + 6.0)


# ------------------------------------------------------------------------------ envío


@pytest.mark.asyncio
async def test_shipping_profitability_by_state_and_sku(env):
    _, maker = env
    rows = [
        {"date": TODAY - timedelta(days=i), "order_id": f"F{i}", "sku": "HEAVY", "gross_sales": 20.0, "ship_state": "Florida"}
        for i in range(4)
    ] + [
        {"date": TODAY - timedelta(days=i), "order_id": f"T{i}", "sku": "LIGHT", "gross_sales": 100.0, "ship_state": "Texas"}
        for i in range(4)
    ]
    async with maker() as s:
        await import_orders(s, frame(rows))
        for i in range(4):
            s.add(ShippingLabelCost(source="redo", external_order_id=f"F{i}", amount=Decimal(8), charges=1))
            s.add(ShippingLabelCost(source="redo", external_order_id=f"T{i}", amount=Decimal(8), charges=1))
        await s.commit()
    async with maker() as s:
        report = await shipping_profitability_report(s, days=30)
    states = {r["key"]: r for r in report["by_state"]}
    assert states["Florida"]["shipping_share"] == pytest.approx(0.4)
    assert states["Texas"]["shipping_share"] == pytest.approx(0.08)
    skus = {r["key"]: r for r in report["by_sku"]}
    assert skus["HEAVY"]["flag"] and not skus["LIGHT"]["flag"]
    assert report["summary"]["state_coverage"] == pytest.approx(1.0)


# ------------------------------------------------------------------------------ promociones


def test_promo_effect_compares_promo_and_normal_days():
    days = {}
    for i in range(10):
        d = date(2026, 1, 1) + timedelta(days=i)
        promo_day = i % 2 == 0
        days[d] = {"units": 10.0 if promo_day else 4.0, "gross": 100.0, "promo": 20.0 if promo_day else 0.0,
                   "profit": 30.0 if promo_day else 15.0}
    series = daily_series(days, date(2026, 1, 1), date(2026, 1, 10))
    result = compare_sides(series, 0.05)
    assert result["promo_days"] == 5 and result["normal_days"] == 5
    assert result["uplift"] == pytest.approx(10 / 4 - 1)
    assert result["profit_delta_per_day"] == pytest.approx(15.0) and result["verdict"] == "worth_it"


def test_promo_effect_needs_enough_days():
    series = daily_series({date(2026, 1, 1): {"units": 1.0, "gross": 10.0, "promo": 5.0, "profit": 1.0}},
                          date(2026, 1, 1), date(2026, 1, 2))
    assert compare_sides(series, 0.05)["verdict"] == "insufficient"


# ------------------------------------------------------------------------------ creadores


def test_creator_decision_rules():
    base = {"affiliate_orders": 0, "net": -20.0}
    assert decide(base, days_since_sample=5, wait_days=14) == "wait"
    assert decide(base, days_since_sample=30, wait_days=14) == "cut"
    assert decide({"affiliate_orders": 3, "net": 50.0}, 60, 14) == "repeat"
    assert decide({"affiliate_orders": 1, "net": 50.0}, 60, 14) == "keep"
    assert decide({"affiliate_orders": 3, "net": -5.0}, 60, 14) == "keep"


# ------------------------------------------------------------------------------ pronóstico y metas


def test_forecast_primitives():
    assert ewma([10.0] * 5) == pytest.approx(10.0)
    assert ewma([0.0] * 14 + [10.0] * 14) > ewma([10.0] * 14 + [0.0] * 14)  # pesa más lo reciente
    dates = [date(2026, 1, 5) + timedelta(days=i) for i in range(28)]
    values = [20.0 if d.weekday() == 5 else 10.0 for d in dates]  # los sábados se vende el doble
    index = weekday_index(dates, values)
    assert index[5] > index[0]
    forecast, _ = project(dates, [10.0] * 28, [date(2026, 2, 2)], multiplier=1.5)
    assert forecast[0] == pytest.approx(15.0)


def test_goal_status():
    assert classify_progress(1100, 1000) == "on_track"
    assert classify_progress(950, 1000) == "at_risk"
    assert classify_progress(500, 1000) == "behind"
    assert classify_progress(10, 0) == "no_target"


@pytest.mark.asyncio
async def test_goal_progress_projects_month_end(env):
    _, maker = env
    rows = [{"date": TODAY - timedelta(days=i), "order_id": f"O{i}", "sku": "A", "gross_sales": 100.0} for i in range(28)]
    async with maker() as s:
        await import_orders(s, frame(rows))
        await refresh_daily_profit(s, None, None, reason="test")
        await upsert_goal(s, TODAY, "gross_sales", 10_000.0)
        await upsert_goal(s, TODAY, "gross_sales", 20_000.0)  # actualizar, no duplicar
        await s.commit()
    async with maker() as s:
        report = await goal_progress(s, TODAY, today=TODAY)
    (goal,) = report["goals"]
    assert goal["target"] == 20_000.0 and goal["actual"] > 0
    assert goal["projected"] >= goal["actual"] and goal["status"] in ("behind", "at_risk", "on_track")
    async with maker() as s:
        with pytest.raises(ValueError):
            await upsert_goal(s, TODAY, "bogus", 1.0)


# ------------------------------------------------------------------------------ estado de resultados


def _expense(**kw) -> Expense:
    base = {
        "category_id": 1,
        "description": "x",
        "amount": Decimal(120),
        "recurrence": "monthly",
        "start_date": date(2026, 2, 10),
        "end_date": None,
        "channel_code": None,
        "is_active": True,
    }
    return Expense(**{**base, **kw})


def test_expense_amount_by_month():
    monthly = _expense()
    assert expense_for_month(monthly, date(2026, 1, 1)) == 0
    assert expense_for_month(monthly, date(2026, 2, 1)) == 120 and expense_for_month(monthly, date(2027, 1, 1)) == 120
    assert expense_for_month(_expense(end_date=date(2026, 3, 31)), date(2026, 4, 1)) == 0
    one = _expense(recurrence="one_time")
    assert expense_for_month(one, date(2026, 2, 1)) == 120 and expense_for_month(one, date(2026, 3, 1)) == 0
    annual = _expense(recurrence="annual")
    assert expense_for_month(annual, date(2026, 2, 1)) == 10 and expense_for_month(annual, date(2027, 2, 1)) == 0
    assert expense_for_month(_expense(is_active=False), date(2026, 2, 1)) == 0


def test_months_between():
    assert months_between(date(2026, 11, 20), date(2027, 2, 3)) == [
        date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)
    ]


@pytest.mark.asyncio
async def test_pnl_allocates_shared_expenses_by_sales(env):
    _, maker = env
    month = TODAY.replace(day=1)
    rows = [
        {"date": TODAY, "order_id": "W1", "sku": "A", "gross_sales": 300.0, "platform_fees": 45.0},
        {"date": TODAY, "order_id": "T1", "sku": "A", "gross_sales": 100.0, "channel": "tiktok_shop",
         "account": "tiktok_shop_us"},
    ]
    async with maker() as s:
        await import_orders(s, frame(rows))
        await set_costs(s, {"A": 10.0})
        await refresh_daily_profit(s, None, None, reason="test")
        cat = await s.scalar(select(ExpenseCategory.id).limit(1))
        s.add(Expense(category_id=cat, description="Software", amount=Decimal(40), recurrence="monthly", start_date=month))
        s.add(Expense(category_id=cat, description="Bodega TikTok", amount=Decimal(10), recurrence="monthly",
                      start_date=month, channel_code="tiktok_shop"))
        await s.commit()
    async with maker() as s:
        report = await pnl_report(s, start=month, end=month, today=TODAY)
    (m,) = report["months"]
    by = {r["channel"]: r for r in m["channels"]}
    assert by["walmart"]["expenses"] == pytest.approx(30.0)  # 75 % de $40
    assert by["tiktok_shop"]["expenses"] == pytest.approx(10.0 + 10.0)  # 25 % de $40 + su gasto propio
    assert m["total"]["expenses"] == pytest.approx(50.0)
    assert m["total"]["net_profit"] == pytest.approx(m["total"]["contribution"] - 50.0)
    csv_text = pnl_csv(report)
    assert csv_text.splitlines()[0].startswith("month,channel") and "TOTAL" in csv_text


# ------------------------------------------------------------------------------ API


GET_ENDPOINTS = [
    "/api/pricing/floor",
    "/api/pricing/parity",
    "/api/tariffs",
    "/api/reorder",
    "/api/cashflow/projection",
    "/api/reconciliation",
    "/api/returns/cost",
    "/api/shipping-profitability",
    "/api/promotions/effect",
    "/api/creators/decisions",
    "/api/forecast",
    "/api/goals",
    "/api/goals/all",
    "/api/pnl",
    "/api/pnl.csv",
    "/api/expenses",
    "/api/expense-categories",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", GET_ENDPOINTS)
async def test_every_report_answers_on_an_empty_database(env, path):
    client, _ = env
    response = await client.get(path)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_expenses_and_goals_crud_over_http(env):
    client, _ = env
    categories = (await client.get("/api/expense-categories")).json()
    assert categories  # en desarrollo se siembran las categorías

    bad = await client.post("/api/expenses", json={"category_id": categories[0]["id"], "description": "x", "amount": 5,
                                                   "start_date": "2026-03-01", "end_date": "2026-01-01"})
    assert bad.status_code == 422
    ok = await client.post("/api/expenses", json={"category_id": categories[0]["id"], "description": "Hosting", "amount": 25,
                                                  "start_date": "2026-03-01", "recurrence": "monthly"})
    assert ok.status_code == 200
    listed = (await client.get("/api/expenses")).json()
    assert listed[0]["description"] == "Hosting" and listed[0]["recurrence"] == "monthly"
    assert (await client.delete(f"/api/expenses/{ok.json()['id']}")).status_code == 200
    assert (await client.delete("/api/expenses/9999")).status_code == 404

    put = await client.put("/api/goals", json={"month": "2026-03-17", "metric": "net_profit", "target": 5000, "channel": "walmart"})
    assert put.status_code == 200
    assert (await client.get("/api/goals/all")).json()[0]["month"] == "2026-03-01"  # siempre el día 1
    assert (await client.delete(f"/api/goals/{put.json()['id']}")).status_code == 200
    assert (await client.put("/api/goals", json={"month": "2026-03-01", "metric": "nope", "target": 1})).status_code == 422


@pytest.mark.asyncio
async def test_landed_import_rejects_garbage(env):
    client, _ = env
    response = await client.post(
        "/api/import/landed-costs", files={"file": ("x.xlsx", b"not an excel", "application/octet-stream")}
    )
    assert response.status_code == 422
