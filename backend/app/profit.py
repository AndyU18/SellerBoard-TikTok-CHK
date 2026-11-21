"""Motor de utilidad unificado para Amazon, TikTok Shop y Walmart.

Entrada: una fila por (fecha, canal, cuenta, sku) con montos NORMALIZADOS:
todos los costos son números positivos y se restan aquí.

    net_profit = gross_sales - promo - platform_fees - shipping_cost
                 - refund_amount - ad_spend - cogs - allocated_expenses
    unit_profit = net_profit / units   (puede ser negativo)
"""

from __future__ import annotations

import polars as pl

REQUIRED_COLUMNS = [
    "channel",  # amazon | tiktok_shop | walmart
    "account",  # p. ej. amazon_1, amazon_2
    "sku",
    "units",
    "gross_sales",
    "promo",
    "platform_fees",
    "shipping_cost",
    "refund_amount",
    "ad_spend",
    "cogs_unit",
]

_MONEY_COLUMNS = [
    "gross_sales",
    "promo",
    "platform_fees",
    "shipping_cost",
    "refund_amount",
    "ad_spend",
    "cogs",
    "allocated_expenses",
    "net_profit",
]


def _check_columns(df: pl.DataFrame) -> None:
    # Si el DataFrame ya trae `cogs` (capa oro), no hace falta `cogs_unit`.
    required = [
        c for c in REQUIRED_COLUMNS if not (c == "cogs_unit" and "cogs" in df.columns)
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas: {missing}")


def add_line_profit(df: pl.DataFrame) -> pl.DataFrame:
    """Calcula cogs (si no viene) y utilidad neta por línea."""
    _check_columns(df)
    if "allocated_expenses" not in df.columns:
        df = df.with_columns(pl.lit(0.0).alias("allocated_expenses"))
    if "cogs" not in df.columns:
        df = df.with_columns((pl.col("units") * pl.col("cogs_unit")).alias("cogs"))
    return df.with_columns(
        (
            pl.col("gross_sales")
            - pl.col("promo")
            - pl.col("platform_fees")
            - pl.col("shipping_cost")
            - pl.col("refund_amount")
            - pl.col("ad_spend")
            - pl.col("cogs")
            - pl.col("allocated_expenses")
        ).alias("net_profit")
    )


def summarize(df: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    """Agrega por las columnas `by` (p. ej. ['channel'] o ['channel', 'sku']).

    Devuelve sumas, ganancia unitaria, margen, ROI y TACoS. Los cocientes
    quedan en null cuando el divisor es 0 (nunca inf/NaN).
    """
    lines = add_line_profit(df)
    agg = lines.group_by(by).agg(
        pl.col("units").sum().alias("units"),
        *[pl.col(c).sum().alias(c) for c in _MONEY_COLUMNS],
    )

    def ratio(num: str, den: str) -> pl.Expr:
        return pl.when(pl.col(den) != 0).then(pl.col(num) / pl.col(den)).otherwise(None)

    return (
        agg.with_columns(
            ratio("net_profit", "units").alias("unit_profit"),
            ratio("net_profit", "gross_sales").alias("margin"),
            ratio("net_profit", "cogs").alias("roi"),
            ratio("ad_spend", "gross_sales").alias("tacos"),
        )
        .with_columns(*[pl.col(c).round(2) for c in [*_MONEY_COLUMNS, "unit_profit"]])
        .with_columns(*[pl.col(c).round(4) for c in ["margin", "roi", "tacos"]])
        .sort(by)
    )


def overall(df: pl.DataFrame) -> dict | None:
    """Totales de todo el DataFrame (None si está vacío)."""
    out = summarize(df.with_columns(pl.lit("all").alias("_all")), ["_all"]).drop("_all")
    return out.to_dicts()[0] if out.height else None


ABC_A_LIMIT = 0.80
ABC_B_LIMIT = 0.95


def abc_classes(products: pl.DataFrame, metric: str = "profit") -> pl.DataFrame:
    """Clasificación Pareto ABC de un resumen por producto (salida de `summarize(df, ["sku"])`).

    Se ordena por la métrica (`profit` = utilidad neta, `sales` = ventas) y se acumula su peso:
    A = los que llegan al 80 % acumulado, B = hasta el 95 %, C = el resto. Un producto es A si el
    acumulado ANTES de sumarlo todavía no llega al 80 % (así el primero siempre es A).
    Los productos con utilidad negativa no suman al total: quedan como "loss" sin participación.
    """
    column = "net_profit" if metric == "profit" else "gross_sales"
    positive = products.filter(pl.col(column) > 0).sort(column, descending=True)
    total = float(positive[column].sum()) if positive.height else 0.0
    positive = positive.with_columns(
        (pl.col(column) / total).alias("share")
    ).with_columns(pl.col("share").cum_sum().alias("cumulative_share"))
    before = pl.col("cumulative_share") - pl.col("share")
    positive = positive.with_columns(
        pl.when(before < ABC_A_LIMIT)
        .then(pl.lit("A"))
        .when(before < ABC_B_LIMIT)
        .then(pl.lit("B"))
        .otherwise(pl.lit("C"))
        .alias("abc_class")
    )
    rest = (
        products.filter(~(pl.col(column) > 0))
        .sort(column)
        .with_columns(
            pl.lit(None, dtype=pl.Float64).alias("share"),
            pl.lit(None, dtype=pl.Float64).alias("cumulative_share"),
            pl.lit("loss" if metric == "profit" else "C").alias("abc_class"),
        )
    )
    return pl.concat([positive, rest.select(positive.columns)]).with_columns(
        pl.col("share").round(4), pl.col("cumulative_share").round(4)
    )


def profit_by_channel(df: pl.DataFrame) -> pl.DataFrame:
    return summarize(df, ["channel"])


def profit_by_product(df: pl.DataFrame) -> pl.DataFrame:
    return summarize(df, ["sku"])


def profit_by_channel_and_product(df: pl.DataFrame) -> pl.DataFrame:
    return summarize(df, ["channel", "sku"])
