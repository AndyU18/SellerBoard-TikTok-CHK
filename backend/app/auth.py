"""Autenticación y permisos.

El frontend inicia sesión con Supabase Auth y manda el token en `Authorization: Bearer`.
Aquí se valida el token (firma ES256/RS256 con las claves públicas del proyecto: no hace falta
ningún secreto) y se cargan los permisos del usuario desde app.user_role -> app.role_permission.
Los permisos viven en la base de datos, no en el código: el Dueño puede cambiarlos desde la app.

    @router.get(..., dependencies=[Depends(require("profit.view"))])
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Permission, Profile, Role, RolePermission, UserRole

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)

ALL = "*"  # comodín: solo lo tiene el usuario de desarrollo cuando la autenticación está apagada


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID | None
    email: str | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    roles: tuple[str, ...] = ()

    def can(self, permission: str) -> bool:
        return ALL in self.permissions or permission in self.permissions


DEV_USER = CurrentUser(None, "dev@local", frozenset({ALL}), ("dev",))


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    if not settings.supabase_url:
        raise RuntimeError("Falta SUPABASE_URL: no se pueden validar los tokens")
    url = settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def _signing_key(token: str) -> Any:
    """Clave pública con la que se firmó el token (se cachea una hora)."""
    return _jwks_client().get_signing_key_from_jwt(token).key


def decode_token(token: str) -> dict[str, Any]:
    """Valida firma, expiración, emisor y audiencia. Lanza jwt.PyJWTError si algo falla."""
    issuer = settings.supabase_url.rstrip("/") + "/auth/v1" if settings.supabase_url else None
    return jwt.decode(
        token,
        _signing_key(token),
        algorithms=["ES256", "RS256"],  # nunca HS256 ni "none": no hay secreto compartido aquí
        audience="authenticated",
        issuer=issuer,
        options={"require": ["exp", "sub"]},
    )


async def load_permissions(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[bool | None, frozenset[str], tuple[str, ...]]:
    """(¿perfil activo?, permisos, roles). El primer valor es None si el usuario no tiene perfil."""
    profile = await session.get(Profile, user_id)
    if profile is None:
        return None, frozenset(), ()
    codes = await session.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
        .distinct()
    )
    roles = await session.scalars(
        select(Role.code).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
    )
    return profile.is_active, frozenset(codes.all()), tuple(sorted(roles.all()))


async def current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CurrentUser:
    if not settings.auth_required:
        return DEV_USER
    if creds is None:
        raise _unauthorized("Falta el token de sesión")
    try:
        claims = await asyncio.to_thread(decode_token, creds.credentials)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError, RuntimeError) as exc:
        log.info("Token rechazado: %s", type(exc).__name__)
        raise _unauthorized("Sesión inválida o expirada") from exc

    active, permissions, roles = await load_permissions(session, user_id)
    if active is None:
        raise HTTPException(403, "Tu usuario aún no tiene perfil. Pide acceso al Dueño.")
    if not active:
        raise HTTPException(403, "Tu usuario está desactivado")
    return CurrentUser(user_id, claims.get("email"), permissions, roles)


UserDep = Annotated[CurrentUser, Depends(current_user)]


async def _set_audit_actor(session: AsyncSession, user: CurrentUser) -> None:
    """Para que los triggers de auditoría registren QUIÉN hizo el cambio (solo Postgres)."""
    if user.id is not None and session.get_bind().dialect.name == "postgresql":
        await session.execute(
            text("select set_config('app.user_id', :uid, true)"), {"uid": str(user.id)}
        )


def require(permission: str, *, writes: bool = False):
    """Dependencia que exige un permiso. `writes=True` marca al usuario en la auditoría."""

    async def checker(
        user: UserDep, session: Annotated[AsyncSession, Depends(get_session)]
    ) -> CurrentUser:
        if not user.can(permission):
            raise HTTPException(403, f"No tienes permiso para esto ({permission})")
        if writes:
            await _set_audit_actor(session, user)
        return user

    return checker
