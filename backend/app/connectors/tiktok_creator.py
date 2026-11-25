"""Conector de TikTok Creator: la cuenta de creador propia de CHK (autorizada aparte del vendedor).

SOLO LECTURA. Nunca crea colaboraciones, solicitudes de muestra, enlaces de afiliado ni vitrinas: los únicos
POST son consultas de búsqueda (`.../search`) y los demás son GET. Reutiliza `TikTokShopClient` (firma, hosts y
manejo de errores) por subclase y composición; `tiktok_shop.py` no se modifica.

Por qué existe `AtomicTokenShopClient`: el refresh token de Creator ROTA en cada renovación y es el único que
existe; si se pierde hay que reautorizar la cuenta desde cero. El cliente base guarda con `write_text` (un corte
a mitad de escritura deja el archivo vacío), así que aquí la renovación:
  1. toma un bloqueo de archivo (nunca dos procesos renuevan a la vez, o el segundo invalidaría al primero),
  2. relee el archivo (otro proceso pudo renovar mientras esperábamos el bloqueo),
  3. guarda con archivo temporal en el mismo directorio + `os.replace` (atómico) y deja el último archivo
     bueno en `tokens_creator.json.bak`.
Si la renovación falla el archivo bueno queda intacto y el error es `CreatorReauthRequired`: el sincronizador
lo reporta como "hay que reautorizar" sin tumbar al resto de las fuentes.

Qué entrega la API (verificado con la cuenta real): perfil y permisos, catálogo de colaboraciones abiertas
(máximo 20 por página y 10.000 productos por consulta; sin filtros de servidor descubiertos, solo orden por
`commission_rate`, `product_sales_price`, `commission` o `units_sold`), vitrina propia y pedidos de afiliado.
Qué NO entrega: id de la tienda del producto (solo su nombre), comisión de anuncios (solo la estándar), y las
colaboraciones dirigidas exigen un `shop_id` que ningún otro endpoint expone.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Self

import httpx
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import SettingsConfigDict

from app.connectors.tiktok_shop import (
    TikTokShopClient,
    TikTokShopError,
    TikTokShopSettings,
)

Row = dict[str, Any]
Sleep = Callable[[float], None]
Clock = Callable[[], float]

# --- rutas (todas de solo lectura) -------------------------------------------------------------------------
PROFILE_PATH = "/affiliate_creator/202405/profiles"
OPEN_COLLAB_SEARCH_PATH = "/affiliate_creator/202405/open_collaborations/products/search"
SHOWCASE_PATH = "/affiliate_creator/202405/showcases/products"
ORDERS_SEARCH_PATH = "/affiliate_creator/202410/orders/search"

# --- límites de la API -------------------------------------------------------------------------------------
CREATOR_PAGE_SIZE = 20  # máximo permitido: con más, TikTok responde 36009004
CATALOG_VISIBLE_CAP = 10_000  # `total_count` nunca pasa de esto
MAX_CATALOG_PAGES = CATALOG_VISIBLE_CAP // CREATOR_PAGE_SIZE + 20  # 500 páginas + margen
MAX_SIMPLE_PAGES = 50  # vitrina y pedidos: tope de seguridad (1.000 filas)
SORT_FIELDS = frozenset({"commission_rate", "product_sales_price", "commission", "units_sold"})
SORT_ORDERS = frozenset({"ASC", "DESC"})
SHOWCASE_ORIGINS = frozenset({"LIVE", "SHOWCASE"})

# --- ritmo y reintentos ------------------------------------------------------------------------------------
PAGE_PAUSE_SECONDS = 0.3  # cada consulta ya tarda ~1,6 s; la pausa extra evita ráfagas
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 2.0  # 2, 4, 8 s ante fallos de red
RATE_LIMIT_WAIT_SECONDS = 15.0  # se multiplica por el número de intento
RATE_LIMIT_MARKERS = ("rate limit", "too many", "frequen", "throttl")

# --- tokens ------------------------------------------------------------------------------------------------
RENEWAL_MARGIN_SECONDS = 3600  # renovar con 1 h de margen para no vencer a mitad de una carga larga
LOCK_TIMEOUT_SECONDS = 60.0
LOCK_STALE_SECONDS = 300.0  # un bloqueo más viejo que esto es de un proceso caído
LOCK_POLL_SECONDS = 0.2
SAVE_ATTEMPTS = 3  # reintentos de escritura tras renovar: el token nuevo no puede perderse
BACKUP_SUFFIX = ".bak"
LOCK_SUFFIX = ".lock"

# --- datos dudosos -----------------------------------------------------------------------------------------
# El catálogo trae productos con precio de billones y unidades en miles de millones (datos de prueba). Esos
# valores desbordan numeric(14,4) y falsearían cualquier puntaje: se marcan `suspect` y su dinero se descarta.
MAX_PLAUSIBLE_PRICE_USD = Decimal(100_000)
MAX_PLAUSIBLE_UNITS = 5_000_000
BASIS_POINTS = Decimal(10_000)  # commission.rate viene en puntos base (1000 = 10 %)
CATEGORY_SEPARATOR = " > "


class TikTokCreatorError(RuntimeError):
    """Error de la API o de la configuración de Creator. Nunca incluye tokens ni secretos."""


class CreatorReauthRequired(TikTokCreatorError):
    """El refresh token ya no sirve (vencido, rechazado o archivo ilegible): el dueño debe reautorizar."""


class CreatorLockTimeout(TikTokCreatorError):
    """No se obtuvo el bloqueo de renovación a tiempo (otro proceso sigue renovando)."""


# --- configuración -----------------------------------------------------------------------------------------


class TikTokCreatorSettings(TikTokShopSettings):
    """Mismas credenciales de la app que el vendedor (TTS_APP_KEY/TTS_APP_SECRET), pero token propio.

    El campo usa un alias de entorno exclusivo: `TTS_TOKENS_FILE` (el del vendedor) nunca puede apuntar
    este módulo al archivo equivocado. Como `ConnectedTikTokOrganicSettings`, no existe sin autorización:
    `sync_jobs.is_configured` trata el ValidationError como "fuente no configurada".
    """

    tokens_file: Path = Field(default=Path("tokens_creator.json"), validation_alias=AliasChoices("TTS_CREATOR_TOKENS_FILE"))

    model_config = SettingsConfigDict(env_file=".env", env_prefix="TTS_", extra="ignore", populate_by_name=True)

    @model_validator(mode="after")
    def _require_tokens_file(self) -> TikTokCreatorSettings:
        if not self.tokens_file.exists():
            raise ValueError("TikTok Creator no está autorizado: falta el archivo de tokens")
        return self


# --- archivos: escritura atómica, respaldo y bloqueo -------------------------------------------------------


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_SUFFIX)


def lock_path(path: Path) -> Path:
    return path.with_name(path.name + LOCK_SUFFIX)


def atomic_write_text(path: Path, text: str) -> None:
    """Escribe a un temporal del MISMO directorio y lo promueve con `os.replace` (atómico en el mismo volumen).

    Un corte de luz o un error a mitad de escritura deja el archivo anterior intacto en vez de vacío.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def has_credentials(data: Any) -> bool:
    """¿Parece un archivo de tokens usable? (access y refresh token no vacíos)."""
    return (
        isinstance(data, dict)
        and bool(str(data.get("access_token") or ""))
        and bool(str(data.get("refresh_token") or ""))
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_token_file(path: Path) -> Row:
    """Lee los tokens; si el archivo principal está dañado, cae al respaldo. Sin ninguno usable: reautorizar."""
    for candidate in (path, backup_path(path)):
        data = _read_json(candidate)
        if has_credentials(data):
            return dict(data)
    raise CreatorReauthRequired("El archivo de tokens de Creator falta o es ilegible: hay que reautorizar la cuenta")


class FileLock:
    """Bloqueo entre procesos con `O_CREAT | O_EXCL` (atómico también en Windows, sin dependencias).

    Un bloqueo más viejo que `stale_after` se considera de un proceso caído y se rompe.
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = LOCK_TIMEOUT_SECONDS,
        stale_after: float = LOCK_STALE_SECONDS,
        sleep: Sleep = time.sleep,
        clock: Clock = time.time,
    ) -> None:
        self._path, self._timeout, self._stale_after = path, timeout, stale_after
        self._sleep, self._clock = sleep, clock

    def _is_stale(self) -> bool:
        try:
            return self._clock() - self._path.stat().st_mtime > self._stale_after
        except OSError:  # desapareció entre el intento y la revisión: reintentar
            return False

    def __enter__(self) -> Self:
        deadline = self._clock() + self._timeout
        while True:
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._is_stale():
                    self._path.unlink(missing_ok=True)
                    continue
                if self._clock() >= deadline:
                    raise CreatorLockTimeout("Otro proceso está renovando el token de Creator") from None
                self._sleep(LOCK_POLL_SECONDS)
            else:
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self

    def __exit__(self, *_: object) -> None:
        self._path.unlink(missing_ok=True)


def token_status(path: Path, now: float | None = None) -> Row:
    """Vencimientos del archivo de tokens SIN exponer ningún token (para el estado de la API)."""
    now = time.time() if now is None else now
    data = _read_json(path)
    if not has_credentials(data):
        return {"present": path.exists(), "usable": False, "access_expires_at": None, "refresh_expires_at": None,
                "access_valid": False, "refresh_valid": False}
    access, refresh = _epoch(data.get("access_token_expire_in")), _epoch(data.get("refresh_token_expire_in"))
    return {
        "present": True,
        "usable": True,
        "access_expires_at": _to_datetime(access),
        "refresh_expires_at": _to_datetime(refresh),
        "access_valid": access is not None and access > now,
        "refresh_valid": refresh is None or refresh > now,
    }


def _epoch(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _to_datetime(epoch: float | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, tz=UTC) if epoch is not None else None


# --- cliente de tokens duraderos ---------------------------------------------------------------------------


class AtomicTokenShopClient(TikTokShopClient):
    """`TikTokShopClient` cuya renovación es atómica, serializada entre procesos y con respaldo."""

    def __init__(
        self,
        settings: TikTokShopSettings,
        http: httpx.Client | None = None,
        *,
        sleep: Sleep = time.sleep,
        clock: Clock = time.time,
    ) -> None:
        self._sleep, self._clock = sleep, clock
        super().__init__(settings, http)

    # Cada hook sustituye al del padre, que guarda sin atomicidad.
    def _load_tokens(self) -> Row:
        return read_token_file(self.settings.tokens_file)

    def _needs_renewal(self, tokens: Row) -> bool:
        expires = _epoch(tokens.get("access_token_expire_in")) or 0.0
        return not tokens.get("access_token") or expires - self._clock() < RENEWAL_MARGIN_SECONDS

    def _ensure_token(self) -> None:
        if self._needs_renewal(self._tokens):
            self.refresh()

    def _save_tokens(self) -> None:
        """Respalda el último archivo bueno y escribe el nuevo atómicamente (con reintentos: no se puede perder)."""
        path = self.settings.tokens_file
        self._backup_current(path)
        for attempt in range(1, SAVE_ATTEMPTS + 1):
            try:
                atomic_write_text(path, json.dumps(self._tokens))
                return
            except OSError:
                if attempt == SAVE_ATTEMPTS:
                    raise
                self._sleep(0.5 * attempt)

    @staticmethod
    def _backup_current(path: Path) -> None:
        """Copia el archivo actual al .bak SOLO si es válido: un archivo dañado no pisa un buen respaldo."""
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            return
        if has_credentials(_parse(current)):
            atomic_write_text(backup_path(path), current)

    def refresh(self) -> None:
        """Renueva bajo bloqueo. Un fallo deja el archivo bueno intacto y se informa como reautorización."""
        path = self.settings.tokens_file
        with FileLock(lock_path(path), sleep=self._sleep, clock=self._clock):
            on_disk = read_token_file(path)
            if not self._needs_renewal(on_disk):  # otro proceso renovó mientras esperábamos el bloqueo
                self._tokens = on_disk
                return
            self._tokens = on_disk
            self._require_live_refresh_token(on_disk)
            snapshot = dict(on_disk)
            try:
                super().refresh()
            except TikTokShopError as exc:
                self._tokens = snapshot
                raise CreatorReauthRequired(f"TikTok rechazó la renovación del token de Creator: {exc}") from exc
            except KeyError as exc:
                self._tokens = snapshot
                raise TikTokCreatorError("La respuesta de renovación de Creator vino incompleta") from exc

    def _require_live_refresh_token(self, tokens: Row) -> None:
        expires = _epoch(tokens.get("refresh_token_expire_in"))
        if expires is not None and expires <= self._clock():
            raise CreatorReauthRequired("El refresh token de Creator venció: hay que reautorizar la cuenta")


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


# --- cliente de solo lectura -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Page:
    """Una página de resultados. `has_more` es False en la última (sirve para saber si se leyó TODO)."""

    items: list[Row]
    total_count: int | None
    has_more: bool


def _is_rate_limit(exc: TikTokShopError) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in RATE_LIMIT_MARKERS)


class TikTokCreatorClient:
    """Lecturas de la cuenta de creador. Pagina de 20 en 20 con reintentos y espera ante límite de tasa."""

    def __init__(
        self,
        settings: TikTokCreatorSettings | None = None,
        http: httpx.Client | None = None,
        *,
        sleep: Sleep = time.sleep,
        shop: TikTokShopClient | None = None,
    ) -> None:
        self.settings = settings or TikTokCreatorSettings()
        self._sleep = sleep
        self._shop = shop or AtomicTokenShopClient(self.settings, http or httpx.Client(timeout=30), sleep=sleep)

    def _call(self, method: str, path: str, params: Row | None = None, body: Row | None = None) -> Row:
        """Una petición con reintentos: red caída (con retroceso) y límite de tasa (espera mayor)."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last = attempt == MAX_ATTEMPTS
            try:
                return self._shop.request(method, path, params, body, shop=False)
            except CreatorReauthRequired:
                raise
            except TikTokShopError as exc:
                if last or not _is_rate_limit(exc):
                    raise
                self._sleep(RATE_LIMIT_WAIT_SECONDS * attempt)
            except (httpx.TransportError, ValueError) as exc:  # ValueError: cuerpo que no es JSON (p. ej. un 429)
                if last:
                    raise TikTokCreatorError(f"TikTok Creator no respondió tras {MAX_ATTEMPTS} intentos: {type(exc).__name__}") from exc
                self._sleep(RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1))
        raise AssertionError("inalcanzable: el último intento siempre devuelve o lanza")

    def _pages(
        self, method: str, path: str, items_key: str, params: Row, body: Row | None, max_pages: int, pause: float
    ) -> Iterator[Page]:
        token = ""
        for number in range(max_pages):
            if number:
                self._sleep(pause)
            query = {**params, "page_size": CREATOR_PAGE_SIZE, **({"page_token": token} if token else {})}
            data = self._call(method, path, query, body)
            token = str(data.get("next_page_token") or "")
            yield Page(list(data.get(items_key) or []), _int_or_none(data.get("total_count")), bool(token))
            if not token:
                return

    # --- lecturas ---------------------------------------------------------------------------------------
    def profile(self) -> Row:
        return self._call("GET", PROFILE_PATH)

    def iter_open_collab_pages(
        self,
        *,
        sort_field: str | None = None,
        sort_order: str = "DESC",
        max_pages: int = MAX_CATALOG_PAGES,
        pause: float = PAGE_PAUSE_SECONDS,
    ) -> Iterator[Page]:
        """Productos de colaboración abierta. Sin `sort_field` TikTok ordena por tasa de comisión descendente."""
        params: Row = {}
        if sort_field is not None:
            if sort_field not in SORT_FIELDS or sort_order not in SORT_ORDERS:
                raise ValueError(f"Orden inválido: {sort_field} {sort_order}")
            params = {"sort_field": sort_field, "sort_order": sort_order}
        yield from self._pages("POST", OPEN_COLLAB_SEARCH_PATH, "products", params, {}, max_pages, pause)

    def iter_open_collab_products(self, **kwargs: Any) -> Iterator[Row]:
        for page in self.iter_open_collab_pages(**kwargs):
            yield from page.items

    def iter_showcase_pages(
        self, origin: str = "SHOWCASE", *, max_pages: int = MAX_SIMPLE_PAGES, pause: float = PAGE_PAUSE_SECONDS
    ) -> Iterator[Page]:
        """Productos de la vitrina propia (`origin` LIVE o SHOWCASE)."""
        if origin not in SHOWCASE_ORIGINS:
            raise ValueError(f"origin inválido: {origin}")
        yield from self._pages("GET", SHOWCASE_PATH, "products", {"origin": origin}, None, max_pages, pause)

    def iter_orders(self, *, max_pages: int = MAX_SIMPLE_PAGES, pause: float = PAGE_PAUSE_SECONDS) -> Iterator[Page]:
        """Pedidos de afiliado propios. El cuerpo va vacío: no se confirmó ningún filtro de fechas."""
        yield from self._pages("POST", ORDERS_SEARCH_PATH, "orders", {}, {}, max_pages, pause)


# --- constructores puros (sin red) -------------------------------------------------------------------------


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    """Dinero de TikTok llega como texto; "" o basura es "sin dato", no cero."""
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _dict(value: Any) -> Row:
    return value if isinstance(value, dict) else {}


def _plausible(value: Decimal | None) -> Decimal | None:
    """Descarta importes negativos o fuera de rango (datos de prueba que desbordarían la columna)."""
    if value is None or value < 0 or value > MAX_PLAUSIBLE_PRICE_USD:
        return None
    return value


def _price_range(price: Any) -> tuple[Decimal | None, Decimal | None, bool]:
    """(mínimo, máximo, ¿tenía un importe fuera de rango?) de un bloque `{minimum_amount, maximum_amount}`."""
    block = _dict(price)
    raw = (_decimal(block.get("minimum_amount")), _decimal(block.get("maximum_amount")))
    clean = tuple(_plausible(v) for v in raw)
    out_of_range = any(r is not None and c is None for r, c in zip(raw, clean, strict=True))
    return clean[0], clean[1], out_of_range


def _category_names(chains: Any) -> list[str]:
    return [name for c in (chains if isinstance(chains, list) else []) if (name := _text(_dict(c).get("local_name")))]


def build_profile(raw: Row, today: date) -> Row:
    """Foto diaria del perfil de creador (sin tokens; la foto de avatar es solo una URL)."""
    permissions = raw.get("permissions")
    return {
        "snapshot_date": today,
        "creator_user_id": _text(raw.get("creator_user_id")),
        "username": _text(raw.get("username")),
        "avatar_url": _text(_dict(raw.get("avatar")).get("url")),
        "user_type": _text(raw.get("user_type")),
        "seller_type": _text(raw.get("seller_type")),
        "register_region": _text(raw.get("register_region")),
        "selection_region": _text(raw.get("selection_region")),
        "permissions": sorted(str(p) for p in permissions) if isinstance(permissions, list) else [],
    }


def build_product(raw: Row) -> Row | None:
    """Producto del catálogo de afiliados normalizado. None si no trae id (no se podría guardar)."""
    product_id = _text(raw.get("id"))
    if product_id is None:
        return None
    original_min, original_max, price_bad = _price_range(raw.get("original_price"))
    sale_min, sale_max, sale_bad = _price_range(raw.get("sales_price"))
    commission = _dict(raw.get("commission"))
    amount = _decimal(commission.get("amount"))
    commission_amount = _plausible(amount)
    basis_points = _decimal(commission.get("rate"))
    units = max(_int_or_none(raw.get("units_sold")) or 0, 0)
    names = _category_names(raw.get("category_chains"))
    return {
        "product_id": product_id,
        "title": _text(raw.get("title")),
        "image_url": _text(raw.get("main_image_url")),
        "detail_link": _text(raw.get("detail_link")),
        "category": CATEGORY_SEPARATOR.join(names) or None,
        "category_leaf": names[-1] if names else None,
        "currency": _text(_dict(raw.get("original_price")).get("currency")) or _text(commission.get("currency")),
        "original_price_min": original_min,
        "original_price_max": original_max,
        "sale_price_min": sale_min,
        "sale_price_max": sale_max,
        "commission_rate": basis_points / BASIS_POINTS if basis_points is not None else None,
        "commission_amount": commission_amount,
        "units_sold": units,
        "has_inventory": bool(raw.get("has_inventory")),
        "shop_name": _text(_dict(raw.get("shop")).get("name")),
        "sale_region": _text(raw.get("sale_region")),
        "suspect": price_bad or sale_bad or (amount is not None and commission_amount is None) or units > MAX_PLAUSIBLE_UNITS,
    }


def build_history_row(product: Row, snapshot_date: date) -> Row:
    """Foto semanal de lo que cambia de un producto (precio, comisión, unidades)."""
    return {
        "product_id": product["product_id"],
        "snapshot_date": snapshot_date,
        "price": product["sale_price_min"] if product["sale_price_min"] is not None else product["original_price_min"],
        "commission_rate": product["commission_rate"],
        "commission_amount": product["commission_amount"],
        "units_sold": product["units_sold"],
    }


def build_showcase_product(raw: Row, origin: str) -> Row | None:
    """Producto de la vitrina propia con su estado de revisión e inventario."""
    product_id = _text(raw.get("id"))
    if product_id is None:
        return None
    price, status = _dict(raw.get("price")), _dict(raw.get("status"))
    original_min, original_max, _ = _price_range(price.get("original_price"))
    discount_min, discount_max, _ = _price_range(price.get("platform_discount_price"))
    images = raw.get("main_images")
    first_image = _dict(images[0]) if isinstance(images, list) and images else {}
    return {
        "product_id": product_id,
        "origin": origin,
        "title": _text(raw.get("title")),
        "image_url": _text(first_image.get("url")),
        "source": _text(raw.get("source")),
        "currency": _text(_dict(price.get("original_price")).get("currency")),
        "original_price_min": original_min,
        "original_price_max": original_max,
        "discount_price_min": discount_min,
        "discount_price_max": discount_max,
        "added_status": _text(status.get("added_status")),
        "inventory_status": _text(status.get("inventory_status")),
        "review_status": _text(status.get("review_status")),
        "is_hidden": bool(status.get("is_hidden")),
        "shop_name": _text(_dict(raw.get("shop")).get("name")),
    }


def build_order(raw: Row) -> Row | None:
    """Pedido de afiliado. ESTRUCTURA SIN VERIFICAR: la cuenta no tenía pedidos al explorar la API.

    Por eso se guarda el pedido crudo en `payload` y solo se extraen campos con nombre estándar de TikTok
    (`id`, `status`, `create_time`, `commission`); si alguno no viene queda en None en vez de inventarse.
    """
    order_id = _text(raw.get("id")) or _text(raw.get("order_id"))
    if order_id is None:
        return None
    commission = _dict(raw.get("commission"))
    created = _epoch(raw.get("create_time"))
    return {
        "order_id": order_id,
        "status": _text(raw.get("status")),
        "created_at": _to_datetime(created),
        "commission_amount": _plausible(_decimal(commission.get("amount"))),
        "currency": _text(commission.get("currency")),
        "payload": raw,
    }
