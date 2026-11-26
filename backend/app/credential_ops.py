"""Persistencia de la seguridad de credenciales: rotaciones marcadas y foto de permisos. Idempotente.

Solo guarda NOMBRES de permisos y quién/cuándo marcó una rotación; jamás un valor secreto.
Los helpers hacen `flush`; quien llama (el router) decide el `commit`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credential_audit import classify_scope
from app.models import CredentialRotation, CredentialScopeSnapshot

MAX_NOTE_LENGTH = 500


@dataclass(frozen=True, slots=True)
class ScopeSnapshot:
    """Permisos capturados de una plataforma y cuándo."""

    captured_at: datetime
    scopes: tuple[str, ...]


async def load_rotations(session: AsyncSession) -> dict[str, CredentialRotation]:
    rows = await session.scalars(select(CredentialRotation))
    return {row.platform: row for row in rows}


async def mark_rotated(
    session: AsyncSession, platform: str, email: str | None, note: str | None, now: datetime | None = None
) -> CredentialRotation:
    """Marca la plataforma como rotada. Repetirlo no duplica ni mueve la fecha: gana la primera marca.

    Para registrar una rotación posterior, primero se reabre (`reopen_rotation`) y luego se marca de nuevo.
    """
    existing = await session.get(CredentialRotation, platform)
    if existing is not None:
        return existing
    row = CredentialRotation(
        platform=platform,
        rotated_at=now or datetime.now(UTC),
        rotated_by_email=email,
        note=(note or "").strip()[:MAX_NOTE_LENGTH] or None,
    )
    session.add(row)
    await session.flush()
    return row


async def reopen_rotation(session: AsyncSession, platform: str) -> bool:
    """Quita la marca de rotada (vuelve a pendiente). Devuelve False si no había marca."""
    result = await session.execute(delete(CredentialRotation).where(CredentialRotation.platform == platform))
    await session.flush()
    return bool(result.rowcount)  # type: ignore[attr-defined]  # CursorResult en DELETE


async def replace_scope_snapshot(
    session: AsyncSession, platform: str, scopes: tuple[str, ...], now: datetime | None = None
) -> int:
    """Reemplaza la foto de permisos de la plataforma por `scopes` (cada captura sustituye a la anterior)."""
    captured_at = now or datetime.now(UTC)
    await session.execute(delete(CredentialScopeSnapshot).where(CredentialScopeSnapshot.platform == platform))
    session.add_all(
        CredentialScopeSnapshot(
            platform=platform, scope=scope, level=classify_scope(scope, platform).level, captured_at=captured_at
        )
        for scope in dict.fromkeys(scopes)
    )
    await session.flush()
    return len(set(scopes))


async def load_scope_snapshots(session: AsyncSession) -> dict[str, ScopeSnapshot]:
    """Última foto de permisos por plataforma."""
    rows = (
        await session.scalars(
            select(CredentialScopeSnapshot).order_by(CredentialScopeSnapshot.platform, CredentialScopeSnapshot.scope)
        )
    ).all()
    grouped: dict[str, list[CredentialScopeSnapshot]] = {}
    for row in rows:
        grouped.setdefault(row.platform, []).append(row)
    return {
        platform: ScopeSnapshot(max(as_utc(r.captured_at) for r in group), tuple(r.scope for r in group))
        for platform, group in grouped.items()
    }


def as_utc(value: datetime) -> datetime:
    """SQLite devuelve fechas sin zona; todo lo que se guarda es UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
