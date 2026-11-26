"""Proveedor del Centro de acciones para el mercado de creadores del vendedor.

Importado solo por `app.action_providers`. Una sola acción agrupada: "invita a los N mejores creadores nuevos
del mercado". Solo lectura; apunta a la pantalla "tiktok".

Impacto (el Centro oculta las acciones con impacto 0): por cada candidato, su GMV mensual reportado x
ACHIEVABLE_SHARE x la comisión media que ofrecemos en colaboraciones abiertas (o DEFAULT_COMMISSION_RATE si aún no
hay datos). El GMV que reporta TikTok es del creador en TODAS las marcas y de una ventana larga: ACHIEVABLE_SHARE
es la fracción de ese GMV que razonablemente captura una sola marca, y GMV_WINDOW_MONTHS lo pasa a mes. Es una
estimación del ingreso en comisión que ese creador movería, no una promesa de ventas.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import tiktok_creator_market_analytics as analytics
from app.action_center import ActionItem, action_provider
from app.models import TikTokOpenCollaboration

TARGET = "tiktok"
KIND = "tiktok_creator_candidates"
TOP_CANDIDATES = 5
GMV_WINDOW_MONTHS = 12.0  # TikTok no documenta la ventana del GMV: se asume un acumulado anual
ACHIEVABLE_SHARE = 0.02
DEFAULT_COMMISSION_RATE = 0.10
MIN_IMPACT_USD = 1.0


async def offered_commission_rate(session: AsyncSession) -> float:
    """Comisión media de nuestras colaboraciones abiertas activas (fracción); la de respaldo si no hay datos."""
    average = await session.scalar(
        select(func.avg(TikTokOpenCollaboration.commission_rate)).where(
            TikTokOpenCollaboration.active.is_(True), TikTokOpenCollaboration.commission_rate.is_not(None)
        )
    )
    return float(average) if average else DEFAULT_COMMISSION_RATE


def estimated_monthly_commission(gmv: float, commission_rate: float) -> float:
    """Comisión mensual estimada de un creador (ver el docstring del módulo)."""
    return gmv / GMV_WINDOW_MONTHS * ACHIEVABLE_SHARE * commission_rate


@action_provider(KIND, "Creadores nuevos para invitar (mercado de creadores)")
async def creator_candidate_actions(session: AsyncSession) -> list[ActionItem]:
    """Los mejores creadores nuevos del mercado por puntaje, agrupados en una sola acción."""
    top = (await analytics.candidates(session, TOP_CANDIDATES))["rows"]
    if not top:
        return []
    rate = await offered_commission_rate(session)
    impact = sum(estimated_monthly_commission(c["gmv"] or 0.0, rate) for c in top)
    names = "; ".join(f"{c['username'] or c['creator_open_id']} (puntaje {c['score']:g})" for c in top)
    return [
        ActionItem(
            key=f"{KIND}:weekly",
            kind=KIND,
            title=f"{len(top)} creadores nuevos para invitar",
            detail=f"Nuevos en el mercado, sin muestras previas ni ventas con nosotros, ordenados por puntaje: {names}.",
            impact_usd=round(max(impact, MIN_IMPACT_USD), 2),
            impact_basis=(
                f"GMV reportado de cada creador / {GMV_WINDOW_MONTHS:g} meses x {ACHIEVABLE_SHARE:.0%} que una sola marca puede "
                f"capturar x comisión media ofrecida ({rate:.1%})."
            ),
            effort="medium",
            target=TARGET,
            entity="weekly",
            meta={"creator_open_ids": [c["creator_open_id"] for c in top], "scores": [c["score"] for c in top]},
        )
    ]
