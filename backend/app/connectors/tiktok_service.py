"""Conector de SOLO LECTURA del servicio al cliente de TikTok Shop, con privacidad por diseño.

Los mensajes de servicio al cliente traen texto de compradores. Este módulo existe para que ese texto NUNCA
salga de aquí: todo lo que devuelve son cifras y metadatos.

Reglas de privacidad (las verifica `tests/test_tiktok_service.py`):
  * No se conserva el TEXTO de ningún mensaje, ni nombres, avatares, `user_id`/`im_user_id` ni participantes.
    Los `participants[]` y el `sender` de la API (nickname, avatar, ids) no se leen jamás; del remitente solo
    se toma el campo `role`.
  * La CATEGORÍA (envío, devolución, producto...) se deriva del texto del comprador con palabras clave, EN
    MEMORIA, dentro de `build_conversation_row`; el texto muere al salir de esa función.
  * Los `fetch_*` que tocan mensajes reducen cada conversación a su fila antes de devolver nada: el texto crudo
    no sale de `fetch_conversation_row`.
  * Los errores y logs llevan solo el tipo de excepción y el código de TikTok, nunca cuerpos de respuesta.

Solo GET. Nunca se envían mensajes, ni se cierran chats, ni se cambia la configuración de agentes.

Límites de la API (medidos): conversaciones <= 20 por página, mensajes <= 10 por página (los más nuevos primero),
y el listado de conversaciones devuelve a lo sumo ~1000 (el orden es por actividad, no por fecha de creación).
`/customer_service/202407/performance` entrega métricas oficiales agregadas (sin personas) con ventana <= 90 días.

Importado por `app.tiktok_service_ops` (la sincronización) y las pruebas.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Literal, TypeVar
from zoneinfo import ZoneInfo

import httpx

from app.connectors.tiktok_shop import TikTokShopClient, TikTokShopError

log = logging.getLogger("chk.tiktok_service")

CONVERSATIONS_PATH: Final = "/customer_service/202309/conversations"
MESSAGES_PATH: Final = "/customer_service/202309/conversations/{id}/messages"
AGENT_SETTINGS_PATH: Final = "/customer_service/202309/agents/settings"
PERFORMANCE_PATH: Final = "/customer_service/202407/performance"  # la única que vive en otra versión

CONVERSATION_PAGE_SIZE: Final = 20  # máximo que acepta la API (36009004 si se pide más)
MESSAGE_PAGE_SIZE: Final = 10  # idem, para mensajes
MAX_CONVERSATION_PAGES: Final = 100  # tope duro contra un cursor que nunca termina (el listado real llega a ~51)
MAX_MESSAGE_PAGES: Final = 5  # hasta 50 mensajes por conversación; si hay más, la fila queda marcada como incompleta
PAUSE_SECONDS: Final = 0.25  # pausa entre llamadas: respeta el límite de la API y no castiga al vendedor
MAX_ATTEMPTS: Final = 3
BACKOFF_SECONDS: Final = 1.0  # espera = BACKOFF_SECONDS * intento
TRANSIENT_HINTS: Final = ("rate", "too many", "timeout", "busy", "try again")  # mensajes de TikTok que sí reintentan

# Cuándo una conversación deja de esperar a alguien y se da por cerrada.
CLOSE_AFTER_DAYS: Final = 14

Role = Literal["buyer", "seller", "bot", "system"]
Status = Literal["waiting_seller", "waiting_buyer", "closed"]

# Rol de TikTok -> rol propio. CUSTOMER_SERVICE es un agente humano de la tienda, igual que SHOP.
ROLE_MAP: Final[dict[str, Role]] = {
    "BUYER": "buyer",
    "SHOP": "seller",
    "CUSTOMER_SERVICE": "seller",
    "ROBOT": "bot",
    "SYSTEM": "system",
}
UNKNOWN_ROLE: Final[Role] = "system"  # un rol nuevo nunca se cuenta como comprador ni como vendedor

# Tipos de mensaje que TikTok documenta; cualquier otro se agrupa en "OTHER" para no guardar valores libres.
MESSAGE_TYPES: Final = frozenset(
    {"TEXT", "IMAGE", "VIDEO", "EMOJI", "ORDER_CARD", "PRODUCT_CARD", "LOGISTICS_CARD", "NOTIFICATION", "COUPON_CARD", "OTHER"}
)
TEXT_TYPE: Final = "TEXT"
OTHER_TYPE: Final = "OTHER"

# --- categorías (palabras clave en español e inglés; editables) --------------------------------------
CATEGORY_OTHER: Final = "other"
# El orden es el de desempate cuando dos categorías puntúan lo mismo: lo más urgente primero.
CATEGORY_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "complaint": (
        "queja", "reclamo", "pésimo", "pesimo", "inaceptable", "estafa", "fraude", "molesto", "enojado", "decepcion",
        "complaint", "terrible", "unacceptable", "scam", "fraud", "angry", "disappointed", "worst", "ridiculous", "furious",
    ),
    "return_refund": (
        "devolución", "devolucion", "devolver", "reembolso", "reintegro", "cambio", "cancelar", "cancelación",
        "return", "refund", "exchange", "cancel", "money back", "send back", "rma",
    ),
    "shipping_tracking": (
        "envío", "envio", "seguimiento", "rastreo", "paquete", "entrega", "llegado", "transportista", "guía", "guia",
        "shipping", "tracking", "package", "delivery", "delivered", "carrier", "arrive", "where is my", "shipped",
    ),
    "order_status": (
        "pedido", "orden", "estado de mi", "confirmación", "confirmacion", "factura", "pago", "dirección", "direccion",
        "order", "status", "confirmation", "invoice", "payment", "address", "receipt", "charged",
    ),
    "product_question": (
        "talla", "tamaño", "tamano", "medidas", "color", "material", "disponible", "stock", "funciona", "compatible",
        "size", "dimension", "available", "in stock", "does it", "works with", "how to use", "ingredients", "warranty",
    ),
}
CATEGORIES: Final = (*CATEGORY_KEYWORDS, CATEGORY_OTHER)

SECONDS_PER_MINUTE: Final = 60
PERFORMANCE_MAX_WINDOW_DAYS: Final = 90  # TikTok rechaza ventanas de 180 días ("invalid time range")

T = TypeVar("T")
Row = dict[str, Any]


# --- utilidades puras --------------------------------------------------------------------------------


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> datetime | None:
    """Epoch en segundos -> datetime UTC (None si falta o es 0)."""
    number = _int(value)
    if not number:
        return None
    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _opt_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize_role(raw: Any) -> Role:
    """Rol de TikTok -> comprador / vendedor / bot / sistema. Lo desconocido cuenta como sistema."""
    return ROLE_MAP.get(str(raw or "").upper(), UNKNOWN_ROLE)


def normalize_type(raw: Any) -> str:
    """Tipo de mensaje acotado al catálogo conocido (nada de texto libre en la base)."""
    kind = str(raw or "").upper()
    return kind if kind in MESSAGE_TYPES else OTHER_TYPE


def classify_text(text: str) -> str:
    """Categoría por palabras clave. Pura: recibe el texto, devuelve solo el nombre de la categoría.

    Gana la categoría con más coincidencias; el empate lo decide el orden de `CATEGORY_KEYWORDS` (lo más
    urgente primero). Sin ninguna coincidencia -> "other".
    """
    haystack = text.casefold()
    scores = {name: sum(1 for word in words if word in haystack) for name, words in CATEGORY_KEYWORDS.items()}
    best = max(scores.values(), default=0)
    if best == 0:
        return CATEGORY_OTHER
    return next(name for name, score in scores.items() if score == best)


# --- reducción de una conversación a una fila sin datos personales -----------------------------------


@dataclass(frozen=True, slots=True)
class ListedConversation:
    """Lo único que se conserva de un elemento del listado: sin participantes ni contenido del último mensaje."""

    conversation_id: str
    created_at: datetime
    last_message_at: datetime | None
    last_sender_role: Role | None
    last_message_type: str | None
    unread_count: int
    can_send_message: bool

    @property
    def last_activity_at(self) -> datetime:
        return max(self.created_at, self.last_message_at or self.created_at)


def reduce_listed(raw: Row) -> ListedConversation | None:
    """Elemento del listado -> `ListedConversation`. Devuelve None si no trae id o fecha de creación."""
    conversation_id = str(raw.get("id") or "").strip()
    created = _ts(raw.get("create_time"))
    if not conversation_id or created is None:
        return None
    latest = _dict(raw.get("latest_message"))
    has_latest = bool(latest)
    return ListedConversation(
        conversation_id=conversation_id,
        created_at=created,
        last_message_at=_ts(latest.get("create_time")),
        last_sender_role=normalize_role(_dict(latest.get("sender")).get("role")) if has_latest else None,
        last_message_type=normalize_type(latest.get("type")) if has_latest else None,
        unread_count=max(_int(raw.get("unread_count")) or 0, 0),
        can_send_message=bool(raw.get("can_send_message")),
    )


def _message_order(message: Row) -> tuple[int, int]:
    """Orden cronológico estable: por fecha y, a igual fecha, por el índice de la conversación."""
    return _int(message.get("create_time")) or 0, _int(message.get("index")) or 0


def _local_day(moment: datetime, tz: ZoneInfo) -> str:
    return moment.astimezone(tz).date().isoformat()


def derive_status(
    last_buyer: datetime | None, last_seller: datetime | None, last_activity: datetime, now: datetime
) -> Status:
    """Estado derivado: ¿a quién le toca? Solo cuentan comprador y vendedor humano (el bot no resuelve dudas).

    * waiting_seller: el comprador escribió después de la última respuesta del vendedor (o el vendedor no ha
      respondido nunca). Sigue así por viejo que sea: es justamente lo que el vendedor debe atender.
    * waiting_buyer: la última palabra fue del vendedor y hubo actividad en los últimos CLOSE_AFTER_DAYS.
    * closed: nunca hubo conversación humana, o la pelota lleva más de CLOSE_AFTER_DAYS en el comprador.
    """
    if last_buyer is not None and (last_seller is None or last_buyer > last_seller):
        return "waiting_seller"
    if last_seller is not None and now - last_activity <= timedelta(days=CLOSE_AFTER_DAYS):
        return "waiting_buyer"
    return "closed"


@dataclass(slots=True)
class _Tally:
    """Acumulador interno de una conversación (solo cifras)."""

    roles: Counter[str]
    types: Counter[str]
    per_day: dict[str, dict[str, int]]
    buyer_times: list[datetime]
    seller_times: list[datetime]
    buyer_text_parts: list[str]


def _tally_messages(messages: list[Row], tz: ZoneInfo) -> _Tally:
    tally = _Tally(Counter(), Counter(), defaultdict(lambda: {"b": 0, "s": 0}), [], [], [])
    for message in messages:
        moment = _ts(message.get("create_time"))
        if moment is None or message.get("is_visible") is False:
            continue  # los mensajes ocultos no los ve nadie: no cuentan como atención
        role = normalize_role(_dict(message.get("sender")).get("role"))
        kind = normalize_type(message.get("type"))
        tally.roles[role] += 1
        tally.types[kind] += 1
        day = _local_day(moment, tz)
        if role == "buyer":
            tally.buyer_times.append(moment)
            tally.per_day[day]["b"] += 1
            if kind == TEXT_TYPE:
                tally.buyer_text_parts.append(str(message.get("content") or ""))  # vive solo hasta clasificar
        elif role == "seller":
            tally.seller_times.append(moment)
            tally.per_day[day]["s"] += 1
    return tally


def build_conversation_row(
    listed: ListedConversation, messages: list[Row], now: datetime, tz: ZoneInfo, *, complete: bool = True
) -> Row:
    """Fila de una conversación: SOLO cifras, fechas, roles, tipos, categoría y estado. Pura.

    `messages` es la respuesta cruda de la API (con texto); aquí se cuenta, se clasifica y se descarta. La fila
    resultante no contiene ningún campo de texto libre: `category` es un valor de `CATEGORIES`.

    Primera respuesta = segundos desde el primer mensaje del comprador hasta la primera respuesta HUMANA del
    vendedor posterior (el bot automático no cuenta). `waiting_since` = el mensaje más viejo del comprador que
    sigue sin respuesta humana.
    """
    ordered = sorted(messages, key=_message_order)
    tally = _tally_messages(ordered, tz)
    category = classify_text("\n".join(tally.buyer_text_parts)) if tally.buyer_text_parts else CATEGORY_OTHER

    first_buyer = min(tally.buyer_times, default=None)
    last_buyer = max(tally.buyer_times, default=None)
    last_seller = max(tally.seller_times, default=None)
    first_reply = (
        next((t for t in sorted(tally.seller_times) if t >= first_buyer), None) if first_buyer is not None else None
    )
    unanswered = [t for t in tally.buyer_times if last_seller is None or t > last_seller]
    last_message_at = listed.last_message_at or max([*tally.buyer_times, *tally.seller_times], default=listed.created_at)
    status = derive_status(last_buyer, last_seller, max(listed.last_activity_at, last_message_at), now)

    return {
        "conversation_id": listed.conversation_id,
        "created_at": listed.created_at,
        "last_message_at": last_message_at,
        "last_sender_role": listed.last_sender_role,
        "last_message_type": listed.last_message_type,
        "unread_count": listed.unread_count,
        "can_send_message": listed.can_send_message,
        "message_count": sum(tally.roles.values()),
        "buyer_messages": tally.roles["buyer"],
        "seller_messages": tally.roles["seller"],
        "bot_messages": tally.roles["bot"],
        "system_messages": tally.roles["system"],
        "type_counts": dict(tally.types),
        "day_counts": {day: dict(counts) for day, counts in sorted(tally.per_day.items())},
        "category": category,
        "first_buyer_at": first_buyer,
        "first_response_seconds": int((first_reply - first_buyer).total_seconds())
        if first_reply is not None and first_buyer is not None
        else None,
        "waiting_since": min(unanswered) if status == "waiting_seller" and unanswered else None,
        "status": status,
        "messages_complete": complete,
    }


def build_performance_row(day: date, performance: Row) -> Row:
    """Métricas oficiales agregadas de TikTok para un día (no hay personas en ellas)."""
    response_minutes = _opt_float(performance.get("response_time_mins"))
    return {
        "day": day,
        # Un día sin datos devuelve un objeto vacío: 0 sesiones (y no None) para que no se vuelva a consultar.
        "official_sessions": _int(performance.get("support_session_count")) or 0,
        "official_response_percentage": _opt_float(performance.get("response_percentage")),
        "official_response_seconds": round(response_minutes * SECONDS_PER_MINUTE) if response_minutes is not None else None,
        "official_satisfaction_percentage": _opt_float(performance.get("satisfaction_percentage")),
        "official_conversion_percentage": _opt_float(performance.get("conversion_rate")),
        "official_guided_gmv": _opt_float(performance.get("cs_guided_gmv")),
        "official_currency": str(performance.get("currency") or "") or None,
    }


# --- red: llamadas con reintentos y pausa ------------------------------------------------------------


def _is_transient(exc: Exception) -> bool:
    """¿Vale la pena reintentar? Errores de red y mensajes de límite de uso; un error de validación, no."""
    if isinstance(exc, httpx.HTTPError | ValueError):
        return True
    return isinstance(exc, TikTokShopError) and any(hint in str(exc).lower() for hint in TRANSIENT_HINTS)


def _describe(exc: Exception) -> str:
    """Descripción segura para logs y errores: tipo y código, nunca el cuerpo de la respuesta."""
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__}{f' ({code})' if code is not None else ''}"


def call_with_retries(
    client: TikTokShopClient,
    path: str,
    params: dict[str, Any],
    *,
    sleep: Callable[[float], None] | None = None,
) -> Row:
    """GET con reintentos ante fallos transitorios y una pausa fija tras cada llamada exitosa.

    `sleep` se resuelve en cada llamada (no como valor por defecto) para que las pruebas puedan sustituir la pausa.
    """
    pause = sleep or time.sleep
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = client.request("GET", path, params)
        except (TikTokShopError, httpx.HTTPError, ValueError) as exc:
            if attempt == MAX_ATTEMPTS or not _is_transient(exc):
                log.warning("Servicio al cliente: llamada falló (%s)", _describe(exc))
                raise
            pause(BACKOFF_SECONDS * attempt)
        else:
            pause(PAUSE_SECONDS)
            return data
    raise AssertionError("inalcanzable")  # pragma: no cover - el bucle siempre retorna o relanza


def _pages(
    client: TikTokShopClient, path: str, key: str, page_size: int, max_pages: int, sleep: Callable[[float], None] | None
) -> Iterator[Row]:
    """Recorre `next_page_token` hasta agotarlo o llegar al tope de páginas."""
    token = ""
    for _ in range(max_pages):
        params: dict[str, Any] = {"page_size": page_size, **({"page_token": token} if token else {})}
        data = call_with_retries(client, path, params, sleep=sleep)
        yield from data.get(key) or []
        token = data.get("next_page_token") or ""
        if not token:
            return


def list_conversations(
    client: TikTokShopClient, since: datetime, *, sleep: Callable[[float], None] | None = None
) -> list[ListedConversation]:
    """Todas las conversaciones con actividad desde `since`, ya reducidas (participantes y contenido descartados)."""
    listed = (reduce_listed(raw) for raw in _pages(client, CONVERSATIONS_PATH, "conversations", CONVERSATION_PAGE_SIZE, MAX_CONVERSATION_PAGES, sleep))
    return [item for item in listed if item is not None and item.last_activity_at >= since]


def fetch_conversation_row(
    client: TikTokShopClient,
    listed: ListedConversation,
    now: datetime,
    tz: ZoneInfo,
    *,
    sleep: Callable[[float], None] | None = None,
) -> Row:
    """Pide los mensajes (más nuevos primero) y los reduce a la fila. El texto no sale de esta función."""
    path = MESSAGES_PATH.format(id=listed.conversation_id)
    messages: list[Row] = []
    token = ""
    complete = True
    for page in range(MAX_MESSAGE_PAGES):
        params: dict[str, Any] = {"page_size": MESSAGE_PAGE_SIZE, **({"page_token": token} if token else {})}
        data = call_with_retries(client, path, params, sleep=sleep)
        messages.extend(data.get("messages") or [])
        token = data.get("next_page_token") or ""
        if not token:
            break
        complete = page + 1 < MAX_MESSAGE_PAGES  # quedó cursor pendiente tras el último permitido: falta historia
    return build_conversation_row(listed, messages, now, tz, complete=complete)


def fetch_rows(
    client: TikTokShopClient,
    pending: Iterable[ListedConversation],
    now: datetime,
    tz: ZoneInfo,
    *,
    sleep: Callable[[float], None] | None = None,
) -> tuple[list[Row], list[str]]:
    """Filas de varias conversaciones. Una que falla se anota (solo tipo y código) y no frena a las demás."""
    rows: list[Row] = []
    problems: list[str] = []
    for item in pending:
        try:
            rows.append(fetch_conversation_row(client, item, now, tz, sleep=sleep))
        except (TikTokShopError, httpx.HTTPError, ValueError) as exc:
            problems.append(f"mensajes: {_describe(exc)}")
    return rows, problems


def fetch_agent_can_accept_chat(client: TikTokShopClient, *, sleep: Callable[[float], None] | None = None) -> bool | None:
    """¿Los agentes aceptan chats? (solo lectura). None si la API no lo informa."""
    data = call_with_retries(client, AGENT_SETTINGS_PATH, {}, sleep=sleep)
    value = data.get("can_accept_chat")
    return bool(value) if value is not None else None


def fetch_performance_rows(
    client: TikTokShopClient, days: Iterable[date], *, sleep: Callable[[float], None] | None = None
) -> tuple[list[Row], list[str]]:
    """Métricas oficiales de TikTok, una llamada por día (ventana [día, día+1)). Un día que falla no frena a los demás."""
    rows: list[Row] = []
    problems: list[str] = []
    for day in days:
        params = {"support_date_ge": day.isoformat(), "support_date_lt": (day + timedelta(days=1)).isoformat()}
        try:
            data = call_with_retries(client, PERFORMANCE_PATH, params, sleep=sleep)
        except (TikTokShopError, httpx.HTTPError, ValueError) as exc:
            problems.append(f"rendimiento: {_describe(exc)}")
            continue
        rows.append(build_performance_row(day, _dict(data.get("performance"))))
    return rows, problems


# --- selección incremental (pura) --------------------------------------------------------------------


KnownState = dict[str, tuple[datetime | None, str]]  # id -> (último mensaje guardado, estado guardado)


def partition_pending(
    listed: list[ListedConversation], known: KnownState
) -> tuple[list[ListedConversation], list[ListedConversation]]:
    """(nuevas o cambiadas, abiertas sin cambios), cada grupo con lo más reciente primero.

    Una conversación vuelve a pedir mensajes si es nueva, si su último mensaje cambió, o si no está cerrada
    (su estado depende del reloj: "esperando al comprador" termina cerrándose sin que llegue nada nuevo).
    """

    def changed(item: ListedConversation) -> bool:
        stored = known.get(item.conversation_id)
        return stored is None or _aware(stored[0]) != item.last_message_at

    def is_open(item: ListedConversation) -> bool:
        stored = known.get(item.conversation_id)
        return stored is not None and stored[1] != "closed"

    def newest_first(items: Iterable[ListedConversation]) -> list[ListedConversation]:
        return sorted(items, key=lambda i: i.last_activity_at, reverse=True)

    fresh = newest_first(i for i in listed if changed(i))
    still_open = newest_first(i for i in listed if not changed(i) and is_open(i))
    return fresh, still_open


def select_pending(listed: list[ListedConversation], known: KnownState, limit: int) -> list[ListedConversation]:
    """Qué conversaciones vuelven a pedir mensajes en esta corrida, con tope `limit`.

    Primero las nuevas o cambiadas (la carga inicial deja lo reciente listo pronto) y después las abiertas.
    """
    fresh, still_open = partition_pending(listed, known)
    return [*fresh, *still_open][:limit]


def _aware(value: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona: se asume UTC para poder comparar."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value

