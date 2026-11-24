"""Proveedores del Centro de acciones para TikTok Shop (datos extra).

Importado solo por `app.action_providers`. Miran lo que dejó la sincronización `tiktok_shop_extra` y la capa
oro; todas apuntan a la pantalla "tiktok". Solo lectura.

Criterio de impacto (el Centro oculta las acciones con impacto 0, así que cada proveedor lo define):
  * listado rechazado/desactivado: ventas liquidadas de los últimos 90 días proyectadas a 30 días
    (lo que se vendía y hoy no se puede vender). Un producto sin ventas no genera acción.
  * paquete atascado: valor de sus órdenes (real si ya hay liquidación; si no, el ticket promedio de TikTok).
  * pago con problema: monto del pago fallido, o el valor absoluto de la diferencia de cuadre.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app import tiktok_extra_analytics as analytics
from app.action_center import ActionItem, action_provider

TARGET = "tiktok"
SALES_DAYS = 90
PROJECTION_DAYS = 30
MAX_ACTIONS = 15
STUCK_DAYS = analytics.DEFAULT_STUCK_DAYS
MIN_IMPACT_USD = 1.0


def _money(value: float) -> str:
    return f"${value:,.2f}"


@action_provider("tiktok_listing_rejected", "Listados de TikTok rechazados o desactivados")
async def listing_rejected_actions(session: AsyncSession) -> list[ActionItem]:
    """Productos con auditoría fallida o inactivos que tenían ventas. Impacto: ventas históricas a 30 días."""
    today = datetime.now(UTC).date()
    items: list[ActionItem] = []
    for p in await analytics.problem_products(session, SALES_DAYS, today):
        if p["sales"] <= 0:
            continue
        projected = p["sales"] / SALES_DAYS * PROJECTION_DAYS
        why = "auditoría rechazada" if p["audit_status"] == analytics.AUDIT_FAILED else f"estado {p['status']}"
        items.append(
            ActionItem(
                key=f"tiktok_listing_rejected:{p['product_id']}",
                kind="tiktok_listing_rejected",
                title=f"{p['title'] or p['product_id']}: {why}",
                detail=(
                    f"Vendió {_money(p['sales'])} en {SALES_DAYS} días y hoy no se puede comprar. "
                    "Corrige el motivo en Seller Center y vuelve a enviarlo a revisión."
                ),
                impact_usd=round(max(projected, MIN_IMPACT_USD), 2),
                impact_basis=f"Ventas liquidadas de {SALES_DAYS} días proyectadas a {PROJECTION_DAYS} días.",
                effort="medium",
                target=TARGET,
                entity=p["product_id"],
                meta={"status": p["status"], "audit_status": p["audit_status"], "sales_90d": round(p["sales"], 2)},
            )
        )
    items.sort(key=lambda i: -i.impact_usd)
    return items[:MAX_ACTIONS]


@action_provider("tiktok_packages_stuck", "Paquetes de TikTok atascados")
async def packages_stuck_actions(session: AsyncSession) -> list[ActionItem]:
    """Paquetes sin movimiento más de 5 días. Impacto: valor de sus órdenes."""
    items: list[ActionItem] = []
    for p in await analytics.stuck_packages(session, STUCK_DAYS, datetime.now(UTC)):
        value = p["value_usd"]
        items.append(
            ActionItem(
                key=f"tiktok_packages_stuck:{p['package_id']}",
                kind="tiktok_packages_stuck",
                title=f"Paquete {p['tracking_number'] or p['package_id']} sin movimiento {p['days_without_movement']:g} días",
                detail=(
                    f"Estado {p['status']}; último evento: {p['last_event'] or 'sin datos de seguimiento'}. "
                    "Un paquete detenido termina en cancelación o reembolso."
                ),
                impact_usd=round(max(value, MIN_IMPACT_USD), 2),
                impact_basis=(
                    "Valor de las órdenes del paquete"
                    + (" (estimado con el ticket promedio: aún sin liquidar)." if p["value_estimated"] else ".")
                ),
                effort="low",
                target=TARGET,
                entity=p["package_id"],
                meta={"days": p["days_without_movement"], "orders": p["order_count"], "estimated": p["value_estimated"]},
            )
        )
    items.sort(key=lambda i: -i.impact_usd)
    return items[:MAX_ACTIONS]


@action_provider("tiktok_payment_issue", "Pagos de TikTok con problemas")
async def payment_issue_actions(session: AsyncSession) -> list[ActionItem]:
    """Pagos fallidos o cuyo monto no cuadra con sus liquidaciones. Impacto: el monto o la diferencia."""
    items: list[ActionItem] = []
    for p in await analytics.payment_issues(session, datetime.now(UTC)):
        failed = p["kind"] == "failed"
        impact = p["amount"] if failed else abs(p["difference"])
        detail = (
            f"El pago está en estado {p['status']}. Revisa la cuenta bancaria (terminación {p['bank_last4'] or '—'})."
            if failed
            else (
                f"TikTok pagó {_money(p['settlement_amount'])} y las liquidaciones suman {_money(p['settled_amount'])} "
                f"(diferencia {_money(p['difference'])})."
            )
        )
        items.append(
            ActionItem(
                key=f"tiktok_payment_issue:{p['payment_id']}",
                kind="tiktok_payment_issue",
                title=f"Pago de TikTok {'fallido' if failed else 'con diferencia'}: {_money(impact)}",
                detail=detail,
                impact_usd=round(max(impact, MIN_IMPACT_USD), 2),
                impact_basis="Monto del pago fallido." if failed else "Diferencia absoluta entre lo pagado y lo liquidado.",
                effort="low" if failed else "medium",
                target=TARGET,
                entity=p["payment_id"],
                meta={"issue": p["kind"], "status": p["status"], "difference": p["difference"]},
            )
        )
    items.sort(key=lambda i: -i.impact_usd)
    return items[:MAX_ACTIONS]
