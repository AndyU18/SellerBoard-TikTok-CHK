"""Endpoints del apartado TikTok orgánico (la cuenta de contenido de CHK, no TikTok Shop).

Solo lectura, salvo la conexión OAuth (`/connect`, `/disconnect`), que exige `sync.run`. Ninguna respuesta
incluye tokens, secretos ni el archivo de tokens.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.connectors.tiktok_organic import (
    HTTP_TIMEOUT_SECONDS,
    REQUIRED_SCOPES,
    STATE_TTL_SECONDS,
    TikTokOrganicError,
    TikTokOrganicSettings,
    TokenRecord,
    TokenStore,
    build_authorize_url,
    connection_status,
    exchange_code,
    make_state,
    verify_state,
)
from app.db import get_session
from app.models import TikTokOrganicAccount
from app.tiktok_organic_analytics import (
    SortKey,
    hashtags,
    overview,
    performance,
    posting_times,
    videos_list,
    vs_shop,
)

router = APIRouter(prefix="/api/tiktok-organic")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
CONNECT = [Depends(require("sync.run", writes=True))]
Days = Annotated[int, Query(ge=7, le=365)]
DEFAULT_DAYS = 90
NOT_CONFIGURED = "Faltan TIKTOK_ORGANIC_CLIENT_KEY y TIKTOK_ORGANIC_CLIENT_SECRET en backend/.env"


# --- dependencias (se sustituyen en las pruebas) -----------------------------------------------------------


def organic_settings() -> TikTokOrganicSettings | None:
    """Las claves de la app, o None si no están en el entorno."""
    try:
        return TikTokOrganicSettings()
    except ValidationError:
        return None


def organic_http() -> Iterator[httpx.Client]:
    """Cliente HTTP para canjear el código; se cierra al terminar la petición."""
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        yield client


SettingsDep = Annotated[TikTokOrganicSettings | None, Depends(organic_settings)]
HttpDep = Annotated[httpx.Client, Depends(organic_http)]


def _require_settings(cfg: TikTokOrganicSettings | None) -> TikTokOrganicSettings:
    if cfg is None:
        raise HTTPException(409, NOT_CONFIGURED)
    return cfg


def _read_tokens(cfg: TikTokOrganicSettings | None) -> tuple[TokenRecord | None, str | None]:
    """Tokens guardados y, si el archivo está dañado, el motivo (sin romper el estado)."""
    if cfg is None:
        return None, None
    try:
        return TokenStore(cfg.tokens_file).load(), None
    except TikTokOrganicError as exc:
        return None, str(exc)


# --- conexión ---------------------------------------------------------------------------------------------


class ConnectBody(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=512)


@router.get("/status", dependencies=VIEW)
async def status(session: SessionDep, cfg: SettingsDep) -> dict[str, Any]:
    """¿Configurado? ¿Conectado? Usuario, vencimiento del token y pasos pendientes."""
    record, token_error = _read_tokens(cfg)
    out = connection_status(cfg, record, time.time())
    last = await session.scalar(select(TikTokOrganicAccount).order_by(TikTokOrganicAccount.snapshot_date.desc()).limit(1))
    out["username"] = last.username if last else None
    out["last_snapshot_date"] = last.snapshot_date if last else None
    out["token_file_error"] = token_error
    if token_error:
        out["pending_steps"].append("Volver a autorizar: el archivo de tokens está dañado")
    return out


@router.get("/authorize-url", dependencies=VIEW)
async def authorize_url(cfg: SettingsDep) -> dict[str, Any]:
    """URL para que el dueño autorice la app, con un `state` firmado que vence en 15 minutos."""
    settings = _require_settings(cfg)
    try:
        state = make_state(settings.client_secret, time.time())
        url = build_authorize_url(state, settings)
    except TikTokOrganicError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "authorize_url": url,
        "redirect_uri": settings.redirect_uri,
        "state": state,
        "scopes": list(REQUIRED_SCOPES),
        "expires_in_seconds": STATE_TTL_SECONDS,
    }


@router.post("/connect", dependencies=CONNECT)
async def connect(body: ConnectBody, cfg: SettingsDep, http: HttpDep) -> dict[str, Any]:
    """Canjea el `code` del callback por tokens y los guarda. Nunca devuelve tokens."""
    settings = _require_settings(cfg)
    if not verify_state(settings.client_secret, body.state, time.time()):
        raise HTTPException(400, "El parámetro state no es válido o venció: genera una URL de autorización nueva")
    try:
        record = await asyncio.to_thread(exchange_code, body.code, settings, http)
    except TikTokOrganicError as exc:
        raise HTTPException(409 if exc.code == "no_redirect_uri" else 400, str(exc)) from exc
    return connection_status(settings, record, time.time())


@router.post("/disconnect", dependencies=CONNECT)
async def disconnect(cfg: SettingsDep) -> dict[str, Any]:
    """Borra el archivo de tokens (idempotente). La autorización también se puede quitar desde TikTok."""
    settings = _require_settings(cfg)
    removed = TokenStore(settings.tokens_file).delete()
    return {"disconnected": True, "had_tokens": removed}


# --- análisis ---------------------------------------------------------------------------------------------


@router.get("/overview", dependencies=VIEW)
async def get_overview(session: SessionDep, days: Days = DEFAULT_DAYS) -> dict[str, Any]:
    """Seguidores, crecimiento 7/30 días, vistas totales, engagement y ritmo de publicación."""
    return await overview(session, days=days)


@router.get("/videos", dependencies=VIEW)
async def get_videos(
    session: SessionDep,
    sort: SortKey = "recent",
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Videos con portada, métricas, engagement, vistas por día y crecimiento reciente."""
    return await videos_list(session, sort=sort, q=q, limit=limit, offset=offset)


@router.get("/performance", dependencies=VIEW)
async def get_performance(session: SessionDep, days: Days = DEFAULT_DAYS) -> dict[str, Any]:
    """Series diarias, top videos, videos que explotan frente a la mediana y distribución de vistas."""
    return await performance(session, days=days)


@router.get("/posting-times", dependencies=VIEW)
async def get_posting_times(session: SessionDep, days: Days = 180) -> dict[str, Any]:
    """Mapa de calor día x hora de publicación contra vistas medias y mejores franjas."""
    return await posting_times(session, days=days)


@router.get("/hashtags", dependencies=VIEW)
async def get_hashtags(session: SessionDep, days: Days = 180) -> dict[str, Any]:
    """Hashtags y menciones con su rendimiento, y vistas por rango de duración."""
    return await hashtags(session, days=days)


@router.get("/vs-shop", dependencies=VIEW)
async def get_vs_shop(session: SessionDep, days: Days = DEFAULT_DAYS) -> dict[str, Any]:
    """Ventas de TikTok Shop atribuidas a videos orgánicos (si coinciden los ids)."""
    return await vs_shop(session, days=days)
