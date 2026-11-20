"""Importador de formato canónico (CSV/Excel) para órdenes y publicidad.

Normaliza cabeceras (alias en inglés/español), montos ("$1,234.56", "(12.00)"),
fechas y canal. Los costos se guardan SIEMPRE en positivo.
Los perfiles específicos por plataforma se añadirán con reportes reales.
"""

from __future__ import annotations

import io
from pathlib import PurePath

import polars as pl

COST_COLUMNS = ["promo", "platform_fees", "shipping_cost", "refund_amount"]

ORDER_ALIASES: dict[str, list[str]] = {
    "date": ["date", "fecha", "order date", "order_date", "purchase date"],
    "channel": ["channel", "canal", "marketplace"],
    "account": ["account", "cuenta", "store"],
    "order_id": ["order_id", "order id", "order-id", "amazon-order-id", "orden", "order number"],
    "sku": ["sku", "seller sku", "seller-sku", "product sku"],
    "product_name": ["product_name", "product name", "title", "nombre", "producto"],
    "units": ["units", "unidades", "quantity", "qty", "quantity-purchased"],
    "gross_sales": ["gross_sales", "sales", "ventas", "item price", "item-price", "revenue"],
    "promo": ["promo", "promotion", "promotions", "descuento", "item-promotion-discount"],
    "platform_fees": ["platform_fees", "fees", "comisiones", "referral fee", "marketplace fees"],
    "shipping_cost": ["shipping_cost", "shipping", "envio", "envío", "shipping cost"],
    "refund_amount": ["refund_amount", "refunds", "reembolsos", "refund"],
}

AD_ALIASES: dict[str, list[str]] = {
    "date": ORDER_ALIASES["date"],
    "channel": ORDER_ALIASES["channel"],
    "account": ORDER_ALIASES["account"],
    "sku": ORDER_ALIASES["sku"],
    "amount": ["amount", "spend", "ad spend", "ad_spend", "cost", "gasto", "monto"],
}

COGS_ALIASES: dict[str, list[str]] = {
    "sku": ORDER_ALIASES["sku"],
    "cogs_unit": ["cogs_unit", "cogs", "cost", "unit cost", "costo", "costo unitario", "cost per unit"],
    "product_name": ORDER_ALIASES["product_name"],
}

CHANNEL_MAP = {
    "amazon": "amazon",
    "amazon.com": "amazon",
    "tiktok": "tiktok_shop",
    "tiktok shop": "tiktok_shop",
    "tiktok_shop": "tiktok_shop",
    "tiktokshop": "tiktok_shop",
    "tiktok ads": "tiktok_shop",
    "walmart": "walmart",
    "walmart.com": "walmart",
}


class ImportValidationError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def read_table(data: bytes, filename: str) -> pl.DataFrame:
    """Lee CSV o Excel como texto (sin inferir tipos)."""
    ext = PurePath(filename).suffix.lower()
    if ext in {".xlsx", ".xls"}:
        df = pl.read_excel(io.BytesIO(data), engine="calamine")
        return df.cast(dict.fromkeys(df.columns, pl.Utf8))
    if ext in {".csv", ".txt"}:
        return pl.read_csv(io.BytesIO(data), infer_schema_length=0, truncate_ragged_lines=True)
    raise ImportValidationError([f"Formato no soportado: {ext or 'sin extensión'}"])


def _rename_to_canonical(df: pl.DataFrame, aliases: dict[str, list[str]]) -> pl.DataFrame:
    lookup = {a: canon for canon, names in aliases.items() for a in names}
    rename: dict[str, str] = {}
    for col in df.columns:
        canon = lookup.get(col.strip().lower())
        if canon and canon not in rename.values():
            rename[col] = canon
    return df.select(list(rename)).rename(rename)


def _money(col: str) -> pl.Expr:
    s = pl.col(col).cast(pl.Utf8).str.strip_chars()
    negative = s.str.starts_with("(") & s.str.ends_with(")")
    value = s.str.replace_all(r"[\$,()\s]", "").cast(pl.Float64, strict=False)
    return pl.when(negative).then(-value).otherwise(value).fill_null(0.0)


def _date(col: str) -> pl.Expr:
    s = pl.col(col).cast(pl.Utf8).str.strip_chars()
    return pl.coalesce(
        s.str.to_date("%Y-%m-%d", strict=False),
        s.str.to_date("%m/%d/%Y", strict=False),
        s.str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False),
    )


def _channel() -> pl.Expr:
    s = pl.col("channel").cast(pl.Utf8).str.strip_chars().str.to_lowercase()
    return s.replace_strict(CHANNEL_MAP, default=None)


def _require(df: pl.DataFrame, needed: list[str]) -> None:
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ImportValidationError([f"Falta la columna: {c}" for c in missing])


def _check_rows(df: pl.DataFrame, checks: dict[str, pl.Expr]) -> None:
    problems = []
    for label, bad in checks.items():
        rows = df.with_row_index("n", offset=2).filter(bad)["n"].to_list()
        if rows:
            problems.append(f"{label} en filas {rows[:10]}{'…' if len(rows) > 10 else ''}")
    if problems:
        raise ImportValidationError(problems)


def normalize_orders(raw: pl.DataFrame) -> pl.DataFrame:
    """Devuelve el esquema canónico de órdenes, listo para guardar y para profit.py."""
    df = _rename_to_canonical(raw, ORDER_ALIASES)
    _require(df, ["date", "channel", "account", "order_id", "sku", "units", "gross_sales"])
    for c in COST_COLUMNS:
        if c not in df.columns:
            df = df.with_columns(pl.lit("0").alias(c))
    if "product_name" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("product_name"))

    out = df.select(
        _date("date").alias("date"),
        _channel().alias("channel"),
        pl.col("account").str.strip_chars().alias("account"),
        pl.col("order_id").str.strip_chars().alias("order_id"),
        pl.col("sku").str.strip_chars().alias("sku"),
        pl.col("product_name"),
        pl.col("units")
        .cast(pl.Utf8)
        .str.replace_all(",", "")
        .cast(pl.Float64, strict=False)
        .round(0)
        .cast(pl.Int64, strict=False)
        .alias("units"),
        _money("gross_sales").alias("gross_sales"),
        *[_money(c).abs().alias(c) for c in COST_COLUMNS],
    )
    _check_rows(
        out,
        {
            "Fecha inválida": pl.col("date").is_null(),
            "Canal desconocido": pl.col("channel").is_null(),
            "Cuenta vacía": pl.col("account").is_null() | (pl.col("account") == ""),
            "Orden vacía": pl.col("order_id").is_null() | (pl.col("order_id") == ""),
            "SKU vacío": pl.col("sku").is_null() | (pl.col("sku") == ""),
            "Unidades inválidas": pl.col("units").is_null(),
        },
    )
    return out


def normalize_cogs(raw: pl.DataFrame) -> pl.DataFrame:
    """Costo unitario por SKU. Si un SKU se repite, gana la última fila."""
    df = _rename_to_canonical(raw, COGS_ALIASES)
    _require(df, ["sku", "cogs_unit"])
    if "product_name" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("product_name"))
    out = df.select(
        pl.col("sku").str.strip_chars().alias("sku"),
        pl.col("product_name"),
        _money("cogs_unit").alias("cogs_unit"),
    )
    _check_rows(
        out,
        {
            "SKU vacío": pl.col("sku").is_null() | (pl.col("sku") == ""),
            "Costo negativo": pl.col("cogs_unit") < 0,
        },
    )
    return out.unique(subset="sku", keep="last", maintain_order=True)


def normalize_ad_spend(raw: pl.DataFrame) -> pl.DataFrame:
    df = _rename_to_canonical(raw, AD_ALIASES)
    _require(df, ["date", "channel", "account", "amount"])
    if "sku" not in df.columns:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("sku"))
    out = df.select(
        _date("date").alias("date"),
        _channel().alias("channel"),
        pl.col("account").str.strip_chars().alias("account"),
        pl.col("sku").str.strip_chars().alias("sku"),
        _money("amount").abs().alias("amount"),
    )
    _check_rows(
        out,
        {
            "Fecha inválida": pl.col("date").is_null(),
            "Canal desconocido": pl.col("channel").is_null(),
            "Cuenta vacía": pl.col("account").is_null() | (pl.col("account") == ""),
        },
    )
    return out
