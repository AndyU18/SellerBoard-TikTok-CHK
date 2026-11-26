"""Servicio al cliente de TikTok Shop: constructores puros, privacidad, sincronización incremental, API y acciones.

Todo con datos sintéticos y SQLite en memoria; ninguna prueba toca la red (el cliente es un doble). Los textos de
prueba llevan marcadores ("SECRETO-123"...) que NUNCA deben aparecer en una fila, tabla, log ni respuesta de API.
"""

from __future__ import annotations

import json
import logging
import typing
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import action_center, action_providers, sync_jobs
from app import tiktok_service_analytics as analytics
from app import tiktok_service_ops as ops
from app.connectors import tiktok_service as svc
from app.connectors.tiktok_shop import TikTokShopError
from app.db import create_dev_schema, get_session, make_engine
from app.models import TikTokCsConversation, TikTokCsDaily, TikTokCsState
from app.tiktok_service_api import CategoryFilter, router

NOW = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)
TZ = ZoneInfo("America/New_York")
SECRETS = (
    "SECRETO-123",
    "NOMBRE-SECRETO",
    "IMID-SECRETO",
    "BUYERID-SECRETO",
    "SECRETO-AVATAR",
    "SECRETO-TEL-555",
    "SECRETO-CALLE",
)
ALLOWED_TEXT_FIELDS = {"conversation_id", "last_sender_role", "last_message_type", "category", "status"}


def _epoch(minutes_ago: float) -> int:
    return int((NOW - timedelta(minutes=minutes_ago)).timestamp())


def _msg(index: int, role: str, kind: str, minutes_ago: float, text: str = "", visible: bool = True) -> dict[str, Any]:
    return {
        "id": f"m{index}",
        "index": str(index),
        "create_time": _epoch(minutes_ago),
        "type": kind,
        "content": text,
        "is_visible": visible,
        "sender": {"role": role, "nickname": "NOMBRE-SECRETO", "im_user_id": "IMID-SECRETO", "avatar": "https://x/SECRETO-AVATAR"},
    }


def _listed_raw(cid: str, messages: list[dict[str, Any]], unread: int = 0) -> dict[str, Any]:
    """Elemento del listado de la API, con participantes y contenido sensibles que jamás deben guardarse."""
    ordered = sorted(messages, key=lambda m: int(m["create_time"]))
    return {
        "id": cid,
        "create_time": ordered[0]["create_time"],
        "unread_count": unread,
        "can_send_message": True,
        "participant_count": 2,
        "participants": [
            {"role": "BUYER", "nickname": "NOMBRE-SECRETO", "user_id": "BUYERID-SECRETO", "im_user_id": "IMID-SECRETO", "avatar": "https://x/SECRETO-AVATAR"},
            {"role": "SHOP", "nickname": "Tienda", "user_id": "shop-1", "im_user_id": "IM-SHOP", "avatar": ""},
        ],
        "latest_message": ordered[-1],
    }


class FakeClient:
    """TikTokShopClient falso: pagina como la API real (20 conversaciones / 10 mensajes por página) y registra las llamadas."""

    def __init__(self, conversations: dict[str, list[dict[str, Any]]]) -> None:
        self.conversations = conversations
        self.unread: dict[str, int] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None, **_: Any) -> dict:
        params = params or {}
        self.calls.append((method, path, params))
        if path == svc.CONVERSATIONS_PATH:
            assert params["page_size"] <= svc.CONVERSATION_PAGE_SIZE
            items = [_listed_raw(cid, msgs, self.unread.get(cid, 0)) for cid, msgs in self.conversations.items()]
            return self._page(items, "conversations", params, svc.CONVERSATION_PAGE_SIZE)
        if path.endswith("/messages"):
            assert params["page_size"] <= svc.MESSAGE_PAGE_SIZE
            cid = path.split("/")[-2]
            newest_first = sorted(self.conversations[cid], key=lambda m: -int(m["create_time"]))
            return self._page(newest_first, "messages", params, svc.MESSAGE_PAGE_SIZE)
        if path == svc.AGENT_SETTINGS_PATH:
            return {"can_accept_chat": True}
        if path == svc.PERFORMANCE_PATH:
            return {"performance": {"support_session_count": 4, "response_percentage": "50", "response_time_mins": "10", "satisfaction_percentage": "100", "conversion_rate": "25", "cs_guided_gmv": "80", "currency": "USD"}}
        raise AssertionError(f"ruta inesperada {path}")

    @staticmethod
    def _page(items: list, key: str, params: dict, size: int) -> dict:
        start = int(params.get("page_token") or 0)
        chunk = items[start : start + size]
        more = start + size < len(items)
        return {key: chunk, "next_page_token": str(start + size) if more else ""}

    def messages_calls(self) -> list[str]:
        return [path for _, path, _ in self.calls if path.endswith("/messages")]


def _scenario() -> dict[str, list[dict[str, Any]]]:
    """Cuatro conversaciones: sin responder, respondida, solo bot y una vieja."""
    return {
        "c-waiting": [
            _msg(1, "ROBOT", "TEXT", 300, "hola SECRETO-TEL-555"),
            _msg(2, "BUYER", "TEXT", 120, "SECRETO-123 ¿dónde está mi paquete? SECRETO-CALLE 12"),
        ],
        "c-answered": [
            _msg(1, "BUYER", "TEXT", 600, "quiero un reembolso SECRETO-123"),
            _msg(2, "ROBOT", "TEXT", 599, "respuesta automática"),
            _msg(3, "SHOP", "TEXT", 540, "te ayudamos"),
        ],
        "c-bot": [_msg(1, "ROBOT", "ORDER_CARD", 200), _msg(2, "SYSTEM", "OTHER", 190)],
        "c-old": [_msg(1, "BUYER", "TEXT", 60 * 24 * 30, "talla SECRETO-123"), _msg(2, "SHOP", "TEXT", 60 * 24 * 30 - 5, "ok")],
    }


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Las pausas entre llamadas no deben frenar las pruebas."""
    monkeypatch.setattr(svc.time, "sleep", lambda _s: None)


async def _sync(maker, client: FakeClient, now: datetime = NOW) -> tuple[dict[str, int], list[str]]:
    async with maker() as session:
        return await ops.sync_service(session, client, now)  # type: ignore[arg-type]


async def _dump(maker) -> str:
    """Todo lo guardado en las tres tablas, como un solo texto, para buscar marcadores."""
    parts: list[str] = []
    async with maker() as session:
        for model in (TikTokCsConversation, TikTokCsDaily, TikTokCsState):
            for row in await session.scalars(select(model)):
                parts.append(json.dumps({c.name: getattr(row, c.name) for c in model.__table__.columns}, default=str))
    return "\n".join(parts)


def _assert_clean(text: str) -> None:
    leaked = [s for s in SECRETS if s in text]
    assert not leaked, f"dato sensible filtrado: {leaked}"


# --- categorías y roles ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Dónde está mi paquete? Quiero el seguimiento", "shipping_tracking"),
        ("Where is my package, tracking says nothing", "shipping_tracking"),
        ("Quiero una devolución y el reembolso", "return_refund"),
        ("I want a refund, please cancel", "return_refund"),
        ("¿Qué talla es? ¿Está disponible en otro color?", "product_question"),
        ("Does it work with my phone? what size", "product_question"),
        ("Quiero saber el estado de mi pedido", "order_status"),
        ("Esto es una estafa, pésimo servicio", "complaint"),
        ("terrible, unacceptable", "complaint"),
        ("hola buenas", "other"),
        ("", "other"),
    ],
)
def test_classify_text_by_keywords(text, expected):
    assert svc.classify_text(text) == expected


def test_classify_tie_goes_to_the_most_urgent_category():
    assert svc.classify_text("refund tracking") == "return_refund"  # empate 1-1: devolución va antes que envío


def test_categories_are_all_known_and_the_api_filter_matches():
    assert set(typing.get_args(CategoryFilter)) == set(svc.CATEGORIES)
    assert svc.CATEGORY_OTHER in svc.CATEGORIES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("BUYER", "buyer"), ("SHOP", "seller"), ("CUSTOMER_SERVICE", "seller"), ("ROBOT", "bot"), ("SYSTEM", "system"), ("ALGO_NUEVO", "system"), (None, "system")],
)
def test_normalize_role(raw, expected):
    assert svc.normalize_role(raw) == expected


def test_unknown_message_type_is_bounded_to_other():
    assert svc.normalize_type("TEXT") == "TEXT"
    assert svc.normalize_type("texto libre SECRETO-123") == "OTHER"


# --- constructores puros: privacidad -----------------------------------------------------------------


def _build(messages: list[dict[str, Any]], *, now: datetime = NOW) -> dict[str, Any]:
    listed = svc.reduce_listed(_listed_raw("conv-1", messages))
    assert listed is not None
    return svc.build_conversation_row(listed, messages, now, TZ)


def test_reduce_listed_drops_participants_and_content():
    listed = svc.reduce_listed(_listed_raw("conv-1", [_msg(1, "BUYER", "TEXT", 10, "SECRETO-123")]))
    assert listed is not None
    _assert_clean(repr(listed))
    assert {f for f in listed.__slots__} == {"conversation_id", "created_at", "last_message_at", "last_sender_role", "last_message_type", "unread_count", "can_send_message"}


def test_reduce_listed_requires_id_and_creation_time():
    assert svc.reduce_listed({"create_time": 5}) is None
    assert svc.reduce_listed({"id": "x"}) is None


def test_row_never_contains_text_names_or_buyer_ids():
    row = _build(_scenario()["c-waiting"] + [_msg(3, "BUYER", "TEXT", 100, "SECRETO-TEL-555 SECRETO-CALLE")])
    _assert_clean(json.dumps(row, default=str))
    _assert_clean(repr(row))
    free_text = {k for k, v in row.items() if isinstance(v, str)}
    assert free_text <= ALLOWED_TEXT_FIELDS  # ningún campo de texto libre: solo ids opacos y enumerados
    assert row["category"] in svc.CATEGORIES


def test_category_is_derived_from_buyer_text_only():
    """Las frases del bot ("tracking", "refund") no deben decidir la categoría: solo el texto del comprador."""
    row = _build([_msg(1, "ROBOT", "TEXT", 50, "tracking refund delivery"), _msg(2, "BUYER", "TEXT", 40, "what size is it?")])
    assert row["category"] == "product_question"
    assert _build([_msg(1, "ROBOT", "TEXT", 50, "tracking refund"), _msg(2, "BUYER", "IMAGE", 40, "SECRETO-123")])["category"] == "other"


# --- primera respuesta y estados ---------------------------------------------------------------------


def test_first_response_is_the_first_human_reply_not_the_bot():
    row = _build([_msg(1, "BUYER", "TEXT", 100, "hola"), _msg(2, "ROBOT", "TEXT", 99), _msg(3, "SHOP", "TEXT", 90)])
    assert row["first_response_seconds"] == 10 * 60
    assert (row["status"], row["waiting_since"]) == ("waiting_buyer", None)
    assert (row["buyer_messages"], row["seller_messages"], row["bot_messages"]) == (1, 1, 1)


def test_buyer_after_seller_is_waiting_seller_since_the_oldest_unanswered_message():
    row = _build([_msg(1, "BUYER", "TEXT", 300, "a"), _msg(2, "SHOP", "TEXT", 290), _msg(3, "BUYER", "TEXT", 200, "b"), _msg(4, "BUYER", "TEXT", 100, "c")])
    assert row["status"] == "waiting_seller"
    assert row["waiting_since"] == NOW - timedelta(minutes=200)
    assert row["first_response_seconds"] == 10 * 60


def test_never_answered_buyer_has_no_first_response_but_waits():
    row = _build([_msg(1, "BUYER", "TEXT", 50, "hola")])
    assert row["first_response_seconds"] is None and row["status"] == "waiting_seller"


def test_bot_only_conversation_is_closed():
    row = _build([_msg(1, "ROBOT", "ORDER_CARD", 50), _msg(2, "SYSTEM", "OTHER", 40)])
    assert row["status"] == "closed" and row["buyer_messages"] == 0 and row["category"] == "other"


def test_status_closes_after_the_inactivity_window():
    recent = svc.derive_status(None, NOW - timedelta(days=1), NOW - timedelta(days=1), NOW)
    stale = svc.derive_status(None, NOW - timedelta(days=svc.CLOSE_AFTER_DAYS + 1), NOW - timedelta(days=svc.CLOSE_AFTER_DAYS + 1), NOW)
    assert (recent, stale) == ("waiting_buyer", "closed")


def test_hidden_messages_do_not_count_and_days_use_the_report_timezone():
    late = int(datetime(2026, 11, 25, 2, 30, tzinfo=UTC).timestamp())  # 21:30 del día 24 en Nueva York
    hidden = _msg(2, "BUYER", "TEXT", 5, "oculto")
    hidden["is_visible"] = False
    buyer = _msg(1, "BUYER", "TEXT", 5, "hola")
    buyer["create_time"] = late
    row = _build([buyer, hidden])
    assert row["buyer_messages"] == 1 and row["day_counts"] == {"2026-11-24": {"b": 1, "s": 0}}


def test_truncated_history_is_flagged_incomplete():
    listed = svc.reduce_listed(_listed_raw("conv-1", [_msg(1, "BUYER", "TEXT", 10, "x")]))
    assert listed is not None
    assert svc.build_conversation_row(listed, [_msg(1, "BUYER", "TEXT", 10, "x")], NOW, TZ, complete=False)["messages_complete"] is False


def test_build_performance_row_converts_units_and_defaults_empty_days():
    row = svc.build_performance_row(date(2026, 11, 20), {"support_session_count": 3, "response_time_mins": "70.5", "response_percentage": "66.67", "currency": "USD"})
    assert row["official_sessions"] == 3 and row["official_response_seconds"] == 4230 and row["official_response_percentage"] == 66.67
    assert svc.build_performance_row(date(2026, 11, 20), {})["official_sessions"] == 0


# --- llamadas: reintentos, pausas, solo GET y logs seguros -------------------------------------------


class Flaky:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.calls = 0

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None, **_: Any) -> dict:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return {"ok": True}


def test_transient_errors_are_retried_with_backoff():
    client, waits = Flaky([httpx.ConnectError("x"), httpx.ReadTimeout("y")]), []
    assert svc.call_with_retries(client, "/p", {}, sleep=waits.append) == {"ok": True}  # type: ignore[arg-type]
    assert client.calls == 3 and waits[:2] == [svc.BACKOFF_SECONDS, svc.BACKOFF_SECONDS * 2] and waits[-1] == svc.PAUSE_SECONDS


def test_validation_errors_are_not_retried_and_logs_hide_the_message(caplog):
    client = Flaky([TikTokShopError(36009004, "SECRETO-123 detalle")])
    with caplog.at_level(logging.DEBUG), pytest.raises(TikTokShopError):
        svc.call_with_retries(client, "/p", {}, sleep=lambda _s: None)  # type: ignore[arg-type]
    assert client.calls == 1
    _assert_clean(caplog.text)
    assert "36009004" in caplog.text


def test_exhausted_retries_raise():
    client = Flaky([httpx.ConnectError("x")] * svc.MAX_ATTEMPTS)
    with pytest.raises(httpx.ConnectError):
        svc.call_with_retries(client, "/p", {}, sleep=lambda _s: None)  # type: ignore[arg-type]
    assert client.calls == svc.MAX_ATTEMPTS


def test_failed_conversation_problem_has_no_message_text():
    client = Flaky([TikTokShopError(1, "SECRETO-123")])
    listed = svc.reduce_listed(_listed_raw("c", [_msg(1, "BUYER", "TEXT", 5, "hola")]))
    assert listed is not None
    rows, problems = svc.fetch_rows(client, [listed], NOW, TZ, sleep=lambda _s: None)  # type: ignore[arg-type]
    assert rows == [] and problems == ["mensajes: TikTokShopError (1)"]
    _assert_clean(" ".join(problems))


def test_message_history_longer_than_the_cap_is_marked_incomplete():
    messages = [_msg(i, "BUYER", "TEXT", 1000 - i, "x") for i in range(svc.MAX_MESSAGE_PAGES * svc.MESSAGE_PAGE_SIZE + 5)]
    client = FakeClient({"big": messages})
    listed = svc.reduce_listed(_listed_raw("big", messages))
    assert listed is not None
    row = svc.fetch_conversation_row(client, listed, NOW, TZ, sleep=lambda _s: None)  # type: ignore[arg-type]
    assert row["messages_complete"] is False and row["message_count"] == svc.MAX_MESSAGE_PAGES * svc.MESSAGE_PAGE_SIZE
    assert len(client.messages_calls()) == svc.MAX_MESSAGE_PAGES


def test_select_pending_is_incremental_and_capped():
    c = {cid: svc.reduce_listed(_listed_raw(cid, msgs)) for cid, msgs in _scenario().items()}
    listed = [i for i in c.values() if i is not None]
    known: dict[str, tuple[datetime | None, str]] = {
        "c-answered": (c["c-answered"].last_message_at, "closed"),  # type: ignore[union-attr]
        "c-waiting": (c["c-waiting"].last_message_at, "waiting_seller"),  # type: ignore[union-attr]
        "c-bot": (NOW - timedelta(days=1), "closed"),  # su último mensaje cambió
    }
    ids = [i.conversation_id for i in svc.select_pending(listed, known, 10)]
    assert ids == ["c-bot", "c-old", "c-waiting"]  # cambiada y nueva (recientes primero) y luego la abierta; c-answered no
    assert len(svc.select_pending(listed, known, 2)) == 2


# --- persistencia y sincronización -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_conversations_is_idempotent(maker):
    listed = svc.reduce_listed(_listed_raw("c1", [_msg(1, "BUYER", "TEXT", 10, "hola")]))
    assert listed is not None
    row = svc.build_conversation_row(listed, [_msg(1, "BUYER", "TEXT", 10, "hola")], NOW, TZ)
    async with maker() as s:
        await ops.upsert_conversations(s, [row], NOW)
        await ops.upsert_conversations(s, [{**row, "unread_count": 4}], NOW)
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(TikTokCsConversation)) == 1
        assert (await s.get(TikTokCsConversation, "c1")).unread_count == 4  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_sync_stores_only_figures_and_logs_nothing_sensitive(maker, caplog):
    client = FakeClient(_scenario())
    with caplog.at_level(logging.DEBUG):
        stats, problems = await _sync(maker, client)
    assert problems == [] and stats["conversations_listed"] == 4 and stats["conversations_with_messages"] == 4
    assert {m for m, _, _ in client.calls} == {"GET"}  # solo lectura
    _assert_clean(await _dump(maker))
    _assert_clean(caplog.text)
    async with maker() as s:
        by_id = {c.conversation_id: c for c in await s.scalars(select(TikTokCsConversation))}
        assert by_id["c-waiting"].status == "waiting_seller" and by_id["c-waiting"].category == "shipping_tracking"
        assert by_id["c-answered"].first_response_seconds == 60 * 60 and by_id["c-answered"].category == "return_refund"
        assert by_id["c-bot"].status == "closed" and by_id["c-old"].status == "closed"
        assert (await s.get(TikTokCsState, "agents")).value == {"can_accept_chat": True}  # type: ignore[union-attr]
        assert (await s.get(TikTokCsState, "sync")).value["initial_load_complete"] is True  # type: ignore[union-attr]
        assert await s.scalar(select(func.count()).select_from(TikTokCsDaily).where(TikTokCsDaily.official_sessions.is_not(None))) > 0


@pytest.mark.asyncio
async def test_initial_load_continues_across_runs_with_a_cap(maker, monkeypatch):
    monkeypatch.setattr(ops, "MAX_CONVERSATIONS_WITH_MESSAGES", 2)
    monkeypatch.setattr(ops, "BATCH_SIZE", 1)
    client = FakeClient(_scenario())
    first, _ = await _sync(maker, client)
    assert (first["conversations_with_messages"], first["pending_remaining"]) == (2, 2)
    second, _ = await _sync(maker, client)
    assert second["pending_remaining"] == 0
    async with maker() as s:
        assert await s.scalar(select(func.count()).select_from(TikTokCsConversation)) == 4
        assert (await s.get(TikTokCsState, "sync")).value["pending_remaining"] == 0  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_incremental_run_only_refetches_open_or_changed_conversations(maker):
    client = FakeClient(_scenario())
    await _sync(maker, client)
    client.calls.clear()
    stats, _ = await _sync(maker, client)
    refetched = {p.split("/")[-2] for p in client.messages_calls()}
    # c-answered sigue "esperando al comprador" (abierta) y c-waiting espera al vendedor; las cerradas no se piden.
    assert refetched == {"c-waiting", "c-answered"} and stats["conversations_with_messages"] == 2
    async with maker() as s:
        assert await s.scalar(select(func.count()).select_from(TikTokCsConversation)) == 4  # idempotente


@pytest.mark.asyncio
async def test_new_message_triggers_a_refetch_and_unread_comes_from_the_listing(maker):
    client = FakeClient(_scenario())
    await _sync(maker, client)
    client.conversations["c-bot"].append(_msg(3, "BUYER", "TEXT", 5, "SECRETO-123 reembolso"))
    client.unread["c-old"] = 7
    client.calls.clear()
    await _sync(maker, client)
    assert "c-bot" in {p.split("/")[-2] for p in client.messages_calls()}
    async with maker() as s:
        assert (await s.get(TikTokCsConversation, "c-bot")).status == "waiting_seller"  # type: ignore[union-attr]
        assert (await s.get(TikTokCsConversation, "c-old")).unread_count == 7  # type: ignore[union-attr]
    _assert_clean(await _dump(maker))


@pytest.mark.asyncio
async def test_daily_history_counts_messages_and_response_times(maker):
    await _sync(maker, FakeClient(_scenario()))
    async with maker() as s:
        today = await s.get(TikTokCsDaily, NOW.astimezone(TZ).date())
        assert today is not None and today.new_conversations == 3 and today.pending_at_close == 1
        assert today.buyer_messages == 2 and today.seller_messages == 1
        assert today.avg_response_seconds == 3600 and today.median_response_seconds == 3600 and today.responded_conversations == 1


@pytest.mark.asyncio
async def test_old_conversations_are_purged_after_the_retention_window(maker):
    await _sync(maker, FakeClient(_scenario()))
    later = NOW + timedelta(days=ops.HISTORY_DAYS + 5)
    async with maker() as s:
        assert await ops.purge_old(s, later - timedelta(days=ops.HISTORY_DAYS)) == 4


def test_performance_days_cover_recent_and_missing_days_up_to_the_cap():
    today = date(2026, 11, 25)
    assert ops.performance_days_to_fetch(set(), today)[:3] == [today, today - timedelta(days=1), today - timedelta(days=2)]
    assert len(ops.performance_days_to_fetch(set(), today)) == ops.MAX_PERFORMANCE_CALLS
    everything = {today - timedelta(days=n) for n in range(ops.HISTORY_DAYS)}
    assert len(ops.performance_days_to_fetch(everything, today)) == ops.PERFORMANCE_RECENT_DAYS


def test_summarize_problems_groups_repeats():
    assert ops.summarize_problems(["a", "a", "b"]) == ["a (x2)", "b"]


def test_source_is_registered_in_sync_jobs():
    assert "tiktok_service" in sync_jobs.SOURCES and sync_jobs.LABELS["tiktok_service"] == "TikTok Shop (servicio al cliente)"
    assert "tiktok_service" in sync_jobs.RUNNERS and "tiktok_service" in sync_jobs.SETTINGS


# --- API ---------------------------------------------------------------------------------------------


def _client(maker) -> httpx.AsyncClient:
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()  # el router se prueba montado aparte
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


ENDPOINTS = ("overview", "conversations", "response-times", "categories", "backlog", "agents")


@pytest.mark.asyncio
async def test_empty_database_endpoints_do_not_fail(maker):
    async with _client(maker) as c:
        for path in ENDPOINTS:
            assert (await c.get(f"/api/tiktok-service/{path}")).status_code == 200, path
        assert (await c.get("/api/tiktok-service/overview")).json()["available"] is False


@pytest_asyncio.fixture
async def synced(maker):
    await _sync(maker, FakeClient(_scenario()))
    return maker


@pytest.mark.asyncio
async def test_endpoints_return_figures_and_never_sensitive_data(synced):
    async with _client(synced) as c:
        bodies = {path: await c.get(f"/api/tiktok-service/{path}") for path in ENDPOINTS}
    for path, response in bodies.items():
        assert response.status_code == 200, path
        _assert_clean(response.text)
    overview = bodies["overview"].json()
    assert overview["waiting_seller"] == 1 and overview["conversations_in_window"] >= 3
    backlog = bodies["backlog"].json()
    assert backlog["total"] == 1 and [b["bucket"] for b in backlog["buckets"]] == [n for n, _ in analytics.WAIT_BUCKETS]
    assert backlog["oldest_wait_hours"] is not None
    assert bodies["agents"].json()["status"] == "accepting_chats"
    assert bodies["agents"].json()["official_last_7_days"]["sessions"] > 0


@pytest.mark.asyncio
async def test_conversations_endpoint_filters_and_exposes_only_metadata(synced):
    async with _client(synced) as c:
        waiting = (await c.get("/api/tiktok-service/conversations", params={"status": "waiting_seller"})).json()
        none = (await c.get("/api/tiktok-service/conversations", params={"min_wait_hours": 2000})).json()
        shipping = (await c.get("/api/tiktok-service/conversations", params={"category": "shipping_tracking", "limit": 1})).json()
    assert waiting["total"] == 1 and waiting["items"][0]["conversation_id"] == "c-waiting"
    assert none["total"] == 0
    assert shipping["total"] == 1 and len(shipping["items"]) == 1
    expected = {"conversation_id", "created_at", "last_message_at", "status", "category", "wait_hours", "unread_count", "message_count",
                "buyer_messages", "seller_messages", "bot_messages", "first_response_seconds", "last_sender_role", "can_send_message"}
    assert set(waiting["items"][0]) == expected


@pytest.mark.asyncio
async def test_response_times_categories_and_ranges(synced):
    async with _client(synced) as c:
        times = (await c.get("/api/tiktok-service/response-times")).json()
        cats = (await c.get("/api/tiktok-service/categories")).json()
    assert times["timezone"] and len(times["by_hour"]) == 24 and len(times["by_weekday"]) == 7
    assert sum(b["count"] for b in times["distribution"]) == times["summary"]["conversations_with_buyer_message"]
    by_cat = {r["category"]: r for r in cats["categories"]}
    assert by_cat["return_refund"]["median_response_seconds"] == 3600 and by_cat["shipping_tracking"]["unresolved"] == 1


@pytest.mark.asyncio
async def test_parameters_are_validated(maker):
    async with _client(maker) as c:
        assert (await c.get("/api/tiktok-service/overview", params={"days": 3})).status_code == 422
        assert (await c.get("/api/tiktok-service/overview", params={"days": 365})).status_code == 422
        assert (await c.get("/api/tiktok-service/conversations", params={"status": "inventado"})).status_code == 422
        assert (await c.get("/api/tiktok-service/conversations", params={"category": "inventada"})).status_code == 422
        assert (await c.get("/api/tiktok-service/conversations", params={"limit": 0})).status_code == 422


def test_router_exposes_no_write_methods():
    methods = {m for route in router.routes for m in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}


# --- reglas puras del análisis -----------------------------------------------------------------------


def test_percentile_and_buckets():
    assert analytics.percentile([], 90) is None
    assert analytics.percentile([10, 20, 30, 40, 50], 50) == 30
    assert analytics.percentile([0, 100], 90) == 90
    assert [analytics.bucket_seconds(s) for s in (60, 600, 1800, 7200, 40000, 100000)] == ["<5 min", "5-15 min", "15-60 min", "1-6 h", "6-24 h", ">24 h"]
    assert [analytics.wait_bucket(h) for h in (0.5, 3, 10, 48, 100)] == ["<1 h", "1-6 h", "6-24 h", "1-3 d", ">3 d"]


def test_response_summary_percentages_use_asked_conversations():
    summary = analytics.response_summary([600, 7200, 100000], asked=4)
    assert summary["within_1h_pct"] == 25.0 and summary["within_24h_pct"] == 50.0 and summary["response_rate_pct"] == 75.0
    assert analytics.response_summary([], 0)["median_seconds"] is None


# --- Centro de acciones ------------------------------------------------------------------------------


def test_provider_is_registered_and_loads():
    assert "app.tiktok_service_actions" in action_providers.PROVIDER_MODULES
    assert "app.tiktok_service_actions" in action_providers.load_providers()
    assert "tiktok_cs_unanswered" in action_center.providers()


@pytest.mark.asyncio
async def test_provider_groups_unanswered_conversations_by_age(synced, monkeypatch):
    from app import tiktok_service_actions as actions

    class _FrozenDatetime(datetime):
        """El proveedor usa el reloj real; se congela en el mismo "ahora" con el que se armaron los datos."""

        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(actions, "datetime", _FrozenDatetime)
    async with synced() as s:
        items = await actions.unanswered_conversation_actions(s)
    assert len(items) == 1
    item = items[0]
    assert item.kind == actions.KIND and item.key.startswith(f"{actions.KIND}:") and item.target == "tiktok"
    assert item.impact_usd >= actions.MIN_IMPACT_USD and "supuesto" in item.impact_basis.lower()
    assert "1 en <1 h" in item.detail or "1 en 1-6 h" in item.detail
    assert item.meta["waiting"] == 1 and item.meta["by_category"] == {"shipping_tracking": 1}
    _assert_clean(json.dumps([item.title, item.detail, item.impact_basis, item.meta]))


@pytest.mark.asyncio
async def test_provider_is_silent_when_nothing_is_waiting(maker):
    from app import tiktok_service_actions as actions

    async with maker() as s:
        assert await actions.unanswered_conversation_actions(s) == []


def test_estimated_impact_weights_product_questions_highest():
    from app import tiktok_service_actions as actions

    assert actions.estimated_impact({"product_question": 10}, 20.0) == pytest.approx(10 * actions.PRODUCT_QUESTION_CONVERSION * 20.0)
    assert actions.estimated_impact({"other": 10}, 20.0) < actions.estimated_impact({"product_question": 10}, 20.0)
    assert actions.estimated_impact({}, 20.0) == 0.0
