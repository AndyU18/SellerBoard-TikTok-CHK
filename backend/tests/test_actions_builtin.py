"""Proveedores integrados del Centro de acciones, con los reportes reutilizados sustituidos por datos sintéticos."""

from datetime import date

import pytest

from app import actions_builtin as ab

TODAY = date(2026, 11, 24)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(ab, "_today", lambda: TODAY)


def _fake(monkeypatch, name: str, value):
    async def fn(*_a, **_k):
        return value

    monkeypatch.setattr(ab, name, fn)


def _corr_row(sku: str, amount: float, flag: bool = True, reasons=None) -> dict:
    return {
        "sku": sku,
        "shipped_orders": 20,
        "orders_with_correction": 6,
        "correction_rate": 0.3,
        "correction_amount": amount,
        "flag": flag,
        "suggestion": "Revisa el peso" if flag else None,
        "top_reasons": reasons if reasons is not None else [{"reason": "Peso", "count": 6, "amount": amount}],
    }


# ---------------------------------------------------------------- carrier_dimensions
@pytest.mark.asyncio
async def test_carrier_projects_window_amount_to_30_days(monkeypatch):
    _fake(
        monkeypatch,
        "redo_corrections_by_sku",
        {"days": 90, "skus": [_corr_row("A", 90.0), _corr_row("B", 500.0, flag=False), _corr_row("C", 0.0)]},
    )
    items = await ab.carrier_dimensions(None)
    assert [i.key for i in items] == ["carrier_dimensions:A"]
    assert items[0].impact_usd == pytest.approx(30.0)  # 90 x 30/90
    assert items[0].target == "redoCorrections" and "Peso" in items[0].detail


@pytest.mark.asyncio
async def test_carrier_without_data_or_reasons(monkeypatch):
    _fake(monkeypatch, "redo_corrections_by_sku", {"days": 90, "skus": []})
    assert await ab.carrier_dimensions(None) == []
    _fake(monkeypatch, "redo_corrections_by_sku", {"days": 90, "skus": [_corr_row("A", 10.0, reasons=[])]})
    assert len(await ab.carrier_dimensions(None)) == 1


@pytest.mark.asyncio
async def test_carrier_caps_and_orders_by_impact(monkeypatch):
    rows = [_corr_row(f"S{i:02d}", 10.0 + i) for i in range(ab.MAX_ACTIONS_PER_PROVIDER + 5)]
    _fake(monkeypatch, "redo_corrections_by_sku", {"days": 90, "skus": rows})
    items = await ab.carrier_dimensions(None)
    assert len(items) == ab.MAX_ACTIONS_PER_PROVIDER
    assert items[0].impact_usd >= items[-1].impact_usd


# ---------------------------------------------------------------- loss_orders
def _loss_report() -> dict:
    return {
        "by_driver": [
            {"key": "shipping", "label": "Envío", "orders": 3, "amount": 45.0},
            {"key": "promo", "label": "Promociones", "orders": 1, "amount": 5.0},
            {"key": "desconocida", "label": "X", "orders": 1, "amount": 9.0},
            {"key": "fees", "label": "Comisiones", "orders": 0, "amount": 0.0},
        ],
        "rows": [
            {"driver": "shipping", "profit": -10.0, "skus": ["A"]},
            {"driver": "shipping", "profit": -20.0, "skus": ["A", "B"]},
            {"driver": "shipping", "profit": -15.0, "skus": ["B"]},
            {"driver": "promo", "profit": -5.0, "skus": ["C"]},
            {"driver": None, "profit": 4.0, "skus": ["Z"]},
        ],
    }


@pytest.mark.asyncio
async def test_loss_orders_one_action_per_cause(monkeypatch):
    _fake(monkeypatch, "order_profit_report", _loss_report())
    items = await ab.loss_orders(None)
    assert [i.key for i in items] == ["loss_orders:shipping", "loss_orders:promo"]
    shipping = items[0]
    assert shipping.impact_usd == 45.0 and shipping.target == "orderProfit"
    # A pierde 10 + 10 = 20 y B pierde 10 + 15 = 25: el más afectado es B
    assert shipping.meta["top_sku"] == "B" and "B" in shipping.detail and "3 órdenes" in shipping.title


@pytest.mark.asyncio
async def test_loss_orders_without_losses(monkeypatch):
    _fake(monkeypatch, "order_profit_report", {"by_driver": [], "rows": []})
    assert await ab.loss_orders(None) == []


def test_worst_skus_ignores_profitable_and_unattributed_orders():
    rows = [
        {"driver": "fees", "profit": 3.0, "skus": ["A"]},
        {"driver": "fees", "profit": -3.0, "skus": []},
        {"driver": None, "profit": -3.0, "skus": ["A"]},
    ]
    assert ab._worst_skus(rows) == {}


# ---------------------------------------------------------------- unpublished_listings
def _stock_row(sku: str, status: str = "unpublished", daily: float = 2.0, reasons=None) -> dict:
    return {
        "account": "Walmart",
        "sku": sku,
        "status": status,
        "daily_units": daily,
        "published_status": "UNPUBLISHED",
        "unpublished_reasons": reasons,
        "avail_to_sell": 10,
    }


@pytest.mark.asyncio
async def test_unpublished_impact_is_daily_units_times_profit(monkeypatch):
    rows = [
        _stock_row("A", reasons="Imagen rechazada"),
        _stock_row("B"),  # sin utilidad conocida
        _stock_row("C", daily=0.0),  # sin ventas
        _stock_row("D", status="ok"),
        _stock_row("E"),  # utilidad negativa
    ]
    _fake(monkeypatch, "inventory_report", {"rows": rows})
    _fake(monkeypatch, "_unit_profit_by_sku", {"A": 4.0, "D": 9.0, "E": -1.0})
    items = await ab.unpublished_listings(None)
    assert [i.key for i in items] == ["unpublished_listings:Walmart:A"]
    assert items[0].impact_usd == pytest.approx(2.0 * 30 * 4.0)
    assert "Imagen rechazada" in items[0].detail and items[0].target == "inventory"


@pytest.mark.asyncio
async def test_unpublished_without_candidates_skips_profit_lookup(monkeypatch):
    _fake(monkeypatch, "inventory_report", {"rows": [_stock_row("A", status="ok")]})

    async def boom(*_a, **_k):
        raise AssertionError("no debía consultar utilidades")

    monkeypatch.setattr(ab, "_unit_profit_by_sku", boom)
    assert await ab.unpublished_listings(None) == []


@pytest.mark.asyncio
async def test_unit_profit_empty_frame(monkeypatch):
    import polars as pl

    _fake(monkeypatch, "load_gold_frame", pl.DataFrame())
    assert await ab._unit_profit_by_sku(None) == {}
