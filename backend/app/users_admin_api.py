"""Vista de administración de usuarios (complementa `users_api.py`).

- GET /api/users/overview   users.view: usuarios con su estado, el catálogo de roles con sus permisos,
                            quién consulta y cuántos Dueños activos hay.

Asignar o quitar roles y activar/desactivar ya existen en `users_api.py` (PUT /users/{id}/roles y
PATCH /users/{id}); aquí solo se junta en una respuesta lo que la pantalla necesita.

Estado de un usuario:
- `disabled`: desactivado, sin acceso aunque tenga rol.
- `pending`: activo pero sin ningún rol (se registró y espera que el Dueño le dé acceso).
- `active`: activo y con al menos un rol.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, require
from app.db import get_session
from app.models import Permission, Profile, Role, RolePermission, UserRole

router = APIRouter(prefix="/api")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
Viewer = Annotated[CurrentUser, Depends(require("users.view"))]
OWNER = "owner"
Status = Literal["active", "pending", "disabled"]


def user_status(is_active: bool, roles: list[str]) -> Status:
    if not is_active:
        return "disabled"
    return "active" if roles else "pending"


@router.get("/users/overview")
async def users_overview(session: SessionDep, actor: Viewer) -> dict:
    """Todo lo que necesita la pantalla de usuarios en una sola llamada."""
    profiles = (await session.scalars(select(Profile).order_by(Profile.email))).all()

    roles_by_user: dict[uuid.UUID, list[str]] = {}
    for user_id, code in (
        await session.execute(select(UserRole.user_id, Role.code).join(Role, Role.id == UserRole.role_id))
    ).all():
        roles_by_user.setdefault(user_id, []).append(code)

    permissions_by_role: dict[str, list[str]] = {}
    for code, permission in (
        await session.execute(
            select(Role.code, Permission.code)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
        )
    ).all():
        permissions_by_role.setdefault(code, []).append(permission)

    users = []
    for p in profiles:
        roles = sorted(roles_by_user.get(p.user_id, []))
        users.append(
            {
                "id": str(p.user_id),
                "email": p.email,
                "full_name": p.full_name,
                "is_active": p.is_active,
                "roles": roles,
                "status": user_status(p.is_active, roles),
            }
        )

    role_rows = (await session.scalars(select(Role).order_by(Role.id))).all()
    return {
        "me": {
            "id": str(actor.id) if actor.id else None,
            "can_manage": actor.can("users.manage"),
            "can_manage_owner": actor.can("roles.manage"),
        },
        "active_owners": sum(1 for u in users if u["is_active"] and OWNER in u["roles"]),
        "roles": [
            {"code": r.code, "name": r.name, "permissions": sorted(permissions_by_role.get(r.code, []))}
            for r in role_rows
        ],
        "users": users,
    }
