"""Costo puesto en bodega (landed cost): lectura de los Excel, recepciones por contenedor, historial
de costos (COGS) y simulador de aranceles.

Dos formatos de archivo:
  * **Reporte por contenedor** (hoja "BY CONTAINER"): una fila por contenedor, PO y SKU con el precio
    de fábrica (`Expected Rate`), `Unit Duties` y `Unit Freight`. Se guarda en `ops.inbound_receipt` y
    de ahí sale el historial: el costo de un SKU rige desde la fecha en que llegó cada contenedor.
  * **Lista plana** (columnas `ITEM` y `LANDED UNIT COST`): un costo por SKU, vigente desde hoy.

El simulador de aranceles suma puntos de arancel sobre el valor de fábrica (FOB), igual que el
reporte ("Duties % of Goods"), y mide el efecto en el margen con las tasas reales de cada canal.
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import BASE_VALID_FROM, product_cost_component_id, set_product_cost
from app.economics import base_sku, load_unit_economics
from app.models import InboundReceipt, Product, ProductCostHistory

CONTAINER_SHEET = "BY CONTAINER"
TOTAL_ROW = "CONTAINER TOTAL"
ECOMM_SUFFIX = re.compile(r"(?<=\d)E$")  # "GDL-187E" y "GDL-187" son el mismo producto


class LandedFileError(ValueError):
    """El archivo no tiene un formato reconocido."""


def match_key(sku: str) -> str:
    """Clave para unir el SKU de un archivo con el de un producto (sin empaque ni sufijo E)."""
    return ECOMM_SUFFIX.sub("", base_sku(sku).upper())


@dataclass
class ParsedLanded:
    kind: str  # container_report | flat_costs
    receipts: list[dict[str, Any]] = field(default_factory=list)
    flat_costs: dict[str, float] = field(default_factory=dict)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(_text(value)).date()
    except ValueError:
        return None


def _column(df: pl.DataFrame, *names: str) -> str | None:
    """Nombre real de la primera columna que coincide (sin importar mayúsculas ni espacios)."""
    wanted = {n.lower() for n in names}
    return next((c for c in df.columns if c.strip().lower() in wanted), None)


def parse_container_report(df: pl.DataFrame) -> list[dict[str, Any]]:
    cols = {
        "date": _column(df, "Received Date"),
        "container": _column(df, "Container"),
        "po": _column(df, "PO"),
        "sku": _column(df, "Item"),
        "desc": _column(df, "Description"),
        "qty": _column(df, "Qty Received"),
        "fob": _column(df, "Expected Rate"),
        "duty": _column(df, "Unit Duties"),
        "freight": _column(df, "Unit Freight"),
        "landed": _column(df, "Unit Landed Cost"),
    }
    missing = [k for k in ("date", "container", "sku", "qty", "fob") if cols[k] is None]
    if missing:
        raise LandedFileError(f"Al reporte por contenedor le faltan columnas: {missing}")

    acc: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        sku = _text(row[cols["sku"]])
        if not sku or sku.upper() == TOTAL_ROW:
            continue
        qty = int(_num(row[cols["qty"]]))
        received = _day(row[cols["date"]])
        container = _text(row[cols["container"]])
        if qty <= 0 or received is None or not container:
            continue
        duty = _num(row[cols["duty"]]) if cols["duty"] else 0.0
        freight = _num(row[cols["freight"]]) if cols["freight"] else 0.0
        if not cols["duty"] and not cols["freight"] and cols["landed"]:
            freight = _num(row[cols["landed"]])  # sin desglose: todo el recargo cuenta como flete
        key = (container, _text(row[cols["po"]]) if cols["po"] else "", sku)
        a = acc.setdefault(
            key,
            {
                "container": container,
                "po": key[1],
                "sku": sku,
                "description": _text(row[cols["desc"]]) if cols["desc"] else None,
                "received_date": received,
                "qty": 0,
                "_fob": 0.0,
                "_duty": 0.0,
                "_freight": 0.0,
            },
        )
        a["qty"] += qty
        a["_fob"] += qty * _num(row[cols["fob"]])
        a["_duty"] += qty * duty
        a["_freight"] += qty * freight
        a["received_date"] = min(a["received_date"], received)
    return [
        {
            **{k: v for k, v in a.items() if not k.startswith("_")},
            "fob_unit": a["_fob"] / a["qty"],
            "duty_unit": a["_duty"] / a["qty"],
            "freight_unit": a["_freight"] / a["qty"],
        }
        for a in acc.values()
    ]


def parse_flat_costs(df: pl.DataFrame) -> dict[str, float]:
    sku_col, cost_col = _column(df, "ITEM", "SKU"), _column(df, "LANDED UNIT COST", "UNIT COST")
    if sku_col is None or cost_col is None:
        raise LandedFileError("La lista plana necesita las columnas ITEM y LANDED UNIT COST")
    out: dict[str, float] = {}
    for row in df.iter_rows(named=True):
        sku, cost = _text(row[sku_col]), _num(row[cost_col])
        if sku and cost > 0:
            out[sku] = cost
    return out


def parse_landed_workbook(data: bytes) -> ParsedLanded:
    """Detecta el formato por la hoja y las columnas."""
    try:
        df = pl.read_excel(io.BytesIO(data), sheet_name=CONTAINER_SHEET, engine="calamine")
    except Exception:  # noqa: BLE001 - la hoja no existe: probamos la lista plana
        df = None
    if df is not None:
        return ParsedLanded("container_report", receipts=parse_container_report(df))
    try:
        flat = pl.read_excel(io.BytesIO(data), engine="calamine")
    except Exception as exc:
        raise LandedFileError(f"No se pudo leer el Excel: {exc}") from exc
    return ParsedLanded("flat_costs", flat_costs=parse_flat_costs(flat))


# --------------------------------------------------------------------------- persistencia


async def save_receipts(session: AsyncSession, receipts: list[dict[str, Any]]) -> dict[str, int]:
    """Inserta o actualiza por (contenedor, PO, SKU): volver a cargar el mismo archivo no duplica."""
    existing = {(r.container, r.po, r.sku): r for r in await session.scalars(select(InboundReceipt))}
    inserted = updated = 0
    for r in receipts:
        key = (r["container"], r["po"], r["sku"])
        row = existing.get(key)
        if row is None:
            row = InboundReceipt(container=r["container"], po=r["po"], sku=r["sku"])
            session.add(row)
            inserted += 1
        else:
            updated += 1
        row.description = r["description"]
        row.received_date = r["received_date"]
        row.qty = r["qty"]
        row.fob_unit = Decimal(str(round(r["fob_unit"], 4)))
        row.duty_unit = Decimal(str(round(r["duty_unit"], 6)))
        row.freight_unit = Decimal(str(round(r["freight_unit"], 6)))
    await session.flush()
    return {"receipts_inserted": inserted, "receipts_updated": updated}


async def _products_by_key(session: AsyncSession) -> dict[str, list[Product]]:
    out: dict[str, list[Product]] = defaultdict(list)
    for p in await session.scalars(select(Product)):
        out[match_key(p.sku)].append(p)
    return out


async def _history_by_product(session: AsyncSession, component_id: int) -> dict[int, list[ProductCostHistory]]:
    out: dict[int, list[ProductCostHistory]] = defaultdict(list)
    for h in await session.scalars(
        select(ProductCostHistory).where(ProductCostHistory.component_type_id == component_id)
    ):
        out[h.product_id].append(h)
    return out


def _unit(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def receipt_cost_history(receipts: list[InboundReceipt]) -> dict[str, list[tuple[date, float]]]:
    """Por clave de SKU: (fecha de recepción, costo unitario ponderado por cantidad), de más viejo a nuevo."""
    acc: dict[str, dict[date, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for r in receipts:
        unit = float(r.fob_unit) + float(r.duty_unit) + float(r.freight_unit)
        a = acc[match_key(r.sku)][r.received_date]
        a[0] += r.qty
        a[1] += r.qty * unit
    return {key: sorted((d, v[1] / v[0]) for d, v in days.items()) for key, days in acc.items()}


async def apply_receipts_to_costs(session: AsyncSession) -> dict[str, Any]:
    """Escribe en el historial de costos el costo de cada recepción. Solo productos que ya existen.

    El primer costo de un producto rige desde siempre; los siguientes, desde la fecha de recepción.
    """
    receipts = list(await session.scalars(select(InboundReceipt)))
    history = receipt_cost_history(receipts)
    products = await _products_by_key(session)
    component = await product_cost_component_id(session)
    existing = await _history_by_product(session, component)
    changes, costed = 0, set()
    for key, points in history.items():
        for product in products.get(key, []):
            rows = existing[product.id]
            for i, (day, cost) in enumerate(points):
                if await set_product_cost(
                    session,
                    product.id,
                    _unit(cost),
                    BASE_VALID_FROM if i == 0 else day,
                    component_id=component,
                    existing=rows,
                ):
                    changes += 1
            costed.add(product.sku)
    unmatched = sorted(k for k in history if k not in products)
    await session.flush()
    return {
        "products_costed": len(costed),
        "cost_changes": changes,
        "skus_without_product": len(unmatched),
        "skus_without_product_sample": unmatched[:15],
    }


async def apply_flat_costs(session: AsyncSession, costs: dict[str, float]) -> dict[str, Any]:
    """Un costo por SKU, vigente desde hoy (o desde siempre si el producto aún no tenía)."""
    products = await _products_by_key(session)
    component = await product_cost_component_id(session)
    existing = await _history_by_product(session, component)
    changes, costed, unmatched = 0, set(), []
    for sku, cost in costs.items():
        targets = products.get(match_key(sku), [])
        if not targets:
            unmatched.append(sku)
        for product in targets:
            if await set_product_cost(
                session, product.id, _unit(cost), None, component_id=component, existing=existing[product.id]
            ):
                changes += 1
            costed.add(product.sku)
    await session.flush()
    return {
        "products_costed": len(costed),
        "cost_changes": changes,
        "skus_without_product": len(unmatched),
        "skus_without_product_sample": sorted(unmatched)[:15],
    }


async def import_landed_workbook(session: AsyncSession, data: bytes, apply_costs: bool = True) -> dict[str, Any]:
    """Lee el Excel, guarda las recepciones (si es reporte por contenedor) y actualiza los costos."""
    parsed = parse_landed_workbook(data)
    out: dict[str, Any] = {"kind": parsed.kind}
    if parsed.kind == "container_report":
        out["rows_read"] = len(parsed.receipts)
        out.update(await save_receipts(session, parsed.receipts))
        if apply_costs:
            out.update(await apply_receipts_to_costs(session))
    else:
        out["rows_read"] = len(parsed.flat_costs)
        if apply_costs:
            out.update(await apply_flat_costs(session, parsed.flat_costs))
    return out


# --------------------------------------------------------------------------- simulador


def _latest_costs(receipts: list[InboundReceipt]) -> dict[str, dict[str, Any]]:
    """Desglose del costo más reciente por clave de SKU (ponderado por cantidad en la última fecha)."""
    by_key: dict[str, dict[date, list[InboundReceipt]]] = defaultdict(lambda: defaultdict(list))
    for r in receipts:
        by_key[match_key(r.sku)][r.received_date].append(r)
    out: dict[str, dict[str, Any]] = {}
    for key, days in by_key.items():
        day = max(days)
        rows = days[day]
        qty = sum(r.qty for r in rows)
        fob = sum(r.qty * float(r.fob_unit) for r in rows) / qty
        duty = sum(r.qty * float(r.duty_unit) for r in rows) / qty
        freight = sum(r.qty * float(r.freight_unit) for r in rows) / qty
        out[key] = {
            "sku": rows[0].sku,
            "received_date": day,
            "fob": fob,
            "duty": duty,
            "freight": freight,
            "landed": fob + duty + freight,
            "duty_rate": duty / fob if fob else 0.0,
            "freight_rate": freight / fob if fob else 0.0,
        }
    return out


def monthly_summary(receipts: list[InboundReceipt]) -> list[dict[str, Any]]:
    """Valor de mercancía, aranceles y flete por mes de recepción (como la hoja SUMMARY del reporte)."""
    months: dict[str, dict[str, Any]] = {}
    for r in receipts:
        m = months.setdefault(
            r.received_date.strftime("%Y-%m"),
            {"month": r.received_date.strftime("%Y-%m"), "containers": set(), "goods": 0.0, "duties": 0.0, "freight": 0.0},
        )
        m["containers"].add(r.container)
        m["goods"] += r.qty * float(r.fob_unit)
        m["duties"] += r.qty * float(r.duty_unit)
        m["freight"] += r.qty * float(r.freight_unit)
    out = []
    for m in sorted(months.values(), key=lambda x: x["month"]):
        goods = m["goods"]
        out.append(
            {
                "month": m["month"],
                "containers": len(m["containers"]),
                "goods": goods,
                "duties": m["duties"],
                "freight": m["freight"],
                "duty_rate": m["duties"] / goods if goods else None,
                "freight_rate": m["freight"] / goods if goods else None,
                "landed_rate": (m["duties"] + m["freight"]) / goods if goods else None,
            }
        )
    return out


async def tariff_report(
    session: AsyncSession,
    *,
    extra_duty: float = 0.0,
    days: int = 60,
    today: date | None = None,
) -> dict[str, Any]:
    """Efecto de subir el arancel `extra_duty` puntos (0.10 = +10 % del valor de fábrica) en el margen."""
    receipts = list(await session.scalars(select(InboundReceipt)))
    today = today or datetime.now(UTC).date()
    latest = _latest_costs(receipts)
    economics = await load_unit_economics(session, days=days, today=today)

    rows: list[dict[str, Any]] = []
    for e in economics:
        cost = latest.get(match_key(e.sku))
        if cost is None or e.net_sales <= 0:
            continue
        delta = cost["fob"] * extra_duty  # costo extra por unidad
        keep = 1 - e.fee_rate - e.refund_rate
        profit_now, profit_new = e.net_profit, e.net_profit - delta * e.units
        rows.append(
            {
                "channel": e.channel,
                "sku": e.sku,
                "product_name": e.product_name,
                "units": e.units,
                "fob": cost["fob"],
                "duty_rate": cost["duty_rate"],
                "freight_rate": cost["freight_rate"],
                "landed_unit": cost["landed"],
                "delta_unit": delta,
                "profit_now": profit_now,
                "profit_new": profit_new,
                "margin_now": profit_now / e.net_sales,
                "margin_new": profit_new / e.net_sales,
                "realized_price": e.realized_price,
                # Aumento de precio por unidad que deja la utilidad por unidad como estaba.
                "price_increase": delta / keep if keep > 0.05 else None,
                "turns_negative": profit_now >= 0 > profit_new,
            }
        )
    rows.sort(key=lambda r: r["profit_new"] - r["profit_now"])
    total_now, total_new = sum(r["profit_now"] for r in rows), sum(r["profit_new"] for r in rows)
    return {
        "days": days,
        "extra_duty": extra_duty,
        "has_receipts": bool(receipts),
        "summary": {
            "skus": len({r["sku"] for r in rows}),
            "profit_now": total_now,
            "profit_new": total_new,
            "profit_change": total_new - total_now,
            "turn_negative": sum(1 for r in rows if r["turns_negative"]),
        },
        "rows": rows,
        "monthly": monthly_summary(receipts),
        "products": sorted(
            ({k: v for k, v in c.items()} for c in latest.values()), key=lambda c: c["sku"]
        ),
    }
