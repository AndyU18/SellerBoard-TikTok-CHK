"""Conector de TikTok orgánico: la cuenta de contenido propia de CHK (NO TikTok Shop).

Usa Login Kit + Display API v2 (open.tiktokapis.com). Es de SOLO LECTURA: ningún scope pedido permite
publicar ni editar (no se usa `video.publish` / `video.upload` de Content Posting API).

Qué entrega la API (con el usuario autorizado): perfil, contadores de la cuenta y, por video, vistas,
likes, comentarios y compartidos ACTUALES. Qué NO entrega: demografía de audiencia, impresiones, alcance,
tiempo de reproducción ni retención, ni historial de métricas. Por eso la app toma una foto diaria y
construye su propio historial (ver `app.tiktok_organic_ops`).

Tokens: el access token dura ~2 horas y el refresh token ~180 días y ROTA en cada renovación, así que cada
token nuevo se guarda de inmediato en `tiktok_organic_tokens.json` (ignorado por git). Los tokens nunca se
imprimen, ni van en `repr()`, ni en mensajes de error.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_BASE = "https://open.tiktokapis.com"
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_PATH = "/v2/oauth/token/"
USER_INFO_PATH = "/v2/user/info/"
VIDEO_LIST_PATH = "/v2/video/list/"
VIDEO_QUERY_PATH = "/v2/video/query/"

SCOPE_BASIC = "user.info.basic"
SCOPE_PROFILE = "user.info.profile"
SCOPE_STATS = "user.info.stats"
SCOPE_VIDEOS = "video.list"
REQUIRED_SCOPES: tuple[str, ...] = (SCOPE_BASIC, SCOPE_PROFILE, SCOPE_STATS, SCOPE_VIDEOS)

# Cada campo de /user/info/ exige un scope; pedir uno sin permiso hace fallar TODA la petición,
# por eso solo se piden los campos cuyo scope se concedió.
USER_FIELD_SCOPES: dict[str, str] = {
    "avatar_url": SCOPE_BASIC,
    "display_name": SCOPE_BASIC,
    "username": SCOPE_PROFILE,
    "bio_description": SCOPE_PROFILE,
    "profile_deep_link": SCOPE_PROFILE,
    "is_verified": SCOPE_PROFILE,
    "follower_count": SCOPE_STATS,
    "following_count": SCOPE_STATS,
    "likes_count": SCOPE_STATS,
    "video_count": SCOPE_STATS,
}
VIDEO_FIELDS = (
    "id,title,video_description,create_time,cover_image_url,share_url,duration,height,width,"
    "like_count,comment_count,share_count,view_count,embed_link"
)

VIDEOS_PAGE_SIZE = 20  # máximo de /video/list/
QUERY_BATCH_SIZE = 20  # máximo de ids por /video/query/
MAX_VIDEO_PAGES = 100  # tope de seguridad: 2.000 videos
HTTP_TIMEOUT_SECONDS = 30
EXPIRY_MARGIN_SECONDS = 300  # renovar el access token con 5 min de margen
STATE_TTL_SECONDS = 900  # el `state` del OAuth vale 15 minutos
BIO_MAX_CHARS = 200
INVALID_TOKEN_CODES = frozenset({"access_token_invalid", "invalid_access_token"})

Clock = Callable[[], float]


class TikTokOrganicError(RuntimeError):
    """Error de la API o de configuración. Nunca incluye tokens ni secretos."""

    def __init__(self, code: int | str, message: str):
        self.code = str(code)
        super().__init__(f"TikTok orgánico {code}: {message}")


# --- configuración ----------------------------------------------------------------------------------------


class TikTokOrganicSettings(BaseSettings):
    """Variables TIKTOK_ORGANIC_* del .env: las claves de la app registrada en developers.tiktok.com."""

    client_key: str
    client_secret: str
    redirect_uri: str = ""  # debe coincidir EXACTO con el registrado en el portal de TikTok
    tokens_file: Path = Path("tiktok_organic_tokens.json")

    model_config = SettingsConfigDict(env_file=".env", env_prefix="TIKTOK_ORGANIC_", extra="ignore")


class ConnectedTikTokOrganicSettings(TikTokOrganicSettings):
    """Igual, pero solo existe si el dueño ya autorizó (hay archivo de tokens).

    `sync_jobs.is_configured` trata un ValidationError como "fuente no configurada"; así el sincronizador
    automático omite esta fuente hasta que haya autorización en vez de fallar cada 6 horas.
    """

    @model_validator(mode="after")
    def _require_tokens_file(self) -> ConnectedTikTokOrganicSettings:
        if not self.tokens_file.exists():
            raise ValueError("TikTok orgánico no está autorizado: falta el archivo de tokens")
        return self


# --- tokens -----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenRecord:
    """Tokens de usuario. Los secretos van con repr=False para que no salgan en logs ni tracebacks."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: float  # epoch (s)
    refresh_expires_at: float
    open_id: str
    scope: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at,
            "refresh_expires_at": self.refresh_expires_at,
            "open_id": self.open_id,
            "scope": list(self.scope),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TokenRecord:
        try:
            return cls(
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                access_expires_at=float(data["access_expires_at"]),
                refresh_expires_at=float(data["refresh_expires_at"]),
                open_id=str(data.get("open_id", "")),
                scope=tuple(data.get("scope", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TikTokOrganicError("corrupt_tokens", "el archivo de tokens no tiene el formato esperado") from exc


def parse_token_response(payload: dict[str, Any], now: float, previous: TokenRecord | None = None) -> TokenRecord:
    """Convierte la respuesta (plana) de /v2/oauth/token/ en un registro con vencimientos absolutos.

    `previous` rellena open_id/scope si la renovación no los repite.
    """
    access, refresh = payload.get("access_token"), payload.get("refresh_token")
    if not access or not refresh:
        raise TikTokOrganicError("bad_token_response", "la respuesta no trae access_token y refresh_token")
    scope_text = payload.get("scope")
    scope = (
        tuple(s for s in str(scope_text).split(",") if s) if scope_text else (previous.scope if previous else ())
    )
    return TokenRecord(
        access_token=str(access),
        refresh_token=str(refresh),
        access_expires_at=now + float(payload.get("expires_in") or 0),
        refresh_expires_at=now + float(payload.get("refresh_expires_in") or 0),
        open_id=str(payload.get("open_id") or (previous.open_id if previous else "")),
        scope=scope,
    )


class TokenStore:
    """Archivo JSON de tokens. Escritura atómica: un corte a mitad de guardado no deja el archivo roto."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> TokenRecord | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TikTokOrganicError("corrupt_tokens", "no se pudo leer el archivo de tokens") from exc
        if not isinstance(data, dict):
            raise TikTokOrganicError("corrupt_tokens", "el archivo de tokens no tiene el formato esperado")
        return TokenRecord.from_json(data)

    def save(self, record: TokenRecord) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(record.to_json()), encoding="utf-8")
        try:
            tmp.chmod(0o600)  # en Windows no tiene efecto; en Linux deja el archivo solo para el dueño
        except OSError:
            pass
        os.replace(tmp, self.path)

    def delete(self) -> bool:
        """Borra el archivo. Devuelve False si no existía (desconectar es idempotente)."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True


# --- OAuth: state, URL de autorización, canje y renovación ------------------------------------------------


def make_state(secret: str, now: float, nonce: str | None = None) -> str:
    """`state` firmado y con vencimiento: protege el callback de CSRF sin guardar nada en servidor."""
    body = f"{nonce or secrets.token_urlsafe(16)}.{int(now)}"
    return f"{body}.{_sign(secret, body)}"


def verify_state(secret: str, state: str, now: float, ttl: int = STATE_TTL_SECONDS) -> bool:
    """True si la firma es válida y el state no venció ni viene del futuro."""
    parts = state.split(".")
    if len(parts) != 3 or not parts[1].isdigit():
        return False
    body = f"{parts[0]}.{parts[1]}"
    if not hmac.compare_digest(_sign(secret, body), parts[2]):
        return False
    return 0 <= now - int(parts[1]) <= ttl


def _sign(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def build_authorize_url(state: str, settings: TikTokOrganicSettings | None = None) -> str:
    """URL a la que se manda al dueño para autorizar la app (flujo de código de autorización)."""
    cfg = settings or TikTokOrganicSettings()
    if not cfg.redirect_uri:
        raise TikTokOrganicError("no_redirect_uri", "falta TIKTOK_ORGANIC_REDIRECT_URI")
    query = urlencode(
        {
            "client_key": cfg.client_key,
            "scope": ",".join(REQUIRED_SCOPES),
            "response_type": "code",
            "redirect_uri": cfg.redirect_uri,
            "state": state,
        },
        safe=",",
    )
    return f"{AUTHORIZE_URL}?{query}"


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError as exc:
        raise TikTokOrganicError(resp.status_code, "la respuesta no es JSON") from exc
    if not isinstance(payload, dict):
        raise TikTokOrganicError(resp.status_code, "la respuesta no es un objeto JSON")
    return payload


def raise_for_error(payload: dict[str, Any]) -> None:
    """TikTok usa dos formas: `{"error": {"code", "message"}}` (API) y `{"error": "...", "error_description"}` (OAuth)."""
    err = payload.get("error")
    if not err:
        return
    if isinstance(err, dict):
        code = err.get("code", "unknown")
        if code == "ok":
            return
        raise TikTokOrganicError(code, str(err.get("message", "")))
    raise TikTokOrganicError(err, str(payload.get("error_description", "")))


def _token_request(
    http: httpx.Client,
    settings: TikTokOrganicSettings,
    form: dict[str, str],
    now: float,
    previous: TokenRecord | None = None,
) -> TokenRecord:
    resp = http.post(
        f"{API_BASE}{TOKEN_PATH}",
        data={"client_key": settings.client_key, "client_secret": settings.client_secret, **form},
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
    )
    payload = _json(resp)
    raise_for_error(payload)
    return parse_token_response(payload, now, previous)


def exchange_code(
    code: str,
    settings: TikTokOrganicSettings | None = None,
    http: httpx.Client | None = None,
    clock: Clock = time.time,
) -> TokenRecord:
    """Canjea el `code` del callback por tokens y los guarda en el archivo. Devuelve el registro."""
    cfg = settings or TikTokOrganicSettings()
    if not cfg.redirect_uri:
        raise TikTokOrganicError("no_redirect_uri", "falta TIKTOK_ORGANIC_REDIRECT_URI")
    client = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
    try:
        record = _token_request(
            client,
            cfg,
            {"code": code, "grant_type": "authorization_code", "redirect_uri": cfg.redirect_uri},
            clock(),
        )
    finally:
        if http is None:
            client.close()
    TokenStore(cfg.tokens_file).save(record)
    return record


def connection_status(settings: TikTokOrganicSettings | None, record: TokenRecord | None, now: float) -> dict[str, Any]:
    """Estado de la conexión y pasos pendientes. Función pura: no toca red ni disco, y no incluye tokens."""
    pending: list[str] = []
    if settings is None:
        pending.append("Definir TIKTOK_ORGANIC_CLIENT_KEY y TIKTOK_ORGANIC_CLIENT_SECRET en backend/.env")
    elif not settings.redirect_uri:
        pending.append(
            "Definir TIKTOK_ORGANIC_REDIRECT_URI y registrar esa misma URL en el portal de TikTok (Login Kit)"
        )
    scopes = record.scope if record else ()
    missing = [s for s in REQUIRED_SCOPES if s not in scopes] if record else []
    refresh_left = record.refresh_expires_at - now if record else None
    reauthorize = bool(record and refresh_left is not None and refresh_left <= 0)
    if settings is not None and settings.redirect_uri and (record is None or reauthorize):
        pending.append("Autorizar la cuenta de TikTok desde la pantalla de conexión")
    if missing and not reauthorize:
        pending.append("Volver a autorizar concediendo los permisos que faltan: " + ", ".join(missing))
    return {
        "configured": settings is not None,
        "redirect_uri": settings.redirect_uri if settings and settings.redirect_uri else None,
        "connected": record is not None and not reauthorize,
        "needs_reauthorization": reauthorize,
        "scopes": list(scopes),
        "missing_scopes": missing,
        "access_expires_at": _to_datetime(record.access_expires_at) if record else None,
        "refresh_expires_at": _to_datetime(record.refresh_expires_at) if record else None,
        "refresh_days_left": max(int(refresh_left // 86400), 0) if refresh_left is not None else None,
        "pending_steps": pending,
    }


def _to_datetime(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


# --- cliente ----------------------------------------------------------------------------------------------


class TikTokOrganicClient:
    """Lee perfil y videos con el token del dueño, renovándolo solo. No existe ningún método de escritura."""

    def __init__(
        self,
        settings: TikTokOrganicSettings | None = None,
        http: httpx.Client | None = None,
        clock: Clock = time.time,
    ) -> None:
        self.settings = settings or TikTokOrganicSettings()
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS)
        self._clock = clock
        self._store = TokenStore(self.settings.tokens_file)
        record = self._store.load()
        if record is None:
            raise TikTokOrganicError("not_connected", "la cuenta aún no fue autorizada")
        self._tokens = record

    @property
    def scopes(self) -> tuple[str, ...]:
        return self._tokens.scope

    def has_scope(self, scope: str) -> bool:
        return scope in self._tokens.scope

    # --- tokens
    def refresh(self) -> None:
        """Renueva el access token. El refresh token rota: el nuevo se guarda antes de seguir."""
        now = self._clock()
        if self._tokens.refresh_expires_at <= now:
            raise TikTokOrganicError("reauthorize", "el refresh token venció: hay que volver a autorizar")
        self._tokens = _token_request(
            self._http,
            self.settings,
            {"grant_type": "refresh_token", "refresh_token": self._tokens.refresh_token},
            now,
            previous=self._tokens,
        )
        self._store.save(self._tokens)

    def _ensure_token(self) -> None:
        if self._tokens.access_expires_at - self._clock() < EXPIRY_MARGIN_SECONDS:
            self.refresh()

    # --- peticiones
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Petición autenticada. Si TikTok invalida el token antes de tiempo, renueva y reintenta UNA vez."""
        for attempt in (1, 2):
            self._ensure_token()
            resp = self._http.request(
                method,
                f"{API_BASE}{path}",
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {self._tokens.access_token}"},
            )
            payload = _json(resp)
            try:
                raise_for_error(payload)
            except TikTokOrganicError as exc:
                if attempt == 1 and exc.code in INVALID_TOKEN_CODES:
                    self.refresh()
                    continue
                raise
            return payload.get("data") or {}
        raise AssertionError("inalcanzable")  # pragma: no cover

    # --- datos
    def user_info(self) -> dict[str, Any]:
        """Perfil y contadores. Solo pide los campos cuyo scope fue concedido."""
        fields = [f for f, scope in USER_FIELD_SCOPES.items() if self.has_scope(scope)]
        if not fields:
            raise TikTokOrganicError("missing_scope", f"falta el permiso {SCOPE_BASIC}")
        data = self._request("GET", USER_INFO_PATH, params={"fields": ",".join(fields)})
        return data.get("user") or {}

    def iter_videos(self, max_pages: int = MAX_VIDEO_PAGES) -> Iterator[dict[str, Any]]:
        """Videos públicos, del más nuevo al más viejo, paginando por cursor mientras `has_more`."""
        cursor: int | None = None
        for _ in range(max_pages):
            body: dict[str, Any] = {"max_count": VIDEOS_PAGE_SIZE}
            if cursor is not None:
                body["cursor"] = cursor
            data = self._request("POST", VIDEO_LIST_PATH, params={"fields": VIDEO_FIELDS}, body=body)
            videos = data.get("videos") or []
            yield from videos
            next_cursor = data.get("cursor")
            # Sin has_more, sin videos o con un cursor que no avanza se corta: evita un bucle infinito.
            if not data.get("has_more") or not videos or next_cursor is None or next_cursor == cursor:
                return
            cursor = int(next_cursor)

    def query_videos(self, video_ids: list[str]) -> list[dict[str, Any]]:
        """Métricas ACTUALES de videos concretos, en lotes de 20 ids (útil para refrescar sin recorrer la lista)."""
        out: list[dict[str, Any]] = []
        for start in range(0, len(video_ids), QUERY_BATCH_SIZE):
            batch = video_ids[start : start + QUERY_BATCH_SIZE]
            data = self._request(
                "POST",
                VIDEO_QUERY_PATH,
                params={"fields": VIDEO_FIELDS},
                body={"filters": {"video_ids": batch}},
            )
            out.extend(data.get("videos") or [])
        return out


# --- constructores puros: respuesta de la API -> filas ---------------------------------------------------


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_account_row(user: dict[str, Any], today: date) -> dict[str, Any]:
    """Foto de la cuenta del día. Los contadores quedan en None si no se concedió `user.info.stats`."""
    bio = (user.get("bio_description") or "").strip()
    return {
        "snapshot_date": today,
        "open_id": user.get("open_id"),
        "username": user.get("username"),
        "display_name": user.get("display_name"),
        "avatar_url": user.get("avatar_url"),
        "bio": bio[:BIO_MAX_CHARS] or None,
        "profile_url": user.get("profile_deep_link"),
        "is_verified": user.get("is_verified"),
        "follower_count": _int(user.get("follower_count")),
        "following_count": _int(user.get("following_count")),
        "likes_count": _int(user.get("likes_count")),
        "video_count": _int(user.get("video_count")),
    }


def build_video_row(video: dict[str, Any]) -> dict[str, Any] | None:
    """Datos estables de un video. None si la API no trae id (no se puede guardar sin clave)."""
    video_id = video.get("id")
    if not video_id:
        return None
    created = _int(video.get("create_time"))
    return {
        "video_id": str(video_id),
        "title": video.get("title") or None,
        "description": video.get("video_description") or None,
        "cover_url": video.get("cover_image_url"),  # el enlace caduca (~6 h): se actualiza en cada sync
        "created_at": datetime.fromtimestamp(created, UTC) if created else None,
        "duration_seconds": _int(video.get("duration")),
        "share_url": video.get("share_url"),
        "embed_link": video.get("embed_link"),
        "width": _int(video.get("width")),
        "height": _int(video.get("height")),
    }


def build_metric_row(video: dict[str, Any], today: date) -> dict[str, Any] | None:
    """Foto diaria de las métricas de un video."""
    video_id = video.get("id")
    if not video_id:
        return None
    return {
        "video_id": str(video_id),
        "snapshot_date": today,
        "views": _int(video.get("view_count")) or 0,
        "likes": _int(video.get("like_count")) or 0,
        "comments": _int(video.get("comment_count")) or 0,
        "shares": _int(video.get("share_count")) or 0,
    }


@dataclass(frozen=True, slots=True)
class OrganicSnapshot:
    """Todo lo leído en una sincronización, ya normalizado a filas."""

    account: dict[str, Any] | None
    videos: list[dict[str, Any]]
    metrics: list[dict[str, Any]]
    problems: list[str]


def fetch_snapshot(client: TikTokOrganicClient, today: date) -> OrganicSnapshot:
    """Lee perfil y videos. Un permiso faltante degrada esa parte y se reporta; no tira todo el sync."""
    problems: list[str] = []
    account: dict[str, Any] | None = None
    try:
        account = build_account_row(client.user_info(), today)
    except TikTokOrganicError as exc:
        problems.append(f"perfil: {exc}")
    videos: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    if client.has_scope(SCOPE_VIDEOS):
        try:
            for raw in client.iter_videos():
                row, metric = build_video_row(raw), build_metric_row(raw, today)
                if row and metric:
                    videos.append(row)
                    metrics.append(metric)
        except TikTokOrganicError as exc:
            problems.append(f"videos: {exc}")
    else:
        problems.append(f"videos: falta el permiso {SCOPE_VIDEOS}; vuelve a autorizar")
    return OrganicSnapshot(account=account, videos=videos, metrics=metrics, problems=problems)
