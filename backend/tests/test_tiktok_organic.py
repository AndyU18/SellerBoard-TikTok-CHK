"""TikTok orgánico: OAuth, conector (MockTransport), upserts, analíticas, API y fuente de sincronización.

Todo con datos sintéticos y SQLite en memoria; ninguna prueba toca la red real ni el archivo de tokens real.
"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import sync_jobs
from app import tiktok_organic_analytics as an
from app.connectors import tiktok_organic as to
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    AffiliateOrder,
    SyncRun,
    TikTokOrganicAccount,
    TikTokOrganicVideo,
    TikTokOrganicVideoMetric,
    VideoPerformance,
)
from app.tiktok_organic_api import organic_http, organic_settings, router
from app.tiktok_organic_ops import METRIC_HISTORY_DAYS, save_snapshot

NOW = 1_800_000_000.0
TODAY = date(2026, 11, 24)
ALL_SCOPES = ",".join(to.REQUIRED_SCOPES)
SECRET_ACCESS, SECRET_REFRESH = "act.SECRET-ACCESS", "rft.SECRET-REFRESH"


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _settings(tmp_path: Path, redirect: str = "https://chk.test/callback") -> to.TikTokOrganicSettings:
    return to.TikTokOrganicSettings(
        client_key="ck", client_secret="cs", redirect_uri=redirect, tokens_file=tmp_path / "tokens.json"
    )


def _record(**over) -> to.TokenRecord:
    base = {
        "access_token": SECRET_ACCESS,
        "refresh_token": SECRET_REFRESH,
        "access_expires_at": NOW + 7200,
        "refresh_expires_at": NOW + 86400 * 100,
        "open_id": "oid",
        "scope": to.REQUIRED_SCOPES,
    }
    return to.TokenRecord(**{**base, **over})


def _token_payload(access="new-access", refresh="new-refresh") -> dict:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": 7200,
        "refresh_expires_in": 15_552_000,
        "open_id": "oid",
        "scope": ALL_SCOPES,
        "token_type": "Bearer",
    }


def _http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _client(tmp_path: Path, handler, record: to.TokenRecord | None = None, now: float = NOW) -> to.TikTokOrganicClient:
    settings = _settings(tmp_path)
    to.TokenStore(settings.tokens_file).save(record or _record())
    return to.TikTokOrganicClient(settings, _http(handler), clock=lambda: now)


def _raw_video(i: int, views: int = 1000, created: datetime | None = None, **over) -> dict:
    created = created or datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    return {
        "id": f"v{i}",
        "title": f"Video {i}",
        "video_description": f"Video {i} #chk @amigo",
        "create_time": int(created.timestamp()),
        "cover_image_url": f"https://cdn/{i}.jpg",
        "share_url": f"https://tiktok/v{i}",
        "embed_link": f"https://tiktok/embed/v{i}",
        "duration": 20,
        "width": 1080,
        "height": 1920,
        "view_count": views,
        "like_count": 100,
        "comment_count": 10,
        "share_count": 5,
        **over,
    }


# --- OAuth: state, URL, canje ------------------------------------------------------------------------------


def test_state_round_trip_tamper_and_expiry():
    state = to.make_state("secret", NOW, nonce="abc")
    assert to.verify_state("secret", state, NOW + 10)
    assert not to.verify_state("otro", state, NOW + 10)  # firma de otro secreto
    assert not to.verify_state("secret", state.replace("abc", "abd"), NOW + 10)  # manipulado
    assert not to.verify_state("secret", state, NOW + to.STATE_TTL_SECONDS + 1)  # vencido
    assert not to.verify_state("secret", state, NOW - 100)  # del futuro
    assert not to.verify_state("secret", "basura", NOW)


def test_authorize_url_has_required_params(tmp_path):
    url = to.build_authorize_url("st", _settings(tmp_path))
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == to.AUTHORIZE_URL
    assert q["client_key"] == ["ck"] and q["response_type"] == ["code"] and q["state"] == ["st"]
    assert q["redirect_uri"] == ["https://chk.test/callback"]
    assert q["scope"] == [ALL_SCOPES]
    assert "video.publish" not in url and "video.upload" not in url  # nunca permisos de escritura


def test_authorize_url_requires_redirect_uri(tmp_path):
    with pytest.raises(to.TikTokOrganicError) as exc:
        to.build_authorize_url("st", _settings(tmp_path, redirect=""))
    assert exc.value.code == "no_redirect_uri"


def test_token_record_repr_hides_secrets():
    text = repr(_record())
    assert SECRET_ACCESS not in text and SECRET_REFRESH not in text


def test_parse_token_response_computes_absolute_expiry_and_scopes():
    rec = to.parse_token_response(_token_payload(), NOW)
    assert rec.access_expires_at == NOW + 7200 and rec.refresh_expires_at == NOW + 15_552_000
    assert rec.scope == to.REQUIRED_SCOPES and rec.open_id == "oid"
    with pytest.raises(to.TikTokOrganicError):
        to.parse_token_response({"access_token": "x"}, NOW)


def test_exchange_code_posts_form_and_saves_tokens(tmp_path):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, json=_token_payload("a1", "r1"))

    settings = _settings(tmp_path)
    rec = to.exchange_code("the-code", settings, _http(handler), clock=lambda: NOW)
    assert seen["path"] == "/v2/oauth/token/"
    assert seen["form"]["grant_type"] == ["authorization_code"] and seen["form"]["code"] == ["the-code"]
    assert seen["form"]["redirect_uri"] == ["https://chk.test/callback"]
    assert seen["form"]["client_key"] == ["ck"]
    stored = json.loads(settings.tokens_file.read_text())
    assert stored["access_token"] == "a1" and stored["refresh_token"] == "r1"
    assert rec.access_token == "a1"


def test_exchange_code_error_saves_nothing(tmp_path):
    settings = _settings(tmp_path)
    handler = lambda r: httpx.Response(400, json={"error": "invalid_grant", "error_description": "code used"})
    with pytest.raises(to.TikTokOrganicError) as exc:
        to.exchange_code("bad", settings, _http(handler))
    assert exc.value.code == "invalid_grant" and not settings.tokens_file.exists()


# --- cliente: renovación, perfil, videos -------------------------------------------------------------------


def test_expired_token_is_refreshed_and_rotated_before_the_call(tmp_path):
    calls: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("authorization")))
        if request.url.path == "/v2/oauth/token/":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == [SECRET_REFRESH]
            return httpx.Response(200, json=_token_payload("fresh", "rotated"))
        return httpx.Response(200, json={"data": {"user": {"display_name": "CHK"}}, "error": {"code": "ok"}})

    client = _client(tmp_path, handler, _record(access_expires_at=NOW + 10))  # dentro del margen
    assert client.user_info() == {"display_name": "CHK"}
    assert [c[0] for c in calls] == ["/v2/oauth/token/", "/v2/user/info/"]
    assert calls[1][1] == "Bearer fresh"
    saved = json.loads((tmp_path / "tokens.json").read_text())
    assert saved["refresh_token"] == "rotated"  # el refresh token rota y se guarda


def test_valid_token_is_not_refreshed(tmp_path):
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": {"user": {}}, "error": {"code": "ok"}})

    _client(tmp_path, handler).user_info()
    assert paths == ["/v2/user/info/"]


def test_invalid_token_error_refreshes_and_retries_once(tmp_path):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/oauth/token/":
            return httpx.Response(200, json=_token_payload("again", "r2"))
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(401, json={"error": {"code": "access_token_invalid", "message": "x"}})
        return httpx.Response(200, json={"data": {"user": {"username": "chk"}}, "error": {"code": "ok"}})

    assert _client(tmp_path, handler).user_info() == {"username": "chk"}
    assert attempts["n"] == 2


def test_expired_refresh_token_asks_to_reauthorize(tmp_path):
    handler = lambda r: httpx.Response(500)
    client = _client(tmp_path, handler, _record(access_expires_at=NOW - 1, refresh_expires_at=NOW - 1))
    with pytest.raises(to.TikTokOrganicError) as exc:
        client.user_info()
    assert exc.value.code == "reauthorize"


def test_user_info_requests_only_granted_fields(tmp_path):
    fields: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fields.extend(request.url.params["fields"].split(","))
        return httpx.Response(200, json={"data": {"user": {}}, "error": {"code": "ok"}})

    _client(tmp_path, handler, _record(scope=(to.SCOPE_BASIC,))).user_info()
    assert set(fields) == {"avatar_url", "display_name"}  # sin follower_count: no hay user.info.stats


def test_iter_videos_follows_cursor_until_has_more_false(tmp_path):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        assert request.url.path == "/v2/video/list/" and "view_count" in request.url.params["fields"]
        first = "cursor" not in body
        data = {"videos": [{"id": "a" if first else "b"}], "cursor": 111, "has_more": first}
        return httpx.Response(200, json={"data": data, "error": {"code": "ok"}})

    ids = [v["id"] for v in _client(tmp_path, handler).iter_videos()]
    assert ids == ["a", "b"]
    assert bodies[0] == {"max_count": 20} and bodies[1] == {"max_count": 20, "cursor": 111}


def test_iter_videos_stops_when_cursor_does_not_advance(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        data = {"videos": [{"id": "x"}], "cursor": 5, "has_more": True}
        return httpx.Response(200, json={"data": data, "error": {"code": "ok"}})

    assert len(list(_client(tmp_path, handler).iter_videos())) == 2  # 2.ª página repite cursor: corta


def test_query_videos_batches_ids_by_20(tmp_path):
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["filters"]["video_ids"]
        sizes.append(len(ids))
        return httpx.Response(200, json={"data": {"videos": [{"id": i} for i in ids]}, "error": {"code": "ok"}})

    out = _client(tmp_path, handler).query_videos([str(i) for i in range(45)])
    assert sizes == [20, 20, 5] and len(out) == 45


def test_api_error_payload_raises(tmp_path):
    handler = lambda r: httpx.Response(
        403, json={"error": {"code": "scope_not_authorized", "message": "no", "log_id": "1"}}
    )
    with pytest.raises(to.TikTokOrganicError) as exc:
        _client(tmp_path, handler).user_info()
    assert exc.value.code == "scope_not_authorized"


# --- constructores puros -----------------------------------------------------------------------------------


def test_row_builders_normalize_api_payloads():
    user = {"open_id": "o", "username": "chk", "display_name": "CHK", "bio_description": "x" * 500, "follower_count": "1500"}
    acc = to.build_account_row(user, TODAY)
    assert acc["follower_count"] == 1500 and len(acc["bio"]) == to.BIO_MAX_CHARS and acc["likes_count"] is None
    video = to.build_video_row(_raw_video(1))
    assert video and video["video_id"] == "v1" and video["created_at"].tzinfo is not None and video["width"] == 1080
    assert to.build_video_row({"title": "sin id"}) is None
    assert to.build_metric_row({"id": "v1", "view_count": None}, TODAY) == {
        "video_id": "v1", "snapshot_date": TODAY, "views": 0, "likes": 0, "comments": 0, "shares": 0,
    }


class FakeClient:
    def __init__(self, videos, scopes=to.REQUIRED_SCOPES, fail_user=False):
        self._videos, self._scopes, self._fail_user = videos, scopes, fail_user

    def has_scope(self, scope):
        return scope in self._scopes

    def user_info(self):
        if self._fail_user:
            raise to.TikTokOrganicError("scope_not_authorized", "x")
        return {"username": "chk", "follower_count": 10}

    def iter_videos(self):
        yield from self._videos


def test_fetch_snapshot_degrades_when_a_scope_is_missing():
    snap = to.fetch_snapshot(FakeClient([_raw_video(1)], scopes=(to.SCOPE_BASIC,), fail_user=True), TODAY)
    assert snap.account is None and snap.videos == [] and len(snap.problems) == 2
    ok = to.fetch_snapshot(FakeClient([_raw_video(1), _raw_video(2)]), TODAY)
    assert len(ok.videos) == len(ok.metrics) == 2 and ok.problems == []


# --- configuración y fuente de sincronización --------------------------------------------------------------


def test_source_is_not_configured_without_tokens_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOK_ORGANIC_CLIENT_KEY", "ck")
    monkeypatch.setenv("TIKTOK_ORGANIC_CLIENT_SECRET", "cs")
    tokens = tmp_path / "tiktok_organic_tokens.json"
    monkeypatch.setenv("TIKTOK_ORGANIC_TOKENS_FILE", str(tokens))
    assert "tiktok_organic" in sync_jobs.SOURCES
    assert sync_jobs.LABELS["tiktok_organic"] == "TikTok orgánico (perfil y videos)"
    assert sync_jobs.is_configured("tiktok_organic") is False
    to.TokenStore(tokens).save(_record())
    assert sync_jobs.is_configured("tiktok_organic") is True


@pytest.mark.asyncio
async def test_run_source_saves_snapshot_and_reports_partial(maker, monkeypatch):
    fake = FakeClient([_raw_video(1, 500), _raw_video(2, 900)], fail_user=True)
    monkeypatch.setattr(sync_jobs, "TikTokOrganicClient", lambda: fake)
    async with maker() as s:
        out = await sync_jobs.run_source(s, "tiktok_organic", 14, "manual")
        run = (await s.scalars(select(SyncRun).where(SyncRun.job_type == "tiktok_organic"))).one()
    assert out["stats"] == {"account": 0, "videos": 2, "metrics": 2}
    assert run.status == "partial" and "perfil" in (run.error or "")


# --- persistencia ------------------------------------------------------------------------------------------


def _snapshot(videos, day=TODAY, followers=100) -> to.OrganicSnapshot:
    return to.OrganicSnapshot(
        account=to.build_account_row({"username": "chk", "follower_count": followers}, day),
        videos=[r for v in videos if (r := to.build_video_row(v))],
        metrics=[r for v in videos if (r := to.build_metric_row(v, day))],
        problems=[],
    )


async def _count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


@pytest.mark.asyncio
async def test_save_snapshot_is_idempotent_and_updates(maker):
    now = datetime(2026, 11, 24, 12, tzinfo=UTC)
    async with maker() as s:
        await save_snapshot(s, _snapshot([_raw_video(1, 100)]), TODAY, now)
        await save_snapshot(s, _snapshot([_raw_video(1, 250)], followers=120), TODAY, now)
        assert await _count(s, TikTokOrganicAccount) == 1
        assert await _count(s, TikTokOrganicVideo) == 1
        assert await _count(s, TikTokOrganicVideoMetric) == 1
        metric = await s.scalar(select(TikTokOrganicVideoMetric))
        account = await s.scalar(select(TikTokOrganicAccount))
        assert metric.views == 250 and account.follower_count == 120
        await save_snapshot(s, _snapshot([_raw_video(1, 300)], day=TODAY + timedelta(days=1)), TODAY + timedelta(days=1), now)
        assert await _count(s, TikTokOrganicVideoMetric) == 2  # un día nuevo agrega foto


@pytest.mark.asyncio
async def test_save_snapshot_prunes_old_metric_history(maker):
    now = datetime(2026, 11, 24, 12, tzinfo=UTC)
    old = TODAY - timedelta(days=METRIC_HISTORY_DAYS + 5)
    async with maker() as s:
        await save_snapshot(s, _snapshot([_raw_video(1)], day=old), old, now)
        await save_snapshot(s, _snapshot([_raw_video(1)]), TODAY, now)
        days = (await s.scalars(select(TikTokOrganicVideoMetric.snapshot_date))).all()
        assert days == [TODAY]


# --- analíticas --------------------------------------------------------------------------------------------


def _at(days_ago: int, hour: int = 15) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time(), tzinfo=UTC).replace(hour=hour)


async def _seed(maker) -> None:
    """6 videos: uno viral (v1), tags/duraciones variadas; historial de 8 días para crecimiento."""
    specs = [
        (1, 30_000, 2, 15, {"video_description": "Gran lanzamiento #chk #oferta @amigo", "duration": 12}),
        (2, 1_000, 5, 15, {"video_description": "Otro #chk", "duration": 40}),
        (3, 900, 9, 15, {"video_description": "Sin etiquetas", "title": "Sin etiquetas", "duration": 40}),
        (4, 1_100, 12, 3, {"video_description": "#oferta del día", "duration": 70}),
        (5, 800, 20, 15, {"video_description": "#chk #tip", "duration": 20}),
        (6, 1_050, 25, 15, {"video_description": "Rutina #tip", "duration": 25}),
    ]
    now = datetime(2026, 11, 24, 12, tzinfo=UTC)
    async with maker() as s:
        for back, followers in ((8, 100), (0, 130)):
            day = TODAY - timedelta(days=back)
            videos = [
                _raw_video(i, views if back == 0 else views // 2, _at(age, hour), **extra)
                for i, views, age, hour, extra in specs
            ]
            snap = _snapshot(videos, day=day, followers=followers)
            await save_snapshot(s, snap, day, now)


def test_engagement_rate_and_growth_edges():
    assert an.engagement_rate(15, 100) == 0.15 and an.engagement_rate(5, 0) is None
    assert an.window_growth([(TODAY, 10)], TODAY, 7) is None
    g = an.window_growth([(TODAY - timedelta(days=6), 100), (TODAY, 130)], TODAY, 7)
    assert g and g["delta"] == 30 and g["pct"] == 30.0
    assert an.window_growth([(TODAY - timedelta(days=6), 0), (TODAY, 5)], TODAY, 7)["pct"] is None  # base 0
    assert an.window_growth([(TODAY - timedelta(days=20), 1), (TODAY, 9)], TODAY, 7) is None  # fuera de ventana


def test_extract_tags_dedups_and_lowercases():
    tags, mentions = an.extract_tags("Mira #CHK y #chk #Oferta @Amigo.Uno. Email a@b.com")
    assert tags == {"chk", "oferta"} and mentions == {"amigo.uno"}


@pytest.mark.asyncio
async def test_overview_totals_growth_and_pace(maker):
    await _seed(maker)
    async with maker() as s:
        out = await an.overview(s, days=30, today=TODAY)
    assert out["followers"] == 130 and out["videos_tracked"] == 6
    assert out["total_views"] == 34_850
    assert out["engagement"] == pytest.approx(round(6 * 115 / 34_850, 4))
    assert out["median_views"] == 1_025.0
    week7 = next(g for g in out["growth"] if g["days"] == 7)["followers"]
    assert week7 is None  # solo hay una foto dentro de 7 días
    week30 = next(g for g in out["growth"] if g["days"] == 30)["followers"]
    assert week30 and week30["delta"] == 30 and week30["pct"] == 30.0
    assert out["posting"]["videos_published"] == 6 and out["posting"]["posts_per_week"] == pytest.approx(1.4)
    assert out["posting"]["days_since_last_post"] == 2


@pytest.mark.asyncio
async def test_videos_sort_search_pagination_and_recent_growth(maker):
    await _seed(maker)
    async with maker() as s:
        by_views = await an.videos_list(s, sort="views", limit=2, today=TODAY)
        assert [i["video_id"] for i in by_views["items"]] == ["v1", "v4"] and by_views["total"] == 6
        page2 = await an.videos_list(s, sort="views", limit=2, offset=2, today=TODAY)
        assert len(page2["items"]) == 2
        found = await an.videos_list(s, q="OFERTA", sort="recent", today=TODAY)
        assert {i["video_id"] for i in found["items"]} == {"v1", "v4"}
        recent = await an.videos_list(s, sort="recent", today=TODAY, limit=1)
        assert recent["items"][0]["video_id"] == "v1"  # el más nuevo
        week = await an.videos_list(s, sort="views_per_day", today=TODAY, limit=1)
        assert week["items"][0]["video_id"] == "v1" and week["items"][0]["views_per_day"] == 15_000.0


@pytest.mark.asyncio
async def test_video_growth_needs_two_snapshots_in_window(maker):
    await _seed(maker)
    async with maker() as s:
        out = await an.videos_list(s, today=TODAY, limit=1)
        assert out["items"][0]["recent_growth"] is None  # snapshots a 8 días: fuera de 7
        await save_snapshot(s, _snapshot([_raw_video(1, 31_000, _at(2), duration=12, video_description="Gran lanzamiento")], day=TODAY - timedelta(days=3)),
                            TODAY - timedelta(days=3), datetime(2026, 11, 24, tzinfo=UTC))
        out = await an.videos_list(s, q="lanzamiento", today=TODAY)
        growth = out["items"][0]["recent_growth"]
        assert growth and growth["delta"] == -1000  # 31.000 -> 30.000: sin inventar crecimiento


@pytest.mark.asyncio
async def test_performance_viral_distribution_and_series(maker):
    await _seed(maker)
    async with maker() as s:
        out = await an.performance(s, days=90, today=TODAY)
    assert [v["video_id"] for v in out["viral"]["videos"]] == ["v1"]
    assert out["viral"]["median_views"] == 1_025.0 and out["viral"]["threshold"] == 3075.0
    assert out["top_videos"][0]["video_id"] == "v1"
    assert sum(b["videos"] for b in out["distribution"]) == 6
    assert out["distribution"][-2]["videos"] == 0 and next(b for b in out["distribution"] if b["label"] == "10K-49K")["videos"] == 1
    assert [p["followers"] for p in out["followers_series"]] == [100, 130]
    gained = [p["views_gained"] for p in out["views_series"]]
    assert gained[0] is None and gained[1] == 34_850 - 34_850 // 2 - 0 or gained[1] > 0


@pytest.mark.asyncio
async def test_viral_needs_minimum_videos(maker):
    async with maker() as s:
        await save_snapshot(s, _snapshot([_raw_video(i) for i in range(1, 4)]), TODAY, datetime(2026, 11, 24, tzinfo=UTC))
        out = await an.performance(s, days=90, today=TODAY)
    assert out["viral"]["videos"] == [] and out["viral"]["note"]


@pytest.mark.asyncio
async def test_posting_times_uses_report_timezone(maker):
    await _seed(maker)
    async with maker() as s:
        utc = await an.posting_times(s, days=90, today=TODAY, timezone="UTC")
        ny = await an.posting_times(s, days=90, today=TODAY, timezone="America/New_York")
    v1_day = (TODAY - timedelta(days=2)).weekday()
    assert utc["heatmap"][v1_day][15]["videos"] >= 1 and utc["best_slots"][0]["hour"] == 15
    assert utc["best_slots"][0]["weekday"] == v1_day and utc["best_slots"][0]["avg_views"] == 15_450.0  # v1 y v3 caen en domingo 15:00 UTC
    assert ny["best_slots"][0]["hour"] == 10 and ny["timezone"] == "America/New_York"  # 15:00 UTC = 10:00 EST
    assert len(utc["heatmap"]) == 7 and len(utc["heatmap"][0]) == 24 and len(utc["by_hour"]) == 24
    assert utc["best_slots"][0]["low_sample"] is True


@pytest.mark.asyncio
async def test_hashtags_mentions_and_duration(maker):
    await _seed(maker)
    async with maker() as s:
        out = await an.hashtags(s, days=90, today=TODAY)
    tags = {t["tag"]: t for t in out["hashtags"]}
    assert tags["chk"]["videos"] == 3 and tags["oferta"]["videos"] == 2 and tags["tip"]["videos"] == 2
    assert tags["chk"]["avg_views"] == pytest.approx((30_000 + 1_000 + 800) / 3, abs=0.1)
    assert [m["tag"] for m in out["mentions"]] == ["amigo"] and out["videos_without_hashtags"] == 1
    assert out["best_duration"] == "0-15 s"
    assert sum(d["videos"] for d in out["durations"]) == 6


@pytest.mark.asyncio
async def test_vs_shop_matches_content_id_and_handles_no_match(maker):
    await _seed(maker)
    async with maker() as s:
        empty = await an.vs_shop(s, days=90, today=TODAY)
        assert empty["matched_videos"] == 0 and "content_id" in empty["note"] and empty["videos_tracked"] == 6
        s.add(VideoPerformance(account_id=1, video_id="v1", gmv=Decimal("250.50"), orders=5, units=7, views=900,
                               window_end=TODAY))
        s.add(VideoPerformance(account_id=1, video_id="otra-cuenta", gmv=Decimal(99)))
        s.add(AffiliateOrder(account_id=1, external_order_id="o1", sku_id="s1", order_date=TODAY - timedelta(days=3),
                             content_id="v1", units=2, commission_base=Decimal(40), commission=Decimal(4)))
        s.add(AffiliateOrder(account_id=1, external_order_id="o2", sku_id="s1", order_date=TODAY - timedelta(days=300),
                             content_id="v1", units=9, commission_base=Decimal(900), commission=Decimal(90)))
        await s.commit()
        out = await an.vs_shop(s, days=90, today=TODAY)
    assert out["matched_videos"] == 1 and out["videos"][0]["video_id"] == "v1"
    assert out["totals"]["shop_gmv"] == 250.5 and out["totals"]["affiliate_sales"] == 40.0  # el de hace 300 días no cuenta
    assert out["totals"]["affiliate_orders"] == 1 and out["videos"][0]["organic_views"] == 30_000


@pytest.mark.asyncio
async def test_vs_shop_without_videos_says_so(maker):
    async with maker() as s:
        out = await an.vs_shop(s, days=90, today=TODAY)
    assert out["matched_videos"] == 0 and "sincronizados" in out["note"]


# --- API ---------------------------------------------------------------------------------------------------


def _app(maker, tmp_path: Path, handler=None, configured: bool = True) -> FastAPI:
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()  # el router aún no está en main.py: se prueba montado aparte
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[organic_settings] = lambda: _settings(tmp_path) if configured else None
    app.dependency_overrides[organic_http] = lambda: _http(handler or (lambda r: httpx.Response(500)))
    return app


def _web(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_status_lists_pending_steps_then_connected(maker, tmp_path):
    async with _web(_app(maker, tmp_path, configured=False)) as c:
        body = (await c.get("/api/tiktok-organic/status")).json()
    assert body["configured"] is False and body["connected"] is False and "TIKTOK_ORGANIC_CLIENT_KEY" in body["pending_steps"][0]
    app = _app(maker, tmp_path)
    async with _web(app) as c:
        body = (await c.get("/api/tiktok-organic/status")).json()
        assert body["configured"] and not body["connected"] and "Autorizar" in body["pending_steps"][0]
        to.TokenStore(tmp_path / "tokens.json").save(_record(access_expires_at=NOW, refresh_expires_at=NOW + 86400 * 90))
        body = (await c.get("/api/tiktok-organic/status")).json()
    assert body["configured"] and body["missing_scopes"] == []
    for secret in (SECRET_ACCESS, SECRET_REFRESH):
        assert secret not in json.dumps(body)


@pytest.mark.asyncio
async def test_authorize_url_endpoint_returns_signed_state(maker, tmp_path):
    async with _web(_app(maker, tmp_path)) as c:
        body = (await c.get("/api/tiktok-organic/authorize-url")).json()
    assert body["redirect_uri"] == "https://chk.test/callback" and body["scopes"] == list(to.REQUIRED_SCOPES)
    assert to.verify_state("cs", body["state"], datetime.now(UTC).timestamp())
    assert f"state={body['state']}" in body["authorize_url"]
    async with _web(_app(maker, tmp_path, configured=False)) as c:
        assert (await c.get("/api/tiktok-organic/authorize-url")).status_code == 409


@pytest.mark.asyncio
async def test_connect_saves_tokens_and_never_returns_them(maker, tmp_path):
    handler = lambda r: httpx.Response(200, json=_token_payload("LEAK-ACCESS", "LEAK-REFRESH"))
    async with _web(_app(maker, tmp_path, handler)) as c:
        state = (await c.get("/api/tiktok-organic/authorize-url")).json()["state"]
        resp = await c.post("/api/tiktok-organic/connect", json={"code": "abc", "state": state})
        assert resp.status_code == 200
        assert "LEAK" not in resp.text and "access_token" not in resp.text and "refresh_token" not in resp.text
        assert resp.json()["connected"] is True and resp.json()["missing_scopes"] == []
        assert json.loads((tmp_path / "tokens.json").read_text())["access_token"] == "LEAK-ACCESS"
        gone = await c.post("/api/tiktok-organic/disconnect")
        assert gone.json() == {"disconnected": True, "had_tokens": True}
        assert not (tmp_path / "tokens.json").exists()
        again = await c.post("/api/tiktok-organic/disconnect")
        assert again.json()["had_tokens"] is False  # idempotente


@pytest.mark.asyncio
async def test_connect_rejects_bad_state_and_tiktok_errors(maker, tmp_path):
    handler = lambda r: httpx.Response(400, json={"error": "invalid_grant", "error_description": "code expired"})
    async with _web(_app(maker, tmp_path, handler)) as c:
        bad = await c.post("/api/tiktok-organic/connect", json={"code": "abc", "state": "forjado.1.firma"})
        assert bad.status_code == 400 and not (tmp_path / "tokens.json").exists()
        state = (await c.get("/api/tiktok-organic/authorize-url")).json()["state"]
        err = await c.post("/api/tiktok-organic/connect", json={"code": "abc", "state": state})
        assert err.status_code == 400 and "invalid_grant" in err.json()["detail"]
        assert not (tmp_path / "tokens.json").exists()


@pytest.mark.asyncio
async def test_connection_endpoints_need_sync_permission(maker, tmp_path, monkeypatch):
    from app.auth import CurrentUser, current_user

    app = _app(maker, tmp_path)
    app.dependency_overrides[current_user] = lambda: CurrentUser(id=None, email="a@b.c", permissions=frozenset({"profit.view"}))
    async with _web(app) as c:
        assert (await c.post("/api/tiktok-organic/disconnect")).status_code == 403
        assert (await c.get("/api/tiktok-organic/overview")).status_code == 200


@pytest.mark.asyncio
async def test_read_endpoints_return_documented_keys(maker, tmp_path):
    await _seed(maker)
    expected = {
        "overview": {"account", "followers", "growth", "total_views", "engagement", "avg_views", "median_views", "posting"},
        "videos": {"total", "items", "sort", "limit", "offset"},
        "performance": {"followers_series", "views_series", "top_videos", "viral", "distribution"},
        "posting-times": {"heatmap", "best_slots", "by_weekday", "by_hour", "timezone"},
        "hashtags": {"hashtags", "mentions", "durations", "best_duration"},
        "vs-shop": {"matched_videos", "totals", "videos", "note"},
    }
    async with _web(_app(maker, tmp_path)) as c:
        for name, keys in expected.items():
            resp = await c.get(f"/api/tiktok-organic/{name}")
            assert resp.status_code == 200, name
            assert keys <= set(resp.json()), name
        assert (await c.get("/api/tiktok-organic/performance?days=3")).status_code == 422  # días 7-365
        assert (await c.get("/api/tiktok-organic/videos?sort=bogus")).status_code == 422
        top = (await c.get("/api/tiktok-organic/videos?sort=views&limit=1&q=lanzamiento")).json()
        assert top["items"][0]["video_id"] == "v1"


@pytest.mark.asyncio
async def test_endpoints_work_with_empty_database(maker, tmp_path):
    async with _web(_app(maker, tmp_path)) as c:
        for name in ("overview", "videos", "performance", "posting-times", "hashtags", "vs-shop", "status"):
            assert (await c.get(f"/api/tiktok-organic/{name}")).status_code == 200, name
