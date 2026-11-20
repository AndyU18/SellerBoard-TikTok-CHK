import polars as pl
import pytest

from app.importers.canonical import (
    ImportValidationError,
    normalize_ad_spend,
    normalize_orders,
    read_table,
)

CSV = (
    "Fecha,Canal,Cuenta,Order ID,SKU,Unidades,Ventas,Comisiones,Envío,Reembolsos\n"
    '11/18/2026,Amazon,amazon_1,111-1,GRN-100,2,"$1,149.98",(172.50),-10.00,0\n'
    "2026-11-18,TikTok Shop,tt_1,TT-9,GRN-100,1,30.00,4.50,0,\n"
    "2026-11-19,walmart.com,wm_1,WM-5,TOR-003,3,75.00,9.00,5,0\n"
).encode()


def test_reads_aliases_money_and_dates():
    out = normalize_orders(read_table(CSV, "orders.csv"))
    assert out.columns[:6] == [
        "date",
        "channel",
        "account",
        "order_id",
        "sku",
        "product_name",
    ]
    amazon = out.filter(pl.col("channel") == "amazon").to_dicts()[0]
    assert amazon["gross_sales"] == 1149.98
    assert amazon["units"] == 2
    assert str(amazon["date"]) == "2026-11-18"
    assert set(out["channel"]) == {"amazon", "tiktok_shop", "walmart"}


def test_costs_are_always_positive():
    out = normalize_orders(read_table(CSV, "orders.csv")).filter(
        pl.col("channel") == "amazon"
    )
    row = out.to_dicts()[0]
    assert row["platform_fees"] == 172.5  # venía como (172.50)
    assert row["shipping_cost"] == 10.0  # venía como -10.00


def test_missing_cost_columns_default_to_zero():
    csv = b"date,channel,account,order_id,sku,units,sales\n2026-11-18,amazon,a1,1,X,1,10\n"
    row = normalize_orders(read_table(csv, "o.csv")).to_dicts()[0]
    assert row["promo"] == 0.0 and row["refund_amount"] == 0.0


def test_rejects_bad_rows_with_row_numbers():
    csv = (
        b"date,channel,account,order_id,sku,units,sales\n"
        b"2026-11-18,amazon,a1,1,X,1,10\n"
        b"not-a-date,ebay,a1,2,X,1,10\n"
    )
    with pytest.raises(ImportValidationError) as exc:
        normalize_orders(read_table(csv, "o.csv"))
    assert any("Fecha inválida en filas [3]" in p for p in exc.value.problems)
    assert any("Canal desconocido en filas [3]" in p for p in exc.value.problems)


def test_rejects_missing_required_column():
    with pytest.raises(ImportValidationError, match="Falta la columna: sku"):
        normalize_orders(
            read_table(b"date,channel,account,order_id,units,sales\n", "o.csv")
        )


def test_rejects_unsupported_extension():
    with pytest.raises(ImportValidationError, match="Formato no soportado"):
        read_table(b"x", "report.pdf")


def test_ad_spend_normalizes_negative_amounts():
    csv = b"date,channel,account,spend\n2026-11-18,TikTok Ads,tt_1,-25.50\n"
    row = normalize_ad_spend(read_table(csv, "ads.csv")).to_dicts()[0]
    assert (
        row["channel"] == "tiktok_shop" and row["amount"] == 25.5 and row["sku"] is None
    )
