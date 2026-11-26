"""Mercado de creadores y colaboraciones del vendedor: constructores puros, cliente de solo lectura, upserts,
puntaje y anti-recomendación, sincronización, API y Centro de acciones.

Todo con datos sintéticos, un cliente falso y SQLite en memoria: ninguna prueba toca la red ni los tokens.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import action_center, sync_jobs
from app import tiktok_creator_market_actions as actions
from app import tiktok_creator_market_analytics as analytics
from app import tiktok_creator_market_ops as ops
from app.connectors import tiktok_creator_market as cm
from app.connectors.tiktok_shop import TikTokShopError
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    SyncRun,
    TikTokMarketCreator,
    TikTokMarketCreatorSnapshot,
    TikTokOpenCollaboration,
    TikTokTargetCollaboration,
)
from app.tiktok_creator_market_api import router

NOW = datetime.now(UTC)
TODAY = NOW.date()
HOME = "600001"
KITCHEN = "600024"
PETS = "602118"


def _creator(open_id: str, username: str, *, followers: int = 20_000, gmv: str | None = "5000", cats: tuple[str, ...] = (HOME,),
             nickname: str | None = None) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "creator_open_id": open_id, "username": username, "nickname": nickname or username, "avatar": {"url": f"https://img/{open_id}"},
        "selection_region": "US", "follower_count": followers, "creator_level": 3, "category_ids": list(cats),
        "gmv_range": {"currency": "USD", "formatted_range": "$1K-$5K", "minimum_amount": "1000"},
        "units_sold_range": {"formatted_range": "100+", "minimum_amount": 100},
        "avg_ec_video_view_count": 900, "top_follower_demographics": {"age_ranges": ["AGE_RANGE_25_34"]},
    }
    if gmv is not None:
        raw["gmv"] = {"amount": gmv, "currency": "USD"}
        raw["video_gmv"] = {"amount": gmv, "currency": "USD"}
    return raw


def _open(cid: str, *, rate: int = 1450, status: str = "NORMAL") -> dict[str, Any]:
    return {
        "id": cid, "status": status, "content_creator_count": 7, "showcase_creator_count": 3,
        "current_commission": {"rate": rate, "start_time": 1_793_974_761, "end_time": 7_258_118_400},
        "product": {"id": f"P{cid}", "title": f"Producto {cid}", "inventory": 40, "status": "LIVE", "main_image_url": "https://img/p",
                    "original_price": {"currency": "USD", "minimum_amount": "22.99", "maximum_amount": "22.99"}},
    }


def _target(cid: str) -> dict[str, Any]:
    return {
        "id": cid, "name": f"Campaña {cid}", "type": "STANDARD", "message": "texto libre que no se guarda", "product_count": 2,
        "content_creator_count": 4, "creator_inivited_count": 9, "showcase_creator_count": 1, "start_time": 1_790_000_000,
        "end_time": 1_800_000_000, "create_time": 1_789_000_000, "update_time": 1_791_000_000,
        "free_sample_rule": {"has_free_sample": True, "is_sample_approval_exempt": False, "use_product_sample_setting": True},
    }


# --- cliente falso ----------------------------------------------------------------------------------------


class FakeShop:
    """Imita `TikTokShopClient.request` para las cuatro rutas del mercado y registra cada llamada."""

    def __init__(self, creators: list[dict] | None = None, opens: list[dict] | None = None, targets: list[dict] | None = None,
                 *, fail: dict[str, Exception] | None = None) -> None:
        self.creators, self.opens, self.targets = creators or [], opens or [], targets or []
        self.fail = fail or {}
        self.calls: list[tuple[str, str, dict, Any]] = []

    @staticmethod
    def _slice(items: list[dict], params: dict, key: str) -> dict:
        start = int(params.get("page_token") or 0)
        size = int(params["page_size"])
        chunk = items[start : start + size]
        out: dict[str, Any] = {key: chunk}
        if start + size < len(items):
            out["next_page_token"] = str(start + size)
        return out

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None, *, shop: bool = True) -> dict:
        self.calls.append((method, path, dict(params or {}), body))
        for needle, error in self.fail.items():
            if needle in path:
                raise error
        params = params or {}
        if path == cm.CREATOR_SEARCH_PATH:
            keyword = (body or {}).get("keyword")
            pool = [c for c in self.creators if not keyword or keyword in c["username"]] if keyword else self.creators
            return self._slice(pool, params, "creators")
        if path.startswith(f"{cm.CREATOR_DETAIL_PATH}/"):
            return {"creator": {"ec_video_engagement_rate": "300", "gpm": {"amount": "12.5", "currency": "USD"}, "rating": "4.5",
                                "promoted_product_num": 8, "brand_collaboration_count": 3}}
        if path == cm.OPEN_COLLAB_SEARCH_PATH:
            return {**self._slice(self.opens, params, "open_collaborations"), "total_count": len(self.opens)}
        if path == cm.TARGET_COLLAB_SEARCH_PATH:
            return {**self._slice(self.targets, params, "target_collaborations"), "total_count": len(self.targets)}
        raise AssertionError(f"ruta inesperada: {path}")


def _client(shop: FakeShop, sleeps: list[float] | None = None) -> cm.CreatorMarketClient:
    return cm.CreatorMarketClient(shop, sleep=(sleeps.append if sleeps is not None else lambda _s: None))


# --- constructores puros ----------------------------------------------------------------------------------


def test_build_creator_normalizes_public_fields_and_ranges():
    row = cm.build_creator(_creator("OID1", "stephanie", followers=6861, gmv="53175.58"))
    assert row is not None
    assert row["creator_open_id"] == "OID1" and row["username"] == "stephanie" and row["follower_count"] == 6861
    assert row["gmv_amount"] == Decimal("53175.58") and row["gmv_range_min"] == Decimal(1000) and row["gmv_range"] == "$1K-$5K"
    assert row["units_sold_min"] == 100 and row["category_ids"] == [HOME] and row["region"] == "US" and row["creator_level"] == 3


def test_build_creator_handles_missing_gmv_nested_level_and_impossible_money():
    no_gmv = _creator("OID2", "x", gmv=None)
    assert cm.build_creator(no_gmv)["gmv_amount"] is None  # type: ignore[index]  # sin dato, no cero
    nested = {**_creator("OID3", "y"), "creator_profile": {"creator_level": 9}}
    nested.pop("creator_level")
    assert cm.build_creator(nested)["creator_level"] == 9  # type: ignore[index]  # contrato 202608
    huge = cm.build_creator(_creator("OID4", "z", gmv="9" * 20))
    assert huge is not None and huge["gmv_amount"] is None  # no desborda numeric(14,4)
    assert cm.build_creator({"username": "sin id"}) is None


def test_build_creator_detail_converts_basis_points_and_leaves_missing_as_none():
    detail = cm.build_creator_detail({"ec_video_engagement_rate": "89", "gpm": {"amount": "26.7"}, "rating": "4.1", "promoted_product_num": 0})
    assert detail["engagement_rate"] == Decimal("0.0089") and detail["gpm_amount"] == Decimal("26.7") and detail["rating"] == Decimal("4.1")
    assert detail["promoted_product_num"] == 0 and detail["avg_commission_rate"] is None and detail["units_sold_total"] is None


def test_build_collaborations_normalize_commission_dates_and_drop_message():
    opened = cm.build_open_collaboration(_open("C1", rate=1450))
    assert opened is not None and opened["commission_rate"] == Decimal("0.145") and opened["price_min"] == Decimal("22.99")
    assert opened["product_id"] == "PC1" and opened["creator_count"] == 7 and opened["commission_end"].year == 2200
    target = cm.build_target_collaboration(_target("T1"), "ONGOING")
    assert target is not None and target["invited_count"] == 9 and target["has_free_sample"] is True and target["status"] == "ONGOING"
    assert "message" not in target
    assert cm.build_open_collaboration({"status": "NORMAL"}) is None and cm.build_target_collaboration({}, "ONGOING") is None


def test_build_snapshot_row_keeps_what_changes_weekly():
    creator = cm.build_creator(_creator("OID1", "a", followers=500))
    assert creator is not None
    snap = cm.build_snapshot_row(creator, date(2026, 11, 23))
    assert snap == {"creator_open_id": "OID1", "week_start": date(2026, 11, 23), "follower_count": 500,
                    "gmv_amount": Decimal(5000), "gmv_range_min": Decimal(1000)}


# --- cliente: solo lectura, paginación y reintentos --------------------------------------------------------


def test_assert_read_only_allows_get_and_search_only():
    cm.assert_read_only("GET", "/affiliate_seller/202508/marketplace_creators/abc")
    cm.assert_read_only("POST", cm.CREATOR_SEARCH_PATH)
    for method, path in (("POST", "/affiliate_seller/202412/open_collaborations"), ("POST", "/affiliate_seller/202508/target_collaborations"),
                         ("PUT", "/x/search"), ("DELETE", "/x"), ("POST", "/affiliate_seller/202412/messages/send")):
        with pytest.raises(cm.ReadOnlyViolation):
            cm.assert_read_only(method, path)


def test_client_refuses_a_write_before_any_request():
    shop = FakeShop()
    with pytest.raises(cm.ReadOnlyViolation):
        _client(shop)._call("POST", "/affiliate_seller/202412/open_collaborations", {}, {})
    assert shop.calls == []


def test_pagination_uses_page_size_twenty_follows_tokens_and_reports_more():
    shop = FakeShop([_creator(f"O{i}", f"u{i}") for i in range(45)])
    pages = list(_client(shop).iter_creator_pages())
    assert [len(p.items) for p in pages] == [20, 20, 5] and [p.has_more for p in pages] == [True, True, False]
    assert {c[2]["page_size"] for c in shop.calls} == {cm.CREATOR_PAGE_SIZE == 20 and 20}
    capped = list(_client(FakeShop([_creator(f"O{i}", f"u{i}") for i in range(45)])).iter_creator_pages(max_pages=1))
    assert len(capped) == 1 and capped[0].has_more is True  # cortado por el tope: el llamador sabe que NO leyó todo


def test_search_body_only_has_validated_server_filters():
    assert cm.creator_search_body(None, (), ()) == {}
    body = cm.creator_search_body("  christmas ", ("GMV_RANGE_0_100",), ("UNITS_SOLD_RANGE_10_100",))
    assert body == {"keyword": "christmas", "gmv_ranges": ["GMV_RANGE_0_100"], "units_sold_ranges": ["UNITS_SOLD_RANGE_10_100"]}
    with pytest.raises(ValueError):
        cm.creator_search_body(None, ("GMV_RANGE_1",), ())
    with pytest.raises(ValueError):
        cm.creator_search_body(None, (), ("X",))


def test_invalid_target_status_and_open_id_are_rejected_before_any_request():
    shop = FakeShop()
    client = _client(shop)
    with pytest.raises(ValueError):
        next(client.iter_target_collaboration_pages("ENDED"))
    for bad in ("", "a/b", "a b"):
        with pytest.raises(ValueError):
            client.creator_detail(bad)
    assert shop.calls == []


def test_rate_limit_is_retried_with_growing_waits_and_other_errors_are_not():
    class Flaky(FakeShop):
        def __init__(self) -> None:
            super().__init__([_creator("O1", "u1")])
            self.fails = 2

        def request(self, *args: Any, **kwargs: Any) -> dict:
            if self.fails:
                self.fails -= 1
                raise TikTokShopError(36009002, "Too many requests")
            return super().request(*args, **kwargs)

    sleeps: list[float] = []
    assert len(next(_client(Flaky(), sleeps).iter_creator_pages()).items) == 1
    assert sleeps == [cm.RATE_LIMIT_WAIT_SECONDS, cm.RATE_LIMIT_WAIT_SECONDS * 2]

    boom = FakeShop(fail={"marketplace_creators": TikTokShopError(36009004, "Invalid API version")})
    with pytest.raises(TikTokShopError):
        list(_client(boom).iter_creator_pages())
    assert len(boom.calls) == 1  # un error que no es de tasa no se reintenta

    always = FakeShop(fail={"marketplace_creators": TikTokShopError(36009002, "Too many requests")})
    with pytest.raises(TikTokShopError):
        list(_client(always, []).iter_creator_pages())
    assert len(always.calls) == cm.MAX_ATTEMPTS


def test_network_errors_are_retried_then_reported_without_secrets():
    shop = FakeShop(fail={"marketplace_creators": httpx.ConnectError("boom")})
    with pytest.raises(cm.TikTokCreatorMarketError):
        list(_client(shop, []).iter_creator_pages())
    assert len(shop.calls) == cm.MAX_ATTEMPTS


# --- base de datos -----------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.asyncio
async def test_upserts_are_idempotent_keep_first_seen_and_never_blank_known_values(maker):
    full = cm.build_creator(_creator("A", "alice", gmv="900"))
    partial = cm.build_creator(_creator("A", "alice", gmv=None))  # misma persona en una vista sin GMV
    assert full is not None and partial is not None
    first = NOW - timedelta(days=3)
    async with maker() as s:
        await ops.upsert_creators(s, [full], first)
        await ops.upsert_creators(s, [partial, partial], NOW)
        await s.commit()
        creator = await s.get(TikTokMarketCreator, "A")
        assert await _count(s, TikTokMarketCreator) == 1
        assert creator is not None and creator.gmv_amount == Decimal(900)  # el None no pisa el valor
        assert creator.first_seen_at.replace(tzinfo=UTC) == first and creator.last_seen_at.replace(tzinfo=UTC) == NOW and creator.active

        await ops.upsert_snapshots(s, [cm.build_snapshot_row(full, ops.week_start(TODAY))], TODAY)
        await ops.upsert_snapshots(s, [cm.build_snapshot_row(full, ops.week_start(TODAY))], TODAY)
        assert await _count(s, TikTokMarketCreatorSnapshot) == 1  # una foto por semana y creador


@pytest.mark.asyncio
async def test_snapshots_are_pruned_to_26_weeks(maker):
    async with maker() as s:
        old = ops.week_start(TODAY) - timedelta(weeks=ops.HISTORY_WEEKS + 2)
        keep = ops.week_start(TODAY) - timedelta(weeks=3)
        s.add_all([TikTokMarketCreatorSnapshot(creator_open_id="A", week_start=old), TikTokMarketCreatorSnapshot(creator_open_id="A", week_start=keep)])
        await s.flush()
        await ops.prune_snapshots(s, TODAY)
        assert [r.week_start for r in await s.scalars(select(TikTokMarketCreatorSnapshot))] == [keep]


# --- puntaje y anti-recomendación --------------------------------------------------------------------------


def _facts(**kw: Any) -> analytics.CreatorFacts:
    base: dict[str, Any] = {"username": "u", "nickname": "u", "follower_count": 20_000, "category_ids": (HOME,), "gmv": 5000.0,
                            "units_sold": 100.0, "engagement_rate": None}
    return analytics.CreatorFacts(**{**base, **kw})


def test_score_parts_sum_to_total_and_each_carries_a_reason():
    score, parts = analytics.score_creator(_facts())
    assert score == round(sum(p["points"] for p in parts), 1) and 0 < score <= 100
    assert [p["name"] for p in parts] == ["ajuste", "rendimiento", "tamaño"] and all(p["reason"] for p in parts)
    assert sum(p["max"] for p in parts) == 100


def test_fit_prefers_our_categories_and_uses_name_keywords_as_a_weaker_hint():
    ours, _ = analytics.fit_value(_facts(category_ids=(HOME, KITCHEN)))
    elsewhere, _ = analytics.fit_value(_facts(category_ids=(PETS,), username="dogs", nickname="dogs"))
    hint, reason = analytics.fit_value(_facts(category_ids=(PETS,), username="christmas_elf_home"))
    assert ours == 1.0 and elsewhere == 0.0 and 0 < hint < ours and "decoración navideña" in reason


def test_size_prefers_mid_creators_and_performance_scale_is_logarithmic():
    assert analytics.size_tier(20_000) == ("mediano", 1.0) and analytics.size_tier(50)[0] == "nano" and analytics.size_tier(3_000_000)[0] == "mega"
    mid, _ = analytics.performance_value(_facts(gmv=1000.0))
    top, _ = analytics.performance_value(_facts(gmv=10_000.0, units_sold=1000.0))
    none, _ = analytics.performance_value(_facts(gmv=0.0, units_sold=0.0))
    assert none == 0 and mid < top == pytest.approx(1.0)
    assert analytics.performance_value(_facts(engagement_rate=0.05))[0] > analytics.performance_value(_facts())[0] - 1  # usa la ficha si hay


def test_relation_flags_sampled_without_sales_paused_and_already_working():
    classify = analytics.classify_relation
    assert classify(None) == (analytics.RELATION_NONE, None)
    assert classify(analytics.OurHistory(2, 0, -30.0, "Pausar"))[0] == analytics.RELATION_SAMPLED_NO_SALES
    assert classify(analytics.OurHistory(3, 1, -5.0, "Cortar"))[0] == analytics.RELATION_PAUSED
    relation, reason = classify(analytics.OurHistory(1, 6, 120.0, "Enviar"))
    assert relation == analytics.RELATION_WORKING and "Ya trabajamos con él" in (reason or "")
    assert analytics.recommendation_for(90, relation) == analytics.RECOMMENDATION_WORKING
    assert analytics.recommendation_for(90, analytics.RELATION_SAMPLED_NO_SALES) == analytics.RECOMMENDATION_NO_SAMPLES
    assert analytics.recommendation_for(90, analytics.RELATION_NONE) == "Candidato"
    assert analytics.recommendation_for(40, analytics.RELATION_NONE) == "Vigilar" and analytics.recommendation_for(5, analytics.RELATION_NONE) == "Bajo ajuste"


@pytest.mark.asyncio
async def test_load_our_history_reuses_sample_policy_and_creator_profit(maker, monkeypatch):
    async def policy(_s: Any, **_kw: Any) -> dict:
        return {"rows": [{"creator": "Alice", "samples": 2, "orders": 0, "recommendation": "Pausar"},
                         {"creator": "Bob", "samples": 1, "orders": 4, "recommendation": "Enviar"}]}

    async def profit(_s: Any, **_kw: Any) -> dict:
        return {"rows": [{"key": "bob", "creator": "bob", "orders": 4, "profit": 75.5}, {"key": "carol", "creator": "carol", "orders": 2, "profit": 10.0}]}

    monkeypatch.setattr(analytics, "sample_policy_report", policy)
    monkeypatch.setattr(analytics, "creator_profit_report", profit)
    async with maker() as s:
        history = await analytics.load_our_history(s)
    assert history["alice"] == analytics.OurHistory(2, 0, 0.0, "Pausar")
    assert history["bob"] == analytics.OurHistory(1, 4, 75.5, "Enviar")
    assert history["carol"] == analytics.OurHistory(0, 2, 10.0, None)  # vende pero nunca le enviamos muestra


# --- sincronización ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_light_sync_stores_everything_is_idempotent_and_only_reads(maker):
    shop = FakeShop([_creator(f"O{i}", f"home{i}", followers=1000 * (i + 1)) for i in range(25)], [_open("C1"), _open("C2")], [_target("T1")])
    async with maker() as s:
        first = await ops.sync_creator_market(s, _client(shop), mode=ops.MODE_LIGHT, now=NOW, detail_pause=0)
        assert first["problems"] == [] and first["stats"]["creators"] == 25 and first["stats"]["details"] == 10
        assert first["stats"]["open_collaborations"] == 2 and first["stats"]["target_collaborations"] == 1
        await ops.sync_creator_market(s, _client(shop), mode=ops.MODE_LIGHT, now=NOW + timedelta(hours=1), detail_pause=0)
        assert await _count(s, TikTokMarketCreator) == 25 and await _count(s, TikTokMarketCreatorSnapshot) == 25
        assert await _count(s, TikTokOpenCollaboration) == 2 and await _count(s, TikTokTargetCollaboration) == 1
        assert await s.scalar(select(func.count()).where(TikTokMarketCreator.detail_at.is_not(None))) == 20
    for method, path, _params, _body in shop.calls:  # NINGÚN endpoint llamó algo que no sea GET o /search
        cm.assert_read_only(method, path)
        assert method == "GET" or path.endswith("/search")
    assert {c[0] for c in shop.calls} == {"GET", "POST"}


@pytest.mark.asyncio
async def test_collaborations_are_retired_only_after_reading_everything(maker):
    async with maker() as s:
        await ops.sync_creator_market(s, _client(FakeShop(opens=[_open("C1"), _open("C2")])), mode=ops.MODE_LIGHT, now=NOW, detail_pause=0)
        await ops.sync_creator_market(s, _client(FakeShop(opens=[_open("C1")])), mode=ops.MODE_LIGHT, now=NOW + timedelta(hours=1), detail_pause=0)
        state = {c.collaboration_id: c.active for c in await s.scalars(select(TikTokOpenCollaboration))}
        assert state == {"C1": True, "C2": False}

        broken = FakeShop(opens=[_open("C1")], fail={"open_collaborations": TikTokShopError(36009002, "Too many requests")})
        result = await ops.sync_creator_market(s, _client(broken), mode=ops.MODE_LIGHT, now=NOW + timedelta(hours=2), detail_pause=0)
        assert any("colaboraciones abiertas" in p for p in result["problems"])
        assert {c.collaboration_id: c.active for c in await s.scalars(select(TikTokOpenCollaboration))} == state  # el fallo no retira nada


@pytest.mark.asyncio
async def test_a_failed_section_does_not_stop_the_others_and_blocks_full_complete(maker):
    shop = FakeShop([_creator("O1", "home1")], [_open("C1")], [_target("T1")],
                    fail={"marketplace_creators/search": TikTokShopError(36009004, "Invalid API version")})
    async with maker() as s:
        result = await ops.sync_creator_market(s, _client(shop), mode=ops.MODE_FULL, now=NOW, detail_pause=0)
        assert any(p.startswith("creadores") for p in result["problems"])
        assert result["stats"]["open_collaborations"] == 1 and result["stats"]["target_collaborations"] == 1
        assert result["stats"]["full_complete"] is False and result["stats"]["creators"] == 0


@pytest.mark.asyncio
async def test_full_sync_retires_long_unseen_creators_but_light_never_does(maker):
    old = NOW - ops.CREATOR_STALE_AFTER - timedelta(days=1)
    async with maker() as s:
        await ops.upsert_creators(s, [cm.build_creator(_creator("OLD", "old"))], old)  # type: ignore[list-item]
        await s.commit()
        shop = FakeShop([_creator("NEW", "home_new")])
        await ops.sync_creator_market(s, _client(shop), mode=ops.MODE_LIGHT, now=NOW, detail_pause=0)
        assert (await s.get(TikTokMarketCreator, "OLD")).active is True  # type: ignore[union-attr]
        result = await ops.sync_creator_market(s, _client(shop), mode=ops.MODE_FULL, now=NOW, detail_pause=0)
        assert result["stats"]["creators_deactivated"] == 1 and result["stats"]["full_complete"] is True
        assert (await s.get(TikTokMarketCreator, "OLD")).active is False  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_choose_mode_runs_full_weekly_and_backs_off_after_an_incomplete_attempt(maker):
    async with maker() as s:
        assert await ops.choose_mode(s, NOW) == ops.MODE_FULL  # nunca corrió
        s.add(SyncRun(job_type=ops.JOB_TYPE, status="success", started_at=NOW - timedelta(days=2), finished_at=NOW - timedelta(days=2),
                      stats={"mode": "full", "full_complete": True}))
        await s.commit()
        assert await ops.choose_mode(s, NOW) == ops.MODE_LIGHT
        assert await ops.choose_mode(s, NOW + ops.FULL_INTERVAL) == ops.MODE_FULL
        s.add(SyncRun(job_type=ops.JOB_TYPE, status="partial", started_at=NOW + ops.FULL_INTERVAL - timedelta(hours=1),
                      finished_at=NOW + ops.FULL_INTERVAL, stats={"mode": "full", "full_complete": False}))
        await s.commit()
        assert await ops.choose_mode(s, NOW + ops.FULL_INTERVAL + timedelta(hours=1)) == ops.MODE_LIGHT  # no repetir cada vuelta


def test_source_is_registered_in_sync_jobs():
    assert "tiktok_creator_market" in sync_jobs.SOURCES and "tiktok_creator_market" in sync_jobs.RUNNERS
    assert sync_jobs.LABELS["tiktok_creator_market"] == "TikTok Shop (mercado de creadores y colaboraciones)"
    assert "tiktok_creator_market" in sync_jobs.SETTINGS


# --- API ---------------------------------------------------------------------------------------------------


def _http(maker) -> httpx.AsyncClient:
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()  # el router se prueba montado aparte
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def seeded(maker, monkeypatch):
    """Carga inicial de 2 creadores y luego 4 NUEVOS: uno bueno, uno al que ya enviamos muestras sin ventas, otro que ya vende, uno ajeno."""
    async def history(_s: Any) -> dict:
        return {"muestreado": analytics.OurHistory(2, 0, 0.0, "Pausar"), "vendedor": analytics.OurHistory(1, 5, 90.0, "Enviar")}

    monkeypatch.setattr(analytics, "load_our_history", history)
    first = NOW - timedelta(days=10)
    base = [_creator("B1", "base_uno", followers=8000, gmv="800"), _creator("B2", "base_dos", followers=15_000, gmv="1500")]
    later = base + [
        _creator("N1", "christmas_home_decor", followers=40_000, gmv="9000", cats=(HOME, KITCHEN)),
        _creator("N2", "muestreado", followers=40_000, gmv="9000", cats=(HOME,)),
        _creator("N3", "vendedor", followers=40_000, gmv="9000", cats=(HOME,)),
        _creator("N4", "dog_lover", followers=900, gmv=None, cats=(PETS,)),
    ]
    async with maker() as s:
        await ops.sync_creator_market(s, _client(FakeShop(base, [_open("C1"), _open("C2", status="OFFLINE")], [_target("T1")])),
                                      mode=ops.MODE_FULL, now=first, detail_pause=0)
        await ops.sync_creator_market(s, _client(FakeShop(later, [_open("C1"), _open("C2", status="OFFLINE")], [_target("T1")])),
                                      mode=ops.MODE_LIGHT, now=NOW - timedelta(hours=2), detail_pause=0)
    return maker


@pytest.mark.asyncio
async def test_empty_database_endpoints_do_not_fail(maker):
    async with _http(maker) as c:
        for path in ("status", "marketplace", "marketplace/summary", "collaborations", "candidates"):
            assert (await c.get(f"/api/tiktok-creators/{path}")).status_code == 200, path
        assert (await c.get("/api/tiktok-creators/marketplace")).json()["total"] == 0
        assert (await c.get("/api/tiktok-creators/marketplace?limit=0")).status_code == 422
        assert (await c.get("/api/tiktok-creators/marketplace?sort=bogus")).status_code == 422
        assert (await c.get("/api/tiktok-creators/collaborations?kind=bogus")).status_code == 422


@pytest.mark.asyncio
async def test_status_lists_routes_versions_coverage_and_last_sync(seeded):
    async with _http(seeded) as c:
        data = (await c.get("/api/tiktok-creators/status")).json()
    assert data["routes"]["marketplace_creators_search"]["version"] == "202508" and data["routes"]["open_collaborations_search"]["version"] == "202412"
    assert data["coverage"]["creators"] == 6 and data["coverage"]["open_collaborations"] == 2 and data["coverage"]["target_collaborations"] == 1
    assert data["last_sync"] is None and data["unavailable"] and data["limits"]["creator_page_size"] == 20  # la sincronización se hizo sin run_source


@pytest.mark.asyncio
async def test_marketplace_scores_explains_and_applies_the_anti_recommendation(seeded):
    async with _http(seeded) as c:
        data = (await c.get("/api/tiktok-creators/marketplace?sort=score")).json()
        by_user = {r["username"]: r for r in data["rows"]}
        assert data["total"] == 6 and data["rows"][0]["username"] == "christmas_home_decor"
        best = by_user["christmas_home_decor"]
        assert best["recommendation"] == "Candidato" and best["relation"] == "none" and {p["name"] for p in best["score_parts"]} == {"ajuste", "rendimiento", "tamaño"}
        assert by_user["muestreado"]["relation"] == "sampled_no_sales" and by_user["muestreado"]["recommendation"] == "No enviar muestras"
        assert "no vendió" in by_user["muestreado"]["relation_reason"] and by_user["muestreado"]["is_candidate"] is False
        assert by_user["vendedor"]["recommendation"] == "Ya trabajamos con él" and by_user["vendedor"]["is_candidate"] is False
        assert by_user["dog_lover"]["is_candidate"] is False

        assert [r["username"] for r in (await c.get("/api/tiktok-creators/marketplace?q=CHRISTMAS")).json()["rows"]] == ["christmas_home_decor"]
        assert (await c.get("/api/tiktok-creators/marketplace?min_followers=30000")).json()["total"] == 3
        assert {r["username"] for r in (await c.get("/api/tiktok-creators/marketplace?category=Kitchenware")).json()["rows"]} == {"christmas_home_decor"}
        page = (await c.get("/api/tiktok-creators/marketplace?sort=followers&limit=2&offset=1")).json()
        assert page["total"] == 6 and len(page["rows"]) == 2
        newest = (await c.get("/api/tiktok-creators/marketplace?sort=newest&limit=1")).json()["rows"][0]
        assert newest["is_new"] is True


@pytest.mark.asyncio
async def test_summary_candidates_and_new_creators(seeded):
    async with _http(seeded) as c:
        summary = (await c.get("/api/tiktok-creators/marketplace/summary")).json()
        shortlist = (await c.get("/api/tiktok-creators/candidates?limit=5")).json()
    assert summary["creators"] == 6 and summary["new"] == 4 and summary["excluded"] == 2 and summary["candidates"] >= 1
    assert {s["category"] for s in summary["by_category"]} >= {"Home Supplies", "Kitchenware", "Pet Supplies"}
    assert [t["tier"] for t in summary["by_size"]][:2] == ["nano", "micro"] and sum(t["creators"] for t in summary["by_size"]) == 6
    names = [r["username"] for r in shortlist["rows"]]
    assert names[0] == "christmas_home_decor" and "muestreado" not in names and "vendedor" not in names  # solo nuevos y no excluidos
    assert shortlist["rows"][0]["reason"].startswith("Puntaje")
    assert "base_uno" not in names  # la carga inicial no cuenta como "nuevos"


@pytest.mark.asyncio
async def test_collaborations_endpoint_filters_by_kind_and_status(seeded):
    async with _http(seeded) as c:
        everything = (await c.get("/api/tiktok-creators/collaborations")).json()
        opened = (await c.get("/api/tiktok-creators/collaborations?kind=open")).json()
        offline = (await c.get("/api/tiktok-creators/collaborations?kind=open&status=offline")).json()
        target = (await c.get("/api/tiktok-creators/collaborations?kind=target")).json()
    assert everything["total"] == 3 and opened["total"] == 2 and [r["id"] for r in offline["rows"]] == ["C2"]
    assert target["rows"][0]["invited"] == 9 and target["rows"][0]["free_sample"] is True and target["counts"] == {"target:ONGOING": 1}
    assert opened["rows"][0]["commission_rate"] == pytest.approx(0.145)


# --- Centro de acciones ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_provider_groups_best_new_creators_with_documented_impact(seeded):
    async with seeded() as s:
        items = await actions.creator_candidate_actions(s)
    assert len(items) == 1
    item = items[0]
    assert item.kind == actions.KIND == "tiktok_creator_candidates" and item.target == "tiktok" and item.impact_usd > 0
    assert "christmas_home_decor" in item.detail and "muestreado" not in item.detail and item.impact_basis
    assert "14.5%" in item.impact_basis  # usa la comisión media de nuestras colaboraciones abiertas
    assert actions.KIND in action_center.providers()


@pytest.mark.asyncio
async def test_action_provider_is_empty_without_data_and_impact_math_is_documented(maker):
    async with maker() as s:
        assert await actions.creator_candidate_actions(s) == []
        assert await actions.offered_commission_rate(s) == actions.DEFAULT_COMMISSION_RATE
    assert actions.estimated_monthly_commission(12_000.0, 0.10) == pytest.approx(12_000 / 12 * 0.02 * 0.10)
