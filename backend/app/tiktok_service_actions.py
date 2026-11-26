"""Proveedor del Centro de acciones para el servicio al cliente de TikTok Shop.

Importado solo por `app.action_providers`. Una sola acción agrupada por antigüedad: "responde las N
conversaciones de compradores que siguen sin respuesta". Solo lectura; apunta a la pantalla "tiktok". El detalle
lleva únicamente conteos: nunca ids de comprador ni texto (no existen en la base).

Impacto (ESTIMACIÓN; el Centro oculta las acciones con impacto 0): por cada conversación sin responder,
ticket promedio de TikTok x tasa de recuperación de su categoría. La tasa es la fracción de esas conversaciones
que, atendidas a tiempo, terminan en una venta (o evitan una pérdida): es un SUPUESTO, no una medición.
Una pregunta de producto sin responder es una venta en duda (la tasa más alta); las demás categorías protegen
ventas ya hechas (envío, estado del pedido, devolución, queja) y valen menos por conversación.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app import tiktok_service_analytics as analytics
from app.action_center import ActionItem, action_provider
from app.tiktok_extra_analytics import average_order_value

TARGET: Final = "tiktok"
KIND: Final = "tiktok_cs_unanswered"
ENTITY: Final = "backlog"
MIN_IMPACT_USD: Final = 1.0

# Supuestos documentados: fracción del ticket promedio que se recupera por conversación atendida a tiempo.
PRODUCT_QUESTION_CONVERSION: Final = 0.15
CATEGORY_RECOVERY_RATE: Final[dict[str, float]] = {
    "product_question": PRODUCT_QUESTION_CONVERSION,
    "order_status": 0.05,
    "shipping_tracking": 0.05,
    "return_refund": 0.03,
    "complaint": 0.03,
    "other": 0.02,
}
STALE_BUCKETS: Final = frozenset({"1-3 d", ">3 d"})  # lo que ya pasó de un día: sube el esfuerzo y la urgencia


def estimated_impact(waiting_by_category: dict[str, int], average_ticket: float) -> float:
    """Impacto estimado en USD de contestar lo pendiente. Pura: depende solo de los conteos y el ticket."""
    return sum(n * CATEGORY_RECOVERY_RATE.get(category, CATEGORY_RECOVERY_RATE["other"]) for category, n in waiting_by_category.items()) * average_ticket


def _age_summary(buckets: list[dict[str, object]]) -> str:
    return ", ".join(f"{b['count']} en {b['bucket']}" for b in buckets if b["count"])


@action_provider(KIND, "Conversaciones de TikTok sin responder")
async def unanswered_conversation_actions(session: AsyncSession) -> list[ActionItem]:
    """Una acción con todo lo que espera respuesta del vendedor, desglosado por antigüedad."""
    now = datetime.now(UTC)
    backlog = await analytics.backlog(session, now)
    if not backlog["total"]:
        return []
    waiting = (await analytics.conversations(session, status=analytics.WAITING_SELLER, limit=analytics.MAX_LIST_LIMIT, now=now))["items"]
    by_category: dict[str, int] = {}
    for item in waiting:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    ticket = await average_order_value(session, now.date())
    impact = max(estimated_impact(by_category, ticket), MIN_IMPACT_USD)
    stale = sum(b["count"] for b in backlog["buckets"] if b["bucket"] in STALE_BUCKETS)
    return [
        ActionItem(
            key=f"{KIND}:{ENTITY}",
            kind=KIND,
            title=f"{backlog['total']} conversaciones de TikTok sin responder",
            detail=f"Compradores esperando respuesta: {_age_summary(backlog['buckets'])}. "
            f"La más antigua lleva {backlog['oldest_wait_hours']:g} h.",
            impact_usd=round(impact, 2),
            impact_basis=(
                f"Ticket promedio de TikTok (${ticket:,.2f}) x tasa de recuperación supuesta por categoría "
                f"(preguntas de producto {PRODUCT_QUESTION_CONVERSION:.0%}; envío y estado del pedido 5%; "
                "devolución y queja 3%; otras 2%) x conversaciones sin responder. Es un supuesto, no una medición."
            ),
            effort="medium" if stale else "low",
            target=TARGET,
            entity=ENTITY,
            meta={"waiting": backlog["total"], "stale": stale, "by_category": by_category, "buckets": backlog["buckets"]},
        )
    ]
