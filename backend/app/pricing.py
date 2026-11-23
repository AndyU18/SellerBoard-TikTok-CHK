"""Precios: piso rentable por SKU y canal, promoción máxima y paridad de precios entre canales.

Modelo (por unidad, con las tasas reales de la ventana):

    utilidad = p - comisión(p) - reembolsos(p) - envío - costo - publicidad
    comisión(p) = fee_rate * p ;  reembolsos(p) = refund_rate * p

donde `p` es el precio realmente cobrado (después de promociones). Despejando `p` para un margen
objetivo `m` sobre ventas netas:

    p = (costo + envío + publicidad) / (1 - fee_rate - refund_rate - m)

Con m = 0 es el punto de equilibrio. La comisión se trata como porcentaje del precio, así que las
comisiones fijas por orden se absorben en la tasa observada.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.economics import UnitEconomics, base_sku, load_unit_economics
from app.models import Channel

MIN_UNITS = 3  # con menos unidades vendidas las tasas no son confiables
DEFAULT_TARGET_MARGIN = 0.15
MIN_DENOMINATOR = 0.05  # si comisión + reembolsos + margen se comen casi todo el precio, no hay piso posible
PARITY_GAP_ALERT = 0.10
STATUS_ORDER = ("loss", "below_target", "ok", "headroom", "no_cost", "insufficient")


def solve_price(e: UnitEconomics, margin: float) -> float | None:
    """Precio realizado mínimo para ganar `margin` sobre ventas netas. None si es imposible."""
    denominator = 1 - e.fee_rate - e.refund_rate - margin
    if denominator <= MIN_DENOMINATOR:
        return None
    return (e.cogs_unit + e.shipping_unit + e.ad_unit) / denominator


def _discount_room(list_price: float | None, floor: float | None) -> float | None:
    """Cuánto se puede bajar el precio de lista (como fracción) antes de llegar al piso."""
    if not list_price or floor is None:
        return None
    return 1 - floor / list_price


def _status(e: UnitEconomics, break_even: float | None, target: float | None, min_units: int) -> str:
    realized = e.realized_price
    if e.units < min_units or realized is None:
        return "insufficient"
    if e.cogs <= 0:
        return "no_cost"  # sin costo cargado el piso no significa nada
    if break_even is not None and realized < break_even:
        return "loss"
    if target is not None and realized < target:
        return "below_target"
    if target is not None and realized >= target * 1.25:
        return "headroom"
    return "ok"


def floor_row(e: UnitEconomics, target_margin: float, min_units: int) -> dict[str, Any]:
    break_even = solve_price(e, 0.0)
    target = solve_price(e, target_margin)
    status = _status(e, break_even, target, min_units)
    realized = e.realized_price
    # Precio de LISTA que deja el precio realizado en la meta, manteniendo la promoción actual.
    keep_promo = 1 - e.promo_rate
    suggested = target / keep_promo if (target is not None and keep_promo > 0.05) else target
    gap = (target - realized) if (target is not None and realized is not None) else None
    return {
        "channel": e.channel,
        "sku": e.sku,
        "product_name": e.product_name,
        "units": e.units,
        "list_price": e.list_price,
        "realized_price": realized,
        "promo_rate": e.promo_rate,
        "cogs_unit": e.cogs_unit,
        "shipping_unit": e.shipping_unit,
        "fee_rate": e.fee_rate,
        "refund_rate": e.refund_rate,
        "unit_profit": e.unit_profit,
        "margin": e.margin,
        "break_even_price": break_even,
        "target_price": target,
        "suggested_list_price": suggested,
        "max_discount_break_even": _discount_room(e.list_price, break_even),
        "max_discount_target": _discount_room(e.list_price, target),
        "gap_to_target": gap,
        # Utilidad que se deja de ganar por unidad vs. la meta (solo si está por debajo).
        "profit_gap_total": gap * e.units if (gap is not None and gap > 0 and status != "no_cost") else 0.0,
        "status": status,
    }


async def price_floor_report(
    session: AsyncSession,
    *,
    days: int = 60,
    target_margin: float = DEFAULT_TARGET_MARGIN,
    min_units: int = MIN_UNITS,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    economics = await load_unit_economics(session, days=days, today=today, channel=channel, account=account)
    rows = [floor_row(e, target_margin, min_units) for e in economics]
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), -r["profit_gap_total"], r["sku"]))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_ORDER}
    return {
        "days": days,
        "target_margin": target_margin,
        "min_units": min_units,
        "summary": {
            "rows": len(rows),
            **counts,
            "profit_gap_total": sum(r["profit_gap_total"] for r in rows),
        },
        "rows": rows,
    }


async def parity_report(
    session: AsyncSession,
    *,
    days: int = 60,
    min_units: int = MIN_UNITS,
    threshold: float = PARITY_GAP_ALERT,
    today: date | None = None,
) -> dict[str, Any]:
    """Mismo producto (SKU sin sufijo de empaque) en varios canales: brecha de precio y de utilidad."""
    economics = await load_unit_economics(session, days=days, today=today)
    groups: dict[str, list[UnitEconomics]] = defaultdict(list)
    for e in economics:
        if e.units >= min_units and e.realized_price:
            groups[base_sku(e.sku)].append(e)

    rows: list[dict[str, Any]] = []
    for base, entries in groups.items():
        # Si un canal vende dos variantes del mismo producto, se resume en una sola fila por canal.
        by_channel: dict[str, list[UnitEconomics]] = defaultdict(list)
        for e in entries:
            by_channel[e.channel].append(e)
        if len(by_channel) < 2:
            continue
        channels = []
        for ch, items in sorted(by_channel.items()):
            units = sum(i.units for i in items)
            net_sales = sum(i.net_sales for i in items)
            profit = sum(i.net_profit for i in items)
            channels.append(
                {
                    "channel": ch,
                    "skus": sorted({i.sku for i in items}),
                    "units": units,
                    "realized_price": net_sales / units,
                    "unit_profit": profit / units,
                    "margin": profit / net_sales if net_sales > 0 else None,
                    "has_cost": all(i.cogs > 0 for i in items),
                }
            )
        prices = [c["realized_price"] for c in channels]
        gap = (max(prices) - min(prices)) / min(prices)
        best = max(channels, key=lambda c: c["unit_profit"])
        worst = min(channels, key=lambda c: c["unit_profit"])
        rows.append(
            {
                "base_sku": base,
                "product_name": next((e.product_name for e in entries if e.product_name), None),
                "channels": channels,
                "price_gap": gap,
                "cheapest_channel": min(channels, key=lambda c: c["realized_price"])["channel"],
                "priciest_channel": max(channels, key=lambda c: c["realized_price"])["channel"],
                "best_profit_channel": best["channel"],
                "profit_gap_unit": best["unit_profit"] - worst["unit_profit"],
                "flag": "gap" if gap >= threshold else "ok",
                "complete_costs": all(c["has_cost"] for c in channels),
            }
        )
    rows.sort(key=lambda r: (r["flag"] != "gap", -r["price_gap"], r["base_sku"]))
    return {
        "days": days,
        "threshold": threshold,
        "summary": {"products": len(rows), "with_gap": sum(1 for r in rows if r["flag"] == "gap")},
        "rows": rows,
    }
