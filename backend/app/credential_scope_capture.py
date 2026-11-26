"""Captura los permisos (`granted_scopes`) del token del vendedor de TikTok Shop, sin exponer ningún token.

Por qué hace falta: el canje original del token devolvió los permisos concedidos, pero nadie los guardó, y la
API de TikTok Shop no tiene un endpoint "¿qué permisos tengo?". El único lugar donde vuelven a aparecer es la
respuesta de `/api/v2/token/refresh`.

Cómo se captura sin riesgo:
  * El cliente de producción (`TikTokShopClient`) renueva con `write_text`: un corte a mitad de escritura
    deja el archivo de tokens vacío. Aquí se renueva con `AtomicTokenShopClient` (archivo temporal +
    `os.replace`, respaldo `.bak` y bloqueo entre procesos), así que un fallo deja el token bueno intacto.
  * La renovación normal del cliente descarta `granted_scopes`. `ScopeRecorder` envuelve al cliente HTTP y
    lee SOLO esa lista de la respuesta (validada: solo nombres con forma de permiso); la respuesta completa,
    que trae tokens, nunca se guarda, se registra ni se devuelve.
  * `ScopeRecorder` también sirve para registrar los permisos cuando la aplicación renueve de forma natural:
    basta pasarlo como `http` y un `on_scopes`; así no hace falta forzar una renovación.

Límite: renovar rota el access token (y puede rotar el refresh token). Un proceso de producción que ya tenga
el token viejo en memoria lo releerá del archivo en su próxima sincronización (el cliente se crea por trabajo).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from app.connectors.tiktok_creator import (
    AtomicTokenShopClient,
    CreatorReauthRequired,
    TikTokCreatorError,
)
from app.connectors.tiktok_shop import AUTH_BASE, TikTokShopError, TikTokShopSettings

REFRESH_PATH = "/api/v2/token/refresh"
GRANTED_SCOPES_KEY = "granted_scopes"
MAX_SCOPES = 200  # un valor mayor no es una lista de permisos: se descarta
# Un permiso tiene forma `seller.product.write`: letras, dígitos y . _ - :. Todo lo demás (un token, un
# mensaje de error, HTML) se rechaza para no guardar por accidente algo que no sea un nombre de permiso.
SCOPE_NAME = re.compile(r"[A-Za-z0-9_.:\-]{1,120}")

# TikTok recibe la renovación por GET con app_secret y refresh_token EN LA URL, y httpx registra cada URL en INFO.
# Durante la captura esos registros se silencian para que un nivel de log permisivo no escriba secretos.
NOISY_HTTP_LOGGERS = ("httpx", "httpcore")

ScopeListener = Callable[[tuple[str, ...]], None]


class ScopeCaptureError(RuntimeError):
    """La captura falló. El mensaje nunca incluye tokens, claves ni el cuerpo de la respuesta."""


def parse_scope_names(raw: Any) -> tuple[str, ...]:
    """Normaliza `granted_scopes` a nombres únicos y ordenados. Acepta lista de textos o texto separado por comas."""
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list | tuple) or len(raw) > MAX_SCOPES:
        return ()
    names = {item.strip() for item in raw if isinstance(item, str)}
    return tuple(sorted(name for name in names if SCOPE_NAME.fullmatch(name)))


def extract_granted_scopes(payload: Any) -> tuple[str, ...]:
    """Saca los permisos de `{"code":0,"data":{"granted_scopes":[...]}}`; cualquier otra forma da `()`."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return ()
    return parse_scope_names(data.get(GRANTED_SCOPES_KEY))


class ScopeRecorder:
    """Proxy del cliente HTTP: reenvía todo y, en la renovación de token, recuerda solo los nombres de permisos."""

    def __init__(self, http: httpx.Client, on_scopes: ScopeListener | None = None) -> None:
        self._http = http
        self._on_scopes = on_scopes
        self.scopes: tuple[str, ...] = ()

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        response = self._http.get(url, **kwargs)
        if url.endswith(REFRESH_PATH):
            self._observe(response)
        return response

    def _observe(self, response: httpx.Response) -> None:
        try:
            scopes = extract_granted_scopes(response.json())
        except ValueError:  # cuerpo que no es JSON: no hay nada que registrar
            return
        if scopes:
            self.scopes = scopes
            if self._on_scopes is not None:
                self._on_scopes(scopes)

    def __getattr__(self, name: str) -> Any:  # request, stream, close... van al cliente real
        return getattr(self._http, name)


class ScopeCapturingClient(AtomicTokenShopClient):
    """Cliente atómico que puede FORZAR una renovación (las normales se omiten mientras el token siga vigente)."""

    def __init__(self, settings: TikTokShopSettings, recorder: ScopeRecorder) -> None:
        self._force_renewal = False
        super().__init__(settings, recorder)  # type: ignore[arg-type]  # el proxy imita a httpx.Client

    def _needs_renewal(self, tokens: dict[str, Any]) -> bool:
        return self._force_renewal or super()._needs_renewal(tokens)

    def renew_now(self) -> None:
        """Una renovación inmediata, bajo bloqueo y con escritura atómica."""
        self._force_renewal = True
        try:
            self.refresh()
        finally:
            self._force_renewal = False


@contextmanager
def quiet_http_logs() -> Iterator[None]:
    """Sube a WARNING los registros de httpx/httpcore mientras dura el bloque y restaura el nivel anterior."""
    loggers = [logging.getLogger(name) for name in NOISY_HTTP_LOGGERS]
    previous = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for logger, level in zip(loggers, previous, strict=True):
            logger.setLevel(level)


def _load_settings() -> TikTokShopSettings:
    """Variables TTS_* del .env. Si faltan, el error de pydantic repite los valores recibidos: no se propaga."""
    try:
        return TikTokShopSettings()
    except ValidationError:
        raise ScopeCaptureError("TikTok Shop no está configurado (faltan variables TTS_*)") from None


@dataclass(frozen=True, slots=True)
class SellerScopeCapture:
    """Resultado de una captura: SOLO nombres de permisos."""

    scopes: tuple[str, ...]


def capture_seller_scopes(
    settings: TikTokShopSettings | None = None, http: httpx.Client | None = None
) -> SellerScopeCapture:
    """Renueva el token del vendedor UNA vez (atómicamente) y devuelve los permisos concedidos.

    Repetible: cada llamada renueva y lee de nuevo. `settings`/`http` se inyectan en las pruebas; en producción
    salen del .env y de un cliente con tiempo de espera. Lanza `ScopeCaptureError` si TikTok rechaza la
    renovación o no informa permisos (entonces el archivo de tokens bueno queda intacto).
    """
    config = settings or _load_settings()
    owns_http = http is None
    client_http = http or httpx.Client(timeout=30)
    recorder = ScopeRecorder(client_http)
    try:
        with quiet_http_logs():
            ScopeCapturingClient(config, recorder).renew_now()
    except (CreatorReauthRequired, TikTokShopError, TikTokCreatorError) as exc:
        raise ScopeCaptureError(f"No se pudo renovar el token del vendedor ({type(exc).__name__})") from exc
    except httpx.HTTPError as exc:
        raise ScopeCaptureError(f"TikTok Shop no respondió ({type(exc).__name__})") from exc
    finally:
        if owns_http:
            client_http.close()
    if not recorder.scopes:
        raise ScopeCaptureError(f"La respuesta de {AUTH_BASE}{REFRESH_PATH} no trajo permisos concedidos")
    return SellerScopeCapture(recorder.scopes)
