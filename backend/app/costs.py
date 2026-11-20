"""COGS con historial: costo unitario de un producto en una fecha dada.

El costo de un producto es la suma de sus componentes (producto, flete, aranceles...). De cada
componente rige la fila con la vigencia más reciente que no pase de la fecha consultada.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CostComponentType, ProductCostHistory

PRODUCT_COST = "product_cost"
# Un costo cargado sin fecha rige desde el principio, para que cubra todo el historial.
BASE_VALID_FROM = date(2000, 1, 1)


class CostHistory:
    """Índice en memoria de ProductCostHistory para consultar muchas fechas rápido."""

    def __init__(self, rows: list[tuple[int, int, date, float]]):
        series: dict[tuple[int, int], list[tuple[date, float]]] = defaultdict(list)
        for product_id, component_id, valid_from, amount in sorted(
            rows, key=lambda r: r[2]
        ):
            series[(product_id, component_id)].append((valid_from, amount))
        self._dates = {k: [d for d, _ in v] for k, v in series.items()}
        self._amounts = {k: [a for _, a in v] for k, v in series.items()}
        self._by_product: dict[int, list[int]] = defaultdict(list)
        for product_id, component_id in series:
            self._by_product[product_id].append(component_id)

    def unit_cost(self, product_id: int, day: date) -> float:
        total = 0.0
        for component_id in self._by_product.get(product_id, ()):
            key = (product_id, component_id)
            i = bisect_right(self._dates[key], day)
            if i:
                total += self._amounts[key][i - 1]
        return total

    def has_cost(self, product_id: int) -> bool:
        return product_id in self._by_product


async def load_cost_history(session: AsyncSession) -> CostHistory:
    rows = (
        await session.execute(
            select(
                ProductCostHistory.product_id,
                ProductCostHistory.component_type_id,
                ProductCostHistory.valid_from,
                ProductCostHistory.amount,
            )
        )
    ).all()
    return CostHistory([(p, c, d, float(a)) for p, c, d, a in rows])


async def product_cost_component_id(session: AsyncSession) -> int:
    component_id = await session.scalar(
        select(CostComponentType.id).where(CostComponentType.code == PRODUCT_COST)
    )
    if component_id is None:
        raise RuntimeError(
            f"Falta el tipo de costo '{PRODUCT_COST}' en catalog.cost_component_type"
        )
    return component_id


async def set_product_cost(
    session: AsyncSession,
    product_id: int,
    amount: Decimal,
    valid_from: date | None = None,
    *,
    component_id: int | None = None,
    existing: list[ProductCostHistory] | None = None,
) -> bool:
    """Fija el costo del producto desde `valid_from`. Devuelve True si cambió algo.

    Sin fecha: si el producto aún no tiene costo rige desde siempre; si ya tenía, rige desde hoy
    (no reescribe lo ya calculado antes de hoy).
    `component_id` y `existing` los pasan las cargas masivas (ya los leyeron en lote); `existing`
    se actualiza con la fila nueva para que la siguiente llamada la vea.
    """
    if component_id is None:
        component_id = await product_cost_component_id(session)
    if existing is None:
        existing = list(
            (
                await session.scalars(
                    select(ProductCostHistory).where(
                        ProductCostHistory.product_id == product_id,
                        ProductCostHistory.component_type_id == component_id,
                    )
                )
            ).all()
        )
    if valid_from is None:
        valid_from = date.today() if existing else BASE_VALID_FROM

    same_day = next((r for r in existing if r.valid_from == valid_from), None)
    if same_day is not None:
        changed = same_day.amount != amount
        same_day.amount = amount
        return changed

    current = max(
        (r for r in existing if r.valid_from <= valid_from),
        key=lambda r: r.valid_from,
        default=None,
    )
    if current is not None and current.amount == amount:
        return False  # ya rige ese mismo costo; no se crea una vigencia redundante
    row = ProductCostHistory(
        product_id=product_id,
        component_type_id=component_id,
        amount=amount,
        valid_from=valid_from,
    )
    session.add(row)
    existing.append(row)
    return True
