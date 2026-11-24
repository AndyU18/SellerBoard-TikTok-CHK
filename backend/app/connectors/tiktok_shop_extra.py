"""Conector de solo lectura de TikTok Shop para lo que `tiktok_shop.py` no consume.

Recursos: catálogo (con la URL de la imagen principal), rendimiento por producto y por SKU, actividades de
promoción y sus productos, cupones, paquetes de fulfillment (con su seguimiento), pagos, retiros, almacenes
y webhooks.

Diseño:
  * Recibe un `TikTokShopClient` y usa su `request` (firma, renovación de token): no se duplica la autenticación.
  * SOLO lecturas: GET y los POST de tipo "search" (son consultas). Nunca se crea, edita ni borra nada en
    TikTok (ni cupones, ni promociones, ni webhooks, ni retiros).
  * Los `fetch_*` hacen la red (HTTP síncrono: se ejecutan fuera del bucle de eventos); los `build_*` son
    PUROS (JSON de la API -> filas) y se prueban sin red.
  * Privacidad: no se guarda nombre, teléfono ni dirección de nadie. De la cuenta bancaria solo los últimos 4
    caracteres; de los almacenes solo nombre, tipo, estado, ciudad, estado, país y código postal.
  * Cada sección falla por separado: un recurso sin permiso o caído no impide sincronizar los demás.

Importado por `app.sync_jobs` y las pruebas.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_shop import TikTokShopClient, TikTokShopError
from app.tiktok_extra_ops import save_snapshot

PRODUCT_SEARCH_PATH = "/product/202312/products/search"
PRODUCT_DETAIL_PATH = "/product/202309/products/{id}"
PRODUCT_PERFORMANCE_PATH = "/analytics/202405/shop_products/performance"
PRODUCT_PERFORMANCE_DETAIL_PATH = "/analytics/202405/shop_products/{id}/performance"
SKU_PERFORMANCE_PATH = "/analytics/202406/shop_skus/performance"
ACTIVITIES_PATH = "/promotion/202309/activities/search"
ACTIVITY_DETAIL_PATH = "/promotion/202309/activities/{id}"
COUPONS_PATH = "/promotion/202406/coupons/search"
COUPON_DETAIL_PATH = "/promotion/202406/coupons/{id}"
PACKAGES_PATH = "/fulfillment/202309/packages/search"
ORDER_TRACKING_PATH = "/fulfillment/202309/orders/{id}/tracking"
PAYMENTS_PATH = "/finance/202309/payments"
WITHDRAWALS_PATH = "/finance/202309/withdrawals"
WAREHOUSES_PATH = "/logistics/202309/warehouses"
WEBHOOKS_PATH = "/event/202309/webhooks"

PERFORMANCE_WINDOW_DAYS = 30  # ventana fija de las fotos de rendimiento: así las fotos de días distintos se comparan
MAX_PACKAGE_WINDOW_DAYS = 90  # TikTok limita la búsqueda de paquetes
MONEY_WINDOW_DAYS = 365  # pagos y retiros son pocos: se traen todos los del último año
PAGE_SIZE = 100
PACKAGE_PAGE_SIZE = 50
MAX_PAGES = 100  # tope duro contra un cursor que nunca termina
# Topes de llamadas "una por elemento": acotan el tiempo de una sincronización (~0.5 s por llamada).
MAX_PRODUCT_DETAILS = 300
MAX_PERFORMANCE_DETAILS = 200
MAX_ACTIVITY_DETAILS = 100
MAX_COUPON_DETAILS = 100
MAX_TRACKING_CALLS = 120
MAX_AUDIT_REASONS = 20
BANK_LAST_DIGITS = 4
CURRENCY_SYMBOLS = {"$": "USD"}

PACKAGE_COMPLETED = "COMPLETED"
PACKAGE_CANCELLED = "CANCELLED"
PACKAGE_TERMINAL = frozenset({PACKAGE_COMPLETED, PACKAGE_CANCELLED})

# Eventos de webhook documentados por TikTok Shop. La API solo lista las suscripciones activas, no el catálogo
# de eventos disponibles; esta lista sirve para mostrar qué falta por suscribir (no se suscribe nada desde aquí).
DOCUMENTED_WEBHOOK_EVENTS: tuple[str, ...] = (
    "ORDER_STATUS_CHANGE",
    "RECIPIENT_ADDRESS_UPDATE",
    "PACKAGE_UPDATE",
    "PRODUCT_STATUS_CHANGE",
    "PRODUCT_INFORMATION_CHANGE",
    "INVENTORY_UPDATE",
    "CANCELLATION_STATUS_CHANGE",
    "RETURN_STATUS_CHANGE",
    "REVERSE_ORDER_STATUS_UPDATE",
    "SELLER_DEAUTHORIZATION",
    "UPCOMING_AUTHORIZATION_EXPIRATION",
    "NEW_MESSAGE",
)

Row = dict[str, Any]


# --- utilidades puras --------------------------------------------------------------------------------


def _opt_float(value: Any) -> float | None:
    """Número o None: un campo vacío no es cero."""
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").rstrip("%"))
    except ValueError:
        return None


def _float0(value: Any) -> float:
    return _opt_float(value) or 0.0


def _opt_int(value: Any) -> int | None:
    number = _opt_float(value)
    return int(number) if number is not None else None


def _int0(value: Any) -> int:
    return _opt_int(value) or 0


def _text(value: Any) -> str | None:
    cleaned = str(value).strip() if value is not None else ""
    return cleaned or None


def _ts(value: Any, *, millis: bool = False) -> datetime | None:
    """Epoch (segundos, o milisegundos) -> datetime UTC. 0 o ausente -> None."""
    number = _opt_int(value)
    if not number:
        return None
    try:
        return datetime.fromtimestamp(number / (1000 if millis else 1), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _amount(node: Any, key: str = "amount") -> float | None:
    """Monto de un nodo {"amount": "12.3", "currency": "USD"}."""
    return _opt_float(node.get(key)) if isinstance(node, dict) else None


def _currency(node: Any) -> str | None:
    """Moneda de un nodo de monto; algunos cupones traen el símbolo ("$") en vez del código."""
    raw = _text(node.get("currency")) if isinstance(node, dict) else None
    return CURRENCY_SYMBOLS.get(raw, raw) if raw else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _last_digits(account: Any, count: int = BANK_LAST_DIGITS) -> str | None:
    """Últimos caracteres de una cuenta bancaria: el número completo no se guarda nunca."""
    text = "".join(ch for ch in str(account or "") if ch.isalnum())
    return text[-count:] if len(text) >= count else None


# --- catálogo ----------------------------------------------------------------------------------------


def _audit_reasons(audit: dict[str, Any]) -> list[Any]:
    """Motivos de auditoría: toda lista cuyo nombre contenga "reason" (los nombres varían por estado)."""
    reasons: list[Any] = []
    for key, value in audit.items():
        if "reason" in key and isinstance(value, list):
            reasons.extend(value)
    return reasons[:MAX_AUDIT_REASONS]


def _sku_rows(product_id: str, skus: list[Any]) -> list[Row]:
    rows: list[Row] = []
    for sku in skus:
        sku = _dict(sku)
        sku_id = _text(sku.get("id"))
        if not sku_id:
            continue
        price = _dict(sku.get("price"))
        rows.append(
            {
                "sku_id": sku_id,
                "product_id": product_id,
                "seller_sku": _text(sku.get("seller_sku")),
                "price": _opt_float(price.get("sale_price")) or _opt_float(price.get("tax_exclusive_price")),
                "currency": _text(price.get("currency")),
                "quantity": sum(_int0(i.get("quantity")) for i in map(_dict, _list(sku.get("inventory")))),
            }
        )
    return rows


def _main_image(detail: dict[str, Any]) -> tuple[str | None, str | None, int]:
    """(URL grande, URL miniatura, total de imágenes) de la primera imagen principal."""
    images = [_dict(i) for i in _list(detail.get("main_images"))]
    if not images:
        return None, None, 0
    first = images[0]
    urls, thumbs = _list(first.get("urls")), _list(first.get("thumb_urls"))
    return _text(urls[0]) if urls else None, _text(thumbs[0]) if thumbs else None, len(images)


def build_product(search_row: Row, detail: Row | None) -> tuple[Row, list[Row]]:
    """Fila de producto + filas de SKU. `detail` (opcional) aporta imágenes, marca y categorías."""
    detail = detail or {}
    product_id = str(search_row["id"])
    audit = {**_dict(detail.get("audit")), **_dict(search_row.get("audit"))}  # la búsqueda manda; el detalle aporta motivos
    image, thumb, image_count = _main_image(detail)
    skus = _sku_rows(product_id, _list(detail.get("skus")) or _list(search_row.get("skus")))
    prices = [s["price"] for s in skus if s["price"] is not None]
    chains = [_dict(c) for c in _list(detail.get("category_chains"))]
    categories = [n for n in (_text(c.get("local_name")) for c in chains) if n]
    row: Row = {
        "product_id": product_id,
        "title": _text(search_row.get("title")) or _text(detail.get("title")),
        "status": _text(search_row.get("status")) or _text(detail.get("status")),
        "audit_status": _text(audit.get("status")),
        "audit_reasons": _audit_reasons(audit),
        "listing_quality_tier": _text(search_row.get("listing_quality_tier")),
        "is_not_for_sale": bool(search_row.get("is_not_for_sale")),
        "main_image_url": image,
        "thumb_url": thumb,
        "image_count": image_count,
        "brand_name": _text(_dict(detail.get("brand")).get("name")),
        "categories": categories,
        "sales_regions": _list(search_row.get("sales_regions")),
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "currency": next((s["currency"] for s in skus if s["currency"]), None),
        "sku_count": len(skus),
        "created_at": _ts(search_row.get("create_time")),
        "updated_at": _ts(search_row.get("update_time")),
    }
    return row, skus


# --- rendimiento -------------------------------------------------------------------------------------


def _source_amounts(breakdowns: Any) -> dict[str, float]:
    """GMV por origen (LIVE, VIDEO, PRODUCT_CARD) a partir de `gmv_breakdowns`."""
    return {
        str(b.get("type")): _float0(b.get("amount")) for b in map(_dict, _list(breakdowns)) if b.get("type")
    }


def build_product_performance(
    list_row: Row, interval: Row | None, window: tuple[date, date], snapshot_date: date
) -> Row:
    """Foto de un producto. `interval` es el detalle (granularidad ALL) con impresiones y vistas, si se pudo leer.

    Conversión = pedidos / visitantes de la página (None si no hay visitantes: un cero sería engañoso).
    """
    interval = interval or {}
    orders = _int0(list_row.get("orders"))
    visitors = _opt_int(interval.get("avg_page_visitors"))
    by_source = _source_amounts(interval.get("gmv_breakdowns"))
    return {
        "product_id": str(list_row["id"]),
        "snapshot_date": snapshot_date,
        "window_start": window[0],
        "window_end": window[1],
        "gmv": _float0(_dict(list_row.get("gmv")).get("amount")),
        "orders": orders,
        "units_sold": _int0(list_row.get("units_sold")),
        "impressions": _opt_int(interval.get("impressions")),
        "page_views": _opt_int(interval.get("page_views")),
        "page_visitors": visitors,
        "click_through_rate": _opt_float(list_row.get("click_through_rate")),
        "conversion_rate": (orders / visitors) if visitors else None,
        "gmv_video": by_source.get("VIDEO") if by_source else None,
        "gmv_live": by_source.get("LIVE") if by_source else None,
        "gmv_product_card": by_source.get("PRODUCT_CARD") if by_source else None,
    }


def build_sku_performance(list_row: Row, window: tuple[date, date], snapshot_date: date) -> Row:
    return {
        "sku_id": str(list_row["id"]),
        "snapshot_date": snapshot_date,
        "product_id": _text(list_row.get("product_id")),
        "window_start": window[0],
        "window_end": window[1],
        "gmv": _float0(_dict(list_row.get("gmv")).get("amount")),
        "orders": _int0(list_row.get("sku_orders")),
        "units_sold": _int0(list_row.get("units_sold")),
    }


# --- promociones y cupones ---------------------------------------------------------------------------


def build_promotion(summary: Row, detail: Row | None) -> tuple[Row, list[Row]]:
    """Actividad + sus productos. Sin detalle solo se guarda el resumen (product_count queda en 0)."""
    detail = detail or {}
    activity_id = str(summary["id"])
    products = [_dict(p) for p in _list(detail.get("products"))]
    rows: list[Row] = []
    for p in products:
        product_id = _text(p.get("id"))
        if not product_id:
            continue
        price = _dict(p.get("activity_price"))
        rows.append(
            {
                "activity_id": activity_id,
                "product_id": product_id,
                "activity_price": _amount(price),
                "currency": _currency(price),
                "discount": _dict(p.get("discount")) or None,
                "quantity_limit": _opt_int(p.get("quantity_limit")),
                "quantity_per_user": _opt_int(p.get("quantity_per_user")),
            }
        )
    activity: Row = {
        "activity_id": activity_id,
        "title": _text(summary.get("title")),
        "activity_type": _text(summary.get("activity_type")),
        "product_level": _text(summary.get("product_level")),
        "status": _text(summary.get("status")),
        "duration_type": _text(summary.get("duration_type")),
        "begin_at": _ts(summary.get("begin_time")),
        "end_at": _ts(summary.get("end_time")),
        "created_at": _ts(summary.get("create_time")),
        "updated_at": _ts(summary.get("update_time")),
        "product_count": len(rows),
        "discount": _dict(detail.get("discount")) or None,
    }
    return activity, rows


def build_coupon(row: Row, detail: Row | None) -> Row:
    """Cupón. `detail` aporta los productos y el uso (reclamados/canjeados)."""
    merged = {**row, **(detail or {})}
    discount = _dict(merged.get("discount"))
    threshold = _dict(merged.get("threshold"))
    claim = _dict(merged.get("claim_duration"))
    redeem = _dict(merged.get("redemption_duration"))
    limits = _dict(merged.get("usage_limits"))
    stats = _dict(merged.get("usage_stats"))
    reduction = _dict(discount.get("reduction_amount"))
    min_spend = _dict(threshold.get("min_spend"))
    return {
        "coupon_id": str(merged["id"]),
        "title": _text(merged.get("title")),
        "status": _text(merged.get("status")),
        "display_type": _text(merged.get("display_type")),
        "promo_code": _text(merged.get("promo_code")),
        "product_scope": _text(merged.get("product_scope")),
        "target_segment": _text(merged.get("target_buyer_segment")),
        "discount_type": _text(discount.get("type")),
        "discount_percent": _opt_float(discount.get("percentage")),
        "discount_amount": _amount(reduction),
        "max_discount": _amount(_dict(discount.get("max_discount"))),
        "threshold_type": _text(threshold.get("type")),
        "min_spend": _amount(min_spend),
        "currency": _currency(reduction) or _currency(min_spend),
        "claim_start": _ts(claim.get("start_time")),
        "claim_end": _ts(claim.get("end_time")),
        "redeem_start": _ts(redeem.get("start_time")),
        "redeem_end": _ts(redeem.get("end_time")),
        "redemption_limit": _opt_int(limits.get("redemption_limit")),
        "per_buyer_limit": _opt_int(limits.get("single_buyer_claim_limit")),
        "claimed_count": _opt_int(stats.get("claimed_count")),
        "redeemed_count": _opt_int(stats.get("redeemed_count")),
        "product_ids": [str(p) for p in _list(merged.get("product_ids"))],
        "creation_source": _text(merged.get("creation_source")),
        "created_at": _ts(merged.get("create_time")),
        "updated_at": _ts(merged.get("update_time")),
    }


# --- paquetes ----------------------------------------------------------------------------------------


def last_tracking_event(events: list[Any]) -> tuple[datetime | None, str | None]:
    """Evento de seguimiento más reciente: (cuándo, descripción)."""
    dated = [
        (when, _text(e.get("description")))
        for e in map(_dict, events)
        if (when := _ts(e.get("update_time_millis"), millis=True)) is not None
    ]
    return max(dated, key=lambda t: t[0]) if dated else (None, None)


def build_package(row: Row, events: list[Any] | None = None) -> Row:
    """Paquete sin datos del comprador.

    `delivered_at` se INFIERE: la API no da fecha de entrega, así que un paquete COMPLETED usa su última
    actualización (el momento en que TikTok lo marcó completado, que es cuando se considera entregado).
    """
    orders = [_dict(o) for o in _list(row.get("orders"))]
    status = _text(row.get("status"))
    updated = _ts(row.get("update_time"))
    last_at, last_text = last_tracking_event(events or [])
    units = sum(_int0(s.get("quantity")) for o in orders for s in map(_dict, _list(o.get("skus"))))
    return {
        "package_id": str(row["id"]),
        "status": status,
        "tracking_number": _text(row.get("tracking_number")),
        "order_ids": [str(o["id"]) for o in orders if o.get("id")],
        "order_count": len(orders),
        "unit_count": units,
        "created_at": _ts(row.get("create_time")),
        "updated_at": updated,
        "delivered_at": updated if status == PACKAGE_COMPLETED else None,
        "last_event_at": last_at,
        "last_event": last_text,
    }


# --- pagos, retiros, almacenes, webhooks -------------------------------------------------------------


def statements_by_payment(statements: list[Row]) -> dict[str, list[str]]:
    """payment_id -> ids de los estados de cuenta (= sales.settlement.external_id) que ese pago cubre."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for s in statements:
        payment_id, statement_id = _text(s.get("payment_id")), _text(s.get("id"))
        if payment_id and statement_id:
            grouped[payment_id].append(statement_id)
    return {k: sorted(v) for k, v in grouped.items()}


def build_payment(row: Row, statement_ids: list[str]) -> Row:
    amount, settlement = _dict(row.get("amount")), _dict(row.get("settlement_amount"))
    return {
        "payment_id": str(row["id"]),
        "status": _text(row.get("status")),
        "currency": _currency(amount) or _currency(settlement),
        "amount": _float0(amount.get("value")),
        "settlement_amount": _float0(settlement.get("value")),
        "reserve_amount": _float0(_dict(row.get("reserve_amount")).get("value")),
        "amount_before_exchange": _opt_float(_dict(row.get("payment_amount_before_exchange")).get("value")),
        "exchange_rate": _opt_float(row.get("exchange_rate")),
        "created_at": _ts(row.get("create_time")),
        "paid_at": _ts(row.get("paid_time")),
        "bank_last4": _last_digits(row.get("bank_account")),
        "statement_ids": statement_ids,
    }


def build_withdrawal(row: Row) -> Row:
    return {
        "withdrawal_id": str(row["id"]),
        "type": _text(row.get("type")),
        "status": _text(row.get("status")),
        "currency": _text(row.get("currency")),
        "amount": _float0(row.get("amount")),
        "created_at": _ts(row.get("create_time")),
    }


def build_warehouse(row: Row) -> Row:
    """Almacén SIN líneas de calle, contacto ni teléfono."""
    address = _dict(row.get("address"))
    return {
        "warehouse_id": str(row["id"]),
        "name": _text(row.get("name")),
        "type": _text(row.get("type")),
        "sub_type": _text(row.get("sub_type")),
        "status": _text(row.get("effect_status")),
        "is_default": bool(row.get("is_default")),
        "city": _text(address.get("city")),
        "state": _text(address.get("state")),
        "region_code": _text(address.get("region_code")),
        "postal_code": _text(address.get("postal_code")),
    }


def build_webhook(row: Row) -> Row | None:
    event, address = _text(row.get("event_type")), _text(row.get("address"))
    if not event or not address:
        return None
    return {"event_type": event, "address": address, "created_at": _ts(row.get("create_time"))}


# --- lectura de la API -------------------------------------------------------------------------------


def _pages(
    client: TikTokShopClient,
    method: str,
    path: str,
    key: str,
    params: dict[str, Any],
    body: dict[str, Any] | None = None,
) -> Iterator[Row]:
    """Recorre `next_page_token` hasta agotarlo o llegar a MAX_PAGES."""
    token = ""
    for _ in range(MAX_PAGES):
        page = {**params, **({"page_token": token} if token else {})}
        data = client.request(method, path, page, body)
        yield from data.get(key) or []
        token = data.get("next_page_token") or ""
        if not token:
            return


def _optional(call: Callable[[], Row]) -> Row | None:
    """Detalle por elemento: si falla, el elemento se guarda solo con lo que ya se sabe."""
    try:
        return call()
    except (TikTokShopError, httpx.HTTPError):
        return None


def fetch_products(client: TikTokShopClient) -> tuple[list[Row], list[Row]]:
    """Catálogo completo (búsqueda) + detalle por producto para la imagen (hasta MAX_PRODUCT_DETAILS)."""
    found = list(_pages(client, "POST", PRODUCT_SEARCH_PATH, "products", {"page_size": PAGE_SIZE}, {}))
    products: list[Row] = []
    skus: list[Row] = []
    for index, search_row in enumerate(found):
        detail = None
        if index < MAX_PRODUCT_DETAILS:
            detail = _optional(lambda r=search_row: client.request("GET", PRODUCT_DETAIL_PATH.format(id=r["id"])))
        row, sku_rows = build_product(search_row, detail)
        products.append(row)
        skus.extend(sku_rows)
    return products, skus


def _performance_params(window: tuple[date, date]) -> dict[str, Any]:
    return {"start_date_ge": window[0].isoformat(), "end_date_lt": window[1].isoformat(), "currency": "USD"}


def fetch_product_performance(client: TikTokShopClient, window: tuple[date, date], today: date) -> list[Row]:
    """Rendimiento por producto (lista, ordenada por GMV) + impresiones/vistas de los de mayor GMV."""
    listed = list(
        _pages(
            client,
            "GET",
            PRODUCT_PERFORMANCE_PATH,
            "products",
            {**_performance_params(window), "page_size": PAGE_SIZE, "sort_field": "gmv", "sort_order": "DESC"},
        )
    )
    rows: list[Row] = []
    for index, item in enumerate(listed):
        interval = None
        if index < MAX_PERFORMANCE_DETAILS:
            detail = _optional(
                lambda i=item: client.request(
                    "GET",
                    PRODUCT_PERFORMANCE_DETAIL_PATH.format(id=i["id"]),
                    {**_performance_params(window), "granularity": "ALL"},
                )
            )
            intervals = _list(_dict(_dict(detail).get("performance")).get("intervals"))
            interval = _dict(intervals[0]) if intervals else None
        rows.append(build_product_performance(item, interval, window, today))
    return rows


def fetch_sku_performance(client: TikTokShopClient, window: tuple[date, date], today: date) -> list[Row]:
    listed = _pages(
        client,
        "GET",
        SKU_PERFORMANCE_PATH,
        "skus",
        {**_performance_params(window), "page_size": PAGE_SIZE, "sort_field": "gmv", "sort_order": "DESC"},
    )
    return [build_sku_performance(item, window, today) for item in listed]


def fetch_promotions(client: TikTokShopClient) -> tuple[list[Row], list[Row]]:
    summaries = list(_pages(client, "POST", ACTIVITIES_PATH, "activities", {"page_size": PAGE_SIZE}, {"page_size": PAGE_SIZE}))
    activities: list[Row] = []
    products: list[Row] = []
    for index, summary in enumerate(summaries):
        detail = None
        if index < MAX_ACTIVITY_DETAILS:
            detail = _optional(lambda s=summary: client.request("GET", ACTIVITY_DETAIL_PATH.format(id=s["id"])))
        activity, rows = build_promotion(summary, detail)
        activities.append(activity)
        products.extend(rows)
    return activities, products


def fetch_coupons(client: TikTokShopClient) -> list[Row]:
    listed = list(_pages(client, "POST", COUPONS_PATH, "coupons", {"page_size": PAGE_SIZE}, {"page_size": PAGE_SIZE}))
    rows: list[Row] = []
    for index, coupon in enumerate(listed):
        detail = None
        if index < MAX_COUPON_DETAILS:
            found = _optional(lambda c=coupon: client.request("GET", COUPON_DETAIL_PATH.format(id=c["id"])))
            detail = _dict(_dict(found).get("coupon")) or None
        rows.append(build_coupon(coupon, detail))
    return rows


def _tracking_events(client: TikTokShopClient, order_id: str) -> list[Any]:
    data = _optional(lambda: client.request("GET", ORDER_TRACKING_PATH.format(id=order_id)))
    return _list(_dict(data).get("tracking"))


def fetch_packages(client: TikTokShopClient, start: datetime, end: datetime) -> list[Row]:
    """Paquetes creados en la ventana. El seguimiento (1 llamada por orden) solo se pide para los NO terminados.

    Se priorizan los de actualización más antigua: son los candidatos a estar atascados.
    """
    found = list(
        _pages(
            client,
            "POST",
            PACKAGES_PATH,
            "packages",
            {"page_size": PACKAGE_PAGE_SIZE},
            {"create_time_ge": int(start.timestamp()), "create_time_lt": int(end.timestamp())},
        )
    )
    open_ids = sorted(
        (p for p in found if _text(p.get("status")) not in PACKAGE_TERMINAL and p.get("orders")),
        key=lambda p: _int0(p.get("update_time")),
    )[:MAX_TRACKING_CALLS]
    events = {str(p["id"]): _tracking_events(client, str(_dict(p["orders"][0])["id"])) for p in open_ids}
    return [build_package(p, events.get(str(p["id"]))) for p in found]


def fetch_payments(client: TikTokShopClient, start: datetime, end: datetime) -> list[Row]:
    """Pagos de la ventana, enlazados a sus estados de cuenta (liquidaciones)."""
    window = {"create_time_ge": int(start.timestamp()), "create_time_lt": int(end.timestamp())}
    payments = _pages(client, "GET", PAYMENTS_PATH, "payments", {"page_size": PAGE_SIZE, "sort_field": "create_time", **window})
    statements = list(client.iter_statements(start, end))
    links = statements_by_payment(statements)
    return [build_payment(p, links.get(str(p["id"]), [])) for p in payments]


def fetch_withdrawals(client: TikTokShopClient, start: datetime, end: datetime) -> list[Row]:
    window = {"create_time_ge": int(start.timestamp()), "create_time_lt": int(end.timestamp())}
    rows = _pages(client, "GET", WITHDRAWALS_PATH, "withdrawals", {"page_size": PAGE_SIZE, "types": "WITHDRAW", **window})
    return [build_withdrawal(r) for r in rows]


def fetch_warehouses(client: TikTokShopClient) -> list[Row]:
    data = client.request("GET", WAREHOUSES_PATH)
    return [build_warehouse(w) for w in _list(data.get("warehouses"))]


def fetch_webhooks(client: TikTokShopClient) -> list[Row]:
    data = client.request("GET", WEBHOOKS_PATH)
    return [w for w in (build_webhook(_dict(r)) for r in _list(data.get("webhooks"))) if w]


# --- orquestación ------------------------------------------------------------------------------------


@dataclass(slots=True)
class ExtraSnapshot:
    """Todo lo leído de TikTok en una sincronización, ya como filas. `problems` = secciones que fallaron."""

    products: list[Row] = field(default_factory=list)
    product_skus: list[Row] = field(default_factory=list)
    product_performance: list[Row] = field(default_factory=list)
    sku_performance: list[Row] = field(default_factory=list)
    promotions: list[Row] = field(default_factory=list)
    promotion_products: list[Row] = field(default_factory=list)
    coupons: list[Row] = field(default_factory=list)
    packages: list[Row] = field(default_factory=list)
    payments: list[Row] = field(default_factory=list)
    withdrawals: list[Row] = field(default_factory=list)
    warehouses: list[Row] = field(default_factory=list)
    webhooks: list[Row] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    failed: set[str] = field(default_factory=set)  # secciones que fallaron: la persistencia no retira nada de ellas


def _section(snapshot: ExtraSnapshot, name: str, load: Callable[[], None]) -> None:
    """Ejecuta una sección; si falla, lo anota en `problems` y sigue con las demás."""
    try:
        load()
    except (TikTokShopError, httpx.HTTPError, KeyError, ValueError) as exc:
        snapshot.problems.append(f"{name}: {str(exc)[:160]}")
        snapshot.failed.add(name)


def fetch_extra_snapshot(client: TikTokShopClient, now: datetime, days: int) -> ExtraSnapshot:
    """Lee todas las secciones (bloqueante). `days` fija la ventana de paquetes (máx. 90 días)."""
    today = now.date()
    window = (today - timedelta(days=PERFORMANCE_WINDOW_DAYS), today)
    package_days = min(max(days, 1), MAX_PACKAGE_WINDOW_DAYS)
    snap = ExtraSnapshot()

    def catalog() -> None:
        snap.products, snap.product_skus = fetch_products(client)

    def promotions() -> None:
        snap.promotions, snap.promotion_products = fetch_promotions(client)

    _section(snap, "catálogo", catalog)
    _section(snap, "rendimiento por producto", lambda: setattr(snap, "product_performance", fetch_product_performance(client, window, today)))
    _section(snap, "rendimiento por SKU", lambda: setattr(snap, "sku_performance", fetch_sku_performance(client, window, today)))
    _section(snap, "promociones", promotions)
    _section(snap, "cupones", lambda: setattr(snap, "coupons", fetch_coupons(client)))
    _section(snap, "paquetes", lambda: setattr(snap, "packages", fetch_packages(client, now - timedelta(days=package_days), now)))
    _section(snap, "pagos", lambda: setattr(snap, "payments", fetch_payments(client, now - timedelta(days=MONEY_WINDOW_DAYS), now)))
    _section(snap, "retiros", lambda: setattr(snap, "withdrawals", fetch_withdrawals(client, now - timedelta(days=MONEY_WINDOW_DAYS), now)))
    _section(snap, "almacenes", lambda: setattr(snap, "warehouses", fetch_warehouses(client)))
    _section(snap, "webhooks", lambda: setattr(snap, "webhooks", fetch_webhooks(client)))
    return snap


async def sync_tiktok_shop_extra(
    session: AsyncSession, client: TikTokShopClient, days: int, now: datetime | None = None
) -> tuple[dict[str, int], list[str]]:
    """Lee TikTok Shop (en un hilo: el cliente es HTTP síncrono) y guarda todo. Devuelve (conteos, problemas)."""
    now = now or datetime.now(UTC)
    snapshot = await asyncio.to_thread(fetch_extra_snapshot, client, now, days)
    stats = await save_snapshot(session, snapshot, now)
    return stats, snapshot.problems
