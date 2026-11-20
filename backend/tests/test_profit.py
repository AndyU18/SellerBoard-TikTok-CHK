import polars as pl
import pytest

from app.profit import (
    profit_by_channel,
    profit_by_channel_and_product,
    profit_by_product,
)


def _row(channel, account, sku, units, sales, **kw):
    base = {
        "channel": channel,
        "account": account,
        "sku": sku,
        "units": units,
        "gross_sales": sales,
        "promo": 0.0,
        "platform_fees": 0.0,
        "shipping_cost": 0.0,
        "refund_amount": 0.0,
        "ad_spend": 0.0,
        "cogs_unit": 0.0,
    }
    base.update(kw)
    return base


@pytest.fixture
def df():
    return pl.DataFrame(
        [
            # Amazon: 10 u x $50, fees 150, cogs 20/u, ads 40 -> 500-150-200-40 = 110
            _row(
                "amazon",
                "amazon_1",
                "GRN-100",
                10,
                500.0,
                platform_fees=150.0,
                cogs_unit=20.0,
                ad_spend=40.0,
            ),
            # TikTok Shop pierde dinero: 150-40-25-100-30 = -45
            _row(
                "tiktok_shop",
                "tt_1",
                "GRN-100",
                5,
                150.0,
                platform_fees=40.0,
                shipping_cost=25.0,
                cogs_unit=20.0,
                ad_spend=30.0,
            ),
            # Walmart, otro producto: 100-12-40 = 48
            _row(
                "walmart",
                "wm_1",
                "TOR-003",
                4,
                100.0,
                platform_fees=12.0,
                cogs_unit=10.0,
            ),
        ]
    )


def test_net_profit_by_channel(df):
    out = {r["channel"]: r for r in profit_by_channel(df).to_dicts()}
    assert out["amazon"]["net_profit"] == 110.0
    assert out["tiktok_shop"]["net_profit"] == -45.0
    assert out["walmart"]["net_profit"] == 48.0


def test_unit_profit_can_be_negative(df):
    out = {r["channel"]: r for r in profit_by_channel(df).to_dicts()}
    assert out["amazon"]["unit_profit"] == 11.0
    assert out["tiktok_shop"]["unit_profit"] == -9.0  # -45 / 5


def test_by_product_merges_channels(df):
    out = {r["sku"]: r for r in profit_by_product(df).to_dicts()}
    assert out["GRN-100"]["net_profit"] == 65.0  # 110 + (-45)
    assert out["GRN-100"]["units"] == 15
    assert out["GRN-100"]["unit_profit"] == pytest.approx(4.33)


def test_by_channel_and_product(df):
    rows = profit_by_channel_and_product(df).to_dicts()
    assert len(rows) == 3
    tt = next(r for r in rows if r["channel"] == "tiktok_shop")
    assert tt["sku"] == "GRN-100" and tt["net_profit"] == -45.0


def test_zero_units_gives_null_not_inf():
    df = pl.DataFrame([_row("walmart", "wm_1", "X", 0, 0.0, ad_spend=5.0)])
    row = profit_by_channel(df).to_dicts()[0]
    assert row["net_profit"] == -5.0
    assert row["unit_profit"] is None
    assert row["margin"] is None


def test_allocated_expenses_reduce_profit(df):
    df = df.with_columns(pl.lit(10.0).alias("allocated_expenses"))
    out = {r["channel"]: r for r in profit_by_channel(df).to_dicts()}
    assert out["amazon"]["net_profit"] == 100.0


def test_missing_columns_raise():
    with pytest.raises(ValueError, match="Faltan columnas"):
        profit_by_channel(pl.DataFrame({"channel": ["amazon"]}))
