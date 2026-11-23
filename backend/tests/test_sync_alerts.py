"""Sincronización automática (registro de ejecuciones, vencimiento) y alertas."""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import sync_jobs
from app.connectors.walmart import (
    build_listing_stock,
    build_order_lines,
    build_return_cases,
)
from app.db import create_dev_schema, get_session, make_engine
from app.gold import refresh_daily_profit
from app.importers.persist import import_orders
from app.main import app
from app.models import SyncRun
from app.ops_data import upsert_listing_stock

NOW_MS = int(datetime.now(UTC).timestamp() * 1000)


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


async def _ok(session, days):
    return {"days": days, "stats": {"lines": 3}}


async def _boom(session, days):
    raise RuntimeError("Walmart 500: caído")


@pytest.mark.asyncio
async def test_run_source_records_success_and_failure(env, monkeypatch):
    _, maker = env
    monkeypatch.setitem(sync_jobs.RUNNERS, "walmart", _ok)
    async with maker() as s:
        await sync_jobs.run_source(s, "walmart", 7, "manual")
    monkeypatch.setitem(sync_jobs.RUNNERS, "walmart", _boom)
    async with maker() as s:
        with pytest.raises(RuntimeError):
            await sync_jobs.run_source(s, "walmart", 7, "schedule")
    async with maker() as s:
        runs = (await s.scalars(select(SyncRun).order_by(SyncRun.id))).all()
    assert [(r.status, r.triggered_by) for r in runs] == [("success", "manual"), ("failed", "schedule")]
    assert runs[0].stats == {"lines": 3}
    assert "caído" in runs[1].error and runs[1].finished_at is not None


@pytest.mark.asyncio
async def test_run_due_skips_recent_sources_and_runs_stale_ones(env, monkeypatch):
    _, maker = env
    monkeypatch.setitem(sync_jobs.RUNNERS, "walmart", _ok)
    monkeypatch.setattr(sync_jobs, "is_configured", lambda source: source == "walmart")

    assert await sync_jobs.run_due(maker) == {"walmart": "ok"}  # nunca se había corrido
    assert await sync_jobs.run_due(maker) == {}  # recién corrió: no toca
    later = datetime.now(UTC) + sync_jobs.interval() + timedelta(minutes=1)
    assert await sync_jobs.run_due(maker, now=later) == {"walmart": "ok"}  # ya venció


@pytest.mark.asyncio
async def test_stuck_running_run_does_not_block_forever(env):
    _, maker = env
    async with maker() as s:
        s.add(SyncRun(job_type="walmart", status="running"))
        await s.commit()
    async with maker() as s:
        assert not await sync_jobs.is_due(s, "walmart")
        late = datetime.now(UTC) + sync_jobs.STALE_RUNNING + timedelta(minutes=1)
        assert await sync_jobs.is_due(s, "walmart", now=late)


def _order(oid, sku, qty, price):
    return {
        "customerOrderId": oid,
        "orderDate": NOW_MS,
        "orderLines": {
            "orderLine": [
                {
                    "item": {"sku": sku, "productName": f"Prod {sku}"},
                    "orderLineQuantity": {"amount": str(qty)},
                    "charges": {"charge": [{"chargeType": "PRODUCT", "chargeAmount": {"amount": price}}]},
                    "orderLineStatuses": {"orderLineStatus": [{"status": "Delivered"}]},
                }
            ]
        },
    }


def _return(rid, order, sku, qty, price):
    return {
        "returnOrderId": rid,
        "customerOrderId": order,
        "returnOrderDate": "2026-11-10T15:00:00.000Z",
        "returnOrderLines": [
            {
                "returnReason": "DAMAGED",
                "item": {"sku": sku, "productName": f"Prod {sku}"},
                "quantity": {"measurementValue": float(qty)},
                "charges": [{"chargeName": "ItemPrice", "chargePerUnit": {"currencyAmount": price}}],
                "status": "COMPLETED",
                "statusTime": "2026-11-12T10:00:00.000Z",
            }
        ],
    }


@pytest.mark.asyncio
async def test_alerts_flag_loss_stock_and_failed_sync(env, monkeypatch):
    client, maker = env
    monkeypatch.setattr(sync_jobs, "is_configured", lambda source: source == "walmart")
    cases = build_return_cases([_return("R1", "O1", "A", 2, 39.99)])
    frame, _ = build_order_lines([_order("O1", "A", 5, "50.00")], [], returns=cases)
    stock = build_listing_stock(
        [{"sku": "A", "productName": "Prod A", "price": {"amount": 10}, "publishedStatus": "PUBLISHED"}],
        [{"sku": "A", "nodes": [{"availToSellQty": {"amount": 0}}]}],
    )
    async with maker() as s:
        await import_orders(s, frame)
        await upsert_listing_stock(s, "walmart", "walmart_1", stock)
        await refresh_daily_profit(s, date.today() - timedelta(days=2), None, reason="test")
        s.add(SyncRun(job_type="walmart", status="failed", error="Walmart 500: caído"))
        await s.commit()

    body = (await client.get("/api/alerts")).json()
    by_id = {a["id"]: a for a in body["alerts"]}
    assert by_id["loss:walmart:A"]["severity"] in ("warning", "critical")  # 50 - comisión - 79.98 < 0
    assert by_id["stock:out:walmart_1:A"]["severity"] == "critical"
    assert by_id["sync:failed:walmart"]["severity"] == "critical"
    assert body["counts"]["critical"] >= 2
    severities = [a["severity"] for a in body["alerts"]]
    assert severities == sorted(severities, key=["critical", "warning", "info"].index)  # críticas primero


@pytest.mark.asyncio
async def test_sync_status_endpoint_lists_every_source(env, monkeypatch):
    client, maker = env
    monkeypatch.setattr(sync_jobs, "is_configured", lambda source: True)
    async with maker() as s:
        s.add(SyncRun(job_type="tiktok_shop", status="success", triggered_by="schedule", finished_at=datetime.now(UTC)))
        await s.commit()
    body = (await client.get("/api/sync/status")).json()
    rows = {r["source"]: r for r in body["sources"]}
    assert set(rows) == {"tiktok_shop", "tiktok_ads", "walmart", "redo"}
    assert rows["tiktok_shop"]["status"] == "success" and rows["tiktok_shop"]["next_due_at"]
    assert rows["walmart"]["status"] is None
