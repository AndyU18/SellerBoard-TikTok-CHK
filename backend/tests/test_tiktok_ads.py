"""Conector de TikTok Ads: gasto diario hacia ad_spend_daily, solo lectura, sin credenciales reales."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.tiktok_ads import (
    TikTokAdsClient,
    TikTokAdsError,
    TikTokAdsSettings,
    build_spend_frame,
    sync_tiktok_ads,
)
from app.db import create_dev_schema, make_engine
from app.models import AdSpendDaily


def _client(handler) -> TikTokAdsClient:
    settings = TikTokAdsSettings(access_token="tok", advertiser_ids="111, 222")
    http = httpx.Client(base_url="https://x.test", transport=httpx.MockTransport(handler))
    return TikTokAdsClient(settings, http)


def _row(day: str, spend: str) -> dict:
    return {"dimensions": {"stat_time_day": f"{day} 00:00:00"}, "metrics": {"spend": spend}}


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_settings_split_advertisers_and_require_token():
    assert TikTokAdsSettings(access_token="t", advertiser_ids="1, 2 ,").advertisers == ["1", "2"]
    with pytest.raises(ValueError):
        TikTokAdsSettings(_env_file=None, access_token="", advertiser_ids="1")


def test_build_spend_frame_sums_advertisers_and_skips_zero_days():
    frame = build_spend_frame(
        [
            {"date": date(2026, 11, 1), "amount": 10.0},
            {"date": date(2026, 11, 1), "amount": 5.5},
            {"date": date(2026, 11, 2), "amount": 0.0},
        ]
    )
    assert frame.height == 1
    row = frame.row(0, named=True)
    assert row["amount"] == 15.5 and row["channel"] == "tiktok_shop" and row["sku"] is None


def test_client_rejects_api_errors_and_only_uses_get():
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json={"code": 40105, "message": "Access token is invalid"})

    with pytest.raises(TikTokAdsError, match="40105"):
        _client(handler).advertiser_info("111")
    assert methods == ["GET"]


def test_iter_daily_spend_splits_in_30_day_chunks_and_pages():
    calls: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params
        calls.append((q["start_date"], q["end_date"], q["page"]))
        assert request.headers["Access-Token"] == "tok"
        page = int(q["page"])
        rows = [_row(q["start_date"], "1.50")] if page == 1 else [_row(q["end_date"], "2.50")]
        return httpx.Response(200, json={"code": 0, "data": {"list": rows, "page_info": {"total_page": 2}}})

    out = _client(handler).iter_daily_spend("111", date(2026, 10, 1), date(2026, 11, 5))
    assert [c[:2] for c in calls] == [("2026-10-01", "2026-10-30")] * 2 + [("2026-10-31", "2026-11-05")] * 2
    assert sum(r["amount"] for r in out) == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_sync_writes_spend_replaces_window_and_skips_foreign_currency(maker):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/advertiser/info/"):
            ids = request.url.params["advertiser_ids"]
            cur = "USD" if "111" in ids else "MXN"
            return httpx.Response(200, json={"code": 0, "data": {"list": [{"name": f"A{ids}", "currency": cur}]}})
        return httpx.Response(
            200,
            json={"code": 0, "data": {"list": [_row("2026-11-20", "30.25"), _row("2026-11-21", "10")]}},
        )

    async with maker() as s:
        report = await sync_tiktok_ads(s, _client(handler), days=7, today=date(2026, 11, 22))
        await s.commit()
    assert report.stats["days_with_spend"] == 2 and report.stats["spend_total"] == 40.25
    assert len(report.problems) == 1 and "MXN" in report.problems[0]

    async with maker() as s:  # segunda corrida: reemplaza, no duplica
        await sync_tiktok_ads(s, _client(handler), days=7, today=date(2026, 11, 22))
        await s.commit()
        rows = (await s.scalars(select(AdSpendDaily))).all()
    assert len(rows) == 2
    assert sum(r.spend for r in rows) == Decimal("40.25")


@pytest.mark.asyncio
async def test_sync_without_spend_imports_nothing(maker):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/advertiser/info/"):
            return httpx.Response(200, json={"code": 0, "data": {"list": [{"name": "A", "currency": "USD"}]}})
        return httpx.Response(200, json={"code": 0, "data": {"list": []}})

    async with maker() as s:
        report = await sync_tiktok_ads(s, _client(handler), days=7, today=date(2026, 11, 22))
    assert report.stats["spend_total"] == 0.0 and report.imported.inserted == 0
