"""Conector del mercado de creadores y las colaboraciones del VENDEDOR de TikTok Shop (SOLO LECTURA).

Qué rutas existen y con qué versión (descubierto contra la API real; la versión es distinta por endpoint y
TikTok responde 36009004 "Invalid API version" a cualquier versión que ese endpoint no tenga, 40006 a una ruta
que no existe y otro error de validación a una versión válida con parámetros incompletos):

  ================================================  =========  ==============================================
  Ruta                                              Versión    Para qué sirve
  ================================================  =========  ==============================================
  POST /affiliate_seller/{v}/marketplace_creators/search          202508 (y 202608)  Buscar creadores del mercado
  GET  /affiliate_seller/{v}/marketplace_creators/{open_id}       202508 (y 202608)  Ficha de rendimiento del creador
  POST /affiliate_seller/{v}/open_collaborations/search           202412             Colaboraciones abiertas propias
  POST /affiliate_seller/{v}/target_collaborations/search         202508             Colaboraciones dirigidas propias
  ================================================  =========  ==============================================

Las versiones que el equipo había probado (202405-202410, 202412, 202501, 202502, 202506) no existen para
`marketplace_creators` ni para `target_collaborations`; `open_collaborations` solo existe en 202412.

Límites verificados:
  * Creadores: `page_size` SOLO 12 o 20 (otro valor da 36009004). Sin `total_count`; se pagina con
    `next_page_token` y no se encontró tope (40 páginas = 778 creadores distintos en una búsqueda).
  * Filtros de servidor confirmados en el cuerpo: `keyword`, `gmv_ranges` y `units_sold_ranges` (valores
    permitidos abajo). Los campos desconocidos se IGNORAN en silencio (no dan error), así que categoría y rango
    de seguidores NO se filtran en el servidor: se filtran al leer (la respuesta trae `category_ids` y seguidores).
  * Colaboraciones abiertas y dirigidas: páginas de hasta 20 y `total_count`. Las dirigidas EXIGEN
    `collaboration_status` en el cuerpo y solo se aceptó `ONGOING`.
  * Límite de tasa compartido (36009002 "Too many requests"): se reintenta con espera creciente.

Qué NO se consiguió: el filtro avanzado ("Get Creator Marketplace Advanced Search Filters", documentado solo
para 202608) no responde en ninguna de las rutas probadas; y la API no entrega el id numérico del creador ni
sus ventas por producto, solo `creator_open_id` (opaco), usuario y rangos o importes de GMV.

Datos PÚBLICOS únicamente: usuario, apodo, foto, seguidores, categorías, GMV/unidades reportados, región. Nunca
se leen ni se guardan datos privados. Ningún método puede crear nada: `assert_read_only` rechaza cualquier
petición que no sea GET o POST a `.../search`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.connectors.tiktok_creator import (
    Page,
    Row,
    _decimal,
    _dict,
    _epoch,
    _int_or_none,
    _text,
    _to_datetime,
)
from app.connectors.tiktok_shop import TikTokShopError

Sleep = Callable[[float], None]

# --- rutas y versiones (todas de solo lectura) -------------------------------------------------------------
CREATOR_VERSION = "202508"  # 202608 también existe: mismo contrato salvo `creator_level` anidado en `creator_profile`
OPEN_COLLAB_VERSION = "202412"
TARGET_COLLAB_VERSION = "202508"
CREATOR_SEARCH_PATH = f"/affiliate_seller/{CREATOR_VERSION}/marketplace_creators/search"
CREATOR_DETAIL_PATH = f"/affiliate_seller/{CREATOR_VERSION}/marketplace_creators"  # + "/{open_id}"
OPEN_COLLAB_SEARCH_PATH = f"/affiliate_seller/{OPEN_COLLAB_VERSION}/open_collaborations/search"
TARGET_COLLAB_SEARCH_PATH = f"/affiliate_seller/{TARGET_COLLAB_VERSION}/target_collaborations/search"
ROUTES_IN_USE: dict[str, dict[str, str]] = {
    "marketplace_creators_search": {"method": "POST", "path": CREATOR_SEARCH_PATH, "version": CREATOR_VERSION},
    "marketplace_creator_detail": {"method": "GET", "path": f"{CREATOR_DETAIL_PATH}/{{open_id}}", "version": CREATOR_VERSION},
    "open_collaborations_search": {"method": "POST", "path": OPEN_COLLAB_SEARCH_PATH, "version": OPEN_COLLAB_VERSION},
    "target_collaborations_search": {"method": "POST", "path": TARGET_COLLAB_SEARCH_PATH, "version": TARGET_COLLAB_VERSION},
}
SEARCH_SUFFIX = "/search"

# --- límites y filtros de la API ---------------------------------------------------------------------------
CREATOR_PAGE_SIZE = 20  # TikTok solo acepta 12 o 20
COLLAB_PAGE_SIZE = 20
MAX_PAGES_DEFAULT = 50  # tope de seguridad por consulta (1.000 creadores)
GMV_RANGES = frozenset({"GMV_RANGE_0_100", "GMV_RANGE_100_1000", "GMV_RANGE_1000_10000", "GMV_RANGE_10000_AND_ABOVE"})
UNITS_SOLD_RANGES = frozenset(
    {"UNITS_SOLD_RANGE_0_10", "UNITS_SOLD_RANGE_10_100", "UNITS_SOLD_RANGE_100_1000", "UNITS_SOLD_RANGE_1000_AND_ABOVE"}
)
TARGET_STATUSES = frozenset({"ONGOING"})  # los demás valores probados dan 98001004 "invalid params"
KEYWORD_MAX_LENGTH = 80

# --- ritmo y reintentos ------------------------------------------------------------------------------------
PAGE_PAUSE_SECONDS = 0.3
MAX_ATTEMPTS = 4
RETRY_BACKOFF_SECONDS = 2.0  # 2, 4, 8 s ante fallos de red
RATE_LIMIT_WAIT_SECONDS = 15.0  # se multiplica por el número de intento
RATE_LIMIT_CODES = frozenset({"36009002"})
RATE_LIMIT_MARKERS = ("rate limit", "too many", "throttl")

# --- datos dudosos -----------------------------------------------------------------------------------------
MAX_PLAUSIBLE_GMV_USD = Decimal(1_000_000_000)  # más que esto desbordaría numeric(14,4) y no es creíble
BASIS_POINTS = Decimal(10_000)  # comisión y tasas de TikTok vienen en puntos base (1450 = 14,5 %)


class ReadOnlyViolation(RuntimeError):
    """Se intentó una petición que podría crear o modificar algo en TikTok."""


class TikTokCreatorMarketError(RuntimeError):
    """Error de red o de la API del mercado de creadores. Nunca incluye tokens ni secretos."""


class ShopRequester(Protocol):
    """Lo único que se necesita de `TikTokShopClient` (permite un cliente falso en las pruebas)."""

    def request(
        self, method: str, path: str, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None, *, shop: bool = True
    ) -> dict[str, Any]: ...


def assert_read_only(method: str, path: str) -> None:
    """Solo GET y los POST cuyo nombre termina en `/search` (consultas). Todo lo demás es una escritura potencial."""
    if method == "GET" or (method == "POST" and path.endswith(SEARCH_SUFFIX)):
        return
    raise ReadOnlyViolation(f"Petición no permitida (solo lectura): {method} {path}")


def _is_rate_limit(exc: TikTokShopError) -> bool:
    return str(exc.code) in RATE_LIMIT_CODES or any(marker in str(exc).lower() for marker in RATE_LIMIT_MARKERS)


# --- cliente de solo lectura -------------------------------------------------------------------------------


class CreatorMarketClient:
    """Lecturas del mercado de creadores y de las colaboraciones. Pagina con reintentos y pausa entre páginas."""

    def __init__(self, shop: ShopRequester, *, sleep: Sleep = time.sleep) -> None:
        self._shop = shop
        self._sleep = sleep

    def _call(self, method: str, path: str, params: Row | None = None, body: Row | None = None) -> Row:
        """Una petición con reintentos: red caída (con retroceso) y límite de tasa (espera mayor)."""
        assert_read_only(method, path)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            last = attempt == MAX_ATTEMPTS
            try:
                return self._shop.request(method, path, params, body)
            except TikTokShopError as exc:
                if last or not _is_rate_limit(exc):
                    raise
                self._sleep(RATE_LIMIT_WAIT_SECONDS * attempt)
            except (httpx.TransportError, ValueError) as exc:  # ValueError: cuerpo que no es JSON (p. ej. un 429)
                if last:
                    raise TikTokCreatorMarketError(
                        f"TikTok no respondió tras {MAX_ATTEMPTS} intentos: {type(exc).__name__}"
                    ) from exc
                self._sleep(RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1))
        raise AssertionError("inalcanzable: el último intento siempre devuelve o lanza")

    def _pages(
        self, path: str, items_key: str, page_size: int, body: Row, max_pages: int, pause: float
    ) -> Iterator[Page]:
        token = ""
        for number in range(max_pages):
            if number:
                self._sleep(pause)
            params: Row = {"page_size": page_size, **({"page_token": token} if token else {})}
            data = self._call("POST", path, params, body)
            token = str(data.get("next_page_token") or "")
            yield Page(list(data.get(items_key) or []), _int_or_none(data.get("total_count")), bool(token))
            if not token:
                return

    # --- lecturas ---------------------------------------------------------------------------------------
    def iter_creator_pages(
        self,
        *,
        keyword: str | None = None,
        gmv_ranges: tuple[str, ...] = (),
        units_sold_ranges: tuple[str, ...] = (),
        max_pages: int = MAX_PAGES_DEFAULT,
        pause: float = PAGE_PAUSE_SECONDS,
    ) -> Iterator[Page]:
        """Creadores del mercado. Sin filtros TikTok devuelve su orden por defecto; `keyword` busca por usuario/apodo."""
        yield from self._pages(
            CREATOR_SEARCH_PATH, "creators", CREATOR_PAGE_SIZE,
            creator_search_body(keyword, gmv_ranges, units_sold_ranges), max_pages, pause,
        )

    def creator_detail(self, open_id: str) -> Row:
        """Ficha de rendimiento de un creador (engagement, GPM, colaboraciones con marcas). Vacío si no hay."""
        safe = quote(open_id, safe="")
        if not open_id or safe != open_id:  # la firma cubre la ruta: un id con caracteres escapables no se firmaría igual
            raise ValueError("creator_open_id inválido")
        return _dict(self._call("GET", f"{CREATOR_DETAIL_PATH}/{safe}").get("creator"))

    def iter_open_collaboration_pages(
        self, *, max_pages: int = MAX_PAGES_DEFAULT, pause: float = PAGE_PAUSE_SECONDS
    ) -> Iterator[Page]:
        yield from self._pages(OPEN_COLLAB_SEARCH_PATH, "open_collaborations", COLLAB_PAGE_SIZE, {}, max_pages, pause)

    def iter_target_collaboration_pages(
        self, status: str = "ONGOING", *, max_pages: int = MAX_PAGES_DEFAULT, pause: float = PAGE_PAUSE_SECONDS
    ) -> Iterator[Page]:
        if status not in TARGET_STATUSES:
            raise ValueError(f"collaboration_status inválido: {status}")
        yield from self._pages(
            TARGET_COLLAB_SEARCH_PATH, "target_collaborations", COLLAB_PAGE_SIZE,
            {"collaboration_status": status}, max_pages, pause,
        )


def creator_search_body(keyword: str | None, gmv_ranges: tuple[str, ...], units_sold_ranges: tuple[str, ...]) -> Row:
    """Cuerpo de la búsqueda con SOLO los filtros que el servidor honra, validados antes de enviar."""
    body: Row = {}
    if keyword and keyword.strip():
        body["keyword"] = keyword.strip()[:KEYWORD_MAX_LENGTH]
    if gmv_ranges:
        if not set(gmv_ranges) <= GMV_RANGES:
            raise ValueError(f"gmv_ranges inválido: {gmv_ranges}")
        body["gmv_ranges"] = list(gmv_ranges)
    if units_sold_ranges:
        if not set(units_sold_ranges) <= UNITS_SOLD_RANGES:
            raise ValueError(f"units_sold_ranges inválido: {units_sold_ranges}")
        body["units_sold_ranges"] = list(units_sold_ranges)
    return body


# --- constructores puros (sin red) -------------------------------------------------------------------------


def _money(value: Any) -> Decimal | None:
    """Importe de TikTok (texto) o None si falta o es imposible (no se guarda dinero que desbordaría la columna)."""
    amount = _decimal(value)
    if amount is None or amount < 0 or amount > MAX_PLAUSIBLE_GMV_USD:
        return None
    return amount


def _amount(block: Any) -> Decimal | None:
    return _money(_dict(block).get("amount"))


def _rate(value: Any) -> Decimal | None:
    """Tasa en puntos base (texto o entero) a fracción: 1450 -> 0.1450."""
    number = _decimal(value)
    return number / BASIS_POINTS if number is not None and number >= 0 else None


def _category_ids(value: Any) -> list[str]:
    return [text for item in (value if isinstance(value, list) else []) if (text := _text(item))]


def build_creator(raw: Row) -> Row | None:
    """Creador del mercado normalizado (solo datos públicos). None si no trae `creator_open_id`."""
    open_id = _text(raw.get("creator_open_id"))
    if open_id is None:
        return None
    gmv_range = _dict(raw.get("gmv_range"))
    units_range = _dict(raw.get("units_sold_range"))
    profile = _dict(raw.get("creator_profile"))  # 202608 anida el nivel aquí; 202508 lo trae arriba
    return {
        "creator_open_id": open_id,
        "username": _text(raw.get("username")),
        "nickname": _text(raw.get("nickname")),
        "avatar_url": _text(_dict(raw.get("avatar")).get("url")),
        "region": _text(raw.get("selection_region")),
        "follower_count": max(_int_or_none(raw.get("follower_count")) or 0, 0),
        "creator_level": _int_or_none(raw.get("creator_level") if raw.get("creator_level") is not None else profile.get("creator_level")),
        "category_ids": _category_ids(raw.get("category_ids")),
        "gmv_amount": _amount(raw.get("gmv")),
        "video_gmv_amount": _amount(raw.get("video_gmv")),
        "gmv_range": _text(gmv_range.get("formatted_range")),
        "gmv_range_min": _money(gmv_range.get("minimum_amount")),
        "units_sold_range": _text(units_range.get("formatted_range")),
        "units_sold_min": _int_or_none(units_range.get("minimum_amount")),
        "avg_video_views": _int_or_none(raw.get("avg_ec_video_view_count")),
        "avg_live_viewers": _int_or_none(raw.get("avg_ec_live_uv")),
        "demographics": _dict(raw.get("top_follower_demographics")) or None,
        "currency": _text(_dict(raw.get("gmv")).get("currency")) or _text(gmv_range.get("currency")),
    }


def build_creator_detail(raw: Row) -> Row:
    """Campos de la ficha de rendimiento que enriquecen al creador. Lo que no viene queda en None (no se inventa).

    `engagement_rate` se asume en puntos base como las demás tasas de TikTok (valores observados: 36, 89, 94);
    la unidad no está documentada, por eso el puntaje le da poco peso.
    """
    return {
        "engagement_rate": _rate(raw.get("ec_video_engagement_rate")),
        "live_engagement_rate": _rate(raw.get("ec_live_engagement_rate")),
        "gpm_amount": _amount(raw.get("gpm")),
        "avg_gmv_per_buyer": _amount(raw.get("avg_gmv_per_buyer")),
        "avg_commission_rate": _rate(raw.get("avg_commission_rate")),
        "rating": _decimal(raw.get("rating")),
        "promoted_product_num": _int_or_none(raw.get("promoted_product_num")),
        "brand_collaboration_count": _int_or_none(raw.get("brand_collaboration_count")),
        "units_sold_total": _int_or_none(raw.get("units_sold")),
    }


def build_snapshot_row(creator: Row, week_start: date) -> Row:
    """Foto semanal de lo que cambia de un creador (seguidores y GMV): sirve para ver su crecimiento."""
    return {
        "creator_open_id": creator["creator_open_id"],
        "week_start": week_start,
        "follower_count": creator["follower_count"],
        "gmv_amount": creator["gmv_amount"],
        "gmv_range_min": creator["gmv_range_min"],
    }


def build_open_collaboration(raw: Row) -> Row | None:
    """Colaboración abierta (la comisión que ofrecemos por un producto). None si no trae id."""
    collab_id = _text(raw.get("id"))
    if collab_id is None:
        return None
    product, commission = _dict(raw.get("product")), _dict(raw.get("current_commission"))
    price = _dict(product.get("original_price"))
    return {
        "collaboration_id": collab_id,
        "product_id": _text(product.get("id")),
        "product_title": _text(product.get("title")),
        "image_url": _text(product.get("main_image_url")),
        "currency": _text(price.get("currency")),
        "price_min": _money(price.get("minimum_amount")),
        "price_max": _money(price.get("maximum_amount")),
        "inventory": _int_or_none(product.get("inventory")),
        "product_status": _text(product.get("status")),
        "status": _text(raw.get("status")),
        "commission_rate": _rate(commission.get("rate")),
        "commission_start": _to_datetime(_epoch(commission.get("start_time"))),
        "commission_end": _to_datetime(_epoch(commission.get("end_time"))),
        "creator_count": _int_or_none(raw.get("content_creator_count")),
        "showcase_creator_count": _int_or_none(raw.get("showcase_creator_count")),
    }


def build_target_collaboration(raw: Row, status: str) -> Row | None:
    """Colaboración dirigida (invitación a creadores concretos). `status` es el filtro con que se consultó.

    No se guarda `message` (texto libre de la invitación): no hace falta para el análisis.
    """
    collab_id = _text(raw.get("id"))
    if collab_id is None:
        return None
    sample = _dict(raw.get("free_sample_rule"))
    return {
        "collaboration_id": collab_id,
        "name": _text(raw.get("name")),
        "kind": _text(raw.get("type")),
        "status": status,
        "product_count": _int_or_none(raw.get("product_count")),
        "creator_count": _int_or_none(raw.get("content_creator_count")),
        "invited_count": _int_or_none(raw.get("creator_inivited_count")),  # sic: así lo escribe TikTok
        "showcase_creator_count": _int_or_none(raw.get("showcase_creator_count")),
        "start_time": _to_datetime(_epoch(raw.get("start_time"))),
        "end_time": _to_datetime(_epoch(raw.get("end_time"))),
        "created_time": _to_datetime(_epoch(raw.get("create_time"))),
        "updated_time": _to_datetime(_epoch(raw.get("update_time"))),
        "has_free_sample": bool(sample.get("has_free_sample")),
        "sample_approval_exempt": bool(sample.get("is_sample_approval_exempt")),
    }
