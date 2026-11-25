"""Proveedor del Centro de acciones para TikTok Creator.

Importado solo por `app.action_providers`. Una sola acción agrupada: "revisa los N mejores productos nuevos
del catálogo de afiliados". Solo lectura; apunta a la pantalla "tiktok".

Impacto (el Centro oculta las acciones con impacto 0): comisión por unidad x demanda mensual aproximada x
ACHIEVABLE_SHARE. `units_sold` es un acumulado, así que la demanda mensual es `units_sold / 12` (ver
`analytics.monthly_units`), con piso MIN_MONTHLY_UNITS para los productos aún sin ventas. ACHIEVABLE_SHARE es
la fracción de esa demanda que una sola cuenta de creador puede razonablemente capturar: sin ella el
impacto sería el de TODO el mercado, no el nuestro.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import tiktok_creator_analytics as analytics
from app.action_center import ActionItem, action_provider

TARGET = "tiktok"
KIND = "tiktok_creator_opportunities"
TOP_PRODUCTS = 5
MIN_SCORE = 2.0  # por debajo de esto (sin afinidad, sin ventas, comisión mínima) no vale la pena
ACHIEVABLE_SHARE = 0.02
MIN_MONTHLY_UNITS = 1.0
MIN_IMPACT_USD = 1.0


def estimated_monthly_commission(product: dict[str, Any]) -> float:
    """Comisión mensual estimada de un producto (ver el docstring del módulo)."""
    per_unit = product["commission_per_unit"] or 0.0
    demand = max(analytics.monthly_units(product["units_sold"]), MIN_MONTHLY_UNITS)
    return per_unit * demand * ACHIEVABLE_SHARE


@action_provider(KIND, "Productos nuevos para afiliados (TikTok Creator)")
async def creator_opportunity_actions(session: AsyncSession) -> list[ActionItem]:
    """Los mejores productos nuevos de la semana por puntaje, agrupados en una sola acción."""
    candidates = [p for p in await analytics.new_products(session, datetime.now(UTC)) if p["score"] >= MIN_SCORE]
    top = candidates[:TOP_PRODUCTS]
    if not top:
        return []
    impact = sum(estimated_monthly_commission(p) for p in top)
    names = "; ".join(f"{(p['title'] or p['product_id'])[:60]} (puntaje {p['score']:g})" for p in top)
    return [
        ActionItem(
            key=f"{KIND}:weekly",
            kind=KIND,
            title=f"{len(top)} productos nuevos para promocionar como afiliado",
            detail=f"Nuevos esta semana en el catálogo de afiliados, ordenados por puntaje: {names}.",
            impact_usd=round(max(impact, MIN_IMPACT_USD), 2),
            impact_basis=(
                f"Comisión por unidad x demanda mensual aproximada (unidades acumuladas / {analytics.UNITS_LIFETIME_MONTHS} meses, "
                f"mínimo {MIN_MONTHLY_UNITS:g}) x {ACHIEVABLE_SHARE:.0%} de la demanda que una cuenta puede capturar."
            ),
            effort="medium",
            target=TARGET,
            entity="weekly",
            meta={"product_ids": [p["product_id"] for p in top], "scores": [p["score"] for p in top]},
        )
    ]
