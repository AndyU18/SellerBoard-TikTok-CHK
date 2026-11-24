"""Conector de solo lectura de los "insights" de Walmart Marketplace que `walmart.py` no consume.

Recursos (todos GET): calidad de listados, inventario WFS, feeds, plantillas de envío, estrategias del
repricer, solicitudes de reporte (+ descarga del reporte ya generado) y pedidos por surtir.

Diseño:
  * Recibe un `WalmartClient` y usa su `_get` (token, reintentos 429/5xx): no se duplica la autenticación.
  * Los `fetch_*`/`iter_*` hacen la red; los `build_*` son PUROS (dict/DataFrame -> filas) y se prueban sin red.
  * Nunca se hace POST/PUT/DELETE a Walmart. Calidad POR ÍTEM no se consume porque Walmart solo la ofrece
    por POST (`/listingQuality/items` responde 520 a GET).
  * Los pedidos por surtir se normalizan SIN datos personales (nada de nombre, dirección, correo, teléfono).

Importado por `app.sync_jobs`, `app.walmart_ops`, `app.walmart_analytics` y `app.walmart_api`.
"""

from __future__ import annotations

import asyncio
import codecs
import io
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx
import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.walmart import WalmartClient, WalmartError, _f, _ts
from app.walmart_ops import (
    replace_item_snapshots,
    replace_repricer_strategies,
    replace_shipping_templates,
    replace_wfs_inventory,
    upsert_feeds,
    upsert_listing_quality,
    upsert_report_requests,
)

LISTING_QUALITY_PATH = "/v3/insights/items/listingQuality/score"
WFS_INVENTORY_PATH = "/v3/fulfillment/inventory"
FEEDS_PATH = "/v3/feeds"
SHIPPING_TEMPLATES_PATH = "/v3/settings/shipping/templates"
REPRICER_PATH = "/v3/repricer/strategies"
REPORT_REQUESTS_PATH = "/v3/reports/reportRequests"
REPORT_DOWNLOAD_PATH = "/v3/reports/downloadReport"
RELEASED_ORDERS_PATH = "/v3/orders/released"

WFS_PAGE = 100
FEEDS_PAGE = 50
MAX_FEEDS = 200  # los más recientes: basta para medir la tasa de éxito reciente
MAX_PAGES = 200  # tope duro contra un cursor que nunca termina
REPORT_TYPES = ("ITEM", "BUYBOX")
READY = "READY"
MAX_REPORT_BYTES = 25 * 1024 * 1024  # tope de lo que se descarga (el ITEM de 65 SKU pesa ~13 KB)
MAX_REPORT_UNZIPPED = 100 * 1024 * 1024
MAX_REPORT_ROWS = 50_000
UTF8_BOM = codecs.BOM_UTF8
DEFAULT_NODE = "WFSFulfilled"
OPEN_ORDER_STATUS_PRIORITY = ("Created", "Acknowledged")
RELEASED_PAGE = 100

# Encabezados del reporte ITEM v4 -> columna de ops.walmart_item_snapshot.
ITEM_COLUMNS: dict[str, str] = {
    "SKU": "sku",
    "Item ID": "item_id",
    "Product Name": "product_name",
    "Publish Status": "publish_status",
    "Lifecycle Status": "lifecycle_status",
    "Status Change Reason": "status_change_reason",
    "Fulfillment Type": "fulfillment_type",
    "Price": "price",
    "Buy Box Item Price": "buybox_price",
    "Buy Box Eligible": "buybox_eligible",
    "Repricer Strategy": "repricer_strategy",
    "Repricer Status": "repricer_status",
    "Minimum Seller Allowed Price": "min_allowed_price",
    "Maximum Seller Allowed Price": "max_allowed_price",
    "Reviews Count": "reviews_count",
    "Average Rating": "average_rating",
}


# --- utilidades puras --------------------------------------------------------------------------------


def _opt_float(value: Any) -> float | None:
    """Número o None: un campo vacío no es cero (un puntaje ausente no debe verse como 0)."""
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _opt_int(value: Any) -> int | None:
    number = _opt_float(value)
    return int(number) if number is not None else None


def _int0(value: Any) -> int:
    return _opt_int(value) or 0


def _text(value: Any) -> str | None:
    cleaned = str(value).strip() if value is not None else ""
    return cleaned or None


def _yes(value: Any) -> bool | None:
    text = (_text(value) or "").lower()
    if not text:
        return None
    return text in {"yes", "true", "y", "1"}


# --- calidad de listados -----------------------------------------------------------------------------


def build_listing_quality(body: dict[str, Any], day: date) -> dict[str, Any] | None:
    """Foto del día del puntaje de calidad. None si la respuesta no trae `payload`."""
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return None
    score = payload.get("score") or {}
    post = payload.get("postPurchaseQuality") or {}
    return {
        "snapshot_date": day,
        "listing_quality": _opt_float(payload.get("listingQuality")),
        "offer_score": _opt_float(score.get("offerScore")),
        "rating_review_score": _opt_float(score.get("ratingReviewScore")),
        "content_score": _opt_float(score.get("contentScore")),
        "price_score": _opt_float(score.get("priceScore")),
        "shipping_score": _opt_float(score.get("shippingScore")),
        "transactibility_score": _opt_float(score.get("transactibilityScore")),
        "item_defect_count": _opt_int(post.get("itemDefectCnt")),
        "defect_ratio": _opt_float(post.get("defectRatio")),
    }


def fetch_listing_quality(client: WalmartClient) -> dict[str, Any]:
    return client._get(LISTING_QUALITY_PATH).json()


# --- inventario WFS ----------------------------------------------------------------------------------


def build_wfs_inventory(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Una fila por (sku, nodo). Si Walmart no da id de nodo se usa el tipo (WFSFulfilled)."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        sku = _text(item.get("sku"))
        if not sku:
            continue
        for node in item.get("shipNodes") or []:
            node_type = _text(node.get("shipNodeType"))
            node_id = _text(node.get("shipNode")) or node_type or DEFAULT_NODE
            rows[(sku, node_id)] = {
                "sku": sku,
                "node": node_id,
                "offer_id": _text(item.get("offerId")),
                "node_type": node_type,
                "avail_to_sell": _int0(node.get("availToSellQty")),
                "on_hand": _int0(node.get("onHandQty")),
                "first_in_stock_date": _ts(node.get("firstInStockDate")),
                "modified_at": _ts(node.get("modifiedDate")),
            }
    return sorted(rows.values(), key=lambda r: (r["sku"], r["node"]))


def iter_wfs_inventory(client: WalmartClient) -> Iterator[dict[str, Any]]:
    """Recorre el inventario WFS por desplazamiento (offset) hasta `headers.totalCount`."""
    offset = 0
    for _ in range(MAX_PAGES):
        body = client._get(WFS_INVENTORY_PATH, {"limit": WFS_PAGE, "offset": offset}).json()
        page = (body.get("payload") or {}).get("inventory") or []
        yield from page
        offset += len(page)
        total = _int0((body.get("headers") or {}).get("totalCount"))
        if not page or offset >= total:
            return


# --- feeds -------------------------------------------------------------------------------------------


def build_feeds(feeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for feed in feeds:
        feed_id = _text(feed.get("feedId"))
        if not feed_id:
            continue
        rows[feed_id] = {
            "feed_id": feed_id,
            "feed_type": _text(feed.get("feedType")),
            "feed_source": _text(feed.get("feedSource")),
            "status": _text(feed.get("feedStatus")),
            "items_received": _int0(feed.get("itemsReceived")),
            "items_succeeded": _int0(feed.get("itemsSucceeded")),
            "items_failed": _int0(feed.get("itemsFailed")),
            "items_processing": _int0(feed.get("itemsProcessing")),
            "data_error_count": _int0(feed.get("itemDataErrorCount")),
            "system_error_count": _int0(feed.get("itemSystemErrorCount")),
            "feed_date": _ts(feed.get("feedDate")),
            "modified_at": _ts(feed.get("modifiedDtm")),
        }
    return list(rows.values())


def iter_feeds(client: WalmartClient, limit: int = MAX_FEEDS) -> Iterator[dict[str, Any]]:
    """Feeds más recientes primero, hasta `limit`."""
    offset = 0
    while offset < limit:
        page_size = min(FEEDS_PAGE, limit - offset)
        body = client._get(FEEDS_PATH, {"limit": page_size, "offset": offset}).json()
        page = (body.get("results") or {}).get("feed") or []
        yield from page
        offset += len(page)
        if not page or offset >= _int0(body.get("totalResults")):
            return


# --- plantillas de envío -----------------------------------------------------------------------------


def build_shipping_template(summary: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any] | None:
    template_id = _text(summary.get("id"))
    if not template_id:
        return None
    return {
        "id": template_id,
        "name": _text(summary.get("name")),
        "type": _text(summary.get("type")),
        "rate_model_type": _text(summary.get("rateModelType")),
        "status": _text(summary.get("status")),
        "detail": detail,
    }


def fetch_shipping_templates(client: WalmartClient) -> list[dict[str, Any]]:
    return client._get(SHIPPING_TEMPLATES_PATH).json().get("shippingTemplates") or []


def fetch_shipping_template_detail(client: WalmartClient, template_id: str) -> dict[str, Any] | None:
    """Detalle (métodos, regiones, tarifas). Un fallo de una plantilla no debe perder las demás."""
    try:
        return client._get(f"{SHIPPING_TEMPLATES_PATH}/{template_id}").json()
    except WalmartError:
        return None


# --- repricer ----------------------------------------------------------------------------------------


def build_repricer_strategies(collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in collections:
        collection_id = _text(c.get("strategyCollectionId"))
        if not collection_id:
            continue
        rows.append(
            {
                "collection_id": collection_id,
                "strategy": _text(c.get("repricerStrategy")) or collection_id,
                "enabled": bool(c.get("enabled")),
                "assigned_count": _int0(c.get("assignedCount")),
                "enable_for_promotion": c.get("enableRepricerForPromotion"),
                "restore_seller_price_without_target": c.get("restoreSellerPriceWithoutTarget"),
                "enable_buybox_meet_external": c.get("enableBuyboxMeetExternal"),
                "compare_with_3p_offer_only": c.get("compareWith3pOfferOnly"),
                "strategies": c.get("strategies") or [],
            }
        )
    return rows


def fetch_repricer_strategies(client: WalmartClient) -> list[dict[str, Any]]:
    return client._get(REPRICER_PATH).json().get("strategyCollections") or []


# --- reportes ----------------------------------------------------------------------------------------


def build_report_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for r in requests:
        request_id = _text(r.get("requestId"))
        if not request_id:
            continue
        rows[request_id] = {
            "request_id": request_id,
            "report_type": _text(r.get("reportType")) or "UNKNOWN",
            "status": _text(r.get("requestStatus")),
            "report_version": _text(r.get("reportVersion")),
            "submitted_at": _ts(r.get("requestSubmissionDate")),
            "generated_at": _ts(r.get("reportGenerationDate")),
        }
    return list(rows.values())


def fetch_report_requests(client: WalmartClient, report_type: str, limit: int = 20) -> list[dict[str, Any]]:
    body = client._get(REPORT_REQUESTS_PATH, {"reportType": report_type, "limit": limit}).json()
    return body.get("requests") or []


def download_report(client: WalmartClient, request_id: str, max_bytes: int = MAX_REPORT_BYTES) -> bytes:
    """Descarga un reporte YA generado (GET). La URL firmada es de otro host: NO se le envía el token.

    Se lee en streaming y se aborta al pasar `max_bytes`, para que un archivo enorme no llene la memoria.
    """
    meta = client._get(REPORT_DOWNLOAD_PATH, {"requestId": request_id}).json()
    url = _text(meta.get("downloadURL"))
    if not url or not url.startswith("https://"):
        raise WalmartError("sin_url", "el reporte no trae una URL de descarga https")
    received = bytearray()
    with client.http.stream("GET", url) as res:
        if res.status_code != 200:
            raise WalmartError(res.status_code, "no se pudo descargar el reporte")
        for chunk in res.iter_bytes():
            received.extend(chunk)
            if len(received) > max_bytes:
                raise WalmartError("demasiado_grande", f"el reporte supera {max_bytes // (1024 * 1024)} MB")
    return bytes(received)


def _first_zip_member(blob: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            members = [i for i in z.infolist() if not i.is_dir()]
            if not members:
                raise WalmartError("reporte_vacio", "el ZIP del reporte no trae archivos")
            if members[0].file_size > MAX_REPORT_UNZIPPED:
                raise WalmartError("demasiado_grande", "el reporte descomprimido es demasiado grande")
            return z.read(members[0])
    except zipfile.BadZipFile as exc:
        raise WalmartError("reporte_invalido", "el reporte no es un ZIP válido") from exc


def parse_report_bytes(blob: bytes, max_rows: int = MAX_REPORT_ROWS) -> pl.DataFrame:
    """CSV (o ZIP con un CSV) -> DataFrame de texto (sin inferir tipos: los reportes mezclan formatos).

    Un ZIP que se descomprime a más de MAX_REPORT_UNZIPPED se rechaza (zip bomb).
    """
    if blob[:2] == b"PK":
        blob = _first_zip_member(blob)
    return pl.read_csv(
        io.BytesIO(blob.removeprefix(UTF8_BOM)),
        infer_schema_length=0,
        n_rows=max_rows,
        truncate_ragged_lines=True,
    )


def build_item_snapshots(frame: pl.DataFrame, request_id: str | None) -> list[dict[str, Any]]:
    """Filas de ops.walmart_item_snapshot desde el reporte ITEM. Sin columna SKU no hay nada que guardar."""
    if "SKU" not in frame.columns:
        return []
    rows: dict[str, dict[str, Any]] = {}
    for raw in frame.to_dicts():
        sku = _text(raw.get("SKU"))
        if not sku:
            continue
        row = {target: raw.get(source) for source, target in ITEM_COLUMNS.items()}
        rows[sku] = {
            "sku": sku,
            "item_id": _text(row["item_id"]),
            "product_name": _text(row["product_name"]),
            "publish_status": _text(row["publish_status"]),
            "lifecycle_status": _text(row["lifecycle_status"]),
            "status_change_reason": _text(row["status_change_reason"]),
            "fulfillment_type": _text(row["fulfillment_type"]),
            "price": _opt_float(row["price"]),
            "buybox_price": _opt_float(row["buybox_price"]),
            "buybox_eligible": _yes(row["buybox_eligible"]),
            "repricer_strategy": _text(row["repricer_strategy"]),
            "repricer_status": _text(row["repricer_status"]),
            "min_allowed_price": _opt_float(row["min_allowed_price"]),
            "max_allowed_price": _opt_float(row["max_allowed_price"]),
            "reviews_count": _opt_int(row["reviews_count"]),
            "average_rating": _opt_float(row["average_rating"]),
            "report_request_id": request_id,
        }
    return list(rows.values())


def latest_ready(requests: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Solicitud lista más reciente (por fecha de generación). None si ninguna está lista."""
    ready = [r for r in requests if r.get("requestStatus") == READY and r.get("requestId")]
    if not ready:
        return None
    return max(ready, key=lambda r: _ts(r.get("reportGenerationDate")) or datetime.min.replace(tzinfo=UTC))


# --- pedidos por surtir ------------------------------------------------------------------------------


def _order_status(order_lines: list[dict[str, Any]]) -> str | None:
    statuses = {
        s.get("status")
        for ln in order_lines
        for s in (ln.get("orderLineStatuses") or {}).get("orderLineStatus", [])
        if s.get("status")
    }
    for wanted in OPEN_ORDER_STATUS_PRIORITY:
        if wanted in statuses:
            return wanted
    return min(statuses) if statuses else None


def build_open_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pedidos por surtir SIN datos personales: id, fechas, plazo de envío, estado, SKU, unidades y venta.

    `ship_by` es `shippingInfo.estimatedShipDate` (la promesa de envío al comprador); si falta queda None y el
    análisis lo trata como "sin plazo".
    """
    rows: list[dict[str, Any]] = []
    for order in orders:
        order_id = _text(order.get("purchaseOrderId")) or _text(order.get("customerOrderId"))
        if not order_id:
            continue
        info = order.get("shippingInfo") or {}
        lines = (order.get("orderLines") or {}).get("orderLine", [])
        items: dict[str, int] = {}
        sales = 0.0
        for ln in lines:
            sku = _text((ln.get("item") or {}).get("sku")) or "(sin sku)"
            items[sku] = items.get(sku, 0) + (int(_f((ln.get("orderLineQuantity") or {}).get("amount"))) or 1)
            for ch in (ln.get("charges") or {}).get("charge", []):
                if ch.get("chargeType") == "PRODUCT":
                    sales += _f((ch.get("chargeAmount") or {}).get("amount"))
        rows.append(
            {
                "order_id": order_id,
                "ordered_at": _ts(order.get("orderDate")),
                "ship_by": _ts(info.get("estimatedShipDate")),
                "deliver_by": _ts(info.get("estimatedDeliveryDate")),
                "status": _order_status(lines),
                "sales": round(sales, 2),
                "units": sum(items.values()),
                "lines": [{"sku": sku, "units": units} for sku, units in sorted(items.items())],
            }
        )
    return rows


def iter_released_orders(client: WalmartClient, start: datetime) -> Iterator[dict[str, Any]]:
    """Pedidos liberados (por surtir) creados desde `start`. Pagina con `nextCursor`."""
    path: str = RELEASED_ORDERS_PATH
    params: dict[str, Any] | None = {
        "createdStartDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": RELEASED_PAGE,
    }
    for _ in range(MAX_PAGES):
        body = client._get(path, params).json().get("list") or {}
        yield from (body.get("elements") or {}).get("order", [])
        cursor = (body.get("meta") or {}).get("nextCursor")
        if not cursor:
            return
        # nextCursor ya trae la query completa ("?limit=..&nextCursor=..")
        path, params = RELEASED_ORDERS_PATH + cursor, None


# --- sincronización ----------------------------------------------------------------------------------


@dataclass
class InsightsBundle:
    """Filas ya normalizadas de cada recurso. Un recurso que falló queda vacío/None y se anota en `problems`."""

    quality: dict[str, Any] | None = None
    inventory: list[dict[str, Any]] | None = None  # None = no se pudo leer (no se debe podar lo guardado)
    feeds: list[dict[str, Any]] = field(default_factory=list)
    templates: list[dict[str, Any]] | None = None
    strategies: list[dict[str, Any]] | None = None
    reports: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] | None = None
    problems: list[str] = field(default_factory=list)


def _snapshot_templates(client: WalmartClient) -> list[dict[str, Any]]:
    rows = (
        build_shipping_template(t, fetch_shipping_template_detail(client, str(t.get("id"))))
        for t in fetch_shipping_templates(client)
    )
    return [r for r in rows if r is not None]


def _snapshot_reports(client: WalmartClient, bundle: InsightsBundle) -> None:
    """Solicitudes de ITEM y BUYBOX; del último ITEM listo se descarga el reporte y se extrae la foto por SKU."""
    item_requests: list[dict[str, Any]] = []
    for report_type in REPORT_TYPES:
        requests = fetch_report_requests(client, report_type)
        bundle.reports.extend(build_report_requests(requests))
        if report_type == "ITEM":
            item_requests = requests
    ready = latest_ready(item_requests)
    if ready is not None:
        frame = parse_report_bytes(download_report(client, str(ready["requestId"])))
        bundle.items = build_item_snapshots(frame, str(ready["requestId"]))


def fetch_insights(client: WalmartClient, today: date | None = None) -> InsightsBundle:
    """Lee todos los recursos. Un fallo en uno no detiene a los demás (queda en `problems`)."""
    bundle = InsightsBundle()
    day = today or datetime.now(UTC).date()
    steps: tuple[tuple[str, Callable[[], None]], ...] = (
        ("calidad", lambda: setattr(bundle, "quality", build_listing_quality(fetch_listing_quality(client), day))),
        ("inventario WFS", lambda: setattr(bundle, "inventory", build_wfs_inventory(list(iter_wfs_inventory(client))))),
        ("feeds", lambda: setattr(bundle, "feeds", build_feeds(list(iter_feeds(client))))),
        ("plantillas de envío", lambda: setattr(bundle, "templates", _snapshot_templates(client))),
        ("repricer", lambda: setattr(bundle, "strategies", build_repricer_strategies(fetch_repricer_strategies(client)))),
        ("reportes", lambda: _snapshot_reports(client, bundle)),
    )
    for name, step in steps:
        try:
            step()
        except (WalmartError, httpx.HTTPError, zipfile.BadZipFile, pl.exceptions.PolarsError) as exc:
            bundle.problems.append(f"{name}: {exc}")
    return bundle


async def sync_walmart_insights(
    session: AsyncSession, client: WalmartClient, now: datetime | None = None
) -> tuple[dict[str, int], list[str]]:
    """Lee Walmart (en un hilo: el cliente es síncrono) y guarda. Devuelve (conteos, problemas)."""
    now = now or datetime.now(UTC)
    bundle = await asyncio.to_thread(fetch_insights, client, now.date())
    stats: dict[str, int] = {}
    if bundle.quality is not None:
        stats["listing_quality"] = await upsert_listing_quality(session, bundle.quality, now)
    if bundle.inventory is not None:
        stats["wfs_inventory"] = await replace_wfs_inventory(session, bundle.inventory, now.date(), now)
    stats["feeds"] = await upsert_feeds(session, bundle.feeds)
    if bundle.templates is not None:
        stats["shipping_templates"] = await replace_shipping_templates(session, bundle.templates, now)
    if bundle.strategies is not None:
        stats["repricer_strategies"] = await replace_repricer_strategies(session, bundle.strategies, now)
    stats["report_requests"] = await upsert_report_requests(session, bundle.reports)
    if bundle.items is not None:
        stats["item_snapshots"] = await replace_item_snapshots(session, bundle.items, now)
    await session.commit()
    return stats, bundle.problems
