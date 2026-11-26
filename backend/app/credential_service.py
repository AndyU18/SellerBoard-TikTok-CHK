"""Orquesta lo que comparten la API y el proveedor de acciones: permisos, inventario y rotaciones pendientes.

Ninguna función devuelve un valor secreto: el inventario solo trae nombres, existencia, git y vencimientos.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.credential_audit import build_overview, pending_rotation, scope_report
from app.credential_catalog import (
    CLOSING_STEPS,
    EXPOSED_IN_CHAT,
    PLATFORM_LABELS,
    PLATFORMS,
    ROTATION_GUIDES,
)
from app.credential_inventory import BACKEND_DIR, build_inventory, present_platforms
from app.credential_ops import as_utc, load_rotations, load_scope_snapshots


@dataclass(frozen=True, slots=True)
class InventorySource:
    """Dónde buscar credenciales. Las pruebas lo apuntan a una carpeta temporal con valores sintéticos."""

    base_dir: Path = BACKEND_DIR
    environ: dict[str, str] = field(default_factory=lambda: dict(os.environ))


def default_source() -> InventorySource:
    return InventorySource()


async def load_scope_report(session: AsyncSession) -> list[dict[str, Any]]:
    snapshots = await load_scope_snapshots(session)
    return scope_report({p: (s.scopes, s.captured_at) for p, s in snapshots.items()})


async def pending_platforms(session: AsyncSession, source: InventorySource) -> list[str]:
    rotated = set(await load_rotations(session))
    present = await asyncio.to_thread(present_platforms, source.base_dir, source.environ)
    return pending_rotation(rotated, present)


async def load_inventory(source: InventorySource) -> list[dict[str, Any]]:
    """Inventario con git (lanza procesos: va en un hilo para no frenar el servidor)."""
    return await asyncio.to_thread(lambda: build_inventory(base_dir=source.base_dir, environ=source.environ))


async def load_overview(session: AsyncSession, source: InventorySource) -> dict[str, Any]:
    scopes = await load_scope_report(session)
    inventory = await load_inventory(source)
    return build_overview(scopes, inventory, await pending_platforms(session, source))


async def load_rotation_board(session: AsyncSession, source: InventorySource) -> dict[str, Any]:
    """Lista de verificación: una fila por plataforma relevante (expuesta, con credenciales o ya rotada)."""
    rotations = await load_rotations(session)
    present = await asyncio.to_thread(present_platforms, source.base_dir, source.environ)
    rows = []
    for platform in PLATFORMS:
        if platform not in EXPOSED_IN_CHAT and platform not in present and platform not in rotations:
            continue
        row, guide = rotations.get(platform), ROTATION_GUIDES[platform]
        rows.append(
            {
                "platform": platform,
                "label": PLATFORM_LABELS[platform],
                "exposed_in_chat": platform in EXPOSED_IN_CHAT,
                "credentials_present": platform in present,
                "rotated": row is not None,
                "rotated_at": _iso(row.rotated_at) if row else None,
                "rotated_by_email": row.rotated_by_email if row else None,
                "note": row.note if row else None,
                "portal_url": guide.portal_url,
                "steps": [*guide.steps, *CLOSING_STEPS],
            }
        )
    return {"platforms": rows, "pending_count": sum(not r["rotated"] for r in rows)}


def _iso(value: datetime) -> str:
    return as_utc(value).isoformat()
