"""Proveedor del Centro de acciones: rotar credenciales expuestas y quitar permisos de escritura sobrantes.

Importado solo por `app.action_providers`. Todas las acciones apuntan a la pantalla "security".

Sobre el impacto: esta área no mueve dinero medible (es riesgo, no ganancia ni pérdida), pero el Centro
oculta las acciones con impacto 0. Por eso cada acción lleva un importe SIMBÓLICO de `SYMBOLIC_IMPACT_USD`
y `impact_basis` lo dice explícitamente: no debe leerse como dinero ni sumarse a ahorros reales.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.action_center import ActionItem, action_provider
from app.credential_catalog import PLATFORM_LABELS, ROTATION_GUIDES
from app.credential_service import (
    InventorySource,
    default_source,
    load_scope_report,
    pending_platforms,
)

KIND = "security_rotate"
TARGET = "security"
SYMBOLIC_IMPACT_USD = 1.0
IMPACT_BASIS = (
    "Importe simbólico: es un riesgo de seguridad sin monto medible. Se muestra con $1 solo para que el Centro "
    "no lo oculte (descarta el impacto 0); no es dinero ni ahorro real."
)
MAX_SCOPES_LISTED = 6  # nombres de permisos en el texto de la acción; el resto va en meta


def _rotate_action(platform: str) -> ActionItem:
    guide = ROTATION_GUIDES[platform]
    label = PLATFORM_LABELS[platform]
    return ActionItem(
        key=f"{KIND}:rotate:{platform}",
        kind=KIND,
        title=f"Rota las credenciales de {label}",
        detail=(
            "Sus secretos se compartieron fuera de la aplicación y deben considerarse comprometidos. "
            f"Genera nuevos en {guide.portal_url} y márcalo como rotado en Seguridad."
        ),
        impact_usd=SYMBOLIC_IMPACT_USD,
        impact_basis=IMPACT_BASIS,
        effort="medium",
        target=TARGET,
        entity=platform,
        meta={"portal_url": guide.portal_url, "steps": len(guide.steps)},
    )


def _scope_action(platform: str, report: dict[str, Any]) -> ActionItem:
    rec = report["recommendation"]
    names: list[str] = rec["remove_now"]
    shown = ", ".join(names[:MAX_SCOPES_LISTED]) + ("..." if len(names) > MAX_SCOPES_LISTED else "")
    return ActionItem(
        key=f"{KIND}:scopes:{platform}",
        kind=KIND,
        title=f"Quita {len(names)} permisos de escritura sobrantes de {PLATFORM_LABELS[platform]}",
        detail=(
            f"Esta app solo lee, pero el token puede modificar datos ({shown}). "
            "Quítalos en el portal de la plataforma: la aplicación no puede hacerlo por API."
        ),
        impact_usd=SYMBOLIC_IMPACT_USD,
        impact_basis=IMPACT_BASIS,
        effort="low",
        target=TARGET,
        entity=platform,
        meta={"unused_write": len(names), "scopes": names},
    )


@action_provider(KIND, "Seguridad: rotar credenciales y quitar permisos de escritura")
async def security_actions(session: AsyncSession, source: InventorySource | None = None) -> list[ActionItem]:
    """Una acción por plataforma pendiente de rotar y una por plataforma con permisos de escritura sin usar."""
    source = source or default_source()
    items = [_rotate_action(p) for p in await pending_platforms(session, source)]
    items += [
        _scope_action(report["platform"], report)
        for report in await load_scope_report(session)
        if report["unused_write_count"] > 0 and report["recommendation"]["remove_now"]
    ]
    return items
