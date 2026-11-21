"""Embudo de tráfico, LIVE y vistas por video (analytics de TikTok Shop)."""

from datetime import UTC, date, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.tiktok_shop_ops import (
    build_affiliate_orders,
    build_live_sessions,
    build_traffic,
    build_video_performance,
)
from app.db import create_dev_schema, get_session, make_engine
from app.main import app
from app.ops_data import (
    upsert_affiliate_orders,
    upsert_live_sessions,
    upsert_traffic,
    upsert_video_performance,
)

ACC = ("tiktok_shop", "tiktok_shop_us")
T0 = int(datetime(2026, 11, 10, 12, 0, tzinfo=UTC).timestamp())


def _interval(day, impressions, views, buyers, orders, gmv, video=(0, 0, 0, 0)):
    """video = (impresiones, vistas, compradores, gmv) del origen VIDEO; el resto va a la tarjeta de producto."""
    vi, vv, vb, vg = video
    return {
        "start_date": day,
        "end_date": day,
        "product_impressions": impressions,
        "product_page_views": views,
        "avg_product_page_visitors": views - 10,
        "buyers": buyers,
        "orders": orders,
        "units_sold": orders + 1,
        "gmv": {"amount": str(gmv), "currency": "USD"},
        "product_impression_breakdowns": [
            {"type": "LIVE", "amount": 0},
            {"type": "VIDEO", "amount": vi},
            {"type": "PRODUCT_CARD", "amount": impressions - vi},
        ],
        "product_page_view_breakdowns": [
            {"type": "LIVE", "amount": 0},
            {"type": "VIDEO", "amount": vv},
            {"type": "PRODUCT_CARD", "amount": views - vv},
        ],
        "avg_product_page_visitor_breakdowns": [],
        "buyer_breakdowns": [
            {"type": "LIVE", "amount": 0},
            {"type": "VIDEO", "amount": vb},
            {"type": "PRODUCT_CARD", "amount": buyers - vb},
        ],
        "gmv_breakdowns": [
            {"type": "LIVE", "amount": "0.00", "currency": "USD"},
            {"type": "VIDEO", "amount": str(vg), "currency": "USD"},
            {"type": "PRODUCT_CARD", "amount": str(gmv - vg), "currency": "USD"},
        ],
    }


LIVE = {
    "id": "L1",
    "username": "creator1",
    "title": "Oferta",
    "start_time": str(T0),
    "end_time": str(T0 + 7200),  # 2 horas
    "sales_performance": {
        "gmv": {"amount": "200", "currency": "USD"},
        "24h_live_gmv": {"amount": "210", "currency": "USD"},
        "sku_orders": 5,
        "customers": 4,
        "unit_sold": 6,
        "products_added": 3,
        "different_products_sold": 2,
        "click_to_order_rate": "21.43%",
        "avg_price": {"amount": "33.33", "currency": "USD"},
    },
}


@pytest_asyncio.fixture
async def env():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, maker
    app.dependency_overrides.clear()
    await engine.dispose()


def test_builders_split_traffic_by_source_and_parse_percentages():
    rows = build_traffic([_interval("2026-11-10", 1000, 50, 5, 5, 250, video=(800, 40, 4, 200))])
    by_source = {r["source"]: r for r in rows}
    assert set(by_source) == {"ALL", "LIVE", "VIDEO", "PRODUCT_CARD"}
    assert by_source["ALL"]["orders"] == 5 and by_source["ALL"]["gmv"] == 250
    assert by_source["VIDEO"]["impressions"] == 800 and by_source["PRODUCT_CARD"]["impressions"] == 200
    assert by_source["VIDEO"]["orders"] is None  # los pedidos solo vienen en el total

    [live] = build_live_sessions([LIVE])
    assert live["click_to_order_rate"] == pytest.approx(0.2143) and live["gmv"] == 200 and live["orders"] == 5

    [video] = build_video_performance(
        [
            {
                "id": 7,
                "username": "c",
                "views": 1000,
                "click_through_rate": "0.0196",
                "video_post_time": "2026-11-11 01:20:08",
                "gmv": {"amount": "30", "currency": "USD"},
                "sku_orders": 2,
                "units_sold": 2,
            }
        ],
        date(2026, 10, 1),
        date(2026, 11, 1),
    )
    assert video["click_through_rate"] == pytest.approx(0.0196) and video["posted_at"].year == 2026


@pytest.mark.asyncio
async def test_funnel_endpoint_rates_and_sources(env):
    client, maker = env
    rows = build_traffic(
        [
            _interval("2026-11-10", 1000, 50, 5, 5, 250, video=(800, 40, 4, 200)),
            _interval("2026-11-11", 3000, 150, 15, 16, 750, video=(2400, 120, 12, 600)),
        ]
    )
    async with maker() as s:
        await upsert_traffic(s, *ACC, rows)
        await upsert_traffic(s, *ACC, rows)  # idempotente
        await s.commit()

    body = (await client.get("/api/funnel")).json()
    t = body["totals"]
    assert body["days"] == 2
    assert t["impressions"] == 4000 and t["page_views"] == 200 and t["buyers"] == 20 and t["orders"] == 21
    assert t["ctr"] == pytest.approx(0.05) and t["conversion"] == pytest.approx(0.10)
    assert t["aov"] == pytest.approx(1000 / 21)
    video = next(s for s in body["by_source"] if s["source"] == "VIDEO")
    assert video["impressions"] == 3200 and video["gmv"] == 800
    assert video["gmv_share"] == pytest.approx(0.8)  # 800 de 1,000 (LIVE 0, tarjeta 200)
    assert len(body["daily"]) == 2


@pytest.mark.asyncio
async def test_live_endpoint_duration_and_gmv_per_hour(env):
    client, maker = env
    quiet = {
        **LIVE,
        "id": "L2",
        "username": "creator2",
        "sales_performance": {
            **LIVE["sales_performance"],
            "gmv": {"amount": "0", "currency": "USD"},
            "sku_orders": 0,
        },
    }
    async with maker() as s:
        await upsert_live_sessions(s, *ACC, build_live_sessions([LIVE, quiet]))
        await s.commit()

    body = (await client.get("/api/live")).json()
    sm = body["summary"]
    assert sm["sessions"] == 2 and sm["sessions_with_sales"] == 1
    assert sm["gmv"] == 200 and sm["avg_duration_min"] == pytest.approx(120)
    assert sm["gmv_per_hour"] == pytest.approx(200 / 4)  # 200 en 4 horas de transmisión (2 sesiones)
    assert body["top_sessions"][0]["external_id"] == "L1"
    assert body["by_creator"][0]["username"] == "creator1"


@pytest.mark.asyncio
async def test_creatives_include_views_and_ctr(env):
    client, maker = env
    affiliate = {
        "id": "O1",
        "create_time": T0,
        "skus": [
            {
                "sku_id": "s",
                "seller_sku": "A",
                "quantity": 1,
                "creator_username": "c",
                "content_type": "VIDEO",
                "content_id": "7",
                "commission_rate": "1000",
                "actual_commission_base": {},
                "actual_paid_commission": {},
                "estimated_commission_base": {"amount": "50", "currency": "USD"},
                "estimated_paid_commission": {"amount": "5", "currency": "USD"},
                "settlement_status": "PENDING",
            }
        ],
    }
    video = {
        "id": 7,
        "username": "c",
        "views": 1234,
        "click_through_rate": "0.02",
        "video_post_time": "2026-11-09 10:00:00",
        "gmv": {"amount": "50", "currency": "USD"},
        "sku_orders": 1,
        "units_sold": 1,
    }
    async with maker() as s:
        await upsert_affiliate_orders(s, *ACC, build_affiliate_orders([affiliate]))
        await upsert_video_performance(
            s, *ACC, build_video_performance([video], date(2026, 10, 1), date(2026, 11, 1))
        )
        await s.commit()
    [content] = (await client.get("/api/creatives")).json()["contents"]
    assert content["views"] == 1234 and content["ctr"] == pytest.approx(0.02)
