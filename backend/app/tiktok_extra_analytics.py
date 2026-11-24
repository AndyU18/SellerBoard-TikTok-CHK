"""Análisis del apartado TikTok Shop (datos extra): rendimiento, catálogo, promociones, cupones, paquetes, pagos.

Lee las tablas que llena `sync_jobs` (fuente `tiktok_shop_extra`) y las cruza con la capa oro y la plata:
  * SKU de TikTok (`seller_sku`) -> producto maestro: canal TikTok (ChannelListing) > alias > mismo SKU.
  * Utilidad por producto: `gold.daily_profit` del canal TikTok Shop dentro de la ventana de la foto de
    rendimiento (así GMV y utilidad cubren los mismos días). Si dos productos de TikTok comparten SKU maestro,
    cada uno ve la utilidad de ese SKU completa: es un cruce, no un reparto.
  * El GMV de TikTok incluye pedidos aún no liquidados; la capa oro solo trae lo liquidado. Por eso la utilidad
    y el GMV de un mismo producto pueden no coincidir: GMV es la demanda, la utilidad es lo ya cobrado.

Todo es de solo lectura. Las funciones de reglas (`is_problem_product`, `stuck_cutoff`...) son puras.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_shop_extra import (
    DOCUMENTED_WEBHOOK_EVENTS,
    PACKAGE_COMPLETED,
    PACKAGE_TERMINAL,
)
from app.models import (
    Account,
    ChannelListing,
    GoldDailyProfit,
    OrderLine,
    Product,
    SalesOrder,
    Settlement,
    SkuAlias,
    SyncRun,
    TikTokShopCoupon,
    TikTokShopPackage,
    TikTokShopPayment,
    TikTokShopProduct,
    TikTokShopProductPerformance,
    TikTokShopProductSku,
    TikTokShopPromotion,
    TikTokShopPromotionProduct,
    TikTokShopSkuPerformance,
    TikTokShopWarehouse,
    TikTokShopWebhook,
    TikTokShopWithdrawal,
)

CHANNEL = "tiktok_shop"
ACTIVE_PRODUCT_STATUS = "ACTIVATE"  # así lo escribe TikTok
AUDIT_FAILED = "FAILED"
PAID_STATUS = "PAID"
FAILED_PAYMENT_STATUSES = frozenset({"FAILED", "DECLINED", "REJECTED", "RETURNED", "CANCELLED"})
RECONCILE_TOLERANCE_USD = 1.0  # diferencias menores son redondeo/ajustes, no un problema
DEFAULT_STUCK_DAYS = 5
PROMO_BASELINE_DAYS = 14  # días previos a la promoción contra los que se compara
MIN_TRAFFIC_VIEWS = 500  # vistas mínimas para decir que un producto tiene "tráfico alto"
HIGH_TRAFFIC_QUANTILE = 0.75
LOW_CONVERSION_RATIO = 0.5  # conversión bajo la mitad de la de la tienda = "baja"
RECEIVABLE_LOOKBACK_DAYS = 300  # los pagos se traen 365 días: más viejo que esto no se puede enlazar con confianza
DELIVERY_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-2 días", 0, 2),
    ("3-4 días", 3, 4),
    ("5-7 días", 5, 7),
    ("8-14 días", 8, 14),
    ("15+ días", 15, None),
)
MAX_ROWS = 500


def _f(value: object) -> float:
    return float(value or 0)  # type: ignore[arg-type]


def _opt_f(value: object) -> float | None:
    """Decimal de la base -> float (JSON), conservando None (dato ausente != cero)."""
    return None if value is None else float(value)  # type: ignore[arg-type]


def _r(value: float, digits: int = 2) -> float:
    return round(value, digits)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite devuelve fechas sin zona; en Postgres ya vienen con ella. Todo se compara en UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


# --- reglas puras ------------------------------------------------------------------------------------


def is_problem_product(status: str | None, audit_status: str | None) -> bool:
    """Un producto "con problema" está rechazado en la auditoría o no está activo (desactivado, eliminado, fallido)."""
    return audit_status == AUDIT_FAILED or (status or "") != ACTIVE_PRODUCT_STATUS


def package_last_movement(updated_at: datetime | None, last_event_at: datetime | None) -> datetime | None:
    """Último movimiento conocido de un paquete: lo más reciente entre su actualización y su último evento."""
    moments = [m for m in (_aware(updated_at), _aware(last_event_at)) if m is not None]
    return max(moments) if moments else None


def is_stuck(status: str | None, last_movement: datetime | None, now: datetime, stuck_days: int) -> bool:
    """Atascado = no terminado y sin movimiento hace más de `stuck_days` días."""
    if status in PACKAGE_TERMINAL or last_movement is None:
        return False
    return now - last_movement > timedelta(days=stuck_days)


def delivery_bucket(days: float) -> str:
    return next(name for name, lo, hi in DELIVERY_BUCKETS if days >= lo and (hi is None or days < hi + 1))


def discount_label(discount_type: str | None, percent: float | None, amount: float | None, max_discount: float | None) -> str:
    """Descuento de un cupón en una frase corta."""
    if discount_type == "PERCENT_OFF" and percent is not None:
        cap = f" (máx. ${max_discount:,.0f})" if max_discount else ""
        return f"{percent:g} % de descuento{cap}"
    if discount_type == "AMOUNT_OFF" and amount is not None:
        return f"${amount:,.2f} de descuento"
    return discount_type or "—"


def quantile(values: list[float], q: float) -> float:
    """Cuantil por posición (sin interpolar); 0 si no hay valores."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


# --- cruce con la capa oro / plata -------------------------------------------------------------------


async def master_product_ids(session: AsyncSession, seller_skus: set[str]) -> dict[str, int]:
    """seller_sku de TikTok -> id del producto maestro (canal TikTok > alias > mismo SKU maestro)."""
    if not seller_skus:
        return {}
    found: dict[str, int] = {}
    direct = await session.execute(select(Product.sku, Product.id).where(Product.sku.in_(seller_skus)))
    found.update({sku: pid for sku, pid in direct.all()})
    alias = await session.execute(select(SkuAlias.alias, SkuAlias.product_id).where(SkuAlias.alias.in_(seller_skus)))
    found.update({a: pid for a, pid in alias.all()})
    listed = await session.execute(
        select(ChannelListing.channel_sku, ChannelListing.product_id)
        .join(Account, Account.id == ChannelListing.account_id)
        .where(Account.channel_code == CHANNEL, ChannelListing.channel_sku.in_(seller_skus))
    )
    found.update({sku: pid for sku, pid in listed.all()})
    return found


async def gold_by_product(session: AsyncSession, start: date, end: date) -> dict[int, dict[str, float]]:
    """Ventas, unidades, órdenes y utilidad de TikTok Shop por producto maestro entre dos fechas (inclusive)."""
    g = GoldDailyProfit
    rows = await session.execute(
        select(g.product_id, func.sum(g.gross_sales), func.sum(g.units), func.sum(g.orders), func.sum(g.net_profit))
        .where(g.channel_code == CHANNEL, g.product_id.is_not(None), g.profit_date >= start, g.profit_date <= end)
        .group_by(g.product_id)
    )
    return {
        pid: {"sales": _f(s), "units": _f(u), "orders": _f(o), "profit": _f(p)} for pid, s, u, o, p in rows.all()
    }


async def _seller_skus_by_product(session: AsyncSession) -> dict[str, list[str]]:
    """product_id de TikTok -> sus seller_sku."""
    out: dict[str, list[str]] = defaultdict(list)
    for product_id, seller_sku in (await session.execute(select(TikTokShopProductSku.product_id, TikTokShopProductSku.seller_sku))).all():
        if seller_sku:
            out[product_id].append(seller_sku)
    return out


@dataclass(frozen=True, slots=True)
class Crosswalk:
    """Une productos/SKU de TikTok con la utilidad de la capa oro."""

    sku_to_master: dict[str, int]
    gold: dict[int, dict[str, float]]
    skus_by_product: dict[str, list[str]]

    def profit_for_skus(self, seller_skus: list[str]) -> dict[str, float] | None:
        """Suma de la capa oro de los SKU maestros de esos seller_sku. None si ninguno está vinculado."""
        masters = {self.sku_to_master[s] for s in seller_skus if s in self.sku_to_master}
        if not masters:
            return None
        zero = {"sales": 0.0, "units": 0.0, "orders": 0.0, "profit": 0.0}
        total = dict(zero)
        for pid in masters:
            for key, value in self.gold.get(pid, zero).items():
                total[key] += value
        return total


async def build_crosswalk(session: AsyncSession, start: date, end: date) -> Crosswalk:
    by_product = await _seller_skus_by_product(session)
    all_skus = {s for skus in by_product.values() for s in skus}
    return Crosswalk(await master_product_ids(session, all_skus), await gold_by_product(session, start, end), by_product)


async def _latest_day(session: AsyncSession, column: Any) -> date | None:
    return await session.scalar(select(func.max(column)))


async def _titles(session: AsyncSession) -> dict[str, dict[str, Any]]:
    rows = await session.scalars(select(TikTokShopProduct))
    return {p.product_id: {"title": p.title, "thumb_url": p.thumb_url, "image_url": p.main_image_url, "status": p.status, "tier": p.listing_quality_tier} for p in rows}


# --- rendimiento por producto y por SKU --------------------------------------------------------------


async def product_performance(session: AsyncSession, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Última foto de rendimiento por producto (GMV, pedidos, CTR, conversión) + utilidad real + tráfico sin conversión.

    `days` fija la tendencia diaria que se devuelve; la foto es siempre la más reciente (ventana fija de 30 días).
    """
    today = _now(now).date()
    latest = await _latest_day(session, TikTokShopProductPerformance.snapshot_date)
    if latest is None:
        return {"available": False, "window": None, "summary": None, "products": [], "traffic_low_conversion": [], "trend": []}
    P = TikTokShopProductPerformance
    perf = list(await session.scalars(select(P).where(P.snapshot_date == latest).order_by(P.gmv.desc())))
    window = (perf[0].window_start, perf[0].window_end)
    cross = await build_crosswalk(session, window[0], window[1])
    titles = await _titles(session)

    total_orders = sum(p.orders for p in perf)
    total_visitors = sum(p.page_visitors or 0 for p in perf)
    store_conversion = _ratio(total_orders, total_visitors)
    views = [float(p.page_views) for p in perf if p.page_views]
    high_views = max(quantile(views, HIGH_TRAFFIC_QUANTILE), MIN_TRAFFIC_VIEWS)

    rows: list[dict[str, Any]] = []
    for rank, p in enumerate(perf, start=1):
        gold = cross.profit_for_skus(cross.skus_by_product.get(p.product_id, []))
        info = titles.get(p.product_id, {})
        conversion = _f(p.conversion_rate) if p.conversion_rate is not None else None
        low_conv = bool(
            p.page_views
            and p.page_views >= high_views
            and conversion is not None
            and store_conversion
            and conversion < store_conversion * LOW_CONVERSION_RATIO
        )
        rows.append(
            {
                "rank": rank,
                "product_id": p.product_id,
                "title": info.get("title"),
                "thumb_url": info.get("thumb_url"),
                "status": info.get("status"),
                "gmv": _r(_f(p.gmv)),
                "orders": p.orders,
                "units_sold": p.units_sold,
                "impressions": p.impressions,
                "page_views": p.page_views,
                "page_visitors": p.page_visitors,
                "click_through_rate": _f(p.click_through_rate) if p.click_through_rate is not None else None,
                "conversion_rate": conversion,
                "gmv_by_source": {
                    "video": _opt_f(p.gmv_video),
                    "live": _opt_f(p.gmv_live),
                    "product_card": _opt_f(p.gmv_product_card),
                },
                "profit": _r(gold["profit"]) if gold else None,
                "settled_sales": _r(gold["sales"]) if gold else None,
                "margin": _ratio(gold["profit"], gold["sales"]) if gold and gold["sales"] else None,
                "linked_to_master": gold is not None,
                "high_traffic_low_conversion": low_conv,
            }
        )
    flagged = sorted((r for r in rows if r["high_traffic_low_conversion"]), key=lambda r: -(r["page_views"] or 0))
    trend_rows = await session.execute(
        select(P.snapshot_date, func.sum(P.gmv), func.sum(P.orders), func.sum(P.units_sold))
        .where(P.snapshot_date >= today - timedelta(days=days))
        .group_by(P.snapshot_date)
        .order_by(P.snapshot_date)
    )
    return {
        "available": True,
        "window": {"start": window[0], "end": window[1], "snapshot_date": latest},
        "summary": {
            "products": len(rows),
            "gmv": _r(sum(r["gmv"] for r in rows)),
            "orders": total_orders,
            "units_sold": sum(r["units_sold"] for r in rows),
            "impressions": sum(r["impressions"] or 0 for r in rows),
            "page_views": sum(r["page_views"] or 0 for r in rows),
            "store_conversion_rate": store_conversion,
            "profit": _r(sum(r["profit"] or 0 for r in rows)),
            "high_traffic_views_threshold": _r(high_views, 0),
            "with_traffic_detail": sum(1 for r in rows if r["page_views"] is not None),
        },
        "products": rows[:MAX_ROWS],
        "traffic_low_conversion": flagged[:50],
        "trend": [{"date": d, "gmv": _r(_f(g)), "orders": int(o or 0), "units_sold": int(u or 0)} for d, g, o, u in trend_rows.all()],
    }


async def sku_performance(session: AsyncSession, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Última foto de rendimiento por SKU, unida al SKU maestro y a su utilidad real de la capa oro."""
    latest = await _latest_day(session, TikTokShopSkuPerformance.snapshot_date)
    if latest is None:
        return {"available": False, "window": None, "summary": None, "skus": []}
    S = TikTokShopSkuPerformance
    perf = list(await session.scalars(select(S).where(S.snapshot_date == latest).order_by(S.gmv.desc())))
    window = (perf[0].window_start, perf[0].window_end)
    sku_rows = {
        s.sku_id: s for s in await session.scalars(select(TikTokShopProductSku))
    }
    seller_skus = {s.seller_sku for s in sku_rows.values() if s.seller_sku}
    masters = await master_product_ids(session, seller_skus)
    gold = await gold_by_product(session, window[0], window[1])
    master_info = {pid: (sku, name) for pid, sku, name in (await session.execute(select(Product.id, Product.sku, Product.name))).all()}
    titles = await _titles(session)

    rows: list[dict[str, Any]] = []
    for rank, p in enumerate(perf, start=1):
        sku = sku_rows.get(p.sku_id)
        seller = sku.seller_sku if sku else None
        master_id = masters.get(seller) if seller else None
        g = gold.get(master_id) if master_id is not None else None
        rows.append(
            {
                "rank": rank,
                "sku_id": p.sku_id,
                "seller_sku": seller,
                "product_id": p.product_id,
                "title": titles.get(p.product_id or "", {}).get("title"),
                "thumb_url": titles.get(p.product_id or "", {}).get("thumb_url"),
                "price": _f(sku.price) if sku and sku.price is not None else None,
                "gmv": _r(_f(p.gmv)),
                "orders": p.orders,
                "units_sold": p.units_sold,
                "master_sku": master_info[master_id][0] if master_id in master_info else None,
                "master_name": master_info[master_id][1] if master_id in master_info else None,
                "profit": _r(g["profit"]) if g else None,
                "settled_sales": _r(g["sales"]) if g else None,
                "profit_per_unit": _ratio(g["profit"], g["units"]) if g and g["units"] else None,
                "margin": _ratio(g["profit"], g["sales"]) if g and g["sales"] else None,
                "linked_to_master": master_id is not None,
            }
        )
    return {
        "available": True,
        "window": {"start": window[0], "end": window[1], "snapshot_date": latest},
        "summary": {
            "skus": len(rows),
            "gmv": _r(sum(r["gmv"] for r in rows)),
            "units_sold": sum(r["units_sold"] for r in rows),
            "unlinked": sum(1 for r in rows if not r["linked_to_master"]),
        },
        "skus": rows[:MAX_ROWS],
    }


# --- catálogo ----------------------------------------------------------------------------------------


async def trailing_sales(session: AsyncSession, product_ids: list[str], days: int, today: date) -> dict[str, float]:
    """Ventas liquidadas (capa oro) de los últimos `days` días por producto de TikTok, vía su SKU maestro."""
    cross = await build_crosswalk(session, today - timedelta(days=days), today)
    out: dict[str, float] = {}
    for pid in product_ids:
        found = cross.profit_for_skus(cross.skus_by_product.get(pid, []))
        out[pid] = found["sales"] if found else 0.0
    return out


async def catalog(session: AsyncSession, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Productos con imagen, estado, auditoría y calidad; resumen por estado/calidad; problemáticos con sus ventas."""
    today = _now(now).date()
    products = list(await session.scalars(select(TikTokShopProduct).order_by(TikTokShopProduct.title)))
    latest = await _latest_day(session, TikTokShopProductPerformance.snapshot_date)
    gmv: dict[str, float] = {}
    if latest is not None:
        P = TikTokShopProductPerformance
        gmv = {pid: _f(g) for pid, g in (await session.execute(select(P.product_id, P.gmv).where(P.snapshot_date == latest))).all()}
    sales = await trailing_sales(session, [p.product_id for p in products], days, today)
    rows = [
        {
            "product_id": p.product_id,
            "title": p.title,
            "image_url": p.main_image_url,
            "thumb_url": p.thumb_url,
            "image_count": p.image_count,
            "status": p.status,
            "audit_status": p.audit_status,
            "audit_reasons": p.audit_reasons or [],
            "listing_quality_tier": p.listing_quality_tier,
            "is_not_for_sale": p.is_not_for_sale,
            "brand": p.brand_name,
            "categories": p.categories or [],
            "sales_regions": p.sales_regions or [],
            "min_price": _f(p.min_price) if p.min_price is not None else None,
            "max_price": _f(p.max_price) if p.max_price is not None else None,
            "sku_count": p.sku_count,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
            "gmv_30d": _r(gmv.get(p.product_id, 0.0)),
            "settled_sales": _r(sales.get(p.product_id, 0.0)),
            "problem": is_problem_product(p.status, p.audit_status),
        }
        for p in products
    ]
    problems = sorted((r for r in rows if r["problem"]), key=lambda r: -r["settled_sales"])
    return {
        "available": bool(rows),
        "summary": {
            "products": len(rows),
            "by_status": dict(Counter(r["status"] or "—" for r in rows)),
            "by_quality": dict(Counter(r["listing_quality_tier"] or "—" for r in rows)),
            "by_audit": dict(Counter(r["audit_status"] or "—" for r in rows)),
            "problems": len(problems),
            "problem_settled_sales": _r(sum(r["settled_sales"] for r in problems)),
            "without_image": sum(1 for r in rows if not r["image_url"]),
            "sales_days": days,
        },
        "problem_products": problems,
        "products": rows[:MAX_ROWS],
    }


async def problem_products(session: AsyncSession, sales_days: int, today: date) -> list[dict[str, Any]]:
    """Productos rechazados/desactivados con sus ventas liquidadas de los últimos `sales_days` días (para acciones)."""
    products = [
        p for p in await session.scalars(select(TikTokShopProduct)) if is_problem_product(p.status, p.audit_status)
    ]
    sales = await trailing_sales(session, [p.product_id for p in products], sales_days, today)
    return [
        {"product_id": p.product_id, "title": p.title, "status": p.status, "audit_status": p.audit_status, "sales": sales[p.product_id]}
        for p in products
    ]


# --- promociones y cupones ---------------------------------------------------------------------------


def _span(begin: datetime | None, end: datetime | None, today: date) -> tuple[date, date] | None:
    """Días de la actividad hasta hoy (None si aún no empieza o no tiene fechas)."""
    begin, end = _aware(begin), _aware(end)
    if begin is None or begin.date() > today:
        return None
    last = min(end.date(), today) if end else today
    return (begin.date(), max(last, begin.date()))


def promotion_roi(
    daily: dict[date, tuple[float, float]], span: tuple[date, date], baseline_days: int = PROMO_BASELINE_DAYS
) -> dict[str, Any]:
    """Unidades/día durante la promoción contra los `baseline_days` días previos. `daily[día] = (unidades, ventas)`.

    Comparación observacional: no separa el efecto del descuento de la temporada, la publicidad o los creadores.
    """
    start, end = span
    during_days = (end - start).days + 1
    before_days = [start - timedelta(days=i) for i in range(1, baseline_days + 1)]
    units_during = sum(daily.get(start + timedelta(days=i), (0.0, 0.0))[0] for i in range(during_days))
    sales_during = sum(daily.get(start + timedelta(days=i), (0.0, 0.0))[1] for i in range(during_days))
    units_before = sum(daily.get(d, (0.0, 0.0))[0] for d in before_days)
    sales_before = sum(daily.get(d, (0.0, 0.0))[1] for d in before_days)
    rate_during, rate_before = units_during / during_days, units_before / baseline_days
    return {
        "days_running": during_days,
        "units_per_day_during": _r(rate_during, 3),
        "units_per_day_before": _r(rate_before, 3),
        "uplift": _ratio(rate_during - rate_before, rate_before),
        "units_during": _r(units_during, 1),
        "sales_during": _r(sales_during),
        "sales_per_day_during": _r(sales_during / during_days),
        "sales_per_day_before": _r(sales_before / baseline_days),
    }


async def _daily_units(
    session: AsyncSession, product_ids: set[int] | None, start: date, end: date
) -> dict[date, tuple[float, float]]:
    """Unidades y ventas por día de las líneas de orden de TikTok (de los productos dados, o de toda la tienda)."""
    q = (
        select(OrderLine.order_date, func.sum(OrderLine.units), func.sum(OrderLine.gross_sales))
        .join(Account, Account.id == OrderLine.account_id)
        .where(Account.channel_code == CHANNEL, OrderLine.order_date >= start, OrderLine.order_date <= end)
        .group_by(OrderLine.order_date)
    )
    if product_ids is not None:
        q = q.where(OrderLine.product_id.in_(product_ids))
    return {d: (_f(u), _f(s)) for d, u, s in (await session.execute(q)).all()}


async def promotions(session: AsyncSession, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Actividades con estado, fechas, productos y ROI (unidades/día durante vs. los 14 días previos).

    Entran las actividades que corrieron en los últimos `days` días o siguen activas. Las de nivel tienda
    (sin productos) se miden contra todas las ventas de TikTok.
    """
    today = _now(now).date()
    since = today - timedelta(days=days)
    acts = [
        a
        for a in await session.scalars(select(TikTokShopPromotion).order_by(TikTokShopPromotion.begin_at.desc()))
        if a.status == "ONGOING" or (_aware(a.end_at) is None) or _aware(a.end_at).date() >= since  # type: ignore[union-attr]
    ]
    links: dict[str, list[TikTokShopPromotionProduct]] = defaultdict(list)
    for link in await session.scalars(select(TikTokShopPromotionProduct)):
        links[link.activity_id].append(link)
    titles = await _titles(session)
    cross = await build_crosswalk(session, since, today)

    rows: list[dict[str, Any]] = []
    for a in acts[:MAX_ROWS]:
        span = _span(a.begin_at, a.end_at, today)
        products = links.get(a.activity_id, [])
        masters = {cross.sku_to_master[s] for p in products for s in cross.skus_by_product.get(p.product_id, []) if s in cross.sku_to_master}
        shop_wide = not products
        roi = None
        if span is not None and (masters or shop_wide):
            daily = await _daily_units(session, None if shop_wide else masters, span[0] - timedelta(days=PROMO_BASELINE_DAYS), span[1])
            roi = promotion_roi(daily, span)
        rows.append(
            {
                "activity_id": a.activity_id,
                "title": a.title,
                "type": a.activity_type,
                "level": a.product_level,
                "status": a.status,
                "begin_at": a.begin_at,
                "end_at": a.end_at,
                "product_count": a.product_count,
                "discount": a.discount,
                "products": [
                    {
                        "product_id": p.product_id,
                        "title": titles.get(p.product_id, {}).get("title"),
                        "activity_price": _f(p.activity_price) if p.activity_price is not None else None,
                        "discount": p.discount,
                        "quantity_limit": p.quantity_limit,
                    }
                    for p in products[:25]
                ],
                "roi": roi,
                "roi_scope": "shop" if shop_wide else "products",
            }
        )
    return {
        "available": bool(rows),
        "summary": {
            "activities": len(rows),
            "by_status": dict(Counter(r["status"] or "—" for r in rows)),
            "by_type": dict(Counter(r["type"] or "—" for r in rows)),
            "baseline_days": PROMO_BASELINE_DAYS,
        },
        "activities": rows,
    }


async def coupons(session: AsyncSession, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Cupones vigentes o que terminaron en los últimos `days` días, con descuento, vigencia y uso."""
    today = _now(now)
    since = today - timedelta(days=days)
    rows: list[dict[str, Any]] = []
    for c in await session.scalars(select(TikTokShopCoupon).order_by(TikTokShopCoupon.created_at.desc())):
        end = _aware(c.redeem_end) or _aware(c.claim_end)
        if end is not None and end < since and c.status != "ONGOING":
            continue
        claimed, redeemed = c.claimed_count, c.redeemed_count
        rows.append(
            {
                "coupon_id": c.coupon_id,
                "title": c.title,
                "status": c.status,
                "display_type": c.display_type,
                "promo_code": c.promo_code,
                "scope": c.product_scope,
                "segment": c.target_segment,
                "discount_type": c.discount_type,
                "discount": discount_label(
                    c.discount_type,
                    _f(c.discount_percent) if c.discount_percent is not None else None,
                    _f(c.discount_amount) if c.discount_amount is not None else None,
                    _f(c.max_discount) if c.max_discount is not None else None,
                ),
                "min_spend": _f(c.min_spend) if c.min_spend is not None else None,
                "valid_from": c.redeem_start or c.claim_start,
                "valid_until": c.redeem_end or c.claim_end,
                "claimed": claimed,
                "redeemed": redeemed,
                "redemption_rate": _ratio(redeemed or 0, claimed or 0) if claimed else None,
                "redemption_limit": c.redemption_limit,
                "product_count": len(c.product_ids or []),
            }
        )
    return {
        "available": bool(rows),
        "summary": {
            "coupons": len(rows),
            "by_status": dict(Counter(r["status"] or "—" for r in rows)),
            "by_discount_type": dict(Counter(r["discount_type"] or "—" for r in rows)),
            "redeemed": sum(r["redeemed"] or 0 for r in rows),
        },
        "coupons": rows,
    }


# --- paquetes ----------------------------------------------------------------------------------------


async def average_order_value(session: AsyncSession, today: date, days: int = 90) -> float:
    """Ticket promedio de TikTok (capa oro): estima el valor de órdenes aún no liquidadas."""
    g = GoldDailyProfit
    sales, orders = (
        await session.execute(
            select(func.sum(g.gross_sales), func.sum(g.orders)).where(
                g.channel_code == CHANNEL, g.profit_date >= today - timedelta(days=days), g.profit_date <= today
            )
        )
    ).one()
    return _f(sales) / _f(orders) if _f(orders) else 0.0


async def packages(
    session: AsyncSession, days: int, stuck_days: int = DEFAULT_STUCK_DAYS, now: datetime | None = None
) -> dict[str, Any]:
    """Paquetes creados en los últimos `days` días: por estado, tiempos de entrega, por día y atascados."""
    moment = _now(now)
    pkgs = [
        p
        for p in await session.scalars(select(TikTokShopPackage).where(TikTokShopPackage.created_at >= moment - timedelta(days=days)))
    ]
    by_status = Counter(p.status or "—" for p in pkgs)
    delivery_days = [
        (_aware(p.delivered_at) - _aware(p.created_at)).total_seconds() / 86400  # type: ignore[operator]
        for p in pkgs
        if p.status == PACKAGE_COMPLETED and p.delivered_at and p.created_at
    ]
    by_day: dict[date, Counter[str]] = defaultdict(Counter)
    for p in pkgs:
        if p.created_at:
            by_day[_aware(p.created_at).date()][p.status or "—"] += 1  # type: ignore[union-attr]
    stuck = sorted(
        (
            {
                "package_id": p.package_id,
                "status": p.status,
                "tracking_number": p.tracking_number,
                "order_count": p.order_count,
                "created_at": p.created_at,
                "last_movement": package_last_movement(p.updated_at, p.last_event_at),
                "last_event": p.last_event,
                "days_without_movement": _r((moment - package_last_movement(p.updated_at, p.last_event_at)).total_seconds() / 86400, 1),  # type: ignore[operator]
            }
            for p in pkgs
            if is_stuck(p.status, package_last_movement(p.updated_at, p.last_event_at), moment, stuck_days)
        ),
        key=lambda r: -r["days_without_movement"],
    )
    buckets = Counter(delivery_bucket(d) for d in delivery_days)
    cancelled = by_status.get("CANCELLED", 0)
    completed = by_status.get(PACKAGE_COMPLETED, 0)
    return {
        "available": bool(pkgs),
        "summary": {
            "packages": len(pkgs),
            "by_status": dict(by_status),
            "delivery_rate": _ratio(completed, len(pkgs) - cancelled),
            "avg_delivery_days": _r(sum(delivery_days) / len(delivery_days), 2) if delivery_days else None,
            "stuck": len(stuck),
            "stuck_days": stuck_days,
            "delivered_at_note": "Entrega inferida: la API no da fecha de entrega; se usa la última actualización de un paquete COMPLETED.",
        },
        "delivery_distribution": [{"bucket": name, "count": buckets.get(name, 0)} for name, _, _ in DELIVERY_BUCKETS],
        "by_day": [{"date": d, **dict(c), "total": sum(c.values())} for d, c in sorted(by_day.items())],
        "stuck": stuck[:100],
    }


async def stuck_packages(session: AsyncSession, stuck_days: int, now: datetime, days: int = 60) -> list[dict[str, Any]]:
    """Paquetes atascados con el valor de sus órdenes (real si ya hay liquidación, si no el ticket promedio)."""
    report = await packages(session, days, stuck_days, now)
    stuck = report["stuck"]
    if not stuck:
        return []
    ids = {p.package_id: p.order_ids or [] for p in await session.scalars(select(TikTokShopPackage).where(TikTokShopPackage.package_id.in_([s["package_id"] for s in stuck])))}
    flat = {o for orders in ids.values() for o in orders}
    real: dict[str, float] = {}
    if flat:
        rows = await session.execute(
            select(SalesOrder.external_order_id, func.sum(OrderLine.gross_sales))
            .join(OrderLine, OrderLine.order_id == SalesOrder.id)
            .where(SalesOrder.external_order_id.in_(flat))
            .group_by(SalesOrder.external_order_id)
        )
        real = {o: _f(v) for o, v in rows.all()}
    avg = await average_order_value(session, now.date())
    for s in stuck:
        orders = ids.get(s["package_id"], [])
        s["value_usd"] = _r(sum(real.get(o, avg) for o in orders))
        s["value_estimated"] = any(o not in real for o in orders)
    return stuck


# --- pagos, retiros, almacenes, webhooks -------------------------------------------------------------


async def _settled_by_statement(session: AsyncSession, statement_ids: set[str]) -> dict[str, float]:
    if not statement_ids:
        return {}
    rows = await session.execute(
        select(Settlement.external_id, func.sum(Settlement.net_payout))
        .where(Settlement.external_id.in_(statement_ids))
        .group_by(Settlement.external_id)
    )
    return {sid: _f(v) for sid, v in rows.all()}


def reconcile(payment: TikTokShopPayment, settled: dict[str, float]) -> dict[str, Any]:
    """Cuadre de un pago contra las liquidaciones (estados de cuenta) que cubre.

    Solo se calcula la diferencia si TODAS sus liquidaciones están importadas: con una faltante la
    diferencia sería falsa. `difference` = pagado - liquidado (positivo = TikTok pagó de más).
    """
    ids = list(payment.statement_ids or [])
    missing = [s for s in ids if s not in settled]
    settled_total = sum(settled.get(s, 0.0) for s in ids)
    complete = bool(ids) and not missing
    return {
        "statements": len(ids),
        "missing_statements": len(missing),
        "settled_amount": _r(settled_total) if complete else None,
        "difference": _r(_f(payment.settlement_amount) - settled_total) if complete else None,
        "reconciled": complete and abs(_f(payment.settlement_amount) - settled_total) <= RECONCILE_TOLERANCE_USD,
        "checkable": complete,
    }


async def payments(session: AsyncSession, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Pagos de los últimos `days` días: estado, retención, tipo de cambio y cuadre contra liquidaciones."""
    since = _now(now) - timedelta(days=days)
    pays = list(await session.scalars(select(TikTokShopPayment).where(TikTokShopPayment.created_at >= since).order_by(TikTokShopPayment.created_at.desc())))
    settled = await _settled_by_statement(session, {s for p in pays for s in (p.statement_ids or [])})
    rows = []
    for p in pays:
        check = reconcile(p, settled)
        rows.append(
            {
                "payment_id": p.payment_id,
                "status": p.status,
                "currency": p.currency,
                "amount": _r(_f(p.amount)),
                "settlement_amount": _r(_f(p.settlement_amount)),
                "reserve_amount": _r(_f(p.reserve_amount)),
                "exchange_rate": _f(p.exchange_rate) if p.exchange_rate is not None else None,
                "created_at": p.created_at,
                "paid_at": p.paid_at,
                "bank_last4": p.bank_last4,
                **check,
            }
        )
    checkable = [r for r in rows if r["checkable"]]
    return {
        "available": bool(rows),
        "summary": {
            "payments": len(rows),
            "by_status": dict(Counter(r["status"] or "—" for r in rows)),
            "paid_amount": _r(sum(r["amount"] for r in rows if r["status"] == PAID_STATUS)),
            "pending_amount": _r(sum(r["amount"] for r in rows if r["status"] != PAID_STATUS)),
            "reserve_amount": _r(sum(r["reserve_amount"] for r in rows)),
            "reconciliation": {
                "checkable": len(checkable),
                "reconciled": sum(1 for r in checkable if r["reconciled"]),
                "paid": _r(sum(r["settlement_amount"] for r in checkable)),
                "settled": _r(sum(r["settled_amount"] for r in checkable)),
                "difference": _r(sum(r["difference"] for r in checkable)),
                "tolerance_usd": RECONCILE_TOLERANCE_USD,
            },
        },
        "payments": rows,
    }


async def payment_issues(session: AsyncSession, now: datetime, days: int = 120) -> list[dict[str, Any]]:
    """Pagos fallidos o cuyo monto no cuadra con sus liquidaciones (para el Centro de acciones)."""
    report = await payments(session, days, now)
    issues = []
    for r in report["payments"]:
        failed = (r["status"] or "").upper() in FAILED_PAYMENT_STATUSES
        off = r["checkable"] and abs(r["difference"]) > RECONCILE_TOLERANCE_USD
        if failed or off:
            issues.append({**r, "kind": "failed" if failed else "difference"})
    return issues


async def receivable(session: AsyncSession, now: datetime) -> dict[str, Any]:
    """Saldo por cobrar: liquidaciones recientes que ningún pago PAGADO cubre todavía + pagos aún no pagados."""
    cutoff = now.date() - timedelta(days=RECEIVABLE_LOOKBACK_DAYS)
    pays = list(await session.scalars(select(TikTokShopPayment)))
    covered = {s for p in pays if p.status == PAID_STATUS for s in (p.statement_ids or [])}
    unpaid_statements = [
        s
        for s in await session.scalars(select(Settlement).where(Settlement.statement_date >= cutoff))
        if s.external_id not in covered
    ]
    pending = [p for p in pays if p.status != PAID_STATUS]
    return {
        "unpaid_settlements": len(unpaid_statements),
        "unpaid_settlements_amount": _r(sum(_f(s.net_payout) for s in unpaid_statements)),
        "pending_payments": len(pending),
        "pending_payments_amount": _r(sum(_f(p.amount) for p in pending)),
    }


async def withdrawals(session: AsyncSession, days: int, now: datetime | None = None) -> dict[str, Any]:
    """Retiros de los últimos `days` días, por estado y por mes."""
    since = _now(now) - timedelta(days=days)
    rows = list(await session.scalars(select(TikTokShopWithdrawal).where(TikTokShopWithdrawal.created_at >= since).order_by(TikTokShopWithdrawal.created_at.desc())))
    by_month: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for w in rows:
        if w.created_at:
            month = by_month[_aware(w.created_at).strftime("%Y-%m")]  # type: ignore[union-attr]
            month["count"] += 1
            month["amount"] += _f(w.amount)
    return {
        "available": bool(rows),
        "summary": {
            "withdrawals": len(rows),
            "by_status": dict(Counter(w.status or "—" for w in rows)),
            "total_amount": _r(sum(_f(w.amount) for w in rows)),
        },
        "by_month": [{"month": m, "count": int(v["count"]), "amount": _r(v["amount"])} for m, v in sorted(by_month.items())],
        "withdrawals": [
            {"withdrawal_id": w.withdrawal_id, "type": w.type, "status": w.status, "currency": w.currency, "amount": _r(_f(w.amount)), "created_at": w.created_at}
            for w in rows
        ],
    }


async def warehouses(session: AsyncSession) -> dict[str, Any]:
    rows = list(await session.scalars(select(TikTokShopWarehouse).order_by(TikTokShopWarehouse.name)))
    return {
        "available": bool(rows),
        "warehouses": [
            {
                "warehouse_id": w.warehouse_id,
                "name": w.name,
                "type": w.type,
                "sub_type": w.sub_type,
                "status": w.status,
                "is_default": w.is_default,
                "city": w.city,
                "state": w.state,
                "region_code": w.region_code,
                "postal_code": w.postal_code,
            }
            for w in rows
        ],
    }


async def webhooks(session: AsyncSession) -> dict[str, Any]:
    """Suscripciones activas y, aparte, los eventos documentados que aún no tienen suscripción."""
    rows = list(await session.scalars(select(TikTokShopWebhook).order_by(TikTokShopWebhook.event_type)))
    subscribed = {w.event_type for w in rows}
    return {
        "subscriptions": [{"event_type": w.event_type, "address": w.address, "created_at": w.created_at} for w in rows],
        "documented_events": [{"event_type": e, "subscribed": e in subscribed} for e in DOCUMENTED_WEBHOOK_EVENTS],
        "documented_events_note": "La API solo lista las suscripciones; los eventos disponibles son los documentados por TikTok Shop.",
        "unsubscribed": [e for e in DOCUMENTED_WEBHOOK_EVENTS if e not in subscribed],
    }


# --- portada -----------------------------------------------------------------------------------------


async def overview(session: AsyncSession, days: int, now: datetime | None = None) -> dict[str, Any]:
    """KPIs de TikTok Shop (capa oro) y alertas: productos rechazados, paquetes atascados, pagos y saldo por cobrar."""
    moment = _now(now)
    today = moment.date()
    g = GoldDailyProfit
    sales, profit, orders, units = (
        await session.execute(
            select(func.sum(g.gross_sales), func.sum(g.net_profit), func.sum(g.orders), func.sum(g.units)).where(
                g.channel_code == CHANNEL, g.profit_date >= today - timedelta(days=days), g.profit_date <= today
            )
        )
    ).one()
    bad = await problem_products(session, days, today)
    stuck = await packages(session, 60, DEFAULT_STUCK_DAYS, moment)
    pay = await payments(session, 120, moment)
    last_sync = await session.scalar(
        select(SyncRun.finished_at).where(SyncRun.job_type == "tiktok_shop_extra", SyncRun.status.in_(("success", "partial"))).order_by(SyncRun.started_at.desc()).limit(1)
    )
    return {
        "days": days,
        "kpis": {"sales": _r(_f(sales)), "net_profit": _r(_f(profit)), "orders": int(orders or 0), "units": int(units or 0)},
        "rejected_products": {
            "count": len(bad),
            "with_sales": sum(1 for b in bad if b["sales"] > 0),
            "sales_usd": _r(sum(b["sales"] for b in bad)),
        },
        "stuck_packages": {"count": stuck["summary"]["stuck"], "stuck_days": DEFAULT_STUCK_DAYS},
        "payments": {
            "pending": sum(1 for r in pay["payments"] if r["status"] != PAID_STATUS),
            "pending_amount": pay["summary"]["pending_amount"],
            "reconciliation": pay["summary"]["reconciliation"],
        },
        "receivable": await receivable(session, moment),
        "last_sync_at": _aware(last_sync),
    }
