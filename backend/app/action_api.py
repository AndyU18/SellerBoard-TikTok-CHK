"""Endpoints del Centro de acciones: listar lo pendiente y cambiar el estado de una acción."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.action_center import ActionError, list_actions, set_status
from app.action_providers import load_providers
from app.auth import CurrentUser, require
from app.db import get_session

load_providers()  # registra los proveedores al importar el router

router = APIRouter(prefix="/api/actions")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Editor = Annotated[CurrentUser, Depends(require("profit.view", writes=True))]


class StatusIn(BaseModel):
    status: Literal["open", "done", "dismissed", "snoozed"]
    note: str | None = Field(default=None, max_length=500)
    snooze_days: int | None = Field(default=None, ge=1, le=365)


@router.get("", dependencies=VIEW)
async def actions(
    session: SessionDep,
    status: Literal["open", "done", "dismissed", "snoozed", "all"] = "open",
    kind: Annotated[str | None, Query(max_length=60)] = None,
) -> dict:
    """Acciones calculadas ahora, ordenadas por dinero en juego, con el estado que les dio el equipo."""
    return await list_actions(session, status=status, kind=kind)


@router.put("/{key:path}")
async def update_action(key: str, body: StatusIn, session: SessionDep, user: Editor) -> dict:
    """Marca una acción como hecha, descartada o pospuesta (o la reabre con `open`)."""
    try:
        return await set_status(
            session,
            key,
            body.status,
            note=body.note,
            snooze_days=body.snooze_days,
            user_id=user.id,
            user_email=user.email,
        )
    except ActionError as exc:
        raise HTTPException(422, str(exc)) from exc
