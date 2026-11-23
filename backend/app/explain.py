"""¿Por qué cambió la utilidad? Explicación determinista (sin IA) de la variación entre dos periodos.

Compara los últimos `days` días contra los `days` anteriores usando la capa oro y descompone el cambio:

* Componentes: efecto en la utilidad de cada renglón. Ingresos suman su variación; costos la restan,
  así que los efectos siempre suman EXACTAMENTE el cambio de utilidad.
* Productos: por SKU, efecto volumen (Δunidades x utilidad unitaria previa) + efecto tasa
  (unidades actuales x Δutilidad unitaria) = Δutilidad del SKU. Si el SKU no vendió en el periodo
  previo (o ya no vende) no hay utilidad unitaria comparable: todo el cambio se toma como volumen.
* Salvedades: lo que puede distorsionar la lectura (productos sin costo, datos atrasados, etc.).

El `headline` se arma con reglas, nunca con un modelo: es lo que ve el usuario aunque no haya clave de IA.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gold import UNASSIGNED_SKU, load_gold_frame
from app.models import Channel, Settlement
from app.profit import add_line_profit

TOP_PRODUCTS = 8
FLAT_THRESHOLD = 0.01  # variación menor al 1 % (o a $1) se describe como "estable"

# clave -> (columna, signo sobre la utilidad, etiqueta, frase si empeora, frase si mejora)
COMPONENTS: list[tuple[str, str, int, str, str, str]] = [
    ("sales", "gross_sales", 1, "Ventas brutas", "menores ventas", "mayores ventas"),
    ("promo", "promo", -1, "Promociones", "más descuentos", "menos descuentos"),
    ("fees", "platform_fees", -1, "Comisiones", "más comisiones de la plataforma", "menos comisiones"),
    ("shipping", "shipping_cost", -1, "Envío", "mayor costo de envío", "menor costo de envío"),
    ("refunds", "refund_amount", -1, "Reembolsos", "más reembolsos", "menos reembolsos"),
    ("cogs", "cogs", -1, "Costo del producto", "mayor costo del producto", "menor costo del producto"),
    ("ads", "ad_spend", -1, "Publicidad", "más gasto en publicidad", "menos gasto en publicidad"),
    ("expenses", "allocated_expenses", -1, "Gastos asignados", "más gastos asignados", "menos gastos asignados"),
]
_SUM_COLUMNS = ["units", *(c[1] for c in COMPONENTS), "net_profit"]


# ------------------------------------------------------------------------------ funciones puras


def window_bounds(today: date, days: int) -> tuple[tuple[date, date], tuple[date, date]]:
    """((inicio, fin) actual, (inicio, fin) previo): dos ventanas contiguas de `days` días."""
    cur_start = today - timedelta(days=days - 1)
    prev_end = cur_start - timedelta(days=1)
    return (cur_start, today), (prev_end - timedelta(days=days - 1), prev_end)


def money(value: float) -> str:
    """$1,234 (sin centavos desde $100) o $12.50; el signo se maneja en la frase, no aquí."""
    value = abs(value)
    return f"${value:,.0f}" if value >= 100 else f"${value:,.2f}"


def totals(df: pl.DataFrame) -> dict[str, float]:
    """Sumas del periodo (unidades, cada renglón monetario y utilidad neta)."""
    if df.height == 0:
        return dict.fromkeys(_SUM_COLUMNS, 0.0)
    row = add_line_profit(df).select([pl.col(c).sum() for c in _SUM_COLUMNS]).row(0, named=True)
    return {c: float(row[c] or 0.0) for c in _SUM_COLUMNS}


def component_effects(current: dict[str, float], previous: dict[str, float]) -> list[dict[str, Any]]:
    """Efecto de cada renglón en la utilidad (con signo), ordenado por magnitud."""
    rows = [
        {
            "key": key,
            "label": label,
            "current": current[col],
            "previous": previous[col],
            "delta": current[col] - previous[col],
            "effect": sign * (current[col] - previous[col]),
            "worse": worse,
            "better": better,
        }
        for key, col, sign, label, worse, better in COMPONENTS
    ]
    return sorted(rows, key=lambda r: -abs(r["effect"]))


def _by_sku(df: pl.DataFrame) -> dict[str, dict[str, Any]]:
    if df.height == 0:
        return {}
    lines = add_line_profit(df).group_by("sku").agg(
        pl.col("units").sum().alias("units"),
        pl.col("net_profit").sum().alias("profit"),
        pl.col("cogs").sum().alias("cogs"),
        pl.col("gross_sales").sum().alias("sales"),
        pl.col("product_name").drop_nulls().first().alias("name"),
    )
    return {r["sku"]: r for r in lines.iter_rows(named=True)}


def split_effects(units_prev: float, profit_prev: float, units_cur: float, profit_cur: float) -> tuple[float, float]:
    """(efecto volumen, efecto tasa) de un SKU; siempre suman `profit_cur - profit_prev`."""
    if units_prev <= 0 or units_cur <= 0:
        return profit_cur - profit_prev, 0.0
    unit_prev = profit_prev / units_prev
    return (units_cur - units_prev) * unit_prev, units_cur * (profit_cur / units_cur - unit_prev)


def product_effects(current: pl.DataFrame, previous: pl.DataFrame, limit: int = TOP_PRODUCTS) -> list[dict[str, Any]]:
    """Los `limit` SKU que más explican el cambio (por |Δutilidad|), con su descomposición."""
    cur, prev = _by_sku(current), _by_sku(previous)
    out = []
    for sku in cur.keys() | prev.keys():
        c, p = cur.get(sku), prev.get(sku)
        u1, p1 = (c["units"], c["profit"]) if c else (0, 0.0)
        u0, p0 = (p["units"], p["profit"]) if p else (0, 0.0)
        volume, rate = split_effects(u0, p0, u1, p1)
        out.append(
            {
                "sku": sku,
                "product_name": (c or p)["name"],
                "units_current": int(u1),
                "units_previous": int(u0),
                "profit_current": p1,
                "profit_previous": p0,
                "delta": p1 - p0,
                "volume_effect": volume,
                "rate_effect": rate,
                "unit_profit_current": p1 / u1 if u1 > 0 else None,
                "unit_profit_previous": p0 / u0 if u0 > 0 else None,
            }
        )
    out.sort(key=lambda r: (-abs(r["delta"]), r["sku"]))
    return out[:limit]


def channel_breakdown(current: pl.DataFrame, previous: pl.DataFrame) -> list[dict[str, Any]]:
    """Utilidad y ventas por canal en ambos periodos."""
    def per_channel(df: pl.DataFrame) -> dict[str, tuple[float, float]]:
        if df.height == 0:
            return {}
        agg = add_line_profit(df).group_by("channel").agg(
            pl.col("net_profit").sum().alias("profit"), pl.col("gross_sales").sum().alias("sales")
        )
        return {r["channel"]: (r["profit"], r["sales"]) for r in agg.iter_rows(named=True)}

    cur, prev = per_channel(current), per_channel(previous)
    rows = []
    for ch in sorted(cur.keys() | prev.keys()):
        p1, s1 = cur.get(ch, (0.0, 0.0))
        p0, s0 = prev.get(ch, (0.0, 0.0))
        rows.append(
            {
                "channel": ch,
                "profit_current": p1,
                "profit_previous": p0,
                "delta": p1 - p0,
                "sales_current": s1,
                "sales_previous": s0,
            }
        )
    return sorted(rows, key=lambda r: -abs(r["delta"]))


def build_headline(
    days: int,
    change: dict[str, Any],
    components: list[dict[str, Any]],
    products: list[dict[str, Any]],
    has_data: bool,
) -> str:
    """Una o dos frases en español que nombran la causa principal con cifras."""
    if not has_data:
        return f"No hay ventas registradas en los últimos {2 * days} días: no hay nada que comparar todavía."
    now, before, delta = change["profit_current"], change["profit_previous"], change["profit_delta"]
    if abs(delta) < max(1.0, FLAT_THRESHOLD * abs(before)):
        return f"La utilidad se mantuvo estable en los últimos {days} días ({money(now)} contra {money(before)})."
    up = delta > 0
    pct = f" ({abs(delta) / abs(before):.0%})" if before else ""
    first = (
        f"La utilidad {'subió' if up else 'bajó'} {money(delta)}{pct} en los últimos {days} días: "
        f"de {money(before)} a {money(now)}."
    )
    # Causa principal: el renglón de mayor efecto que va en la misma dirección que el cambio.
    causes = [c for c in components if (c["effect"] > 0) == up and abs(c["effect"]) >= 0.01]
    if not causes:
        return first
    main = causes[0]
    phrase = main["better"] if up else main["worse"]
    second = f"La causa principal es {phrase} ({'+' if up else '-'}{money(main['effect'])} de utilidad)"
    movers = [p for p in products if (p["delta"] > 0) == up and p["delta"] != 0]
    if movers:
        top = movers[0]
        lever = "el volumen" if abs(top["volume_effect"]) >= abs(top["rate_effect"]) else "la utilidad por unidad"
        label = top["product_name"] or top["sku"]
        second += f"; el producto que más pesa es {label} ({'+' if up else '-'}{money(top['delta'])}, por {lever})"
    return f"{first} {second}."


# ------------------------------------------------------------------------------ orquestación


async def _pending_settlements(session: AsyncSession) -> float:
    pending = await session.scalars(
        select(Settlement.net_payout).where(Settlement.status.notin_(("paid", "failed")))
    )
    return sum(float(v or 0) for v in pending)


async def _caveats(
    session: AsyncSession, current: pl.DataFrame, previous: pl.DataFrame, today: date, days: int
) -> list[str]:
    notes: list[str] = []
    if previous.height == 0 and current.height:
        notes.append(f"No hay ventas en los {days} días anteriores: la comparación no es representativa.")
    if current.height:
        last = current["date"].max()
        if last < today - timedelta(days=1):
            notes.append(f"Los datos llegan hasta el {last.isoformat()}: los últimos días pueden estar sin cargar.")
    no_cost = sorted(r["sku"] for r in _by_sku(current).values() if r["units"] > 0 and r["cogs"] == 0 and r["sku"] != UNASSIGNED_SKU)
    if no_cost:
        shown = ", ".join(no_cost[:5]) + ("…" if len(no_cost) > 5 else "")
        notes.append(f"{len(no_cost)} producto(s) vendidos sin costo cargado ({shown}): su utilidad está sobrestimada.")
    unassigned = _by_sku(current).get(UNASSIGNED_SKU)
    if unassigned and unassigned["sales"]:
        notes.append(f"Hay {money(unassigned['sales'])} en ventas sin SKU asignado; no se atribuyen a ningún producto.")
    pending = await _pending_settlements(session)
    if pending > 0:
        notes.append(
            f"Hay {money(pending)} en liquidaciones pendientes: las ventas recientes pueden no estar cobradas todavía."
        )
    return notes


async def explain_profit_change(
    session: AsyncSession,
    days: int = 7,
    today: date | None = None,
    channel: Channel | None = None,
) -> dict[str, Any]:
    """Explica el cambio de utilidad de los últimos `days` días frente a los `days` anteriores."""
    today = today or datetime.now(UTC).date()
    (cs, ce), (ps, pe) = window_bounds(today, days)
    frame = await load_gold_frame(session, start=ps, end=ce, channel=channel)
    current = frame.filter(pl.col("date") >= cs)
    previous = frame.filter(pl.col("date") <= pe)

    cur, prev = totals(current), totals(previous)
    change = {
        "profit_current": cur["net_profit"],
        "profit_previous": prev["net_profit"],
        "profit_delta": cur["net_profit"] - prev["net_profit"],
        "profit_pct": (cur["net_profit"] - prev["net_profit"]) / abs(prev["net_profit"]) if prev["net_profit"] else None,
        "units_current": int(cur["units"]),
        "units_previous": int(prev["units"]),
    }
    components = component_effects(cur, prev)
    products = product_effects(current, previous)
    return {
        "days": days,
        "channel": channel.value if channel else None,
        "current": {"start": cs.isoformat(), "end": ce.isoformat(), "totals": cur},
        "previous": {"start": ps.isoformat(), "end": pe.isoformat(), "totals": prev},
        "change": change,
        "components": components,
        "products": products,
        "channels": channel_breakdown(current, previous),
        "caveats": await _caveats(session, current, previous, today, days),
        "headline": build_headline(days, change, components, products, frame.height > 0),
    }
