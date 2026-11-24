"""Núcleo del Centro de acciones: registro de proveedores, aislamiento de fallos, estados y API."""

from datetime import date, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import action_center as ac
from app.db import create_dev_schema, get_session, make_engine
from app.main import app


def _item(kind: str, entity: str, impact: float, **kw) -> ac.ActionItem:
    return ac.ActionItem(
        key=f"{kind}:{entity}",
        kind=kind,
        title=f"Acción {entity}",
        detail="detalle",
        impact_usd=impact,
        impact_basis="prueba",
        effort="low",
        target="dashboard",
        entity=entity,
        **kw,
    )


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Cada prueba usa un registro propio, para no depender de los proveedores reales."""
    monkeypatch.setattr(ac, "_REGISTRY", {})


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _register():
    @ac.action_provider("a", "Área A")
    async def a(_s):
        return [_item("a", "x", 100), _item("a", "y", 300)]

    @ac.action_provider("b", "Área B")
    async def b(_s):
        return [_item("b", "z", 200)]


def test_duplicate_kind_is_rejected():
    _register()
    with pytest.raises(ValueError):

        @ac.action_provider("a", "Otra")
        async def again(_s):
            return []


@pytest.mark.asyncio
async def test_orders_by_impact_and_summarises(maker):
    _register()
    async with maker() as s:
        out = await ac.list_actions(s)
    assert [i["key"] for i in out["items"]] == ["a:y", "b:z", "a:x"]
    assert out["summary"]["open"] == 3 and out["summary"]["open_impact_usd"] == 600
    assert out["by_kind"][0] == {"kind": "a", "label": "Área A", "count": 2, "impact_usd": 400}
    assert out["errors"] == []


@pytest.mark.asyncio
async def test_failing_provider_does_not_break_the_rest(maker):
    _register()

    @ac.action_provider("boom", "Rota")
    async def boom(_s):
        raise RuntimeError("falló")

    async with maker() as s:
        out = await ac.list_actions(s)
    assert out["summary"]["open"] == 3
    assert out["errors"] == [{"kind": "boom", "label": "Rota", "error": "falló"}]


@pytest.mark.asyncio
async def test_invalid_items_are_dropped(maker):
    @ac.action_provider("a", "Área A")
    async def a(_s):
        bad_prefix = ac.ActionItem("otro:1", "a", "t", "d", 10, "b", "low", "dashboard")
        nan = ac.ActionItem("a:nan", "a", "t", "d", float("nan"), "b", "low", "dashboard")
        return [bad_prefix, nan, _item("a", "ok", 5)]

    async with maker() as s:
        out = await ac.list_actions(s)
    assert [i["key"] for i in out["items"]] == ["a:ok"]


@pytest.mark.asyncio
async def test_status_lifecycle(maker):
    _register()
    today = date(2026, 11, 24)
    async with maker() as s:
        await ac.set_status(s, "a:x", "done", note="  listo  ", user_email="u@x.com", today=today)
        await ac.set_status(s, "a:y", "snoozed", snooze_days=3, today=today)
        await ac.set_status(s, "b:z", "dismissed", today=today)
        open_now = await ac.list_actions(s, today=today)
        assert open_now["items"] == [] and open_now["summary"]["snoozed"] == 1

        everything = {i["key"]: i for i in (await ac.list_actions(s, status="all", today=today))["items"]}
        assert everything["a:x"]["note"] == "listo" and everything["a:x"]["updated_by"] == "u@x.com"
        assert everything["a:y"]["snoozed_until"] == today + timedelta(days=3)

        later = await ac.list_actions(s, today=today + timedelta(days=3))  # la pospuesta vuelve sola
        assert [i["key"] for i in later["items"]] == ["a:y"]

        await ac.set_status(s, "a:x", "open", today=today)  # reabrir borra el estado
        reopened = await ac.list_actions(s, today=today)
        assert [i["key"] for i in reopened["items"]] == ["a:x"]


@pytest.mark.asyncio
async def test_done_this_week_counts_recovered_impact(maker):
    _register()
    async with maker() as s:
        await ac.set_status(s, "a:y", "done")
        out = await ac.list_actions(s)
    assert out["summary"]["done_this_week"] == 1 and out["summary"]["done_this_week_impact_usd"] == 300


@pytest.mark.asyncio
async def test_invalid_requests(maker):
    async with maker() as s:
        for kwargs in (
            {"key": "sin-dos-puntos", "status": "done"},
            {"key": "a:x", "status": "inventado"},
            {"key": "a:x", "status": "snoozed", "snooze_days": 0},
        ):
            with pytest.raises(ac.ActionError):
                await ac.set_status(s, **kwargs)


@pytest.mark.asyncio
async def test_api_roundtrip(maker):
    _register()

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/actions", params={"kind": "a"})
            assert res.status_code == 200 and [i["key"] for i in res.json()["items"]] == ["a:y", "a:x"]
            put = await client.put("/api/actions/a:y", json={"status": "done", "note": "ok"})
            assert put.status_code == 200 and put.json()["status"] == "done"
            assert (await client.put("/api/actions/a:y", json={"status": "nope"})).status_code == 422
            after = (await client.get("/api/actions", params={"kind": "a"})).json()
            assert [i["key"] for i in after["items"]] == ["a:x"]
    finally:
        app.dependency_overrides.clear()
