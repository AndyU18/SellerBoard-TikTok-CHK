"""Seguridad de credenciales y permisos: permisos de los tokens, inventario sin valores y rotación.

  GET    /api/security/overview               resumen y riesgo global                       settings.view
  GET    /api/security/scopes                 permisos por plataforma, clasificación         settings.view
  GET    /api/security/credentials            inventario (nombres, existencia, git, vencim.) settings.view
  GET    /api/security/rotation               lista de verificación de rotación             settings.view
  PUT    /api/security/rotation/{platform}    marca como rotada (idempotente)               settings.edit
  DELETE /api/security/rotation/{platform}    reabre (vuelve a pendiente)                   settings.edit
  POST   /api/security/scopes/capture/seller  renueva el token del vendedor y guarda permisos  settings.edit

Ninguna respuesta incluye un valor secreto. La escritura exige `settings.edit` (el permiso que ya usa el Dueño
para cambiar la configuración): marcar una rotación o forzar una renovación de token es una decisión de
administración, no de consulta.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, require
from app.credential_audit import CANNOT_REVOKE_FROM_APP, classify_scope
from app.credential_catalog import PLATFORMS, TIKTOK_SHOP
from app.credential_inventory import FILES_IN_APP_BACKUP, group_by_platform
from app.credential_ops import (
    MAX_NOTE_LENGTH,
    as_utc,
    mark_rotated,
    reopen_rotation,
    replace_scope_snapshot,
)
from app.credential_scope_capture import (
    ScopeCaptureError,
    SellerScopeCapture,
    capture_seller_scopes,
)
from app.credential_service import (
    InventorySource,
    default_source,
    load_inventory,
    load_overview,
    load_rotation_board,
    load_scope_report,
)
from app.db import get_session

router = APIRouter(prefix="/api/security")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("settings.view"))]
Editor = Annotated[CurrentUser, Depends(require("settings.edit", writes=True))]

Capturer = Callable[[], SellerScopeCapture]


def get_source() -> InventorySource:
    """Dónde buscar credenciales (sustituible en pruebas)."""
    return default_source()


def get_capturer() -> Capturer:
    """Función que renueva el token del vendedor y devuelve sus permisos (sustituible en pruebas)."""
    return capture_seller_scopes


SourceDep = Annotated[InventorySource, Depends(get_source)]
CapturerDep = Annotated[Capturer, Depends(get_capturer)]


class RotationBody(BaseModel):
    note: Annotated[str | None, Field(max_length=MAX_NOTE_LENGTH)] = None


def _known_platform(platform: str) -> str:
    if platform not in PLATFORMS:
        raise HTTPException(404, f"Plataforma desconocida: {platform}")
    return platform


@router.get("/overview", dependencies=VIEW)
async def overview(session: SessionDep, source: SourceDep) -> dict[str, Any]:
    """Permisos de escritura concedidos, credenciales pendientes de rotar, vencimientos y riesgo global."""
    return await load_overview(session, source)


@router.get("/scopes", dependencies=VIEW)
async def scopes(session: SessionDep) -> dict[str, Any]:
    """Permisos por plataforma: nivel, qué permiten, cuáles sobran y qué quitar en el portal."""
    return {"platforms": await load_scope_report(session), "cannot_revoke_from_app": CANNOT_REVOKE_FROM_APP}


@router.get("/credentials", dependencies=VIEW)
async def credentials(source: SourceDep) -> dict[str, Any]:
    """Inventario por plataforma: nombre, tipo, presencia, archivo, git, vencimiento. Nunca el valor."""
    items = await load_inventory(source)
    return {
        "platforms": group_by_platform(items),
        "backup_includes_credential_files": bool(FILES_IN_APP_BACKUP),
    }


@router.get("/rotation", dependencies=VIEW)
async def rotation(session: SessionDep, source: SourceDep) -> dict[str, Any]:
    """Lista de verificación: qué plataformas siguen pendientes y los pasos para rotar cada una."""
    return await load_rotation_board(session, source)


@router.put("/rotation/{platform}")
async def mark_platform_rotated(
    platform: str, body: RotationBody, user: Editor, session: SessionDep
) -> dict[str, Any]:
    """Marca la plataforma como rotada. Repetirlo no cambia nada (gana la primera marca)."""
    row = await mark_rotated(session, _known_platform(platform), user.email, body.note)
    await session.commit()
    return {
        "platform": row.platform,
        "rotated": True,
        "rotated_at": as_utc(row.rotated_at).isoformat(),
        "rotated_by_email": row.rotated_by_email,
        "note": row.note,
    }


@router.delete("/rotation/{platform}")
async def reopen_platform(platform: str, _: Editor, session: SessionDep) -> dict[str, Any]:
    """Reabre la rotación: la plataforma vuelve a pendiente."""
    reopened = await reopen_rotation(session, _known_platform(platform))
    await session.commit()
    return {"platform": platform, "rotated": False, "reopened": reopened}


@router.post("/scopes/capture/seller")
async def capture_seller(_: Editor, session: SessionDep, capture: CapturerDep) -> dict[str, Any]:
    """Renueva UNA vez el token del vendedor (escritura atómica) y guarda los permisos. Devuelve solo conteos."""
    try:
        captured = await asyncio.to_thread(capture)
    except ScopeCaptureError as exc:
        raise HTTPException(502, str(exc)) from exc
    await replace_scope_snapshot(session, TIKTOK_SHOP, captured.scopes)
    await session.commit()
    levels = [classify_scope(s, TIKTOK_SHOP) for s in captured.scopes]
    return {
        "platform": TIKTOK_SHOP,
        "granted": len(levels),
        "write": sum(c.is_write for c in levels),
        "delete": sum(c.level == "delete" for c in levels),
        "unused_write": sum(c.is_write and not c.used_by_app for c in levels),
        "undocumented": sum(not c.documented for c in levels),
    }
