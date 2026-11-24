"""Precio mínimo con envío real: el precio al que un SKU deja de perder dinero, por canal y estado de destino.

`app.pricing` calcula un piso con el envío PROMEDIO de la capa oro. Pero un envío a California no cuesta lo
mismo que uno a Maine: con un promedio, los estados lejanos venden por debajo de su piso sin que nadie lo
note. Aquí el envío sale del costo REAL de las etiquetas de Redo (`sales.shipping_label_cost`), repartido
entre las líneas de cada orden y agrupado por estado de destino.

Fórmula (por unidad, con los datos de la ventana):

    piso = (COGS + envío + reembolso) / (1 - comisión% - margen_objetivo)

  * COGS       costo unitario VIGENTE hoy (historial de costos), no el promedio de la ventana.
  * envío      etiqueta real repartida por venta bruta entre las líneas de su orden, más el envío que ya
               traiga la línea. Por estado se usa el promedio real de ese estado; si el estado tiene pocas
               unidades con etiqueta (MIN_STATE_UNITS) o la venta no trae estado, el promedio real del SKU.
  * reembolso  dinero reembolsado por unidad vendida, observado en la ventana.
  * comisión%  comisión de la plataforma como fracción de la venta NETA (neta = bruta - promoción), igual
               que en `app.economics`; así el piso se compara directo con el precio realmente cobrado.
  * margen     utilidad objetivo como fracción del precio. Con 0 es el punto de equilibrio.

La comisión es un porcentaje del precio y el resto son costos por unidad, por eso se despeja el precio:
utilidad = p - comisión%·p - (COGS + envío + reembolso) = margen·p. Si comisión% + margen se comen casi todo
el precio (`MIN_DENOMINATOR` de `app.pricing`) no existe un piso alcanzable y el SKU se marca "unreachable".

Cada venta (línea de orden) se compara contra el piso de SU estado: la pérdida estimada es la suma de
(piso - precio) × unidades de las ventas que quedaron bajo su piso. Es una ESTIMACIÓN: el piso usa costos
promedio y no incluye publicidad (no se reparte por orden).

Importado solo por `app.min_price_api` y, para el Centro de acciones, por `app.action_providers`.
Solo lectura: no escribe nada en la base.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.action_center import ActionItem, action_provider
from app.costs import CostHistory, load_cost_history
from app.models import (
    Account,
    Channel,
    OrderLine,
    Product,
    SalesOrder,
    ShippingLabelCost,
)
from app.pricing import MIN_DENOMINATOR, MIN_UNITS
from app.shipping_profit import NO_STATE

DEFAULT_DAYS = 90
MIN_STATE_UNITS = 2  # unidades con etiqueta real mínimas para fiarse del promedio de un estado
MAX_TARGET_MARGIN = 0.9
PRICE_EPSILON = 0.005  # medio centavo: por debajo de esto un "faltante" es ruido de redondeo
PROJECTION_DAYS = 30  # horizonte común de los importes del Centro de acciones
MAX_ACTIONS = 15

ShippingBasis = Literal["real", "estimated", "none"]
Status = Literal["below", "ok", "no_cost", "insufficient", "unreachable"]
STATUS_ORDER: tuple[Status, ...] = ("below", "ok", "unreachable", "no_cost", "insufficient")


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def solve_min_price(
    *, cogs: float, shipping: float, refund: float, fee_rate: float, margin: float
) -> float | None:
    """Precio realizado mínimo que cubre costos y deja `margin`. None si ninguno lo logra (ver módulo)."""
    denominator = 1 - fee_rate - margin
    if denominator <= MIN_DENOMINATOR:
        return None
    return (cogs + shipping + refund) / denominator


@dataclass(frozen=True, slots=True)
class SaleLine:
    """Una línea de orden con su parte de la etiqueta de Redo (None = la orden no tiene etiqueta cobrada)."""

    channel: str
    sku: str
    product_id: int
    name: str | None
    state: str | None
    units: int
    gross: float
    promo: float
    fees: float
    line_shipping: float
    refund: float
    label: float | None

    @property
    def net(self) -> float:
        return self.gross - self.promo

    @property
    def unit_price(self) -> float:
        return self.net / self.units

    @property
    def shipping(self) -> float:
        """Envío real de la línea: el que ya trae más su parte de la etiqueta."""
        return self.line_shipping + (self.label or 0.0)


async def _label_totals(session: AsyncSession) -> dict[str, float]:
    """Costo neto de etiquetas por orden externa. Una orden con neto <= 0 (anulada) cuenta como sin etiqueta."""
    totals: dict[str, float] = defaultdict(float)
    rows = await session.execute(select(ShippingLabelCost.external_order_id, ShippingLabelCost.amount))
    for external_id, amount in rows.all():
        totals[external_id] += _f(amount)
    return {oid: total for oid, total in totals.items() if total > 0}


def _allocate(label_total: float | None, gross: float, order_gross: float, units: int, order_units: int) -> float | None:
    """Parte de la etiqueta que le toca a una línea: por venta bruta, o por unidades si la orden no tiene venta."""
    if label_total is None:
        return None
    share = gross / order_gross if order_gross > 0 else units / (order_units or 1)
    return label_total * share


async def load_sale_lines(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    channel: Channel | None = None,
    account: str | None = None,
) -> list[SaleLine]:
    """Líneas vendidas en [start, end] con la etiqueta de Redo ya repartida entre las líneas de su orden."""
    query = (
        select(
            OrderLine.order_id,
            SalesOrder.external_order_id,
            SalesOrder.ship_state,
            Account.channel_code,
            Product.id,
            Product.sku,
            Product.name,
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
        .where(OrderLine.order_date >= start, OrderLine.order_date <= end, OrderLine.units > 0)
    )
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)
    fetched = (await session.execute(query)).all()
    labels = await _label_totals(session)

    order_gross: dict[int, float] = defaultdict(float)
    order_units: dict[int, int] = defaultdict(int)
    for row in fetched:
        order_gross[row[0]] += _f(row[8])
        order_units[row[0]] += row[7]

    return [
        SaleLine(
            channel=ch,
            sku=sku,
            product_id=pid,
            name=name,
            state=(state or "").strip() or None,
            units=units,
            gross=_f(gross),
            promo=_f(promo),
            fees=_f(fees),
            line_shipping=_f(ship),
            refund=_f(refund),
            label=_allocate(labels.get(ext_id), _f(gross), order_gross[oid], units, order_units[oid]),
        )
        for oid, ext_id, state, ch, pid, sku, name, units, gross, promo, fees, ship, refund in fetched
    ]


def _real_shipping_unit(lines: list[SaleLine]) -> tuple[float | None, int]:
    """Envío real por unidad de las líneas con etiqueta, y cuántas unidades la respaldan."""
    labeled = [ln for ln in lines if ln.label is not None]
    units = sum(ln.units for ln in labeled)
    return (sum(ln.shipping for ln in labeled) / units if units else None), units


def _sku_shipping(lines: list[SaleLine]) -> tuple[float, ShippingBasis, int]:
    """Envío por unidad del SKU: el real si hay etiquetas; si no, el que traen las líneas (estimado)."""
    real, labeled_units = _real_shipping_unit(lines)
    if real is not None:
        return real, "real", labeled_units
    units = sum(ln.units for ln in lines)
    estimated = sum(ln.line_shipping for ln in lines) / units if units else 0.0
    return estimated, ("estimated" if estimated > 0 else "none"), 0


@dataclass(frozen=True, slots=True)
class Economics:
    """Costos por unidad de un (canal, SKU) que no dependen del estado de destino."""

    cogs: float
    refund: float
    fee_rate: float
    margin: float

    def floor(self, shipping: float, margin: float | None = None) -> float | None:
        return solve_min_price(
            cogs=self.cogs,
            shipping=shipping,
            refund=self.refund,
            fee_rate=self.fee_rate,
            margin=self.margin if margin is None else margin,
        )


def _state_row(state: str, lines: list[SaleLine], sku_ship: float, eco: Economics) -> dict[str, Any]:
    """Piso y resultado de un SKU en un estado: sus ventas allí contra el piso con el envío de ese estado."""
    real, labeled_units = _real_shipping_unit(lines)
    reliable = real is not None and labeled_units >= MIN_STATE_UNITS
    ship = real if (reliable and real is not None) else sku_ship
    floor, break_even = eco.floor(ship), eco.floor(ship, 0.0)
    units = sum(ln.units for ln in lines)
    price = sum(ln.net for ln in lines) / units
    return {
        "state": state,
        "units": units,
        "realized_price": price,
        "shipping_unit": ship,
        "shipping_basis": "state" if reliable else "sku",
        "min_price": floor,
        "break_even_price": break_even,
        "gap": price - floor if floor is not None else None,
        "loses_money": break_even is not None and price < break_even - PRICE_EPSILON,
    }


def _shortfall(lines: list[SaleLine], floor_for: dict[str, float | None], default: float | None) -> tuple[int, float]:
    """Unidades vendidas bajo el piso de su estado y lo que faltó cobrar: suma de (piso - precio) × unidades."""
    below_units, loss = 0, 0.0
    for ln in lines:
        floor = floor_for.get(ln.state or NO_STATE, default)
        if floor is not None and ln.unit_price < floor - PRICE_EPSILON:
            below_units += ln.units
            loss += (floor - ln.unit_price) * ln.units
    return below_units, loss


def _status(units: int, eco: Economics, floor: float | None) -> Status:
    if units < MIN_UNITS:
        return "insufficient"
    if eco.cogs <= 0:
        return "no_cost"  # sin costo cargado el piso no significa nada
    if floor is None:
        return "unreachable"
    return "ok"


def _sku_row(channel: str, sku: str, lines: list[SaleLine], costs: CostHistory, today: date, margin: float) -> dict[str, Any]:
    units = sum(ln.units for ln in lines)
    net = sum(ln.net for ln in lines)
    eco = Economics(
        cogs=costs.unit_cost(lines[0].product_id, today),
        refund=sum(ln.refund for ln in lines) / units,
        fee_rate=_ratio(sum(ln.fees for ln in lines), net) or 0.0,
        margin=margin,
    )
    sku_ship, basis, labeled_units = _sku_shipping(lines)
    floor, break_even = eco.floor(sku_ship), eco.floor(sku_ship, 0.0)
    status = _status(units, eco, floor)

    by_state: dict[str, list[SaleLine]] = defaultdict(list)
    for ln in lines:
        if ln.state:
            by_state[ln.state].append(ln)
    states = [_state_row(s, ls, sku_ship, eco) for s, ls in by_state.items()]
    states.sort(key=lambda r: (-(r["min_price"] or 0.0), r["state"]))

    below_units, loss = 0, 0.0
    if status == "ok":
        below_units, loss = _shortfall(lines, {r["state"]: r["min_price"] for r in states}, floor)
    price = net / units
    return {
        "channel": channel,
        "sku": sku,
        "product_name": next((ln.name for ln in lines if ln.name), None),
        "units": units,
        "realized_price": price,
        "cogs_unit": eco.cogs,
        "fee_rate": eco.fee_rate,
        "refund_rate": _ratio(sum(ln.refund for ln in lines), net) or 0.0,
        "refund_unit": eco.refund,
        "shipping_unit": sku_ship,
        "shipping_basis": basis,
        "labeled_units": labeled_units,
        "min_price": floor,
        "break_even_price": break_even,
        "max_state_min_price": max((r["min_price"] for r in states if r["min_price"] is not None), default=None),
        "gap": price - floor if floor is not None else None,
        "gap_pct": (price - floor) / floor if floor else None,
        "below_units": below_units,
        "below_pct": below_units / units if status == "ok" else None,
        "estimated_loss": loss,
        "status": "below" if below_units > 0 else status,
        "loss_states": sorted(r["state"] for r in states if r["loses_money"] and status == "ok"),
        "states": states,
    }


def _summary(rows: list[dict[str, Any]], lines: list[SaleLine]) -> dict[str, Any]:
    units = sum(ln.units for ln in lines)
    labeled = sum(ln.units for ln in lines if ln.label is not None)
    return {
        "skus": len(rows),
        "below_skus": sum(1 for r in rows if r["status"] == "below"),
        "estimated_loss": sum(r["estimated_loss"] for r in rows),
        "skus_real_shipping": sum(1 for r in rows if r["shipping_basis"] == "real"),
        "skus_estimated_shipping": sum(1 for r in rows if r["shipping_basis"] != "real"),
        "skus_no_cost": sum(1 for r in rows if r["status"] == "no_cost"),
        "label_coverage": _ratio(labeled, units),
    }


async def min_price_report(
    session: AsyncSession,
    *,
    days: int = DEFAULT_DAYS,
    target_margin: float = 0.0,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Precio mínimo por SKU y canal con el envío real de Redo, brecha, pérdida estimada y piso por estado."""
    if not 0 <= target_margin <= MAX_TARGET_MARGIN:
        raise ValueError(f"El margen objetivo debe estar entre 0 y {MAX_TARGET_MARGIN}")
    today = today or datetime.now(UTC).date()
    lines = await load_sale_lines(
        session, start=today - timedelta(days=days - 1), end=today, channel=channel, account=account
    )
    costs = await load_cost_history(session)

    groups: dict[tuple[str, str], list[SaleLine]] = defaultdict(list)
    for ln in lines:
        groups[(ln.channel, ln.sku)].append(ln)
    rows = [_sku_row(ch, sku, ls, costs, today, target_margin) for (ch, sku), ls in groups.items()]
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), -r["estimated_loss"], r["channel"], r["sku"]))
    return {
        "days": days,
        "target_margin": target_margin,
        "min_units": MIN_UNITS,
        "summary": _summary(rows, lines),
        "rows": rows,
    }


# --- Centro de acciones ---------------------------------------------------------------------------------


def _money(value: float) -> str:
    return f"${value:,.2f}"


@action_provider("price_floor", "Precio por debajo del mínimo")
async def price_floor_actions(session: AsyncSession) -> list[ActionItem]:
    """Una acción por SKU con ventas bajo su piso (equilibrio, margen 0): sube el precio o recorta el envío.

    Impacto: lo que faltó cobrar en la ventana (suma de (piso - precio) × unidades), llevado a 30 días. Si el
    SKU sigue vendiéndose a ese precio, la pérdida se repite al mismo ritmo. Un SKU en varios canales suma las
    pérdidas y el detalle muestra el canal donde más se pierde.
    """
    report = await min_price_report(session, days=DEFAULT_DAYS, target_margin=0.0)
    days = int(report["days"]) or DEFAULT_DAYS
    by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report["rows"]:
        if row["status"] == "below" and row["estimated_loss"] > 0:
            by_sku[row["sku"]].append(row)

    items: list[ActionItem] = []
    for sku, rows in by_sku.items():
        worst = max(rows, key=lambda r: r["estimated_loss"])
        loss = sum(r["estimated_loss"] for r in rows)
        where = f" en {len(rows)} canales" if len(rows) > 1 else ""
        states = worst["loss_states"][:5]
        far = f" Pierde dinero en: {', '.join(states)}." if states else ""
        items.append(
            ActionItem(
                key=f"price_floor:{sku}",
                kind="price_floor",
                title=f"{sku}: se vende por debajo de su precio mínimo{where}",
                detail=(
                    f"{worst['channel']}: precio actual {_money(worst['realized_price'])} vs mínimo sugerido "
                    f"{_money(worst['min_price'])} (envío real {_money(worst['shipping_unit'])} por unidad). "
                    f"{worst['below_pct']:.0%} de las unidades quedaron bajo el piso.{far}"
                ),
                impact_usd=loss * PROJECTION_DAYS / days,
                impact_basis=(
                    f"Faltante de cobro de las ventas bajo el piso en {days} días ({_money(loss)}), llevado a "
                    f"{PROJECTION_DAYS} días; el piso usa el costo real de las etiquetas de Redo por estado."
                ),
                effort="low",
                target="pricing",
                entity=sku,
                meta={
                    "channel": worst["channel"],
                    "realized_price": worst["realized_price"],
                    "min_price": worst["min_price"],
                    "below_pct": worst["below_pct"],
                    "loss_states": worst["loss_states"],
                },
            )
        )
    items.sort(key=lambda i: (-i.impact_usd, i.key))
    return items[:MAX_ACTIONS]
