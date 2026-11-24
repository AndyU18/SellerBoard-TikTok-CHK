"""Pagos y comisiones de Walmart: lo que se puede reconstruir con lo que SÍ está guardado.

Qué hay en la base (ver `app.connectors.walmart.build_order_lines`):
  * La comisión de cada línea (`OrderLine.platform_fees`) sale del reporte de conciliación cuando la orden ya
    está en un reporte (comisión real, neta de reembolsos de comisión); si no, se ESTIMA con 15 % de la venta.
  * Los cargos de etiqueta de Walmart ("Shipping Label") quedan en `OrderLine.shipping_cost` y los
    reembolsos en `OrderLine.refund_amount`.

Qué NO hay: la bandera real/estimada por línea (solo se contó al sincronizar), otros ajustes del reporte
(cargos de servicio, WFS, multas...) ni liquidaciones/pagos de Walmart (`Settlement` solo tiene TikTok Shop).
Por eso la comisión estimada se INFIERE (la comisión coincide con la venta x 15 %) y el neto es un neto
ESPERADO, no un pago confirmado. Todo esto se devuelve en `missing` para que la pantalla lo diga.
Solo lectura.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.walmart import DEFAULT_COMMISSION_RATE
from app.models import Account, Channel, OrderLine, SalesOrder

# Las comisiones se guardan con 4 decimales: una estimada (venta x tasa) coincide dentro de este margen.
ESTIMATE_TOLERANCE = 0.00005
PERCENT_DIGITS = 4

ADJUSTMENT_LABELS = {
    "refunds": "Reembolsos al cliente",
    "shipping_labels": "Etiquetas de envío de Walmart",
}

MISSING_NOTES = (
    (
        "No se guarda si la comisión de cada línea es real o estimada: se infiere comparándola con la venta "
        f"x {DEFAULT_COMMISSION_RATE:.0%}. Una comisión real que justo sea del {DEFAULT_COMMISSION_RATE:.0%} "
        "se contaría como estimada."
    ),
    (
        "No hay liquidaciones ni pagos de Walmart guardados (la tabla de liquidaciones solo tiene TikTok "
        "Shop): no se conocen fechas de pago ni el monto depositado."
    ),
    (
        "Los demás ajustes del reporte de conciliación (cargos de servicio, WFS, multas, publicidad) no se "
        "guardan: solo se contaron al sincronizar."
    ),
    "El neto esperado es ventas - comisiones - reembolsos - etiquetas de Walmart; no es el pago confirmado.",
)


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _ratio(part: float, whole: float) -> float | None:
    return part / whole if whole else None


def is_estimated_fee(fee: float, gross: float, rate: float = DEFAULT_COMMISSION_RATE) -> bool:
    """La comisión de la línea es la estimada: venta x tasa (una línea sin venta no se estima)."""
    return gross > 0 and abs(fee - gross * rate) <= ESTIMATE_TOLERANCE


@dataclass
class _Bucket:
    """Acumulador de un mes o del total."""

    orders: set[str]
    estimated_orders: set[str]
    sales: float = 0.0
    fees_actual: float = 0.0
    fees_estimated: float = 0.0
    estimated_sales: float = 0.0
    refunds: float = 0.0
    shipping_labels: float = 0.0

    @classmethod
    def empty(cls) -> _Bucket:
        return cls(orders=set(), estimated_orders=set())

    def add(self, order: str, gross: float, fee: float, refund: float, label: float) -> None:
        self.orders.add(order)
        self.sales += gross
        self.refunds += refund
        self.shipping_labels += label
        if is_estimated_fee(fee, gross):
            self.fees_estimated += fee
            self.estimated_sales += gross
            self.estimated_orders.add(order)
        else:
            self.fees_actual += fee

    @property
    def fees(self) -> float:
        return self.fees_actual + self.fees_estimated

    def as_row(self) -> dict[str, Any]:
        return {
            "orders": len(self.orders),
            "sales": self.sales,
            "fees_actual": self.fees_actual,
            "fees_estimated": self.fees_estimated,
            "fees": self.fees,
            "effective_pct": _ratio(self.fees, self.sales),
            "estimated_orders": len(self.estimated_orders),
            "estimated_sales": self.estimated_sales,
            "refunds": self.refunds,
            "shipping_labels": self.shipping_labels,
            "net_expected": self.sales - self.fees - self.refunds - self.shipping_labels,
        }


def _window(days: int, today: date) -> tuple[date, date]:
    return today - timedelta(days=days - 1), today


def _adjustment_rows(total: _Bucket) -> list[dict[str, Any]]:
    amounts = {"refunds": total.refunds, "shipping_labels": total.shipping_labels}
    return [
        {"key": key, "label": ADJUSTMENT_LABELS[key], "amount": amount}
        for key, amount in amounts.items()
        if amount
    ]


async def walmart_payments_report(
    session: AsyncSession, *, days: int, today: date
) -> dict[str, Any]:
    """Ventas, comisiones (reales vs estimadas), ajustes y neto esperado de Walmart, por mes."""
    start, end = _window(days, today)
    query = (
        select(
            SalesOrder.external_order_id,
            OrderLine.order_date,
            OrderLine.gross_sales,
            OrderLine.platform_fees,
            OrderLine.refund_amount,
            OrderLine.shipping_cost,
        )
        .join(SalesOrder, OrderLine.order_id == SalesOrder.id)
        .join(Account, OrderLine.account_id == Account.id)
        .where(
            Account.channel_code == Channel.walmart.value,
            OrderLine.order_date >= start,
            OrderLine.order_date <= end,
        )
    )
    total = _Bucket.empty()
    months: dict[str, _Bucket] = {}
    for order, day, gross, fee, refund, label in (await session.execute(query)).all():
        args = (str(order), _f(gross), _f(fee), _f(refund), _f(label))
        total.add(*args)
        months.setdefault(day.strftime("%Y-%m"), _Bucket.empty()).add(*args)

    kpis = total.as_row()
    kpis["estimated_fees_pct"] = _ratio(total.fees_estimated, total.fees)
    kpis["estimated_orders_pct"] = _ratio(len(total.estimated_orders), len(total.orders))
    kpis["adjustments"] = total.refunds + total.shipping_labels
    return {
        "days": days,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "assumed_commission_rate": round(DEFAULT_COMMISSION_RATE, PERCENT_DIGITS),
        "kpis": kpis,
        "by_month": [{"month": m, **months[m].as_row()} for m in sorted(months)],
        "by_adjustment": _adjustment_rows(total),
        "missing": list(MISSING_NOTES),
    }
