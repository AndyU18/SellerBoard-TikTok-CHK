"""Importador del reporte "billing-transactions" de Redo (etiquetas de envío).

Solo cuentan los cargos `Charge` de la categoría `Shipping Labels` que traen número de orden.
Se suman por orden (una orden puede tener varios cargos y reversiones: reetiquetados, ajustes de
peso) y quedan en sales.shipping_label_cost. Se excluyen y se reportan aparte:
  - `Transaction`: recargas de saldo, no son costo.
  - Cargos sin orden: cuotas o ajustes que no se pueden atribuir a un producto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.canonical import ImportValidationError, _money
from app.importers.persist import _chunks
from app.models import SalesOrder, ShippingLabelCost

SOURCE = "redo"
REQUIRED = {"created at": "created", "type": "type", "category": "category", "order": "order", "amount": "amount"}
MONTHS = "%b %d, %Y"


@dataclass
class RedoStats:
    charges_used: int = 0
    orders: int = 0
    transactions_excluded: int = 0
    unassigned_charges: int = 0
    unassigned_total: float = 0.0
    other_rows_ignored: int = 0
    # Libro completo guardado en ops.redo_* (solo lo llena la API).
    ledger_charges: int = 0
    bills: int = 0
    topups: int = 0


def normalize_redo(raw: pl.DataFrame) -> tuple[pl.DataFrame, RedoStats]:
    """Devuelve (una fila por orden, estadísticas)."""
    lookup = {c.strip().lower(): c for c in raw.columns}
    missing = [name for name in REQUIRED if name not in lookup]
    if missing:
        raise ImportValidationError(
            [f"Falta la columna: {m.title()} (¿es el reporte billing-transactions de Redo?)" for m in missing]
        )
    df = raw.select([pl.col(lookup[k]).alias(v) for k, v in REQUIRED.items()])
    df = df.with_columns(
        pl.col("type").cast(pl.Utf8).str.strip_chars(),
        pl.col("category").cast(pl.Utf8).str.strip_chars(),
        pl.col("order").cast(pl.Utf8).str.strip_chars(),
        _money("amount").alias("amount"),
        pl.coalesce(
            pl.col("created").cast(pl.Utf8).str.strip_chars().str.to_date(MONTHS, strict=False),
            pl.col("created").cast(pl.Utf8).str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False),
        ).alias("created"),
    )
    bad = df.with_row_index("n", offset=2).filter(pl.col("created").is_null())["n"].to_list()
    if bad:
        raise ImportValidationError([f"Fecha inválida en filas {bad[:10]}{'…' if len(bad) > 10 else ''}"])

    stats = RedoStats()
    stats.transactions_excluded = df.filter(pl.col("type") == "Transaction").height
    is_label = (pl.col("type") == "Charge") & (pl.col("category") == "Shipping Labels")
    stats.other_rows_ignored = df.filter(~is_label & (pl.col("type") != "Transaction")).height
    labels = df.filter(is_label)

    has_order = pl.col("order").is_not_null() & (pl.col("order") != "")
    unassigned = labels.filter(~has_order)
    stats.unassigned_charges = unassigned.height
    stats.unassigned_total = round(float(unassigned["amount"].sum()), 2)

    per_order = (
        labels.filter(has_order)
        .group_by("order")
        .agg(
            pl.col("amount").sum().round(4).alias("amount"),
            pl.len().alias("charges"),
            pl.col("created").min().alias("first_charged"),
            pl.col("created").max().alias("last_charged"),
        )
        .rename({"order": "external_order_id"})
        .sort("external_order_id")
    )
    stats.charges_used = int(per_order["charges"].sum()) if per_order.height else 0
    stats.orders = per_order.height
    return per_order, stats


@dataclass
class LabelImportResult:
    inserted: int = 0
    updated: int = 0
    matched_orders: int = 0
    unmatched_orders: int = 0
    # Fechas de las órdenes afectadas: la API refresca la capa oro solo en ese rango.
    touched_from: date | None = field(default=None, repr=False)
    touched_to: date | None = field(default=None, repr=False)


async def import_label_costs(
    session: AsyncSession, df: pl.DataFrame, source: str = SOURCE
) -> LabelImportResult:
    """Idempotente: reimportar el mismo reporte (o uno más nuevo y completo) actualiza, no duplica."""
    result = LabelImportResult()
    rows = df.to_dicts()
    ids = [r["external_order_id"] for r in rows]

    existing: dict[str, ShippingLabelCost] = {}
    order_dates: dict[str, date] = {}
    for part in _chunks(ids):
        for rec in await session.scalars(
            select(ShippingLabelCost).where(
                ShippingLabelCost.source == source, ShippingLabelCost.external_order_id.in_(part)
            )
        ):
            existing[rec.external_order_id] = rec
        for ext, day in (
            await session.execute(
                select(SalesOrder.external_order_id, SalesOrder.order_date).where(
                    SalesOrder.external_order_id.in_(part)
                )
            )
        ).all():
            order_dates[ext] = day

    for r in rows:
        amount = Decimal(str(round(r["amount"], 4)))
        rec = existing.get(r["external_order_id"])
        if rec is None:
            session.add(
                ShippingLabelCost(
                    source=source,
                    external_order_id=r["external_order_id"],
                    amount=amount,
                    charges=r["charges"],
                    first_charged=r["first_charged"],
                    last_charged=r["last_charged"],
                )
            )
            result.inserted += 1
        else:
            if rec.amount != amount or rec.charges != r["charges"]:
                result.updated += 1
            rec.amount, rec.charges = amount, r["charges"]
            rec.first_charged, rec.last_charged = r["first_charged"], r["last_charged"]

    matched = [order_dates[i] for i in ids if i in order_dates]
    result.matched_orders = len(matched)
    result.unmatched_orders = len(ids) - len(matched)
    if matched:
        result.touched_from, result.touched_to = min(matched), max(matched)
    await session.flush()
    return result
