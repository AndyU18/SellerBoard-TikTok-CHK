"""Análisis del apartado Walmart: calidad, inventario WFS, feeds, repricer, plantillas, reportes y pedidos.

Lee lo que `app.connectors.walmart_insights` dejó en las tablas `ops.walmart_*`, lo cruza con la capa oro
(ventas diarias de Walmart), `ListingStock`, Redo y `min_price`, y devuelve diccionarios listos para la API.
Solo las funciones `report_preview` y `open_orders_*` llaman a Walmart (GET, en vivo). Nada se escribe.

Importado por `app.walmart_api` y `app.walmart_actions`.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.walmart import CHANNEL, WalmartClient
from app.connectors.walmart_insights import (
    build_open_orders,
    download_report,
    iter_released_orders,
    parse_report_bytes,
)
from app.min_price import min_price_report
from app.models import (
    Account,
    Channel,
    ChannelListing,
    GoldDailyProfit,
    ListingStock,
    Product,
    SalesOrder,
    SellerPerformance,
    ShippingLabelCost,
    SkuAlias,
    WalmartFeed,
    WalmartItemSnapshot,
    WalmartListingQuality,
    WalmartReportRequest,
    WalmartRepricerStrategy,
    WalmartShippingTemplate,
    WalmartWfsInventory,
)

DEFAULT_DAYS = 30
LOW_COVER_DAYS = 14  # cobertura bajo la cual un SKU WFS con ventas se considera con poco stock
QUALITY_TARGET = 100.0
OPEN_ORDERS_TTL_SECONDS = 60
OPEN_ORDERS_LOOKBACK_DAYS = 60
DUE_SOON_HOURS = 24
FEED_FAILURE_WINDOW_DAYS = 7
REPORT_PREVIEW_MAX_ROWS = 100
REPORT_PREVIEW_DEFAULT_ROWS = 20
REPORT_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")
QUALITY_LABELS: dict[str, str] = {
    "offer_score": "Oferta",
    "rating_review_score": "Calificaciones y reseñas",
    "content_score": "Contenido",
    "price_score": "Precio competitivo",
    "shipping_score": "Envío",
    "transactibility_score": "Transaccionabilidad",
}
FEED_FAILED_STATUSES = {"ERROR", "FAILED"}


class ReportError(ValueError):
    """Petición inválida sobre un reporte (la API la convierte en 4xx)."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _opt(value: object) -> float | None:
    return float(value) if value is not None else None  # type: ignore[arg-type]


def _aware(value: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona; Postgres con zona. Se normaliza a UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware else None


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def _window(days: int, today: date | None) -> tuple[date, date]:
    end = today or datetime.now(UTC).date()
    return end - timedelta(days=days - 1), end


# --- ventas de Walmart (capa oro) y mapa de SKU ------------------------------------------------------


async def master_sku_map(session: AsyncSession) -> dict[str, str]:
    """SKU de Walmart (o alias) -> SKU maestro. Lo que no esté mapeado se usa tal cual."""
    mapping: dict[str, str] = {}
    rows = await session.execute(
        select(ChannelListing.channel_sku, Product.sku)
        .join(Product, ChannelListing.product_id == Product.id)
        .join(Account, ChannelListing.account_id == Account.id)
        .where(Account.channel_code == CHANNEL)
    )
    mapping.update({channel_sku: master for channel_sku, master in rows.all()})
    aliases = await session.execute(select(SkuAlias.alias, Product.sku).join(Product, SkuAlias.product_id == Product.id))
    mapping.update({alias: master for alias, master in aliases.all()})
    return mapping


@dataclass(frozen=True, slots=True)
class WalmartSales:
    """Ventas de Walmart en una ventana desde la capa oro."""

    days: int
    units_by_sku: dict[str, int] = field(default_factory=dict)
    gross: float = 0.0
    net_profit: float = 0.0
    orders: int = 0
    units: int = 0

    def daily_units(self, master_sku: str) -> float:
        return self.units_by_sku.get(master_sku, 0) / self.days

    @property
    def avg_unit_price(self) -> float | None:
        return _ratio(self.gross, self.units)


async def walmart_sales(session: AsyncSession, days: int, today: date | None = None) -> WalmartSales:
    """Unidades por SKU maestro y totales de Walmart en los últimos `days` días."""
    start, end = _window(days, today)
    rows = await session.execute(
        select(
            Product.sku,
            func.sum(GoldDailyProfit.units),
            func.sum(GoldDailyProfit.gross_sales),
            func.sum(GoldDailyProfit.net_profit),
            func.sum(GoldDailyProfit.orders),
        )
        .outerjoin(Product, GoldDailyProfit.product_id == Product.id)
        .where(
            GoldDailyProfit.channel_code == CHANNEL,
            GoldDailyProfit.profit_date >= start,
            GoldDailyProfit.profit_date <= end,
        )
        .group_by(Product.sku)
    )
    units_by_sku: dict[str, int] = {}
    gross = profit = 0.0
    orders = units = 0
    for sku, u, g, p, o in rows.all():
        if sku:
            units_by_sku[sku] = int(u or 0)
        units += int(u or 0)
        gross += _f(g)
        profit += _f(p)
        orders += int(o or 0)
    return WalmartSales(days, units_by_sku, gross, profit, orders, units)


# --- calidad de listados -----------------------------------------------------------------------------


def _quality_components(row: WalmartListingQuality) -> list[dict[str, Any]]:
    components = [
        {
            "key": key,
            "label": label,
            "score": _opt(getattr(row, key)),
            "gap_to_100": round(QUALITY_TARGET - _f(getattr(row, key)), 2) if getattr(row, key) is not None else None,
        }
        for key, label in QUALITY_LABELS.items()
    ]
    return sorted(components, key=lambda c: (c["score"] is None, c["score"] if c["score"] is not None else 0.0))


async def latest_listing_quality(session: AsyncSession) -> WalmartListingQuality | None:
    return await session.scalar(
        select(WalmartListingQuality).order_by(WalmartListingQuality.snapshot_date.desc()).limit(1)
    )


async def listing_quality(session: AsyncSession, *, days: int = 90, today: date | None = None) -> dict[str, Any]:
    """Último puntaje, historial diario, componentes ordenados de peor a mejor y defectos posteriores a la compra."""
    start, _ = _window(days, today)
    history = (
        await session.scalars(
            select(WalmartListingQuality)
            .where(WalmartListingQuality.snapshot_date >= start)
            .order_by(WalmartListingQuality.snapshot_date)
        )
    ).all()
    latest = await latest_listing_quality(session)
    previous = history[-2] if len(history) >= 2 else None
    delta = (
        round(_f(latest.listing_quality) - _f(previous.listing_quality), 2)
        if latest and previous and latest.listing_quality is not None and previous.listing_quality is not None
        else None
    )
    return {
        "days": days,
        "latest": (
            {
                "snapshot_date": latest.snapshot_date,
                "listing_quality": _opt(latest.listing_quality),
                "change_vs_previous": delta,
                "components": _quality_components(latest),
                "defects": {
                    "item_defect_count": latest.item_defect_count,
                    "defect_ratio": _opt(latest.defect_ratio),
                },
            }
            if latest
            else None
        ),
        "history": [
            {
                "date": h.snapshot_date,
                "listing_quality": _opt(h.listing_quality),
                **{key: _opt(getattr(h, key)) for key in QUALITY_LABELS},
            }
            for h in history
        ],
        "item_level": {
            "available": False,
            "reason": "Walmart solo entrega la calidad por ítem con POST (/listingQuality/items); esta app es de solo lectura.",
        },
    }


# --- inventario WFS ----------------------------------------------------------------------------------


def _stock_status(avail: int, daily: float, cover: float | None, low_days: int) -> str:
    if avail <= 0:
        return "out" if daily > 0 else "empty_no_sales"
    if cover is not None and cover < low_days:
        return "low"
    return "ok" if daily > 0 else "no_sales"


async def _seller_stock(session: AsyncSession) -> dict[str, int | None]:
    """Existencias del inventario normal (ListingStock) de las cuentas Walmart, por SKU de Walmart."""
    rows = await session.execute(
        select(ListingStock.sku, ListingStock.avail_to_sell)
        .join(Account, ListingStock.account_id == Account.id)
        .where(Account.channel_code == CHANNEL)
    )
    return {sku: avail for sku, avail in rows.all()}


async def wfs_inventory(
    session: AsyncSession,
    *,
    days: int = DEFAULT_DAYS,
    low_cover_days: int = LOW_COVER_DAYS,
    today: date | None = None,
) -> dict[str, Any]:
    """Existencias WFS por SKU y nodo, cobertura en días con las ventas de Walmart y contraste con ListingStock."""
    sales = await walmart_sales(session, days, today)
    master = await master_sku_map(session)
    seller_stock = await _seller_stock(session)
    nodes = (await session.scalars(select(WalmartWfsInventory).order_by(WalmartWfsInventory.sku))).all()

    rows: list[dict[str, Any]] = []
    for n in nodes:
        daily = sales.daily_units(master.get(n.sku, n.sku))
        cover = _ratio(n.avail_to_sell, daily)
        status = _stock_status(n.avail_to_sell, daily, cover, low_cover_days)
        seller = seller_stock.get(n.sku)
        rows.append(
            {
                "sku": n.sku,
                "node": n.node,
                "avail_to_sell": n.avail_to_sell,
                "on_hand": n.on_hand,
                "in_transit_or_reserved": max(n.on_hand - n.avail_to_sell, 0),
                "daily_units": round(daily, 3),
                "cover_days": round(cover, 1) if cover is not None else None,
                "status": status,
                "seller_fulfilled_stock": seller,
                "stocked_elsewhere": status in {"out", "empty_no_sales"} and (seller or 0) > 0,
                "snapshot_at": _iso(n.snapshot_at),
            }
        )
    order = {"out": 0, "low": 1, "ok": 2, "no_sales": 3, "empty_no_sales": 4}
    rows.sort(key=lambda r: (order[r["status"]], r["cover_days"] if r["cover_days"] is not None else 1e9, r["sku"]))
    return {
        "days": days,
        "low_cover_days": low_cover_days,
        "summary": {
            "skus": len({r["sku"] for r in rows}),
            "out_of_stock": sum(1 for r in rows if r["status"] == "out"),
            "out_of_stock_total": sum(1 for r in rows if r["avail_to_sell"] <= 0),
            "low_stock": sum(1 for r in rows if r["status"] == "low"),
            "stocked_elsewhere": sum(1 for r in rows if r["stocked_elsewhere"]),
            "avail_to_sell_units": sum(r["avail_to_sell"] for r in rows),
            "snapshot_at": max((r["snapshot_at"] for r in rows if r["snapshot_at"]), default=None),
        },
        "rows": rows,
    }


# --- feeds -------------------------------------------------------------------------------------------


def _feed_failed(feed: WalmartFeed) -> bool:
    return feed.items_failed > 0 or (feed.status or "").upper() in FEED_FAILED_STATUSES


def _feed_view(feed: WalmartFeed) -> dict[str, Any]:
    return {
        "feed_id": feed.feed_id,
        "feed_type": feed.feed_type,
        "status": feed.status,
        "items_received": feed.items_received,
        "items_succeeded": feed.items_succeeded,
        "items_failed": feed.items_failed,
        "items_processing": feed.items_processing,
        "data_errors": feed.data_error_count,
        "system_errors": feed.system_error_count,
        "feed_date": _iso(feed.feed_date),
    }


async def recent_feeds(session: AsyncSession, days: int, now: datetime | None = None) -> list[WalmartFeed]:
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    feeds = (await session.scalars(select(WalmartFeed).order_by(WalmartFeed.feed_date.desc()))).all()
    return [f for f in feeds if (d := _aware(f.feed_date)) is not None and d >= since]


async def feeds_report(
    session: AsyncSession, *, days: int = DEFAULT_DAYS, limit: int = 50, now: datetime | None = None
) -> dict[str, Any]:
    """Cargas recientes, tasa de éxito (ítems exitosos / recibidos), por tipo y las que traen fallos."""
    feeds = await recent_feeds(session, days, now)
    received = sum(f.items_received for f in feeds)
    succeeded = sum(f.items_succeeded for f in feeds)
    by_type: dict[str, dict[str, Any]] = defaultdict(lambda: {"feeds": 0, "received": 0, "succeeded": 0, "failed": 0})
    for f in feeds:
        t = by_type[f.feed_type or "UNKNOWN"]
        t["feeds"] += 1
        t["received"] += f.items_received
        t["succeeded"] += f.items_succeeded
        t["failed"] += f.items_failed
    failing = [f for f in feeds if _feed_failed(f)]
    return {
        "days": days,
        "summary": {
            "feeds": len(feeds),
            "items_received": received,
            "items_succeeded": succeeded,
            "items_failed": sum(f.items_failed for f in feeds),
            "success_rate": _ratio(succeeded, received),
            "feeds_with_failures": len(failing),
        },
        "by_type": [
            {"feed_type": k, **v, "success_rate": _ratio(v["succeeded"], v["received"])}
            for k, v in sorted(by_type.items())
        ],
        "with_failures": [_feed_view(f) for f in failing[:limit]],
        "recent": [_feed_view(f) for f in feeds[:limit]],
    }


# --- repricer ----------------------------------------------------------------------------------------


def _repricer_issue(item: WalmartItemSnapshot, row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Problema de un SKU asignado al repricer contra su precio mínimo real (envío real de Redo incluido)."""
    if row is None or row["status"] in {"insufficient", "no_cost", "unreachable"} or row["min_price"] is None:
        return None
    floor = float(row["min_price"])
    allowed = _opt(item.min_allowed_price)
    if row["status"] == "below":
        return {"type": "realized_below_floor", "gap": round(floor - float(row["realized_price"]), 2)}
    if allowed is None:
        return {"type": "no_repricer_floor", "gap": None}
    if allowed < floor:
        return {"type": "repricer_floor_below_min", "gap": round(floor - allowed, 2)}
    return None


async def repricer_report(session: AsyncSession, *, days: int = 90, today: date | None = None) -> dict[str, Any]:
    """Estrategias, SKU asignados (del reporte ITEM) y su cruce con el precio mínimo real de `min_price`."""
    strategies = (await session.scalars(select(WalmartRepricerStrategy).order_by(WalmartRepricerStrategy.strategy))).all()
    items = (await session.scalars(select(WalmartItemSnapshot).where(WalmartItemSnapshot.repricer_strategy.is_not(None)))).all()
    all_items = await session.scalar(select(func.count()).select_from(WalmartItemSnapshot)) or 0
    master = await master_sku_map(session)
    report = await min_price_report(session, days=days, target_margin=0.0, channel=Channel.walmart, today=today)
    floors = {r["sku"]: r for r in report["rows"] if r["channel"] == CHANNEL}

    assigned: list[dict[str, Any]] = []
    for it in items:
        row = floors.get(master.get(it.sku, it.sku))
        issue = _repricer_issue(it, row)
        assigned.append(
            {
                "sku": it.sku,
                "product_name": it.product_name,
                "strategy": it.repricer_strategy,
                "repricer_status": it.repricer_status,
                "price": _opt(it.price),
                "buybox_price": _opt(it.buybox_price),
                "repricer_min_price": _opt(it.min_allowed_price),
                "repricer_max_price": _opt(it.max_allowed_price),
                "real_min_price": _opt(row["min_price"]) if row else None,
                "realized_price": _opt(row["realized_price"]) if row else None,
                "floor_status": row["status"] if row else None,
                "issue": issue,
            }
        )
    assigned.sort(key=lambda a: (a["issue"] is None, a["sku"]))
    per_strategy: dict[str, int] = defaultdict(int)
    for a in assigned:
        per_strategy[a["strategy"]] += 1
    return {
        "days": days,
        "link": {
            "available": all_items > 0,
            "source": "reporte ITEM de Walmart (columnas Repricer Strategy / Minimum Seller Allowed Price)",
            "note": (
                "La API /v3/repricer no lista los SKU asignados; el vínculo SKU -> estrategia sale del último "
                "reporte ITEM y puede ir un poco atrás de la configuración."
                if all_items
                else "Aún no hay un reporte ITEM descargado: el vínculo SKU -> estrategia no está disponible."
            ),
        },
        "strategies": [
            {
                "collection_id": s.collection_id,
                "strategy": s.strategy,
                "enabled": bool(s.enabled),
                "assigned_count": s.assigned_count,
                "assigned_in_report": per_strategy.get(s.strategy, 0),
                "enable_for_promotion": s.enable_for_promotion,
                "restore_seller_price_without_target": s.restore_seller_price_without_target,
                "enable_buybox_meet_external": s.enable_buybox_meet_external,
                "compare_with_3p_offer_only": s.compare_with_3p_offer_only,
                "rules": s.strategies,
            }
            for s in strategies
        ],
        "assigned_skus": assigned,
        "summary": {
            "strategies": len(strategies),
            "enabled": sum(1 for s in strategies if s.enabled),
            "assigned_total": sum(s.assigned_count for s in strategies),
            "assigned_in_report": len(assigned),
            "with_issue": sum(1 for a in assigned if a["issue"]),
        },
    }


# --- plantillas de envío -----------------------------------------------------------------------------


def _charge(node: dict[str, Any] | None) -> float | None:
    return _opt(node.get("amount")) if isinstance(node, dict) else None


def _template_methods(detail: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Métodos con tiempo de tránsito y lo que cobra al comprador (tarifa fija + por artículo/peso)."""
    methods: list[dict[str, Any]] = []
    for m in (detail or {}).get("shippingMethods") or []:
        for cfg in m.get("configurations") or [{}]:
            per = cfg.get("perShippingCharge") or {}
            methods.append(
                {
                    "ship_method": m.get("shipMethod"),
                    "status": m.get("status"),
                    "transit_days": cfg.get("transitTime"),
                    "regions": [r.get("regionCode") for r in cfg.get("regions") or []],
                    "flat_charge": _charge(per.get("shippingAndHandling")),
                    "charge_per_item": _charge(per.get("chargePerItem")),
                    "charge_per_weight": _charge(per.get("chargePerWeight")),
                    "weight_unit": per.get("unitOfMeasure"),
                }
            )
    return methods


async def redo_label_average(session: AsyncSession, days: int, today: date | None = None) -> dict[str, Any]:
    """Costo neto promedio de etiqueta de Redo por orden de Walmart (órdenes con etiqueta cobrada)."""
    start, end = _window(days, today)
    rows = await session.execute(
        select(ShippingLabelCost.amount)
        .join(SalesOrder, SalesOrder.external_order_id == ShippingLabelCost.external_order_id)
        .join(Account, SalesOrder.account_id == Account.id)
        .where(
            Account.channel_code == CHANNEL,
            SalesOrder.order_date >= start,
            SalesOrder.order_date <= end,
            ShippingLabelCost.amount > 0,
        )
    )
    amounts = [_f(a) for (a,) in rows.all()]
    return {
        "orders": len(amounts),
        "avg_label_cost": (sum(amounts) / len(amounts)) if amounts else None,
    }


async def shipping_templates_report(session: AsyncSession, *, days: int = 90, today: date | None = None) -> dict[str, Any]:
    """Plantillas, tipo, estado y tarifas al comprador; contra el costo real promedio de etiqueta en Redo."""
    templates = (await session.scalars(select(WalmartShippingTemplate).order_by(WalmartShippingTemplate.name))).all()
    label = await redo_label_average(session, days, today)
    avg = label["avg_label_cost"]
    out: list[dict[str, Any]] = []
    for t in templates:
        methods = _template_methods(t.detail)
        for m in methods:
            charged = (m["flat_charge"] or 0.0) + (m["charge_per_item"] or 0.0)
            m["charged_per_single_item_order"] = round(charged, 2)
            m["absorbed_vs_label"] = round(avg - charged, 2) if avg is not None else None
        out.append(
            {
                "id": t.id,
                "name": t.name,
                "type": t.type,
                "rate_model_type": t.rate_model_type,
                "status": t.status,
                "has_detail": t.detail is not None,
                "methods": methods,
                "snapshot_at": _iso(t.snapshot_at),
            }
        )
    return {
        "days": days,
        "redo_label": {
            **label,
            "note": (
                None
                if avg is not None
                else "No hay etiquetas de Redo ligadas a órdenes de Walmart en la ventana: no se puede comparar."
            ),
        },
        "comparison_note": (
            "Compara lo que la plantilla cobra al comprador por una orden de 1 artículo contra el costo promedio "
            "de etiqueta; la tarifa por peso no se estima porque depende del peso de cada pedido."
        ),
        "templates": out,
        "summary": {
            "templates": len(out),
            "active": sum(1 for t in out if (t["status"] or "").upper() == "ACTIVE"),
            "free_shipping_methods": sum(
                1 for t in out for m in t["methods"] if m["charged_per_single_item_order"] == 0
            ),
        },
    }


# --- reportes ----------------------------------------------------------------------------------------


async def reports_list(session: AsyncSession, *, limit: int = 50) -> dict[str, Any]:
    rows = (
        await session.scalars(select(WalmartReportRequest).order_by(WalmartReportRequest.submitted_at.desc()).limit(limit))
    ).all()
    by_status: dict[str, int] = defaultdict(int)
    for r in rows:
        by_status[r.status or "UNKNOWN"] += 1
    return {
        "summary": {"requests": len(rows), "by_status": dict(by_status)},
        "requests": [
            {
                "request_id": r.request_id,
                "report_type": r.report_type,
                "status": r.status,
                "report_version": r.report_version,
                "submitted_at": _iso(r.submitted_at),
                "generated_at": _iso(r.generated_at),
            }
            for r in rows
        ],
    }


def summarize_frame(frame: pl.DataFrame) -> list[dict[str, Any]]:
    """Resumen simple por columna: cuántas filas traen valor."""
    total = frame.height
    return [
        {
            "name": name,
            "filled": (filled := total - frame[name].null_count() - int((frame[name] == "").sum())),
            "filled_pct": round(filled / total, 4) if total else None,
        }
        for name in frame.columns
    ]


def preview_frame(frame: pl.DataFrame, limit: int) -> dict[str, Any]:
    """Encabezados, filas de vista previa y resumen de un reporte ya parseado (todo en memoria)."""
    return {
        "headers": frame.columns,
        "rows": frame.head(limit).to_dicts(),
        "preview_rows": min(limit, frame.height),
        "total_rows": frame.height,
        "columns": summarize_frame(frame),
    }


async def report_preview(
    session: AsyncSession, client: WalmartClient, request_id: str, limit: int = REPORT_PREVIEW_DEFAULT_ROWS
) -> dict[str, Any]:
    """Descarga el reporte YA generado, lo parsea con polars y devuelve la vista previa. No guarda archivos."""
    if not REPORT_ID_PATTERN.match(request_id):
        raise ReportError(422, "Identificador de reporte inválido")
    limit = max(1, min(limit, REPORT_PREVIEW_MAX_ROWS))
    known = await session.get(WalmartReportRequest, request_id)
    if known is not None and (known.status or "").upper() != "READY":
        raise ReportError(409, f"El reporte aún no está listo (estado {known.status})")
    blob = await asyncio.to_thread(download_report, client, request_id)
    frame = await asyncio.to_thread(parse_report_bytes, blob)
    return {
        "request_id": request_id,
        "report_type": known.report_type if known else None,
        "bytes": len(blob),
        **preview_frame(frame, limit),
    }


# --- pedidos por surtir (en vivo, con caché) ---------------------------------------------------------


@dataclass
class OpenOrdersCache:
    """Caché en memoria de los pedidos por surtir: protege a Walmart de una consulta por cada refresco."""

    ttl_seconds: float = OPEN_ORDERS_TTL_SECONDS
    clock: Callable[[], float] = time.monotonic
    _rows: list[dict[str, Any]] | None = None
    _stored_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def fresh(self) -> bool:
        return self._rows is not None and self.clock() - self._stored_at < self.ttl_seconds

    async def get(self, loader: Callable[[], list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], bool]:
        """(filas, vino_de_caché). Un solo refresco a la vez: las demás peticiones esperan y reutilizan."""
        async with self._lock:
            if self.fresh() and self._rows is not None:
                return self._rows, True
            self._rows = await asyncio.to_thread(loader)
            self._stored_at = self.clock()
            return self._rows, False

    def clear(self) -> None:
        self._rows, self._stored_at = None, 0.0


OPEN_ORDERS_CACHE = OpenOrdersCache()


def load_open_orders(client: WalmartClient, now: datetime | None = None) -> list[dict[str, Any]]:
    """Llama a Walmart (GET /v3/orders/released) y normaliza sin datos personales."""
    start = (now or datetime.now(UTC)) - timedelta(days=OPEN_ORDERS_LOOKBACK_DAYS)
    return build_open_orders(list(iter_released_orders(client, start)))


def open_orders_view(rows: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Horas que faltan para el plazo de envío; marca vencidos y por vencer en 24 h."""
    orders: list[dict[str, Any]] = []
    for r in rows:
        ship_by = _aware(r["ship_by"])
        hours = round((ship_by - now).total_seconds() / 3600, 1) if ship_by else None
        orders.append(
            {
                "order_id": r["order_id"],
                "ordered_at": _iso(r["ordered_at"]),
                "ship_by": _iso(ship_by),
                "deliver_by": _iso(r["deliver_by"]),
                "hours_left": hours,
                "overdue": hours is not None and hours < 0,
                "due_soon": hours is not None and 0 <= hours <= DUE_SOON_HOURS,
                "status": r["status"],
                "units": r["units"],
                "sales": r["sales"],
                "lines": r["lines"],
            }
        )
    orders.sort(key=lambda o: (o["hours_left"] is None, o["hours_left"] if o["hours_left"] is not None else 0.0))
    overdue = [o for o in orders if o["overdue"]]
    soon = [o for o in orders if o["due_soon"]]
    return {
        "summary": {
            "open_orders": len(orders),
            "overdue": len(overdue),
            "due_soon": len(soon),
            "no_deadline": sum(1 for o in orders if o["hours_left"] is None),
            "units": sum(o["units"] for o in orders),
            "sales": round(sum(o["sales"] for o in orders), 2),
            "sales_overdue": round(sum(o["sales"] for o in overdue), 2),
            "sales_due_soon": round(sum(o["sales"] for o in soon), 2),
            "due_soon_window_hours": DUE_SOON_HOURS,
        },
        "orders": orders,
    }


async def open_orders(
    client: WalmartClient, *, now: datetime | None = None, cache: OpenOrdersCache | None = None
) -> dict[str, Any]:
    """Pedidos por surtir en vivo (caché de 60 s). Las horas se recalculan en cada llamada."""
    now = now or datetime.now(UTC)
    store = cache or OPEN_ORDERS_CACHE
    rows, cached = await store.get(lambda: load_open_orders(client, now))
    view = open_orders_view(rows, now)
    return {**view, "cached": cached, "cache_ttl_seconds": int(store.ttl_seconds), "as_of": now.isoformat()}


# --- portada -----------------------------------------------------------------------------------------


async def seller_performance_latest(session: AsyncSession) -> dict[str, Any]:
    """Última foto de los indicadores del vendedor (OTD, OTS, VTR, reembolsos, cancelaciones) por ventana."""
    latest = await session.scalar(
        select(func.max(SellerPerformance.snapshot_date))
        .join(Account, SellerPerformance.account_id == Account.id)
        .where(Account.channel_code == CHANNEL)
    )
    if latest is None:
        return {"snapshot_date": None, "windows": []}
    rows = (
        await session.scalars(
            select(SellerPerformance)
            .join(Account, SellerPerformance.account_id == Account.id)
            .where(Account.channel_code == CHANNEL, SellerPerformance.snapshot_date == latest)
            .order_by(SellerPerformance.window_days)
        )
    ).all()
    return {
        "snapshot_date": latest,
        "windows": [
            {
                "window_days": r.window_days,
                "ots": _opt(r.ots),
                "otd": _opt(r.otd),
                "vtr": _opt(r.vtr),
                "refund_rate_seller_fault": _opt(r.refund_rate_seller_fault),
                "cancellation_seller_fault": _opt(r.cancellation_seller_fault),
            }
            for r in rows
        ],
    }


async def overview(
    session: AsyncSession,
    *,
    days: int = DEFAULT_DAYS,
    open_orders_summary: dict[str, Any] | None = None,
    today: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resumen para la portada Walmart. `open_orders_summary` lo aporta la API (llamada en vivo, puede faltar)."""
    sales = await walmart_sales(session, days, today)
    latest = await latest_listing_quality(session)
    feeds = await recent_feeds(session, FEED_FAILURE_WINDOW_DAYS, now)
    out_of_stock = await session.scalar(
        select(func.count()).select_from(WalmartWfsInventory).where(WalmartWfsInventory.avail_to_sell <= 0)
    )
    return {
        "days": days,
        "kpis": {
            "gross_sales": round(sales.gross, 2),
            "net_profit": round(sales.net_profit, 2),
            "orders": sales.orders,
            "units": sales.units,
            "margin": _ratio(sales.net_profit, sales.gross),
        },
        "listing_quality": (
            {"snapshot_date": latest.snapshot_date, "score": _opt(latest.listing_quality)} if latest else None
        ),
        "seller_performance": await seller_performance_latest(session),
        "open_orders": open_orders_summary,
        "feeds_with_failures": sum(1 for f in feeds if _feed_failed(f)),
        "feed_failure_window_days": FEED_FAILURE_WINDOW_DAYS,
        "wfs_out_of_stock_skus": int(out_of_stock or 0),
    }

