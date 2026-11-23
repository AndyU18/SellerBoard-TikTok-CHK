"""Resumen por correo/Slack/webhook: construcción, formato, programador, entrega y endpoints.

Nunca se envía nada real: SMTP y HTTP se sustituyen por dobles (monkeypatch y httpx.MockTransport).
"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import notify
from app.config import settings
from app.db import create_dev_schema, get_session, make_engine
from app.digest import (
    build_digest,
    period_ranges,
    render_html,
    render_slack,
    render_text,
)
from app.models import Account, GoldDailyProfit, NotificationLog, Product
from app.notifications_api import router

TODAY = date(2026, 11, 23)  # lunes; el diario cubre el 22 y el anterior el 21
SECRET_URL = "https://hooks.slack.test/services/T000/B000/SECRETTOKEN"
HOOK_URL = "https://hooks.example.test/digest/abc123"
PASSWORD = "contraseña-super-secreta"


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def client_factory(maker):
    app = FastAPI()
    app.include_router(router)

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    return lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """Sin canales ni envío automático salvo que cada prueba los configure."""
    monkeypatch.setattr(settings, "digest_enabled", False)
    monkeypatch.setattr(settings, "digest_daily_hour", 8)
    monkeypatch.setattr(settings, "digest_weekly_weekday", 0)
    monkeypatch.setattr(settings, "report_timezone", "America/New_York")
    for name in ("smtp_host", "smtp_from", "smtp_user", "smtp_password", "slack_webhook_url", "digest_webhook_url"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "smtp_to", [])


def _all_channels(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from", "resumen@example.test")
    monkeypatch.setattr(settings, "smtp_to", ["dueno@example.test"])
    monkeypatch.setattr(settings, "smtp_user", "usuario-smtp")
    monkeypatch.setattr(settings, "smtp_password", PASSWORD)
    monkeypatch.setattr(settings, "slack_webhook_url", SECRET_URL)
    monkeypatch.setattr(settings, "digest_webhook_url", HOOK_URL)


async def _seed(session, names=("Producto A", "Producto B")):
    session.add_all([Account(channel_code="tiktok_shop", name="tt1"), Account(channel_code="walmart", name="wm1")])
    products = [Product(sku=f"SKU-{i}", name=n) for i, n in enumerate(names)]
    session.add_all(products)
    await session.flush()
    acc = {a.channel_code: a.id for a in (await session.scalars(select(Account))).all()}

    def row(day, channel, product, units, gross, cogs):
        return GoldDailyProfit(
            profit_date=day, channel_code=channel, account_id=acc[channel], product_id=product.id,
            units=units, gross_sales=Decimal(gross), cogs=Decimal(cogs),
        )  # fmt: skip

    a, b = products[0], products[1]
    session.add_all(
        [
            row(date(2026, 11, 22), "tiktok_shop", a, 10, "200", "80"),  # utilidad 120
            row(date(2026, 11, 22), "walmart", b, 5, "100", "130"),  # utilidad -30
            row(date(2026, 11, 21), "tiktok_shop", a, 5, "100", "40"),  # utilidad 60 (periodo anterior)
            row(date(2026, 11, 20), "tiktok_shop", a, 99, "999", "0"),  # fuera de ambos periodos diarios
        ]
    )
    await session.commit()


# ---------------------------------------------------------------------------- periodos y construcción


def test_period_ranges_daily_and_weekly():
    assert period_ranges("daily", TODAY) == (
        (date(2026, 11, 22), date(2026, 11, 22)),
        (date(2026, 11, 21), date(2026, 11, 21)),
    )
    assert period_ranges("weekly", TODAY) == (
        (date(2026, 11, 16), date(2026, 11, 22)),
        (date(2026, 11, 9), date(2026, 11, 15)),
    )


@pytest.mark.asyncio
async def test_build_digest_daily_totals_channels_and_products(maker):
    async with maker() as s:
        await _seed(s)
        digest = await build_digest(s, "daily", today=TODAY)

    assert digest["range"] == {"start": "2026-11-22", "end": "2026-11-22"}
    assert digest["totals"]["gross_sales"] == 300.0
    assert digest["totals"]["net_profit"] == 90.0
    assert digest["totals"]["units"] == 15
    assert digest["totals"]["margin"] == 0.3
    assert digest["previous"]["gross_sales"] == 100.0
    assert digest["change"]["gross_sales"] == 2.0  # +200 %
    assert digest["change"]["net_profit"] == 0.5
    assert [c["channel"] for c in digest["channels"]] == ["tiktok_shop", "walmart"]
    assert digest["best_products"][0]["name"] == "Producto A"
    assert digest["worst_products"][0]["name"] == "Producto B"
    json.dumps(digest)  # debe ser serializable tal cual para el webhook


@pytest.mark.asyncio
async def test_build_digest_weekly_and_empty(maker):
    async with maker() as s:
        empty = await build_digest(s, "weekly", today=TODAY)
        assert empty["totals"]["gross_sales"] == 0.0
        assert empty["change"]["gross_sales"] is None
        assert empty["best_products"] == [] and empty["channels"] == []
        await _seed(s)
        week = await build_digest(s, "weekly", today=TODAY)
    assert week["totals"]["units"] == 119  # 15 (día 22) + 5 (día 21) + 99 (día 20)
    assert week["range"] == {"start": "2026-11-16", "end": "2026-11-22"}


# ---------------------------------------------------------------------------- formato


@pytest.mark.asyncio
async def test_renderers_escape_dangerous_product_names(maker):
    evil = "<script>alert('x')</script> & <b>"
    async with maker() as s:
        await _seed(s, names=(evil, "Normal"))
        digest = await build_digest(s, "daily", today=TODAY)
    out = render_html(digest)
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<b>" not in out
    assert "&lt;" in render_slack(digest) and "<script>" not in render_slack(digest)
    text = render_text(digest)
    assert "Resumen diario" in text and "Ventas brutas: $300.00" in text and "+200.0%" in text


# ---------------------------------------------------------------------------- programador


def _at(hour: int, day: int = 23, minute: int = 0) -> datetime:
    """Instante UTC equivalente a la hora local de Nueva York (UTC-5 en noviembre)."""
    return datetime(2026, 11, day, tzinfo=UTC) + timedelta(hours=hour + 5, minutes=minute)


@pytest.mark.asyncio
async def test_digest_due_before_and_after_hour(maker):
    async with maker() as s:
        assert await notify.digest_due(s, _at(7, minute=59)) == []
        assert await notify.digest_due(s, _at(8)) == ["daily", "weekly"]  # lunes


@pytest.mark.asyncio
async def test_digest_due_weekly_only_on_its_weekday(maker):
    async with maker() as s:
        assert await notify.digest_due(s, _at(9, day=24)) == ["daily"]  # martes


@pytest.mark.asyncio
async def test_digest_due_skips_already_sent_today_but_not_failed_old(maker):
    async with maker() as s:
        s.add(NotificationLog(kind="daily", channel="slack", status="sent", created_at=_at(8, minute=5)))
        s.add(NotificationLog(kind="weekly", channel="slack", status="failed", created_at=_at(8, minute=5)))
        await s.commit()
        assert await notify.digest_due(s, _at(9)) == []  # daily enviado; weekly fallido hace <1 h... espera
        assert await notify.digest_due(s, _at(10)) == ["weekly"]  # pasada la espera de reintento


@pytest.mark.asyncio
async def test_digest_due_uses_local_day_not_utc(maker, monkeypatch):
    """Un envío a las 23:30 locales de ayer no cuenta para hoy, aunque en UTC ya sea el mismo día."""
    async with maker() as s:
        s.add(NotificationLog(kind="daily", channel="slack", status="sent", created_at=_at(23, day=22, minute=30)))
        await s.commit()
        assert "daily" in await notify.digest_due(s, _at(8, day=23))
    monkeypatch.setattr(settings, "report_timezone", "Pacific/Auckland")
    async with maker() as s:
        # 08:00 en Auckland (UTC+13) del 24 = 19:00 UTC del 23: aún no es la hora local en Nueva York.
        assert await notify.digest_due(s, datetime(2026, 11, 23, 18, 0, tzinfo=UTC)) == []
        assert "daily" in await notify.digest_due(s, datetime(2026, 11, 23, 19, 0, tzinfo=UTC))


@pytest.mark.asyncio
async def test_send_due_does_nothing_when_disabled_or_without_channels(maker, monkeypatch):
    assert await notify.send_due(maker, _at(9)) == {}
    monkeypatch.setattr(settings, "digest_enabled", True)
    assert await notify.send_due(maker, _at(9)) == {}  # activo pero sin canales


# ---------------------------------------------------------------------------- entrega


class FakeSMTP:
    sent: ClassVar[list] = []
    fail: ClassVar[bool] = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        if FakeSMTP.fail:
            raise RuntimeError(f"auth falló para {user} con {password} en {SECRET_URL}")

    def send_message(self, message):
        FakeSMTP.sent.append(message)


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_deliver_all_channels_success(maker, monkeypatch):
    _all_channels(monkeypatch)
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    seen: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = json.loads(request.content)
        return httpx.Response(200)

    async with maker() as s:
        await _seed(s)
        digest = await build_digest(s, "daily", today=TODAY)
        async with _mock_client(handler) as http:
            result = await notify.deliver(s, "daily", digest, client=http)
        await s.commit()
        rows = (await s.scalars(select(NotificationLog))).all()

    assert {c: r["status"] for c, r in result.items()} == {"email": "sent", "slack": "sent", "webhook": "sent"}
    assert len(FakeSMTP.sent) == 1 and FakeSMTP.sent[0]["To"] == "dueno@example.test"
    assert "text" in seen["hooks.slack.test"] and "Resumen diario" in seen["hooks.slack.test"]["text"]
    payload = seen["hooks.example.test"]
    assert payload["kind"] == "daily" and payload["digest"]["totals"]["units"] == 15
    assert {(r.channel, r.status) for r in rows} == {("email", "sent"), ("slack", "sent"), ("webhook", "sent")}


@pytest.mark.asyncio
async def test_deliver_one_failure_does_not_stop_others_and_log_has_no_secrets(maker, monkeypatch):
    _all_channels(monkeypatch)
    FakeSMTP.sent, FakeSMTP.fail = [], True  # el correo falla y su error trae contraseña y URL
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500 if request.url.host == "hooks.slack.test" else 200)

    async with maker() as s:
        digest = await build_digest(s, "daily", today=TODAY)
        async with _mock_client(handler) as http:
            result = await notify.deliver(s, "daily", digest, client=http)
        await s.commit()
        rows = (await s.scalars(select(NotificationLog))).all()

    assert result["email"]["status"] == "failed"
    assert result["slack"]["status"] == "failed"
    assert result["webhook"]["status"] == "sent"
    blob = json.dumps(result) + " ".join(r.detail or "" for r in rows)
    for secret in (PASSWORD, "SECRETTOKEN", "usuario-smtp", "abc123", "smtp.example.test", "dueno@example.test"):
        assert secret not in blob
    assert "HTTP 500" in result["slack"]["detail"]


@pytest.mark.asyncio
async def test_deliver_requested_channel_not_configured_is_skipped(maker, monkeypatch):
    monkeypatch.setattr(settings, "slack_webhook_url", SECRET_URL)
    async with maker() as s:
        digest = await build_digest(s, "daily", today=TODAY)
        async with _mock_client(lambda r: httpx.Response(200)) as http:
            result = await notify.deliver(s, "test", digest, ["slack", "email"], client=http)
    assert result["slack"]["status"] == "sent" and result["email"]["status"] == "skipped"
    assert "webhook" not in result


# ---------------------------------------------------------------------------- endpoints


@pytest.mark.asyncio
async def test_test_endpoint_returns_409_without_channels(client_factory):
    async with client_factory() as c:
        res = await c.post("/api/notifications/test")
    assert res.status_code == 409
    assert res.json()["detail"]["problems"]


@pytest.mark.asyncio
async def test_test_endpoint_delivers_and_logs(client_factory, maker, monkeypatch):
    monkeypatch.setattr(settings, "slack_webhook_url", SECRET_URL)
    calls: list[str] = []

    async def fake_slack(text, *, client=None):
        calls.append(text)

    monkeypatch.setattr(notify, "send_slack", fake_slack)
    async with client_factory() as c:
        res = await c.post("/api/notifications/test")
    assert res.status_code == 200
    assert res.json()["results"] == {"slack": {"status": "sent", "detail": ""}}
    assert calls and calls[0].startswith("*[Prueba]")
    async with maker() as s:
        (row,) = (await s.scalars(select(NotificationLog))).all()
    assert (row.kind, row.channel, row.status) == ("test", "slack", "sent")


@pytest.mark.asyncio
async def test_status_does_not_leak_secrets(client_factory, monkeypatch):
    _all_channels(monkeypatch)
    monkeypatch.setattr(settings, "digest_enabled", True)
    async with client_factory() as c:
        res = await c.get("/api/notifications/status")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True and body["channels"] == {"email": True, "slack": True, "webhook": True}
    assert body["email_recipients"] == 1 and body["log"] == []
    for secret in (PASSWORD, "SECRETTOKEN", "abc123", "usuario-smtp", "smtp.example.test", "dueno@example.test"):
        assert secret not in res.text


@pytest.mark.asyncio
async def test_status_reports_disabled_by_default(client_factory):
    async with client_factory() as c:
        body = (await c.get("/api/notifications/status")).json()
    assert body["enabled"] is False and not any(body["channels"].values())


@pytest.mark.asyncio
async def test_preview_does_not_send_and_validates_period(client_factory, maker, monkeypatch):
    _all_channels(monkeypatch)
    monkeypatch.setattr(notify.smtplib, "SMTP", lambda *a, **k: pytest.fail("la vista previa no debe enviar"))
    async with maker() as s:
        await _seed(s)
    async with client_factory() as c:
        ok = await c.get("/api/digest/preview", params={"period": "weekly"})
        bad = await c.get("/api/digest/preview", params={"period": "monthly"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["digest"]["period"] == "weekly" and "Resumen semanal" in body["text"] and "<div" in body["html"]
    assert bad.status_code == 422
