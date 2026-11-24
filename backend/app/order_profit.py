"""Utilidad por orden: cuánto se ganó o se perdió en cada orden y por qué.

Una fila por orden (SalesOrder + sus OrderLine). La utilidad de la orden es:

    ventas brutas - promo - comisiones - envío - reembolsos - COGS

- envío = shipping_cost de las líneas + costo real de la etiqueta de Redo (ShippingLabelCost por
  external_order_id), igual que en shipping_profit.py y en la capa oro.
- COGS = unidades x costo vigente del SKU en la fecha de la orden (app.costs.load_cost_history).
- La publicidad NO se reparte por orden: no hay forma fiel de atribuirla a una orden concreta, así que
  la utilidad de aquí es una contribución antes de publicidad. No cuadra con la ganancia neta del
  tablero, que sí descuenta la publicidad.

Cuando la utilidad es negativa, `driver` indica la causa principal: el costo más grande entre COGS,
comisiones, envío, reembolsos y promo.

El resumen (histograma, causas, canales) cubre TODO el filtro de fechas/canal/tienda/búsqueda, no solo la
página devuelta. Los filtros de faceta (`driver`, `profit_min`, `profit_max`) solo afectan a las filas y a
`total`; así las gráficas no se vacían cuando se hace clic en ellas.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.costs import load_cost_history
from app.models import (
    Account,
    Channel,
    OrderLine,
    Product,
    SalesOrder,
    ShippingLabelCost,
)

SORTS = (
    "profit_asc",
    "profit_desc",
    "sales_asc",
    "sales_desc",
    "date_asc",
    "date_desc",
)

# (clave, etiqueta corta, frase para la tabla)
DRIVERS: dict[str, tuple[str, str]] = {
    "cogs": ("Costo del producto", "El costo del producto se comió el margen"),
    "fees": ("Comisiones", "Las comisiones se comieron el margen"),
    "shipping": ("Envío", "El envío se comió el margen"),
    "refunds": ("Reembolsos", "El reembolso se comió la venta"),
    "promo": ("Promociones", "El descuento promocional se comió el margen"),
}

# (clave, etiqueta, mínimo incluido, máximo excluido)
BINS: list[tuple[str, str, float | None, float | None]] = [
    ("lt_m10", "< -$10", None, -10.0),
    ("m10_0", "-$10 a $0", -10.0, 0.0),
    ("0_5", "$0 a $5", 0.0, 5.0),
    ("5_10", "$5 a $10", 5.0, 10.0),
    ("10_20", "$10 a $20", 10.0, 20.0),
    ("gt_20", "> $20", 20.0, None),
]


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _like(text: str) -> str:
    """Patrón LIKE que trata % y _ (y la barra) como texto literal."""
    esc = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _driver(order: dict[str, Any]) -> str | None:
    if order["profit"] >= 0:
        return None
    parts = {k: order[k] for k in DRIVERS}
    return max(parts, key=lambda k: parts[k])


def _bin_key(profit: float) -> str:
    for key, _label, lo, hi in BINS:
        if (lo is None or profit >= lo) and (hi is None or profit < hi):
            return key
    return BINS[-1][0]


async def order_profit_report(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
    q: str | None = None,
    only_loss: bool = False,
    sort: str = "profit_asc",
    limit: int = 25,
    offset: int = 0,
    driver: str | None = None,
    profit_min: float | None = None,
    profit_max: float | None = None,
) -> dict[str, Any]:
    if sort not in SORTS:
        raise ValueError(f"sort inválido: {sort}")

    query = (
        select(
            OrderLine.order_id,
            SalesOrder.external_order_id,
            SalesOrder.ship_state,
            Account.channel_code,
            Account.name,
            Product.id,
            Product.sku,
            OrderLine.order_date,
            OrderLine.units,
            OrderLine.gross_sales,
            OrderLine.promo,
            OrderLine.platform_fees,
            OrderLine.shipping_cost,
            OrderLine.refund_amount,
        )
        .join(SalesOrder, OrderLine.order_id == SalesOrder.id)
        .join(Account, OrderLine.account_id == Account.id)
        .join(Product, OrderLine.product_id == Product.id)
    )
    if start:
        query = query.where(OrderLine.order_date >= start)
    if end:
        query = query.where(OrderLine.order_date <= end)
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)
    text = (q or "").strip()
    if text:
        pattern = _like(text)
        matching = (
            select(OrderLine.order_id)
            .join(SalesOrder, OrderLine.order_id == SalesOrder.id)
            .join(Product, OrderLine.product_id == Product.id)
            .where(
                or_(
                    SalesOrder.external_order_id.ilike(pattern, escape="\\"),
                    Product.sku.ilike(pattern, escape="\\"),
                )
            )
        )
        query = query.where(OrderLine.order_id.in_(matching))
    fetched = (await session.execute(query)).all()

    labels: dict[str, float] = defaultdict(float)
    for oid, amount in (
        await session.execute(
            select(ShippingLabelCost.external_order_id, ShippingLabelCost.amount)
        )
    ).all():
        labels[oid] += _f(amount)
    costs = await load_cost_history(session)

    orders: dict[int, dict[str, Any]] = {}
    for (
        order_id,
        ext_id,
        state,
        ch,
        acc,
        pid,
        sku,
        day,
        units,
        gross,
        promo,
        fees,
        ship,
        refund,
    ) in fetched:
        o = orders.get(order_id)
        if o is None:
            o = orders[order_id] = {
                "date": day,
                "order": ext_id,
                "channel": ch,
                "account": acc,
                "state": state,
                "units": 0,
                "gross_sales": 0.0,
                "promo": 0.0,
                "fees": 0.0,
                "shipping": 0.0,
                "refunds": 0.0,
                "cogs": 0.0,
                "skus": [],
            }
        o["units"] += units
        o["gross_sales"] += _f(gross)
        o["promo"] += _f(promo)
        o["fees"] += _f(fees)
        o["shipping"] += _f(ship)
        o["refunds"] += _f(refund)
        o["cogs"] += units * float(costs.unit_cost(pid, day))
        if sku not in o["skus"]:
            o["skus"].append(sku)

    rows: list[dict[str, Any]] = []
    for o in orders.values():
        o["label_cost"] = labels.get(o["order"], 0.0)
        o["shipping"] += o["label_cost"]
        o["net_sales"] = o["gross_sales"] - o["promo"]
        o["profit"] = (
            o["net_sales"] - o["fees"] - o["shipping"] - o["refunds"] - o["cogs"]
        )
        o["margin"] = o["profit"] / o["net_sales"] if o["net_sales"] > 0 else None
        key = _driver(o)
        o["driver"] = key
        o["driver_label"] = DRIVERS[key][1] if key else None
        o["driver_short"] = DRIVERS[key][0] if key else None
        o["bin"] = _bin_key(o["profit"])
        rows.append(o)

    # Resumen sobre todo el filtro base (antes de only_loss y de las facetas).
    n = len(rows)
    losses = [r for r in rows if r["profit"] < 0]
    total_profit = sum(r["profit"] for r in rows)
    bins = {k: 0 for k, *_ in BINS}
    by_driver: dict[str, dict[str, float]] = {
        k: {"orders": 0, "amount": 0.0} for k in DRIVERS
    }
    by_channel: dict[str, dict[str, Any]] = {}
    for r in rows:
        bins[r["bin"]] += 1
        if r["driver"]:
            by_driver[r["driver"]]["orders"] += 1
            by_driver[r["driver"]]["amount"] += -r["profit"]
        c = by_channel.setdefault(
            r["channel"],
            {
                "channel": r["channel"],
                "orders": 0,
                "loss_orders": 0,
                "net_sales": 0.0,
                "profit": 0.0,
            },
        )
        c["orders"] += 1
        c["loss_orders"] += 1 if r["profit"] < 0 else 0
        c["net_sales"] += r["net_sales"]
        c["profit"] += r["profit"]

    summary = {
        "orders": n,
        "loss_orders": len(losses),
        "loss_pct": len(losses) / n if n else None,
        "loss_total": -sum(r["profit"] for r in losses),
        "profit_total": total_profit,
        "avg_profit": total_profit / n if n else None,
    }
    histogram = [
        {"key": k, "label": label, "min": lo, "max": hi, "orders": bins[k]}
        for k, label, lo, hi in BINS
    ]
    driver_rows = [
        {
            "key": k,
            "label": DRIVERS[k][0],
            "orders": int(v["orders"]),
            "amount": v["amount"],
        }
        for k, v in by_driver.items()
        if v["orders"]
    ]
    driver_rows.sort(key=lambda d: -d["amount"])
    channel_rows = sorted(by_channel.values(), key=lambda c: c["profit"])

    # Filtros de faceta y de tabla.
    shown = rows
    if only_loss:
        shown = [r for r in shown if r["profit"] < 0]
    if driver:
        shown = [r for r in shown if r["driver"] == driver]
    if profit_min is not None:
        shown = [r for r in shown if r["profit"] >= profit_min]
    if profit_max is not None:
        shown = [r for r in shown if r["profit"] < profit_max]

    field, _, direction = sort.partition("_")
    sort_key = {"profit": "profit", "sales": "net_sales", "date": "date"}[field]
    shown.sort(key=lambda r: r["order"])  # desempate estable
    shown.sort(key=lambda r: r[sort_key], reverse=direction == "desc")

    page = shown[offset : offset + limit]
    out_rows = [
        {
            "date": r["date"].isoformat(),
            "order": r["order"],
            "channel": r["channel"],
            "account": r["account"],
            "state": r["state"],
            "units": r["units"],
            "gross_sales": r["gross_sales"],
            "promo": r["promo"],
            "fees": r["fees"],
            "shipping": r["shipping"],
            "label_cost": r["label_cost"],
            "refunds": r["refunds"],
            "cogs": r["cogs"],
            "net_sales": r["net_sales"],
            "profit": r["profit"],
            "margin": r["margin"],
            "driver": r["driver"],
            "driver_label": r["driver_label"],
            "driver_short": r["driver_short"],
            "skus": r["skus"],
        }
        for r in page
    ]
    return {
        "summary": summary,
        "histogram": histogram,
        "by_driver": driver_rows,
        "by_channel": channel_rows,
        "total": len(shown),
        "limit": limit,
        "offset": offset,
        "rows": out_rows,
    }
