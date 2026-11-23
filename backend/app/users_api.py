"""Quién soy, y gestión de usuarios y roles.

- GET  /api/me                    cualquier usuario autenticado: su perfil y permisos
- GET  /api/roles, /api/users     users.view
- PUT  /api/users/{id}/roles      users.manage (dar o quitar 'owner' exige roles.manage)
- PATCH /api/users/{id}           users.manage (activar / desactivar)

Siempre debe quedar al menos un Dueño activo.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser, UserDep, require
from app.db import get_session
from app.models import Profile, Role, UserRole

router = APIRouter(prefix="/api")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
Manager = Annotated[CurrentUser, Depends(require("users.manage", writes=True))]
OWNER = "owner"


class RolesUpdate(BaseModel):
    roles: list[str]


class ActiveUpdate(BaseModel):
    is_active: bool


@router.get("/me")
async def me(user: UserDep) -> dict:
    return {
        "id": str(user.id) if user.id else None,
        "email": user.email,
        "roles": list(user.roles),
        "permissions": sorted(user.permissions),
    }


@router.get("/roles", dependencies=[Depends(require("users.view"))])
async def list_roles(session: SessionDep) -> list[dict]:
    rows = (await session.scalars(select(Role).order_by(Role.id))).all()
    return [{"code": r.code, "name": r.name} for r in rows]


@router.get("/users", dependencies=[Depends(require("users.view"))])
async def list_users(session: SessionDep) -> list[dict]:
    profiles = (await session.scalars(select(Profile).order_by(Profile.email))).all()
    roles: dict[uuid.UUID, list[str]] = {}
    for user_id, code in (
        await session.execute(select(UserRole.user_id, Role.code).join(Role, Role.id == UserRole.role_id))
    ).all():
        roles.setdefault(user_id, []).append(code)
    return [
        {
            "id": str(p.user_id),
            "email": p.email,
            "full_name": p.full_name,
            "is_active": p.is_active,
            "roles": sorted(roles.get(p.user_id, [])),
        }
        for p in profiles
    ]


async def _other_active_owners(session: AsyncSession, excluding: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .join(Profile, Profile.user_id == UserRole.user_id)
            .where(Role.code == OWNER, Profile.is_active, UserRole.user_id != excluding)
        )
    ) or 0


async def _profile(session: AsyncSession, user_id: uuid.UUID) -> Profile:
    profile = await session.get(Profile, user_id)
    if profile is None:
        raise HTTPException(404, "Usuario no encontrado")
    return profile


@router.put("/users/{user_id}/roles")
async def set_user_roles(
    user_id: uuid.UUID, body: RolesUpdate, session: SessionDep, actor: Manager
) -> dict:
    await _profile(session, user_id)
    wanted = set(body.roles)
    found = {r.code: r.id for r in (await session.scalars(select(Role).where(Role.code.in_(wanted)))).all()}
    unknown = sorted(wanted - found.keys())
    if unknown:
        raise HTTPException(400, f"Roles desconocidos: {unknown}")

    current = set(
        (
            await session.scalars(
                select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
            )
        ).all()
    )
    if (OWNER in wanted) != (OWNER in current):
        if not actor.can("roles.manage"):
            raise HTTPException(403, "Solo el Dueño puede dar o quitar el rol de Dueño")
        if OWNER in current and actor.id == user_id:
            raise HTTPException(400, "No puedes quitarte tu propio acceso de Dueño")
        if OWNER in current and not await _other_active_owners(session, user_id):
            raise HTTPException(400, "Debe quedar al menos un Dueño activo")

    await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
    session.add_all(
        UserRole(user_id=user_id, role_id=found[code], assigned_by=actor.id) for code in sorted(wanted)
    )
    await session.commit()
    return {"id": str(user_id), "roles": sorted(wanted)}


@router.patch("/users/{user_id}")
async def set_user_active(
    user_id: uuid.UUID, body: ActiveUpdate, session: SessionDep, actor: Manager
) -> dict:
    profile = await _profile(session, user_id)
    if not body.is_active:
        if actor.id == user_id:
            raise HTTPException(400, "No puedes desactivarte a ti mismo")
        is_owner = await session.scalar(
            select(func.count())
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.code == OWNER, UserRole.user_id == user_id)
        )
        if is_owner and not await _other_active_owners(session, user_id):
            raise HTTPException(400, "Debe quedar al menos un Dueño activo")
    profile.is_active = body.is_active
    await session.commit()
    return {"id": str(user_id), "is_active": profile.is_active}
