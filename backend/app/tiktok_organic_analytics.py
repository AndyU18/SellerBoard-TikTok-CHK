"""Análisis del TikTok orgánico a partir de las fotos diarias que deja `app.tiktok_organic_ops`.

Límite de fondo: la API de TikTok solo da el valor ACTUAL de vistas/likes/comentarios/compartidos y los
contadores de la cuenta; no da demografía, impresiones, alcance ni tiempo de reproducción. Todo lo que
sea "crecimiento" o "serie" sale de comparar nuestras fotos diarias, así que solo cubre desde que se
conectó la cuenta (cada respuesta dice desde qué fecha para no aparentar más historia de la que hay).

Todas las funciones son de solo lectura y reciben `today` para ser deterministas en pruebas.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import mean, median
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    AffiliateOrder,
    TikTokOrganicAccount,
    TikTokOrganicVideo,
    TikTokOrganicVideoMetric,
    VideoPerformance,
)

SortKey = Literal["recent", "views", "likes", "comments", "shares", "engagement", "views_per_day"]
GROWTH_WINDOWS = (7, 30)
RECENT_GROWTH_DAYS = 7  # ventana del "crecimiento reciente" por video
VIRAL_MULTIPLE = 3.0  # un video "explota" si tiene >= 3 x la mediana de vistas
MIN_VIDEOS_FOR_VIRAL = 5  # con menos videos la mediana no es una referencia confiable
MIN_VIRAL_VIEWS = 1000  # piso absoluto: por debajo de esto un video no se considera "viral" aunque supere 3 x la mediana
TOP_VIDEOS = 10
TOP_SLOTS = 5
MIN_RELIABLE_SLOT_VIDEOS = 3  # una franja con menos videos se marca low_sample
TOP_TAGS = 50
VIEW_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-999", 0, 1_000),
    ("1K-4.9K", 1_000, 5_000),
    ("5K-9.9K", 5_000, 10_000),
    ("10K-49K", 10_000, 50_000),
    ("50K-99K", 50_000, 100_000),
    ("100K+", 100_000, None),
)
DURATION_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-15 s", 0, 15),
    ("15-30 s", 15, 30),
    ("30-60 s", 30, 60),
    ("1-3 min", 60, 180),
    ("3+ min", 180, None),
)
WEEKDAY_NAMES = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
HASHTAG_RE = re.compile(r"(?<!\w)#(\w+)")
MENTION_RE = re.compile(r"(?<![\w@])@(\w[\w.]*\w|\w)")

SORTERS: dict[str, Any] = {
    "recent": lambda v, today: v.created_at or datetime.min.replace(tzinfo=UTC),
    "views": lambda v, today: v.views,
    "likes": lambda v, today: v.likes,
    "comments": lambda v, today: v.comments,
    "shares": lambda v, today: v.shares,
    "engagement": lambda v, today: v.engagement or 0.0,
    "views_per_day": lambda v, today: v.views_per_day(today),
}


# --- modelo en memoria y utilidades puras ----------------------------------------------------------------


def engagement_rate(interactions: int, views: int) -> float | None:
    """(likes + comentarios + compartidos) / vistas. None si no hay vistas (no se puede dividir)."""
    return round(interactions / views, 4) if views > 0 else None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(frozen=True, slots=True)
class VideoStats:
    """Un video con sus métricas más recientes."""

    video_id: str
    title: str | None
    description: str | None
    cover_url: str | None
    created_at: datetime | None
    duration_seconds: int | None
    share_url: str | None
    embed_link: str | None
    width: int | None
    height: int | None
    views: int
    likes: int
    comments: int
    shares: int

    @property
    def interactions(self) -> int:
        return self.likes + self.comments + self.shares

    @property
    def engagement(self) -> float | None:
        return engagement_rate(self.interactions, self.views)

    @property
    def text(self) -> str:
        """Título y descripción sin repetir (TikTok suele guardar el mismo texto en ambos)."""
        parts = [p for p in (self.description, self.title) if p]
        return " ".join(dict.fromkeys(parts))

    def age_days(self, today: date) -> int | None:
        return max((today - self.created_at.date()).days, 0) if self.created_at else None

    def views_per_day(self, today: date) -> float:
        """Vistas por día desde la publicación (mínimo 1 día para no inflar videos de hoy)."""
        age = self.age_days(today)
        return round(self.views / max(age or 1, 1), 2)

    def published_within(self, today: date, days: int) -> bool:
        return bool(self.created_at and self.created_at.date() >= today - timedelta(days=days))


def window_growth(series: list[tuple[date, int]], today: date, days: int) -> dict[str, Any] | None:
    """Cambio entre la primera y la última foto dentro de la ventana. None si hay menos de dos fotos.

    Usa la primera foto disponible dentro de la ventana, no una inventada: `from_date` dice la cobertura real.
    """
    cutoff = today - timedelta(days=days)
    points = sorted(p for p in series if p[0] >= cutoff)
    if len(points) < 2:
        return None
    (start, first), (end, last) = points[0], points[-1]
    return {
        "days": days,
        "from_date": start,
        "to_date": end,
        "delta": last - first,
        "pct": round((last - first) / first * 100, 2) if first else None,
    }


def _bucket_label(value: int, buckets: tuple[tuple[str, int, int | None], ...]) -> str:
    for label, low, high in buckets:
        if value >= low and (high is None or value < high):
            return label
    return buckets[-1][0]


def _avg_views(videos: list[VideoStats]) -> float | None:
    return round(mean(v.views for v in videos), 1) if videos else None


def _group_engagement(videos: list[VideoStats]) -> float | None:
    return engagement_rate(sum(v.interactions for v in videos), sum(v.views for v in videos))


def extract_tags(text: str) -> tuple[set[str], set[str]]:
    """Hashtags y menciones únicos (en minúsculas) de un texto."""
    tags = {m.casefold() for m in HASHTAG_RE.findall(text)}
    mentions = {m.casefold().rstrip(".") for m in MENTION_RE.findall(text)}
    return tags, mentions


def _video_item(v: VideoStats, today: date) -> dict[str, Any]:
    return {
        "video_id": v.video_id,
        "title": v.title,
        "description": v.description,
        "cover_url": v.cover_url,
        "share_url": v.share_url,
        "embed_link": v.embed_link,
        "created_at": v.created_at,
        "duration_seconds": v.duration_seconds,
        "width": v.width,
        "height": v.height,
        "views": v.views,
        "likes": v.likes,
        "comments": v.comments,
        "shares": v.shares,
        "engagement": v.engagement,
        "views_per_day": v.views_per_day(today),
        "age_days": v.age_days(today),
    }


# --- carga ------------------------------------------------------------------------------------------------


async def load_videos(session: AsyncSession) -> list[VideoStats]:
    """Todos los videos con su foto de métricas más reciente (ceros si aún no hay foto)."""
    latest = (
        select(
            TikTokOrganicVideoMetric.video_id,
            func.max(TikTokOrganicVideoMetric.snapshot_date).label("day"),
        )
        .group_by(TikTokOrganicVideoMetric.video_id)
        .subquery()
    )
    stmt = (
        select(TikTokOrganicVideo, TikTokOrganicVideoMetric)
        .outerjoin(latest, latest.c.video_id == TikTokOrganicVideo.video_id)
        .outerjoin(
            TikTokOrganicVideoMetric,
            and_(
                TikTokOrganicVideoMetric.video_id == latest.c.video_id,
                TikTokOrganicVideoMetric.snapshot_date == latest.c.day,
            ),
        )
    )
    out: list[VideoStats] = []
    for video, metric in (await session.execute(stmt)).all():
        out.append(
            VideoStats(
                video_id=video.video_id,
                title=video.title,
                description=video.description,
                cover_url=video.cover_url,
                created_at=_aware(video.created_at),
                duration_seconds=video.duration_seconds,
                share_url=video.share_url,
                embed_link=video.embed_link,
                width=video.width,
                height=video.height,
                views=metric.views if metric else 0,
                likes=metric.likes if metric else 0,
                comments=metric.comments if metric else 0,
                shares=metric.shares if metric else 0,
            )
        )
    return out


async def _account_history(session: AsyncSession, since: date) -> list[TikTokOrganicAccount]:
    rows = await session.scalars(
        select(TikTokOrganicAccount)
        .where(TikTokOrganicAccount.snapshot_date >= since)
        .order_by(TikTokOrganicAccount.snapshot_date)
    )
    return list(rows)


async def _latest_account(session: AsyncSession) -> TikTokOrganicAccount | None:
    return await session.scalar(
        select(TikTokOrganicAccount).order_by(TikTokOrganicAccount.snapshot_date.desc()).limit(1)
    )


def _window_videos(videos: list[VideoStats], today: date, days: int) -> list[VideoStats]:
    return [v for v in videos if v.published_within(today, days)]


# --- resumen ----------------------------------------------------------------------------------------------


async def overview(session: AsyncSession, days: int = 90, today: date | None = None) -> dict[str, Any]:
    """Cuenta, crecimiento de seguidores, totales de videos, engagement y ritmo de publicación."""
    today = today or date.today()
    account = await _latest_account(session)
    videos = await load_videos(session)
    history = await _account_history(session, today - timedelta(days=max(GROWTH_WINDOWS)))
    followers = [(a.snapshot_date, a.follower_count) for a in history if a.follower_count is not None]
    views = [v.views for v in videos]
    recent = _window_videos(videos, today, days)
    dated = [v.created_at for v in videos if v.created_at]
    last_posted = max(dated) if dated else None
    return {
        "has_data": account is not None or bool(videos),
        "account": None
        if account is None
        else {
            "username": account.username,
            "display_name": account.display_name,
            "avatar_url": account.avatar_url,
            "bio": account.bio,
            "profile_url": account.profile_url,
            "is_verified": account.is_verified,
            "snapshot_date": account.snapshot_date,
        },
        "followers": account.follower_count if account else None,
        "following": account.following_count if account else None,
        "likes_total": account.likes_count if account else None,
        "videos_reported": account.video_count if account else None,
        "growth": [{"days": d, "followers": window_growth(followers, today, d)} for d in GROWTH_WINDOWS],
        "videos_tracked": len(videos),
        "total_views": sum(views),
        "total_likes": sum(v.likes for v in videos),
        "total_comments": sum(v.comments for v in videos),
        "total_shares": sum(v.shares for v in videos),
        "engagement": _group_engagement(videos),
        "avg_views": _avg_views(videos),
        "median_views": float(median(views)) if views else None,
        "posting": {
            "days": days,
            "videos_published": len(recent),
            "views": sum(v.views for v in recent),
            "posts_per_week": round(len(recent) / (days / 7), 2),
            "last_posted_at": last_posted,
            "days_since_last_post": (today - last_posted.date()).days if last_posted else None,
        },
    }


# --- videos -----------------------------------------------------------------------------------------------


async def _views_history(session: AsyncSession, since: date) -> dict[str, list[tuple[date, int]]]:
    rows = await session.execute(
        select(
            TikTokOrganicVideoMetric.video_id,
            TikTokOrganicVideoMetric.snapshot_date,
            TikTokOrganicVideoMetric.views,
        ).where(TikTokOrganicVideoMetric.snapshot_date >= since)
    )
    series: dict[str, list[tuple[date, int]]] = defaultdict(list)
    for video_id, day, views in rows.all():
        series[video_id].append((day, views))
    return series


async def videos_list(
    session: AsyncSession,
    sort: SortKey = "recent",
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    today: date | None = None,
) -> dict[str, Any]:
    """Lista paginada de videos con métricas, engagement, vistas por día y crecimiento reciente."""
    today = today or date.today()
    videos = await load_videos(session)
    needle = (q or "").strip().casefold()
    if needle:
        videos = [v for v in videos if needle in v.text.casefold()]
    videos.sort(key=lambda v: SORTERS[sort](v, today), reverse=True)
    page = videos[offset : offset + limit]
    history = await _views_history(session, today - timedelta(days=RECENT_GROWTH_DAYS))
    items = []
    for v in page:
        item = _video_item(v, today)
        item["recent_growth"] = window_growth(history.get(v.video_id, []), today, RECENT_GROWTH_DAYS)
        items.append(item)
    return {"total": len(videos), "sort": sort, "q": q, "limit": limit, "offset": offset, "items": items}


# --- rendimiento ------------------------------------------------------------------------------------------


def distribution(videos: list[VideoStats]) -> list[dict[str, Any]]:
    """Cuántos videos caen en cada rango de vistas."""
    counts: dict[str, int] = dict.fromkeys((b[0] for b in VIEW_BUCKETS), 0)
    for v in videos:
        counts[_bucket_label(v.views, VIEW_BUCKETS)] += 1
    total = len(videos)
    return [
        {"label": label, "min": low, "max": high, "videos": counts[label], "share": round(counts[label] / total, 4) if total else 0.0}
        for label, low, high in VIEW_BUCKETS
    ]


def viral_videos(videos: list[VideoStats], today: date, multiple: float = VIRAL_MULTIPLE) -> dict[str, Any]:
    """Videos con >= `multiple` x la mediana de vistas del grupo. Necesita un mínimo de videos para ser fiable."""
    if len(videos) < MIN_VIDEOS_FOR_VIRAL:
        return {
            "multiple": multiple,
            "median_views": None,
            "mean_views": None,
            "threshold": None,
            "videos": [],
            "note": f"Se necesitan al menos {MIN_VIDEOS_FOR_VIRAL} videos en el periodo para comparar.",
        }
    med = float(median(v.views for v in videos))
    # Con una mediana muy baja (cuentas con muchos videos que casi nadie ve) 3 x mediana serían unas decenas de
    # vistas: no es "viral". Se exige también un mínimo absoluto de vistas, y la mediana nunca baja de 1.
    threshold = max(multiple * max(med, 1.0), float(MIN_VIRAL_VIEWS))
    hits = sorted((v for v in videos if v.views >= threshold), key=lambda v: v.views, reverse=True)
    return {
        "multiple": multiple,
        "median_views": med,
        "mean_views": _avg_views(videos),
        "threshold": round(threshold, 1),
        "videos": [{**_video_item(v, today), "times_median": round(v.views / max(med, 1.0), 2)} for v in hits],
        "note": None,
    }


async def performance(session: AsyncSession, days: int = 90, today: date | None = None) -> dict[str, Any]:
    """Series diarias (seguidores y vistas totales), top videos, virales y distribución de vistas."""
    today = today or date.today()
    since = today - timedelta(days=days)
    accounts = await _account_history(session, since)
    totals = (
        await session.execute(
            select(TikTokOrganicVideoMetric.snapshot_date, func.sum(TikTokOrganicVideoMetric.views))
            .where(TikTokOrganicVideoMetric.snapshot_date >= since)
            .group_by(TikTokOrganicVideoMetric.snapshot_date)
            .order_by(TikTokOrganicVideoMetric.snapshot_date)
        )
    ).all()
    views_series: list[dict[str, Any]] = []
    previous: int | None = None
    for day, total in totals:
        total = int(total or 0)
        # Si el dueño borra un video el total baja: no existe "vistas negativas", se recorta a 0.
        gained = None if previous is None else max(total - previous, 0)
        views_series.append({"date": day, "views_total": total, "views_gained": gained})
        previous = total
    window = _window_videos(await load_videos(session), today, days)
    top = sorted(window, key=lambda v: v.views, reverse=True)[:TOP_VIDEOS]
    return {
        "days": days,
        "tracking_since": accounts[0].snapshot_date if accounts else (totals[0][0] if totals else None),
        "followers_series": [
            {"date": a.snapshot_date, "followers": a.follower_count} for a in accounts if a.follower_count is not None
        ],
        "views_series": views_series,
        "top_videos": [_video_item(v, today) for v in top],
        "viral": viral_videos(window, today),
        "distribution": distribution(window),
    }


# --- horarios ---------------------------------------------------------------------------------------------


def _slot_stats(videos: list[VideoStats]) -> dict[str, Any]:
    return {"videos": len(videos), "avg_views": _avg_views(videos)}


async def posting_times(
    session: AsyncSession, days: int = 90, today: date | None = None, timezone: str | None = None
) -> dict[str, Any]:
    """Mapa de calor día de la semana x hora de publicación contra vistas medias, en la zona horaria del reporte."""
    today = today or date.today()
    tz_name = timezone or settings.report_timezone
    tz = ZoneInfo(tz_name)
    # published_within ya descarta los videos sin fecha de creación.
    window = _window_videos(await load_videos(session), today, days)
    cells: dict[tuple[int, int], list[VideoStats]] = defaultdict(list)
    for v in window:
        local = (v.created_at or datetime.min.replace(tzinfo=UTC)).astimezone(tz)
        cells[(local.weekday(), local.hour)].append(v)
    heatmap = [[_slot_stats(cells.get((d, h), [])) for h in range(24)] for d in range(7)]
    ranked = sorted(
        ((key, vids) for key, vids in cells.items()),
        key=lambda kv: (_avg_views(kv[1]) or 0.0, len(kv[1])),
        reverse=True,
    )[:TOP_SLOTS]
    by_weekday = [
        {"weekday": d, "name": WEEKDAY_NAMES[d], **_slot_stats([v for (wd, _), vs in cells.items() if wd == d for v in vs])}
        for d in range(7)
    ]
    by_hour = [
        {"hour": h, **_slot_stats([v for (_, hr), vs in cells.items() if hr == h for v in vs])} for h in range(24)
    ]
    return {
        "days": days,
        "timezone": tz_name,
        "videos": len(window),
        "weekday_names": list(WEEKDAY_NAMES),
        "heatmap": heatmap,  # heatmap[weekday 0=lunes][hora 0-23] = {videos, avg_views}
        "best_slots": [
            {
                "weekday": wd,
                "weekday_name": WEEKDAY_NAMES[wd],
                "hour": hr,
                **_slot_stats(vids),
                "low_sample": len(vids) < MIN_RELIABLE_SLOT_VIDEOS,
            }
            for (wd, hr), vids in ranked
        ],
        "by_weekday": by_weekday,
        "by_hour": by_hour,
    }


# --- hashtags y duración ----------------------------------------------------------------------------------


def _tag_table(groups: dict[str, list[VideoStats]]) -> list[dict[str, Any]]:
    rows = [
        {
            "tag": tag,
            "videos": len(vids),
            "avg_views": _avg_views(vids),
            "total_views": sum(v.views for v in vids),
            "engagement": _group_engagement(vids),
        }
        for tag, vids in groups.items()
    ]
    rows.sort(key=lambda r: (r["videos"], r["avg_views"] or 0.0), reverse=True)
    return rows[:TOP_TAGS]


def duration_table(videos: list[VideoStats]) -> list[dict[str, Any]]:
    """Vistas medias y engagement por rango de duración (solo videos con duración conocida)."""
    groups: dict[str, list[VideoStats]] = {b[0]: [] for b in DURATION_BUCKETS}
    for v in videos:
        if v.duration_seconds is not None:
            groups[_bucket_label(v.duration_seconds, DURATION_BUCKETS)].append(v)
    return [
        {
            "label": label,
            "min_seconds": low,
            "max_seconds": high,
            "videos": len(groups[label]),
            "avg_views": _avg_views(groups[label]),
            "engagement": _group_engagement(groups[label]),
        }
        for label, low, high in DURATION_BUCKETS
    ]


async def hashtags(session: AsyncSession, days: int = 90, today: date | None = None) -> dict[str, Any]:
    """Hashtags y menciones del título/descripción con su rendimiento, y la duración que mejor funciona."""
    today = today or date.today()
    window = _window_videos(await load_videos(session), today, days)
    by_tag: dict[str, list[VideoStats]] = defaultdict(list)
    by_mention: dict[str, list[VideoStats]] = defaultdict(list)
    for v in window:
        tags, mentions = extract_tags(v.text)
        for tag in tags:
            by_tag[tag].append(v)
        for mention in mentions:
            by_mention[mention].append(v)
    durations = duration_table(window)
    measured = [d for d in durations if d["videos"]]
    best = max(measured, key=lambda d: d["avg_views"] or 0.0) if measured else None
    return {
        "days": days,
        "videos": len(window),
        "videos_without_hashtags": sum(1 for v in window if not extract_tags(v.text)[0]),
        "hashtags": _tag_table(by_tag),
        "mentions": _tag_table(by_mention),
        "durations": durations,
        "best_duration": best["label"] if best else None,
    }


# --- cruce con TikTok Shop --------------------------------------------------------------------------------

NO_MATCH_NOTE = (
    "Ningún video orgánico aparece como content_id en las ventas de afiliado ni en el rendimiento de video "
    "de TikTok Shop. Es lo esperado si la cuenta no publica con enlaces de producto de la tienda."
)


async def vs_shop(session: AsyncSession, days: int = 90, today: date | None = None) -> dict[str, Any]:
    """Ventas de TikTok Shop atribuidas a videos orgánicos (por coincidencia de id de video)."""
    today = today or date.today()
    cutoff = today - timedelta(days=days)
    videos = {v.video_id: v for v in await load_videos(session)}
    if not videos:
        return _vs_shop_empty(days, 0, "Aún no hay videos orgánicos sincronizados.")
    ids = list(videos)
    shop_rows = await session.scalars(
        select(VideoPerformance).where(
            VideoPerformance.video_id.in_(ids),
            (VideoPerformance.window_end.is_(None)) | (VideoPerformance.window_end >= cutoff),
        )
    )
    per_video: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in shop_rows:
        acc = per_video[row.video_id]
        acc["shop_gmv"] += float(row.gmv)
        acc["shop_orders"] += row.orders
        acc["shop_units"] += row.units
        acc["shop_views"] += row.views or 0
    affiliate = await session.execute(
        select(
            AffiliateOrder.content_id,
            func.count(func.distinct(AffiliateOrder.external_order_id)),
            func.sum(AffiliateOrder.units),
            func.sum(AffiliateOrder.commission_base),
            func.sum(AffiliateOrder.commission),
        )
        .where(AffiliateOrder.content_id.in_(ids), AffiliateOrder.order_date >= cutoff)
        .group_by(AffiliateOrder.content_id)
    )
    for content_id, orders, units, base, commission in affiliate.all():
        acc = per_video[content_id]
        acc["affiliate_orders"] += orders or 0
        acc["affiliate_units"] += units or 0
        acc["affiliate_sales"] += float(base or 0)
        acc["affiliate_commission"] += float(commission or 0)
    if not per_video:
        return _vs_shop_empty(days, len(videos), NO_MATCH_NOTE)
    items = []
    for video_id, acc in per_video.items():
        v = videos[video_id]
        items.append(
            {
                "video_id": video_id,
                "title": v.title,
                "share_url": v.share_url,
                "organic_views": v.views,
                **{k: round(acc.get(k, 0.0), 2) for k in _SHOP_KEYS},
            }
        )
    items.sort(key=lambda i: i["shop_gmv"] + i["affiliate_sales"], reverse=True)
    totals = {k: round(sum(i[k] for i in items), 2) for k in _SHOP_KEYS}
    return {
        "days": days,
        "videos_tracked": len(videos),
        "matched_videos": len(items),
        "totals": totals,
        "videos": items,
        "note": "shop_* sale del rendimiento de video de la tienda (ventana de hasta 60 días); "
        "affiliate_* de las órdenes de afiliado con ese content_id dentro del periodo.",
    }


_SHOP_KEYS = (
    "shop_gmv",
    "shop_orders",
    "shop_units",
    "shop_views",
    "affiliate_orders",
    "affiliate_units",
    "affiliate_sales",
    "affiliate_commission",
)


def _vs_shop_empty(days: int, tracked: int, note: str) -> dict[str, Any]:
    return {
        "days": days,
        "videos_tracked": tracked,
        "matched_videos": 0,
        "totals": dict.fromkeys(_SHOP_KEYS, 0.0),
        "videos": [],
        "note": note,
    }
