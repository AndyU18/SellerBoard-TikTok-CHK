"""Utilidad en riesgo por costo faltante: cuánto de la utilidad reportada descansa en SKU sin COGS.

Por qué existe: un SKU sin costo cargado cuenta su costo como 0 (app.costs.CostHistory.unit_cost), así que
su utilidad sale inflada y el total del tablero se ve mejor de lo que es. Este módulo cuantifica ese sesgo
y ordena qué costos cargar primero (los de más ventas cubren más con menos trabajo).

Cálculo (por línea de orden, antes de publicidad y de la etiqueta de Redo, que no se reparte por SKU):

    utilidad reportada = ventas netas - comisiones - envío - reembolsos - COGS
    ventas netas       = ventas brutas - promo

Para los SKU SIN costo vigente hoy:
    costo estimado     = ratio mediano COGS/ventas de los SKU que SÍ tienen costo x ventas netas del SKU
    utilidad ajustada  = utilidad reportada - costo estimado
    utilidad en riesgo = utilidad reportada - utilidad ajustada (= costo estimado)

El costo estimado es una APROXIMACIÓN: supone que el SKU sin costo tiene el mismo peso de costo sobre el
precio que el SKU típico del catálogo. Se usa la mediana (no el promedio) para que un SKU extremo no
mueva la estimación, y `FALLBACK_COST_RATIO` cuando hay muy pocos SKU con costo para confiar en ella.
La prioridad de carga es el ranking por ventas netas: es lo que más utilidad "dudosa" cubre por SKU.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.action_center import ActionItem, action_provider
from app.costs import CostHistory, load_cost_history
from app.models import Account, Channel, OrderLine, Product

# Con menos SKU con costo que esto la mediana no es confiable y se usa el ratio de respaldo.
MIN_COST_SAMPLES = 3
# Peso típico del costo sobre la venta neta en e-commerce de producto físico; solo es respaldo.
FALLBACK_COST_RATIO = 0.35
# Tope defensivo: un costo estimado mayor que la venta no tiene sentido como aproximación.
MAX_COST_RATIO = 0.95
# Cuántos SKU se cubren al cargar los primeros N (los que muestra el resumen).
COVERAGE_STEPS: tuple[int, int] = (5, 10)
# Ventana y límites del proveedor del Centro de acciones.
PROVIDER_DAYS = 90
PROVIDER_TOP = 15
PROJECTION_DAYS = 30
ESTIMATE_NOTE = (
    "El costo estimado es una aproximación: usa el peso mediano del costo sobre la venta de los SKU que "
    "sí tienen costo. Cárgales el costo real para ver la utilidad verdadera."
)


@dataclass(slots=True)
class _SkuTotals:
    """Acumulado de un SKU en la ventana. Todo en USD salvo `units`."""

    product_id: int
    sku: str
    name: str | None
    units: int = 0
    net_sales: float = 0.0
    fees: float = 0.0
    shipping: float = 0.0
    refunds: float = 0.0
    cogs: float = 0.0

    @property
    def reported_profit(self) -> float:
        return self.net_sales - self.fees - self.shipping - self.refunds - self.cogs


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


async def _load_lines(
    session: AsyncSession, start: date, end: date, channel: Channel | None, account: str | None
) -> list[Any]:
    query = (
        select(
            Product.id,
            Product.sku,
            Product.name,
            OrderLine.order_date,
            OrderLine.units,
            OrderLine.gross_sales,
            OrderLine.promo,
            OrderLine.platform_fees,
            OrderLine.shipping_cost,
            OrderLine.refund_amount,
        )
        .join(Product, OrderLine.product_id == Product.id)
        .join(Account, OrderLine.account_id == Account.id)
        .where(OrderLine.order_date >= start, OrderLine.order_date <= end)
    )
    if channel:
        query = query.where(Account.channel_code == channel.value)
    if account:
        query = query.where(Account.name == account)
    return list((await session.execute(query)).all())


def _aggregate(rows: list[Any], costs: CostHistory) -> dict[int, _SkuTotals]:
    """Suma las líneas por SKU. El COGS usa el costo vigente en la fecha de cada línea."""
    totals: dict[int, _SkuTotals] = {}
    for pid, sku, name, day, units, gross, promo, fees, ship, refund in rows:
        t = totals.get(pid)
        if t is None:
            t = totals[pid] = _SkuTotals(product_id=pid, sku=sku, name=name)
        t.units += units
        t.net_sales += _f(gross) - _f(promo)
        t.fees += _f(fees)
        t.shipping += _f(ship)
        t.refunds += _f(refund)
        t.cogs += units * costs.unit_cost(pid, day)
    return totals


def _has_current_cost(costs: CostHistory, product_id: int, today: date) -> bool:
    """Mismo criterio que `has_cost` en la lista de productos: costo vigente hoy mayor que 0."""
    return costs.unit_cost(product_id, today) > 0


def cost_ratio(with_cost: list[_SkuTotals]) -> tuple[float, str, int]:
    """(ratio, origen, muestras): mediana COGS/ventas de los SKU con costo, o el respaldo si son pocos."""
    ratios = [t.cogs / t.net_sales for t in with_cost if t.net_sales > 0 and t.cogs > 0]
    if len(ratios) < MIN_COST_SAMPLES:
        return FALLBACK_COST_RATIO, "fallback", len(ratios)
    return min(MAX_COST_RATIO, median(ratios)), "median", len(ratios)


def _risk_row(t: _SkuTotals, ratio: float) -> dict[str, Any]:
    est_cogs = ratio * max(t.net_sales, 0.0)
    reported = t.reported_profit
    adjusted = reported - est_cogs
    return {
        "sku": t.sku,
        "name": t.name,
        "units": t.units,
        "net_sales": round(t.net_sales, 2),
        "reported_profit": round(reported, 2),
        "est_unit_cost": round(est_cogs / t.units, 2) if t.units > 0 else 0.0,
        "est_cogs": round(est_cogs, 2),
        "adjusted_profit": round(adjusted, 2),
        "profit_at_risk": round(reported - adjusted, 2),
        "adjusted_margin": round(adjusted / t.net_sales, 4) if t.net_sales > 0 else None,
    }


def _rank_by_sales(rows: list[dict[str, Any]]) -> None:
    """Asigna prioridad (1 = cargar primero) y el % acumulado de las ventas sin costo que se cubre."""
    rows.sort(key=lambda r: (-r["net_sales"], r["sku"]))
    total = sum(max(r["net_sales"], 0.0) for r in rows)
    running = 0.0
    for rank, row in enumerate(rows, start=1):
        running += max(row["net_sales"], 0.0)
        row["priority"] = rank
        row["sales_share"] = max(row["net_sales"], 0.0) / total if total > 0 else None
        row["cumulative_sales_share"] = running / total if total > 0 else None


def _coverage(rows: list[dict[str, Any]], n: int) -> float | None:
    """% de las ventas sin costo que cubren los primeros `n` SKU de la lista de carga."""
    if not rows:
        return None
    return rows[min(n, len(rows)) - 1]["cumulative_sales_share"]


def _share(part: float, whole: float) -> float | None:
    return part / whole if whole > 0 else None


def _summary(
    all_rows_sales: float,
    all_profit: float,
    risk_rows: list[dict[str, Any]],
    ratio: tuple[float, str, int],
    window: tuple[int, date, date],
) -> dict[str, Any]:
    missing_sales = sum(r["net_sales"] for r in risk_rows)
    missing_profit = sum(r["reported_profit"] for r in risk_rows)
    at_risk = sum(r["profit_at_risk"] for r in risk_rows)
    days, start, end = window
    return {
        "days": days,
        "start": start,
        "end": end,
        "missing_skus": len(risk_rows),
        "net_sales_total": round(all_rows_sales, 2),
        "net_sales_missing": round(missing_sales, 2),
        "pct_sales_missing": _share(missing_sales, all_rows_sales),
        "reported_profit_total": round(all_profit, 2),
        "reported_profit_missing": round(missing_profit, 2),
        # Solo tiene sentido contra una utilidad total positiva; si no, la proporción engaña.
        "pct_profit_missing": _share(missing_profit, all_profit),
        "adjusted_profit_total": round(all_profit - at_risk, 2),
        "profit_at_risk": round(at_risk, 2),
        "overstated_pct": _share(at_risk, all_profit),
        "cost_ratio": round(ratio[0], 4),
        "cost_ratio_source": ratio[1],
        "cost_ratio_samples": ratio[2],
        "coverage": {str(n): _coverage(risk_rows, n) for n in COVERAGE_STEPS},
        "note": ESTIMATE_NOTE,
    }


async def cost_risk_report(
    session: AsyncSession,
    *,
    days: int = PROVIDER_DAYS,
    channel: Channel | None = None,
    account: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Resumen + una fila por SKU sin costo, ordenada por prioridad de carga (ventas netas)."""
    if days < 1:
        raise ValueError("days debe ser al menos 1")
    today = today or datetime.now(UTC).date()
    start = today - timedelta(days=days - 1)

    costs = await load_cost_history(session)
    totals = _aggregate(await _load_lines(session, start, today, channel, account), costs)

    missing = [t for t in totals.values() if not _has_current_cost(costs, t.product_id, today)]
    with_cost = [t for t in totals.values() if _has_current_cost(costs, t.product_id, today)]
    ratio = cost_ratio(with_cost)

    risk_rows = [_risk_row(t, ratio[0]) for t in missing]
    _rank_by_sales(risk_rows)

    net_total = sum(max(t.net_sales, 0.0) for t in totals.values())
    profit_total = sum(t.reported_profit for t in totals.values())
    return {
        "summary": _summary(net_total, profit_total, risk_rows, ratio, (days, start, today)),
        "skus": risk_rows,
    }


def _projected(at_risk: float, days: int) -> float:
    """Utilidad en riesgo de la ventana llevada a 30 días, para comparar acciones entre sí."""
    return at_risk * PROJECTION_DAYS / days


@action_provider("missing_costs", "Costos faltantes")
async def missing_cost_actions(session: AsyncSession) -> list[ActionItem]:
    """Una acción por cada uno de los SKU de mayor riesgo: cargar su costo real."""
    report = await cost_risk_report(session, days=PROVIDER_DAYS)
    ratio = report["summary"]["cost_ratio"]
    ranked = sorted(
        (r for r in report["skus"] if r["profit_at_risk"] > 0),
        key=lambda r: (-r["profit_at_risk"], r["sku"]),
    )[:PROVIDER_TOP]
    return [
        ActionItem(
            key=f"missing_costs:{r['sku']}",
            kind="missing_costs",
            title=f"Cargar el costo de {r['sku']}",
            detail=(
                f"{r['units']} unidades y ${r['net_sales']:,.0f} en ventas en {PROVIDER_DAYS} días con costo 0: "
                f"su utilidad reportada (${r['reported_profit']:,.0f}) está inflada."
            ),
            impact_usd=_projected(r["profit_at_risk"], PROVIDER_DAYS),
            impact_basis=(
                f"Costo estimado = {ratio:.0%} de las ventas (mediana de los SKU con costo) sobre "
                f"{PROVIDER_DAYS} días, llevado a {PROJECTION_DAYS} días."
            ),
            effort="low",
            target="costs",
            entity=r["sku"],
            meta={"priority": r["priority"], "net_sales": r["net_sales"], "units": r["units"]},
        )
        for r in ranked
    ]
