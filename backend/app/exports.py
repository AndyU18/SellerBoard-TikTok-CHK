"""Exportaciones a CSV para el contador.

Cada exportación es una función `build(session, params) -> (columnas, filas)` registrada en `EXPORTS`.
Decisiones:
- El CSV va en UTF-8 CON BOM: Excel lo necesita para abrir bien los acentos.
- Los encabezados están en español; los importes salen como número con 2 decimales, sin símbolo de
  moneda ni separador de miles (así Excel y el software contable los leen como número).
- Las razones (margen, tasas) salen en porcentaje con 2 decimales; las fechas en ISO (AAAA-MM-DD).
- Protección contra inyección de fórmulas: una celda de TEXTO que empiece con = + - @ (o tabulador /
  retorno) se prefija con una comilla simple, para que Excel no la ejecute.
- `to_csv` y el formato de celdas son funciones puras, sin base de datos.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import build_alerts
from app.gold import load_gold_frame
from app.models import Channel
from app.pnl import MONEY_FIELDS, pnl_report
from app.pricing import price_floor_report
from app.profit import summarize
from app.reconciliation import label_audit
from app.reorder import reorder_report
from app.reports import cashflow_report, inventory_report
from app.returns_cost import returns_cost_report

Kind = Literal["text", "money", "int", "num", "pct", "date", "bool"]
BOM = "﻿"
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class Column:
    """Una columna: clave en la fila, encabezado legible y tipo (define el formato)."""

    key: str
    label: str
    kind: Kind = "text"


Row = dict[str, Any]


@dataclass(frozen=True)
class ExportParams:
    """Parámetros comunes; cada exportación usa solo los que declara en `Export.params`."""

    start: date | None = None
    end: date | None = None
    channel: Channel | None = None
    account: str | None = None
    days: int | None = None


Builder = Callable[[AsyncSession, ExportParams], Awaitable[tuple[list[Column], list[Row]]]]


@dataclass(frozen=True)
class Export:
    title: str
    description: str
    params: tuple[str, ...]  # subconjunto de: start, end, channel, account, days
    build: Builder


# ---------------------------------------------------------------------------- formato (puro)


def _escape_text(text: str) -> str:
    return "'" + text if text.startswith(FORMULA_STARTS) else text


def format_cell(value: Any, kind: Kind) -> str:
    """Convierte un valor al texto del CSV según su tipo. None y no finitos quedan vacíos."""
    if value is None:
        return ""
    if kind == "text":
        return _escape_text(str(value))
    if kind == "date":
        return value.isoformat() if isinstance(value, date | datetime) else _escape_text(str(value))
    if kind == "bool":
        return "Sí" if value else "No"
    number = float(value)
    if not math.isfinite(number):
        return ""
    if kind == "int":
        return str(round(number))
    if kind == "pct":
        return f"{number * 100:.2f}"
    return f"{number:.2f}"  # money y num


def to_csv(columns: list[Column], rows: list[Row]) -> bytes:
    """CSV en UTF-8 con BOM: encabezados legibles y una línea por fila (CRLF, como espera Excel)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([_escape_text(c.label) for c in columns])
    for row in rows:
        writer.writerow([format_cell(row.get(c.key), c.kind) for c in columns])
    return (BOM + buffer.getvalue()).encode("utf-8")


def _clamp(days: int | None, default: int, low: int, high: int) -> int:
    return min(max(days if days is not None else default, low), high)


# ---------------------------------------------------------------------------- rentabilidad

_PROFIT_METRICS = [
    Column("units", "Unidades", "int"),
    Column("gross_sales", "Ventas brutas", "money"),
    Column("promo", "Promociones", "money"),
    Column("platform_fees", "Comisiones de plataforma", "money"),
    Column("shipping_cost", "Costo de envío", "money"),
    Column("refund_amount", "Reembolsos", "money"),
    Column("ad_spend", "Publicidad", "money"),
    Column("cogs", "Costo de producto", "money"),
    Column("allocated_expenses", "Gastos asignados", "money"),
    Column("net_profit", "Utilidad neta", "money"),
    Column("unit_profit", "Utilidad por unidad", "money"),
    Column("margin", "Margen (%)", "pct"),
    Column("roi", "ROI (%)", "pct"),
    Column("tacos", "TACoS (%)", "pct"),
]


async def _profit_by(session: AsyncSession, p: ExportParams, by: list[str], head: list[Column]):
    df = await load_gold_frame(session, start=p.start, end=p.end, channel=p.channel, account=p.account)
    rows = summarize(df, by).to_dicts() if df.height else []
    return [*head, *_PROFIT_METRICS], rows


async def profit_by_product(session: AsyncSession, p: ExportParams):
    return await _profit_by(session, p, ["sku"], [Column("sku", "SKU")])


async def profit_by_channel(session: AsyncSession, p: ExportParams):
    return await _profit_by(session, p, ["channel"], [Column("channel", "Canal")])


# ---------------------------------------------------------------------------- análisis


async def pricing_floor(session: AsyncSession, p: ExportParams):
    report = await price_floor_report(
        session, days=_clamp(p.days, 60, 7, 365), channel=p.channel, account=p.account
    )
    columns = [
        Column("channel", "Canal"),
        Column("sku", "SKU"),
        Column("product_name", "Producto"),
        Column("units", "Unidades", "int"),
        Column("list_price", "Precio de lista", "money"),
        Column("realized_price", "Precio realizado", "money"),
        Column("cogs_unit", "Costo unitario", "money"),
        Column("shipping_unit", "Envío por unidad", "money"),
        Column("fee_rate", "Comisión (%)", "pct"),
        Column("unit_profit", "Utilidad por unidad", "money"),
        Column("margin", "Margen (%)", "pct"),
        Column("break_even_price", "Precio de equilibrio", "money"),
        Column("target_price", "Precio meta", "money"),
        Column("suggested_list_price", "Precio de lista sugerido", "money"),
        Column("profit_gap_total", "Utilidad no ganada vs. meta", "money"),
        Column("status", "Estado"),
    ]
    return columns, report["rows"]


async def reorder(session: AsyncSession, p: ExportParams):
    report = await reorder_report(
        session, days=_clamp(p.days, 30, 7, 90), channel=p.channel, account=p.account
    )
    columns = [
        Column("channel", "Canal"),
        Column("account", "Cuenta"),
        Column("sku", "SKU"),
        Column("product_name", "Producto"),
        Column("avail_to_sell", "Disponible", "int"),
        Column("daily_units", "Unidades por día", "num"),
        Column("days_of_cover", "Días de cobertura", "num"),
        Column("reorder_point", "Punto de reorden", "int"),
        Column("order_qty", "Cantidad a pedir", "int"),
        Column("order_by", "Pedir antes de", "date"),
        Column("unit_cost", "Costo unitario", "money"),
        Column("stock_value", "Valor del inventario", "money"),
        Column("order_cost", "Costo del pedido", "money"),
        Column("excess_value", "Valor en exceso", "money"),
        Column("status", "Estado"),
    ]
    return columns, report["rows"]


async def returns_cost(session: AsyncSession, p: ExportParams):
    report = await returns_cost_report(
        session, days=_clamp(p.days, 90, 7, 365), channel=p.channel, account=p.account
    )
    columns = [
        Column("sku", "SKU"),
        Column("product_name", "Producto"),
        Column("returned_units", "Unidades devueltas", "int"),
        Column("units_sold", "Unidades vendidas", "int"),
        Column("return_rate", "Tasa de devolución (%)", "pct"),
        Column("refund", "Reembolsado", "money"),
        Column("shipping_lost", "Envío perdido", "money"),
        Column("product_lost", "Producto perdido", "money"),
        Column("real_cost", "Costo real", "money"),
        Column("cost_per_return", "Costo por devolución", "money"),
        Column("seller_fault_units", "Unidades por culpa del vendedor", "int"),
    ]
    return columns, report["by_sku"]


async def inventory(session: AsyncSession, p: ExportParams):
    report = await inventory_report(
        session, p.channel, p.account, days=_clamp(p.days, 30, 7, 365)
    )
    columns = [
        Column("channel", "Canal"),
        Column("account", "Cuenta"),
        Column("sku", "SKU"),
        Column("product_name", "Producto"),
        Column("price", "Precio", "money"),
        Column("published_status", "Publicación"),
        Column("avail_to_sell", "Disponible", "int"),
        Column("reserved", "Reservado", "int"),
        Column("units_sold", "Unidades vendidas", "int"),
        Column("sales", "Ventas", "money"),
        Column("daily_units", "Unidades por día", "num"),
        Column("days_of_cover", "Días de cobertura", "num"),
        Column("status", "Estado"),
    ]
    return columns, report["rows"]


async def reconciliation_labels(session: AsyncSession, p: ExportParams):
    audit = await label_audit(session)
    columns = [
        Column("order_id", "Orden"),
        Column("kind", "Tipo"),
        Column("amount", "Monto", "money"),
        Column("charged", "Fecha del cargo", "date"),
        Column("source", "Origen"),
    ]
    return columns, audit["unmatched"]


async def cashflow_settlements(session: AsyncSession, p: ExportParams):
    report = await cashflow_report(session, p.start, p.end, p.channel, p.account)
    columns = [
        Column("channel", "Canal"),
        Column("external_id", "Estado de cuenta"),
        Column("statement_date", "Fecha del estado", "date"),
        Column("payout_date", "Fecha de pago", "date"),
        Column("status", "Estado"),
        Column("gross_amount", "Ventas", "money"),
        Column("fees_amount", "Comisiones", "money"),
        Column("shipping_amount", "Envíos", "money"),
        Column("adjustment_amount", "Ajustes", "money"),
        Column("net_payout", "Pago neto", "money"),
    ]
    return columns, report["rows"]


async def alerts(session: AsyncSession, p: ExportParams):
    report = await build_alerts(session)
    columns = [
        Column("severity", "Severidad"),
        Column("category", "Categoría"),
        Column("title", "Alerta"),
        Column("detail", "Detalle"),
        Column("channel", "Canal"),
        Column("sku", "SKU"),
    ]
    return columns, report["alerts"]


_PNL_LABELS = {
    "gross_sales": "Ventas brutas",
    "promo": "Promociones",
    "net_sales": "Ventas netas",
    "platform_fees": "Comisiones de plataforma",
    "shipping_cost": "Costo de envío",
    "refund_amount": "Reembolsos",
    "cogs": "Costo de producto",
    "gross_profit": "Utilidad bruta",
    "ad_spend": "Publicidad",
    "contribution": "Contribución",
    "expenses": "Gastos indirectos",
    "net_profit": "Utilidad neta",
}


async def pnl_monthly(session: AsyncSession, p: ExportParams):
    report = await pnl_report(session, start=p.start, end=p.end, channel=p.channel)
    columns = [
        Column("month", "Mes"),
        Column("channel", "Canal"),
        *[Column(f, _PNL_LABELS[f], "money") for f in MONEY_FIELDS],
        Column("margin", "Margen (%)", "pct"),
    ]
    rows: list[Row] = []
    for m in report["months"]:
        for r in [*m["channels"], {"channel": "TOTAL", **m["total"]}]:
            rows.append({**r, "month": m["month"][:7]})
    return columns, rows


# ---------------------------------------------------------------------------- registro

EXPORTS: dict[str, Export] = {
    "profit_by_product": Export(
        "Utilidad por producto",
        "Ventas, costos y utilidad neta por SKU en el rango (capa oro, sin gastos indirectos).",
        ("start", "end", "channel", "account"),
        profit_by_product,
    ),
    "profit_by_channel": Export(
        "Utilidad por canal",
        "Ventas, costos y utilidad neta por canal de venta en el rango.",
        ("start", "end", "channel", "account"),
        profit_by_channel,
    ),
    "pnl_monthly": Export(
        "Estado de resultados mensual",
        "Una fila por mes y canal, con total mensual. Incluye los gastos indirectos.",
        ("start", "end", "channel"),
        pnl_monthly,
    ),
    "pricing_floor": Export(
        "Precios mínimos",
        "Precio de equilibrio, precio meta y precio de lista sugerido por SKU y canal.",
        ("channel", "account", "days"),
        pricing_floor,
    ),
    "reorder": Export(
        "Plan de reposición",
        "Cuánto pedir y cuándo, por SKU, según el ritmo de venta y el inventario.",
        ("channel", "account", "days"),
        reorder,
    ),
    "returns_cost": Export(
        "Costo de devoluciones",
        "Costo real de las devoluciones por SKU (reembolso, envío y producto perdidos).",
        ("channel", "account", "days"),
        returns_cost,
    ),
    "inventory": Export(
        "Inventario",
        "Existencias por SKU contra el ritmo de venta.",
        ("channel", "account", "days"),
        inventory,
    ),
    "reconciliation_labels": Export(
        "Etiquetas sin conciliar",
        "Cargos de etiquetas de envío que no corresponden a ninguna orden.",
        (),
        reconciliation_labels,
    ),
    "cashflow_settlements": Export(
        "Liquidaciones",
        "Estados de cuenta de las plataformas: ventas, comisiones, ajustes y pago neto.",
        ("start", "end", "channel", "account"),
        cashflow_settlements,
    ),
    "alerts": Export(
        "Alertas activas",
        "Alertas de utilidad, inventario, desempeño, operación y sincronización.",
        (),
        alerts,
    ),
}
