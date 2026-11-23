"""Endpoints del asistente con IA y de la explicación determinista de la utilidad.

`/api/profit/explain` no usa IA y funciona siempre. Los demás exigen clave configurada: sin ella
responden 503 con `{"problems": [...]}` (mismo formato que el resto de la API) y no envían nada.
Las consultas a la IA tienen un límite en memoria por proceso (20 por hora) para acotar el costo.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import assistant
from app.auth import require
from app.config import settings
from app.db import get_session
from app.explain import explain_profit_change
from app.models import Channel

router = APIRouter(prefix="/api")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]

RATE_LIMIT = 20
RATE_WINDOW_SECONDS = 3600


class RateLimiter:
    """Ventana deslizante en memoria: `limit` usos por `window` segundos (por proceso)."""

    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW_SECONDS) -> None:
        self.limit, self.window = limit, window
        self._hits: deque[float] = deque()

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            return False
        self._hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=800)
    days: int = Field(default=14, ge=7, le=90)


def _guard() -> None:
    """503 si el asistente está apagado; 429 si se agotó el cupo por hora."""
    if not assistant.is_configured():
        raise HTTPException(
            503,
            {"problems": ["El asistente está apagado: configura ANTHROPIC_API_KEY o CHK_AI_API_KEY en backend/.env."]},
        )
    if not limiter.allow():
        raise HTTPException(429, {"problems": [f"Límite de {RATE_LIMIT} consultas por hora alcanzado. Intenta más tarde."]})


@router.get("/assistant/status", dependencies=VIEW)
async def assistant_status() -> dict[str, object]:
    """Solo dice si hay clave y qué modelo se usaría; nunca devuelve la clave."""
    return {"configured": assistant.is_configured(), "model": settings.ai_model}


@router.get("/profit/explain", dependencies=VIEW)
async def profit_explain(
    session: SessionDep,
    days: Annotated[int, Query(ge=7, le=90)] = 7,
    channel: Channel | None = None,
) -> dict:
    return await explain_profit_change(session, days=days, channel=channel)


@router.post("/assistant/ask", dependencies=VIEW)
async def assistant_ask(body: AskBody, session: SessionDep) -> dict:
    _guard()
    try:
        return await assistant.ask(session, body.question, body.days)
    except assistant.AssistantError as exc:
        raise HTTPException(503 if exc.kind == "not_configured" else 502, {"problems": [str(exc)]}) from exc


@router.get("/assistant/weekly-report", dependencies=VIEW)
async def assistant_weekly_report(session: SessionDep) -> dict:
    # Sin clave se entrega el informe determinista: no consume cupo ni envía nada.
    if assistant.is_configured():
        _guard()
    try:
        return await assistant.weekly_report(session)
    except assistant.AssistantError as exc:
        raise HTTPException(503 if exc.kind == "not_configured" else 502, {"problems": [str(exc)]}) from exc
