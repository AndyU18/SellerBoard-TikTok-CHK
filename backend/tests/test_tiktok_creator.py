"""TikTok Creator: constructores puros, renovación atómica de tokens, paginación, upserts, puntaje,
sincronización ligera y completa, API y Centro de acciones.

Todo con datos sintéticos, SQLite en memoria y `httpx.MockTransport`: ninguna prueba toca la red ni los tokens reales.
"""

import json
import os
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import action_center, sync_jobs
from app import tiktok_creator_actions as actions
from app import tiktok_creator_analytics as analytics
from app import tiktok_creator_ops as ops
from app.connectors import tiktok_creator as tc
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    SyncRun,
    TikTokCreatorOrder,
    TikTokCreatorProduct,
    TikTokCreatorProductHistory,
    TikTokCreatorProfile,
    TikTokCreatorShowcaseProduct,
    TikTokShopProduct,
)
from app.tiktok_creator_api import router

NOW = datetime.now(UTC)
TODAY = NOW.date()


def _raw_product(pid: str, *, units: int = 0, rate: int = 1000, amount: str = "3.00", price: str = "30",
                 category: tuple[str, ...] = ("Home & Kitchen", "Candles"), title: str | None = None, inventory: bool = True) -> dict:
    return {
        "id": pid, "title": title or f"Producto {pid}", "main_image_url": f"https://img/{pid}.jpg", "detail_link": f"https://shop/{pid}",
        "category_chains": [{"id": str(i), "is_leaf": i == len(category) - 1, "local_name": n, "parent_id": "0"} for i, n in enumerate(category)],
        "commission": {"amount": amount, "currency": "USD", "rate": rate},
        "original_price": {"currency": "USD", "minimum_amount": price, "maximum_amount": price},
        "sales_price": {"currency": "", "minimum_amount": "", "maximum_amount": ""},
        "sale_region": "US", "units_sold": units, "has_inventory": inventory, "shop": {"name": "Tienda X"},
    }


# --- constructores puros -----------------------------------------------------------------------------------


def test_build_product_normalizes_commission_prices_and_category():
    row = tc.build_product(_raw_product("1", units=40, rate=1250, amount="3.75", price="30"))
    assert row is not None
    assert row["commission_rate"] == Decimal("0.125") and row["commission_amount"] == Decimal("3.75")
    assert row["original_price_min"] == Decimal(30) and row["sale_price_min"] is None  # "" = sin dato, no cero
    assert row["category"] == "Home & Kitchen > Candles" and row["category_leaf"] == "Candles"
    assert row["shop_name"] == "Tienda X" and row["units_sold"] == 40 and row["suspect"] is False


def test_build_product_flags_impossible_values_and_drops_their_money():
    huge = tc.build_product(_raw_product("2", price="3875820019684.21", units=2_000_000_364))
    assert huge is not None and huge["suspect"] is True and huge["original_price_min"] is None  # no desborda numeric(14,4)
    assert tc.build_product(_raw_product("3", units=tc.MAX_PLAUSIBLE_UNITS + 1))["suspect"] is True  # type: ignore[index]
    assert tc.build_product({"title": "sin id"}) is None


def test_build_history_row_prefers_sale_price():
    product = tc.build_product(_raw_product("1", units=5))
    assert product is not None
    row = tc.build_history_row(product, date(2026, 11, 23))
    assert row["price"] == Decimal(30) and row["units_sold"] == 5 and row["snapshot_date"] == date(2026, 11, 23)
    product["sale_price_min"] = Decimal(25)
    assert tc.build_history_row(product, date(2026, 11, 23))["price"] == Decimal(25)


def test_build_profile_showcase_and_order():
    profile = tc.build_profile(
        {"creator_user_id": 7, "username": "chk", "avatar": {"url": "https://a"}, "permissions": ["SELF_SALE_PERMISSION", "LIVE_STREAM_PERMISSION"]}, TODAY
    )
    assert profile["permissions"] == ["LIVE_STREAM_PERMISSION", "SELF_SALE_PERMISSION"] and profile["creator_user_id"] == "7"
    showcase = tc.build_showcase_product(
        {"id": "9", "title": "Santa", "main_images": [{"url": "https://i"}], "shop": {"name": ""},
         "price": {"original_price": {"currency": "USD", "minimum_amount": "63.99", "maximum_amount": "144.99"}},
         "status": {"added_status": "ADDED", "inventory_status": "IN_STOCK", "is_hidden": False, "review_status": "APPROVED"}},
        "SHOWCASE",
    )
    assert showcase is not None and showcase["original_price_max"] == Decimal("144.99") and showcase["shop_name"] is None
    order = tc.build_order({"id": "O1", "status": "PAID", "create_time": 1_700_000_000, "commission": {"amount": "4.2", "currency": "USD"}})
    assert order is not None and order["commission_amount"] == Decimal("4.2") and order["created_at"] is not None
    assert tc.build_order({"status": "x"}) is None


# --- tokens: renovación atómica, respaldo y bloqueo --------------------------------------------------------


def _tokens(**overrides: Any) -> dict:
    base = {"access_token": "old-access", "refresh_token": "old-refresh", "access_token_expire_in": time.time() - 100,
            "refresh_token_expire_in": time.time() + 10 * 86400, "shop_cipher": ""}
    return {**base, **overrides}


def _settings(path: Path) -> tc.TikTokCreatorSettings:
    return tc.TikTokCreatorSettings(app_key="key", app_secret="secret", tokens_file=path)


class FakeTikTok:
    """Doble HTTP de auth.tiktok-shops.com (renovación) y open-api (lecturas)."""

    def __init__(self, *, refresh_code: int = 0) -> None:
        self.refresh_code = refresh_code
        self.refresh_calls = 0
        self.api_calls: list[httpx.Request] = []
        self.api_replies: list[httpx.Response] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.tiktok-shops.com":
            self.refresh_calls += 1
            if self.refresh_code:
                return httpx.Response(200, json={"code": self.refresh_code, "message": "refresh token invalid"})
            assert request.url.params["refresh_token"] == "old-refresh"
            return httpx.Response(200, json={"code": 0, "data": {
                "access_token": "new-access", "refresh_token": "new-refresh",
                "access_token_expire_in": int(time.time()) + 7 * 86400, "refresh_token_expire_in": int(time.time()) + 365 * 86400}})
        self.api_calls.append(request)
        if self.api_replies:
            return self.api_replies.pop(0)
        return httpx.Response(200, json={"code": 0, "data": {"username": "chk"}})


def _client(path: Path, fake: FakeTikTok, sleeps: list[float] | None = None) -> tc.TikTokCreatorClient:
    record = sleeps if sleeps is not None else []
    return tc.TikTokCreatorClient(_settings(path), httpx.Client(transport=httpx.MockTransport(fake)), sleep=record.append)


def test_refresh_is_atomic_keeps_backup_and_uses_new_token(tmp_path: Path):
    path = tmp_path / "tokens_creator.json"
    old = json.dumps(_tokens())
    path.write_text(old)
    fake = FakeTikTok()
    assert _client(path, fake).profile() == {"username": "chk"}
    saved = json.loads(path.read_text())
    assert saved["access_token"] == "new-access" and saved["refresh_token"] == "new-refresh"
    assert path.with_name(path.name + ".bak").read_text() == old  # último archivo bueno
    assert fake.api_calls[0].headers["x-tts-access-token"] == "new-access"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tokens_creator.json", "tokens_creator.json.bak"]  # sin .tmp ni .lock


def test_failed_refresh_leaves_good_file_untouched_and_asks_for_reauthorization(tmp_path: Path):
    path = tmp_path / "tokens_creator.json"
    path.write_text(json.dumps(_tokens()))
    before = path.read_bytes()
    fake = FakeTikTok(refresh_code=105002)
    with pytest.raises(tc.CreatorReauthRequired):
        _client(path, fake).profile()
    assert path.read_bytes() == before and not path.with_name(path.name + ".bak").exists()
    assert not path.with_name(path.name + ".lock").exists()  # el bloqueo se libera aun con error


def test_write_failure_never_corrupts_the_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "tokens_creator.json"
    path.write_text(json.dumps(_tokens()))
    before = path.read_bytes()

    def broken_replace(*_: Any) -> None:
        raise OSError("disco lleno")

    monkeypatch.setattr(tc.os, "replace", broken_replace)
    with pytest.raises(OSError):
        _client(path, FakeTikTok()).profile()
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []  # el temporal se limpia


def test_refresh_skipped_when_another_process_already_renewed(tmp_path: Path):
    path = tmp_path / "tokens_creator.json"
    path.write_text(json.dumps(_tokens(access_token="fresh", access_token_expire_in=time.time() + 86400)))
    fake = FakeTikTok()
    client = tc.AtomicTokenShopClient(_settings(path), httpx.Client(transport=httpx.MockTransport(fake)))
    client._tokens = _tokens()  # lo que este proceso tenía en memoria: vencido
    client.refresh()
    assert fake.refresh_calls == 0 and client._tokens["access_token"] == "fresh"


def test_expired_refresh_token_requires_reauthorization_without_calling_tiktok(tmp_path: Path):
    path = tmp_path / "tokens_creator.json"
    path.write_text(json.dumps(_tokens(refresh_token_expire_in=time.time() - 1)))
    fake = FakeTikTok()
    with pytest.raises(tc.CreatorReauthRequired):
        _client(path, fake).profile()
    assert fake.refresh_calls == 0


def test_corrupt_token_file_falls_back_to_backup_then_to_reauth(tmp_path: Path):
    path = tmp_path / "tokens_creator.json"
    good = json.dumps(_tokens(access_token_expire_in=time.time() + 86400))
    path.write_text("{ roto")
    path.with_name(path.name + ".bak").write_text(good)
    assert tc.read_token_file(path)["access_token"] == "old-access"
    path.with_name(path.name + ".bak").write_text("{ tambien roto")
    with pytest.raises(tc.CreatorReauthRequired):
        tc.read_token_file(path)


def test_file_lock_excludes_second_holder_breaks_stale_and_releases(tmp_path: Path):
    lock = tmp_path / "t.lock"
    with tc.FileLock(lock), pytest.raises(tc.CreatorLockTimeout), tc.FileLock(lock, timeout=0.0, sleep=lambda _: None):
        pass
    assert not lock.exists()
    lock.write_text("999")
    os.utime(lock, (0, 0))  # bloqueo de un proceso caído: antiquísimo
    with tc.FileLock(lock, timeout=0.0):
        assert lock.exists()
    assert not lock.exists()


def test_token_status_exposes_expiry_but_never_tokens(tmp_path: Path):
    path = tmp_path / "tokens_creator.json"
    assert tc.token_status(path)["usable"] is False
    path.write_text(json.dumps(_tokens(access_token_expire_in=time.time() + 100)))
    info = tc.token_status(path)
    assert info["usable"] and info["access_valid"] and info["refresh_valid"] and "old-access" not in str(info)


def test_settings_not_configured_without_token_file_and_ignore_vendor_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from pydantic import ValidationError

    monkeypatch.setenv("TTS_TOKENS_FILE", "tts_tokens.json")  # el archivo del VENDEDOR no debe colarse
    with pytest.raises(ValidationError):
        tc.TikTokCreatorSettings(app_key="k", app_secret="s", tokens_file=tmp_path / "no_existe.json")
    path = tmp_path / "tokens_creator.json"
    path.write_text(json.dumps(_tokens()))
    assert _settings(path).tokens_file == path


# --- paginación y reintentos -------------------------------------------------------------------------------


def _valid_file(tmp_path: Path) -> Path:
    path = tmp_path / "tokens_creator.json"
    path.write_text(json.dumps(_tokens(access_token_expire_in=time.time() + 86400)))
    return path


def _page(n: int, token: str, total: int = 100) -> httpx.Response:
    products = [_raw_product(f"P{n}-{i}") for i in range(tc.CREATOR_PAGE_SIZE)]
    return httpx.Response(200, json={"code": 0, "data": {"products": products, "total_count": total, "next_page_token": token}})


def test_pagination_is_always_twenty_and_follows_tokens(tmp_path: Path):
    fake = FakeTikTok()
    fake.api_replies = [_page(1, "t2"), _page(2, "t3"), _page(3, "")]
    sleeps: list[float] = []
    pages = list(_client(_valid_file(tmp_path), fake, sleeps).iter_open_collab_pages(sort_field="units_sold"))
    assert [p.has_more for p in pages] == [True, True, False] and pages[0].total_count == 100
    assert all(r.url.params["page_size"] == "20" for r in fake.api_calls) and tc.CREATOR_PAGE_SIZE == 20
    assert [r.url.params.get("page_token") for r in fake.api_calls] == [None, "t2", "t3"]
    assert fake.api_calls[0].url.params["sort_field"] == "units_sold" and sleeps == [tc.PAGE_PAUSE_SECONDS] * 2
    assert all(r.method == "POST" and r.url.path.endswith("/search") for r in fake.api_calls)  # solo consultas


def test_max_pages_caps_and_reports_more(tmp_path: Path):
    fake = FakeTikTok()
    fake.api_replies = [_page(1, "t2"), _page(2, "t3")]
    pages = list(_client(_valid_file(tmp_path), fake).iter_open_collab_pages(max_pages=2))
    assert len(pages) == 2 and pages[-1].has_more is True  # se cortó por el tope, no porque terminara


def test_rate_limit_and_network_errors_are_retried_with_waits(tmp_path: Path):
    fake = FakeTikTok()
    fake.api_replies = [
        httpx.Response(200, json={"code": 36009003, "message": "Rate limit exceeded, too many requests"}),
        httpx.Response(429, text="<html>slow down</html>"),  # cuerpo que no es JSON
        _page(1, ""),
    ]
    sleeps: list[float] = []
    pages = list(_client(_valid_file(tmp_path), fake, sleeps).iter_open_collab_pages())
    assert len(pages) == 1 and len(fake.api_calls) == 3
    assert sleeps == [tc.RATE_LIMIT_WAIT_SECONDS, tc.RETRY_BACKOFF_SECONDS * 2]  # espera de tasa, luego retroceso de red


def test_non_rate_limit_api_error_is_not_retried_and_attempts_are_bounded(tmp_path: Path):
    fake = FakeTikTok()
    fake.api_replies = [httpx.Response(200, json={"code": 36009004, "message": "PageSize is invalid"})]
    with pytest.raises(tc.TikTokShopError):
        _client(_valid_file(tmp_path), fake).profile()
    assert len(fake.api_calls) == 1
    always_limited = FakeTikTok()
    always_limited.api_replies = [httpx.Response(200, json={"code": 1, "message": "rate limit"})] * 10
    with pytest.raises(tc.TikTokShopError):
        _client(_valid_file(tmp_path), always_limited).profile()
    assert len(always_limited.api_calls) == tc.MAX_ATTEMPTS


def test_invalid_sort_and_origin_are_rejected_before_any_request(tmp_path: Path):
    client = _client(_valid_file(tmp_path), FakeTikTok())
    with pytest.raises(ValueError):
        list(client.iter_open_collab_pages(sort_field="price"))
    with pytest.raises(ValueError):
        list(client.iter_showcase_pages("OTHER"))


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
async def test_upserts_are_idempotent_and_keep_first_seen(maker):
    rows = [r for pid in ("A", "B", "A") if (r := tc.build_product(_raw_product(pid))) is not None]  # "A" repetido en el lote
    first = NOW - timedelta(days=3)
    async with maker() as s:
        assert await ops.upsert_products(s, rows, first) == 2
        await s.commit()
        assert await ops.upsert_products(s, rows, NOW) == 2
        await s.commit()
        assert await _count(s, TikTokCreatorProduct) == 2
        product = await s.get(TikTokCreatorProduct, "A")
        assert product is not None and product.first_seen_at.replace(tzinfo=UTC) == first.replace(microsecond=product.first_seen_at.microsecond)
        assert product.last_seen_at.replace(tzinfo=UTC) > product.first_seen_at.replace(tzinfo=UTC)
        history = [tc.build_history_row(r, ops.week_start(TODAY)) for r in rows]
        await ops.upsert_history(s, history, ops.week_start(TODAY))
        await ops.upsert_history(s, history, ops.week_start(TODAY))
        await s.commit()
        assert await _count(s, TikTokCreatorProductHistory) == 2


@pytest.mark.asyncio
async def test_history_is_pruned_to_26_weeks(maker):
    old = tc.build_history_row(tc.build_product(_raw_product("A")), TODAY - timedelta(weeks=ops.HISTORY_WEEKS + 2))  # type: ignore[arg-type]
    recent = tc.build_history_row(tc.build_product(_raw_product("B")), ops.week_start(TODAY))  # type: ignore[arg-type]
    async with maker() as s:
        s.add_all([TikTokCreatorProductHistory(**old), TikTokCreatorProductHistory(**recent)])
        await s.commit()
        await ops.prune_history(s, TODAY)
        await s.commit()
        assert [h.product_id for h in await s.scalars(select(TikTokCreatorProductHistory))] == ["B"]


# --- puntaje de oportunidad --------------------------------------------------------------------------------


def test_affinity_prefers_category_over_title_and_matches_whole_words():
    assert analytics.affinity("Home & Kitchen > Candles", "Vela") == (analytics.AFFINITY_CATEGORY, ["cocina", "hogar"])
    assert analytics.affinity("Toys", "Christmas Santa ornament") == (analytics.AFFINITY_TITLE, ["navidad"])
    assert analytics.affinity("Beauty > Haircare", "Bond shampoo") == (analytics.AFFINITY_NONE, [])
    assert analytics.affinity("Toys", "Handbag") == (analytics.AFFINITY_NONE, [])  # "bag" dentro de otra palabra no cuenta


def test_score_combines_commission_demand_availability_and_affinity():
    base = {"per_unit": 5.0, "units_sold": 10_000, "has_inventory": True, "affinity_factor": 1.0, "suspect": False}
    assert analytics.score_product(**base)["score"] == 100.0  # todo saturado
    assert analytics.score_product(**{**base, "has_inventory": False})["score"] == 0.0
    assert analytics.score_product(**{**base, "suspect": True})["score"] == 0.0
    assert analytics.score_product(**{**base, "affinity_factor": analytics.AFFINITY_NONE})["score"] == 40.0
    assert analytics.score_product(**{**base, "per_unit": 2.5})["score"] == 50.0
    unproven = analytics.score_product(**{**base, "units_sold": 0})  # sin ventas: piso, no cero
    assert unproven["demand_factor"] == analytics.DEMAND_FLOOR and unproven["score"] == 10.0
    assert analytics.commission_per_unit(None, 0.1, 30.0) == pytest.approx(3.0) and analytics.commission_per_unit(None, None, 30.0) is None


def test_relation_finds_same_category_and_equivalent_product_with_price_gap():
    ours = [analytics.OurProduct("1", "Scented Soy Candle Lavender", analytics.title_tokens("Scented Soy Candle Lavender"), frozenset({"candles"}), 20.0),
            analytics.OurProduct("2", "Wooden spoon", analytics.title_tokens("Wooden spoon"), frozenset({"utensils"}), 5.0)]
    rel = analytics.relation_to_catalog("Home > Candles", 25.0, analytics.title_tokens("Lavender scented candle gift"), ours)
    assert rel["sells_same_category"] and rel["our_products_in_category"] == 1
    assert rel["equivalent"]["product_id"] == "1" and rel["equivalent"]["price_difference_pct"] == 25.0
    none = analytics.relation_to_catalog("Beauty", 9.0, analytics.title_tokens("Hair shampoo"), ours)
    assert none == {"sells_same_category": False, "our_products_in_category": 0, "equivalent": None}


# --- sincronización ----------------------------------------------------------------------------------------


class FakeCreator:
    """Doble de TikTokCreatorClient: sirve catálogo, vitrina y pedidos desde listas en memoria."""

    def __init__(self, catalog: list[dict], *, showcase: list[dict] | None = None, orders: list[dict] | None = None) -> None:
        self.catalog, self.showcase, self.orders = catalog, showcase or [], orders or []
        self.reauth = False
        self.calls: list[str] = []

    def profile(self) -> dict:
        if self.reauth:
            raise tc.CreatorReauthRequired("hay que reautorizar")
        return {"creator_user_id": "7", "username": "chk", "permissions": ["LIVE_STREAM_PERMISSION", "SELF_SALE_PERMISSION"]}

    @staticmethod
    def _pages(items: list[dict], max_pages: int, total: int | None = None) -> Any:
        size = tc.CREATOR_PAGE_SIZE
        chunks = [items[i : i + size] for i in range(0, len(items), size)] or [[]]
        for number, chunk in enumerate(chunks[:max_pages]):
            yield tc.Page(chunk, total if total is not None else len(items), number < len(chunks) - 1)

    def iter_open_collab_pages(self, *, sort_field: str | None = None, sort_order: str = "DESC", max_pages: int = 500, pause: float = 0.0):
        self.calls.append(f"catalog:{sort_field}:{max_pages}")
        items = sorted(self.catalog, key=lambda p: -p["units_sold"]) if sort_field == "units_sold" else self.catalog
        return self._pages(items, max_pages)

    def iter_showcase_pages(self, origin: str = "SHOWCASE", **_: Any):
        return self._pages(self.showcase, 50)

    def iter_orders(self, **_: Any):
        return self._pages(self.orders, 50)


def _catalog(n: int, **kw: Any) -> list[dict]:
    return [_raw_product(f"P{i:03d}", units=i, **kw) for i in range(n)]


@pytest.mark.asyncio
async def test_light_sync_reads_few_pages_and_is_idempotent(maker):
    fake = FakeCreator(_catalog(300), showcase=[{"id": "S1", "title": "Santa", "status": {"review_status": "APPROVED"}}], orders=[{"id": "O1", "status": "PAID"}])
    async with maker() as s:
        first = await ops.sync_creator(s, fake, mode=ops.MODE_LIGHT, now=NOW)  # type: ignore[arg-type]
        again = await ops.sync_creator(s, fake, mode=ops.MODE_LIGHT, now=NOW)  # type: ignore[arg-type]
        assert first["problems"] == [] and first["stats"] == again["stats"]
        stats = first["stats"]
        assert stats["mode"] == "light" and stats["catalog_pages"] == 2 * ops.LIGHT_PAGES_PER_SORT and stats["catalog_complete"] is False
        assert stats["showcase"] == 1 and stats["orders"] == 1 and stats["catalog_total"] == 300
        assert fake.calls[:2] == [f"catalog:units_sold:{ops.LIGHT_PAGES_PER_SORT}", f"catalog:commission:{ops.LIGHT_PAGES_PER_SORT}"]
        # units_sold DESC aporta los 100 más vendidos y "commission" otros 100 (orden de lista): unión sin duplicados
        assert 100 <= await _count(s, TikTokCreatorProduct) <= 200 and await _count(s, TikTokCreatorProduct) == stats["products"]
        assert await _count(s, TikTokCreatorShowcaseProduct) == 1 and await _count(s, TikTokCreatorOrder) == 1
        profile = await s.get(TikTokCreatorProfile, TODAY)
        assert profile is not None and profile.catalog_total == 300 and profile.showcase_total == 1 and profile.username == "chk"


@pytest.mark.asyncio
async def test_full_sync_reads_everything_and_deactivates_vanished_products(maker):
    async with maker() as s:
        await ops.upsert_products(s, [r for r in [tc.build_product(_raw_product("OLD"))] if r], NOW - timedelta(days=9))
        await s.commit()
        out = await ops.sync_creator(s, FakeCreator(_catalog(45)), mode=ops.MODE_FULL, now=NOW)  # type: ignore[arg-type]
        assert out["stats"]["catalog_complete"] is True and out["stats"]["products"] == 45 and out["stats"]["deactivated"] == 1
        assert (await s.get(TikTokCreatorProduct, "OLD")).active is False  # type: ignore[union-attr]
        assert (await s.get(TikTokCreatorProduct, "P001")).active is True  # type: ignore[union-attr]
        await ops.sync_creator(s, FakeCreator(_catalog(2)), mode=ops.MODE_LIGHT, now=NOW + timedelta(hours=6))  # type: ignore[arg-type]
        assert (await s.get(TikTokCreatorProduct, "P040")).active is True  # el modo ligero NUNCA desactiva


@pytest.mark.asyncio
async def test_full_sync_cut_by_page_cap_or_failure_does_not_deactivate(maker, monkeypatch):
    async with maker() as s:
        await ops.upsert_products(s, [r for r in [tc.build_product(_raw_product("OLD"))] if r], NOW - timedelta(days=9))
        await s.commit()
        monkeypatch.setattr(ops, "MAX_CATALOG_PAGES", 1)
        capped = await ops.sync_creator(s, FakeCreator(_catalog(45)), mode=ops.MODE_FULL, now=NOW)  # type: ignore[arg-type]
        assert capped["stats"]["catalog_complete"] is False and "deactivated" not in capped["stats"]
        assert (await s.get(TikTokCreatorProduct, "OLD")).active is True  # type: ignore[union-attr]

        class Failing(FakeCreator):
            def iter_open_collab_pages(self, **kw: Any):
                yield from list(super().iter_open_collab_pages(**kw))[:1]
                raise tc.TikTokCreatorError("sin respuesta")

        broken = await ops.sync_creator(s, Failing(_catalog(45)), mode=ops.MODE_FULL, now=NOW)  # type: ignore[arg-type]
        assert broken["problems"] and broken["problems"][0].startswith("catálogo") and broken["stats"]["catalog_complete"] is False
        assert (await s.get(TikTokCreatorProduct, "OLD")).active is True  # type: ignore[union-attr]
        assert broken["stats"]["showcase"] == 0  # las demás secciones siguieron


@pytest.mark.asyncio
async def test_choose_mode_runs_full_weekly_and_backs_off_after_incomplete(maker):
    async with maker() as s:
        assert await ops.choose_mode(s, NOW) == ops.MODE_FULL  # nunca hubo carga
        s.add(SyncRun(job_type=ops.JOB_TYPE, status="success", started_at=NOW - timedelta(days=2), finished_at=NOW - timedelta(days=2),
                      stats={"mode": "full", "catalog_complete": True}))
        await s.commit()
        assert await ops.choose_mode(s, NOW) == ops.MODE_LIGHT
        assert await ops.choose_mode(s, NOW + timedelta(days=6)) == ops.MODE_FULL  # ya pasó una semana
        s.add(SyncRun(job_type=ops.JOB_TYPE, status="partial", started_at=NOW + timedelta(days=5), finished_at=NOW + timedelta(days=5),
                      stats={"mode": "full", "catalog_complete": False}))
        await s.commit()
        assert await ops.choose_mode(s, NOW + timedelta(days=5, hours=2)) == ops.MODE_LIGHT  # intento incompleto reciente
        assert await ops.choose_mode(s, NOW + timedelta(days=6)) == ops.MODE_FULL  # pasó el enfriamiento


@pytest.mark.asyncio
async def test_run_source_registered_and_reauth_is_reported_without_failing(maker, monkeypatch):
    assert "tiktok_creator" in sync_jobs.SOURCES and sync_jobs.LABELS["tiktok_creator"] == "TikTok Creator (catálogo de afiliados y pedidos)"
    assert "tiktok_creator" in sync_jobs.RUNNERS and "tiktok_creator" in sync_jobs.SETTINGS
    fake = FakeCreator(_catalog(5))
    fake.reauth = True
    monkeypatch.setattr(sync_jobs, "TikTokCreatorClient", lambda: fake)
    async with maker() as s:
        out = await sync_jobs.run_source(s, "tiktok_creator", 0, "manual")
        assert out["stats"]["needs_reauth"] is True and out["problems"] == ["hay que reautorizar"]
        run = (await s.scalars(select(SyncRun))).one()
        assert run.status == "partial" and run.stats["needs_reauth"] is True
        assert await _count(s, TikTokCreatorProduct) == 0  # sin autorización no se toca nada


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
async def seeded(maker, monkeypatch, tmp_path):
    """Primera carga completa de 3 productos, luego una ligera con 2 productos NUEVOS; nuestro catálogo con una vela."""
    token_file = tmp_path / "tokens_creator.json"
    token_file.write_text(json.dumps(_tokens(access_token_expire_in=time.time() + 86400)))
    monkeypatch.setattr(analytics, "_tokens_file", lambda: token_file)
    base = [
        _raw_product("A", units=9000, amount="5.00", rate=1500, title="Scented Soy Candle Lavender", price="25"),
        _raw_product("B", units=50, amount="1.00", rate=500, category=("Beauty", "Haircare"), title="Bond shampoo", price="20"),
        _raw_product("C", units=500, amount="4.00", rate=2000, category=("Home & Kitchen", "Kitchen Utensils"), title="Wooden spoon set", inventory=False),
    ]
    santa = [{"id": "S1", "title": "Santa", "status": {"review_status": "APPROVED", "inventory_status": "IN_STOCK"}}]
    async with maker() as s:
        s.add(TikTokShopProduct(product_id="OURS", title="Lavender Scented Candle", status="ACTIVATE", categories=["Home & Kitchen", "Candles"], min_price=Decimal(20)))
        await s.commit()
        await ops.sync_creator(s, FakeCreator(base, showcase=santa), mode=ops.MODE_FULL, now=NOW)  # type: ignore[arg-type]
        s.add(SyncRun(job_type=ops.JOB_TYPE, status="success", started_at=NOW, finished_at=NOW, stats={"mode": "full", "catalog_complete": True}))
        await s.commit()
        later = NOW + timedelta(hours=1)
        extra = base + [
            _raw_product("N1", units=900, amount="4.50", title="Christmas Elf Ornament", category=("Holiday", "Christmas Decor")),
            _raw_product("N2", units=0, amount="2.00", category=("Pets", "Toys"), title="Dog toy"),
        ]
        await ops.sync_creator(s, FakeCreator(extra, showcase=santa), mode=ops.MODE_LIGHT, now=later)  # type: ignore[arg-type]
    return maker


@pytest.mark.asyncio
async def test_empty_database_endpoints_do_not_fail(maker, monkeypatch):
    monkeypatch.setattr(analytics, "_tokens_file", lambda: None)
    async with _http(maker) as c:
        for path in ("status", "profile", "opportunities", "opportunities/summary", "orders", "showcase"):
            assert (await c.get(f"/api/tiktok-creator/{path}")).status_code == 200, path
        status = (await c.get("/api/tiktok-creator/status")).json()
        assert status["configured"] is False and status["connected"] is False and status["needs_reauth"] is False
        assert (await c.get("/api/tiktok-creator/orders")).json()["available"] is False
        assert (await c.get("/api/tiktok-creator/orders?days=3")).status_code == 422


@pytest.mark.asyncio
async def test_status_profile_and_showcase_endpoints(seeded):
    async with _http(seeded) as c:
        status = (await c.get("/api/tiktok-creator/status")).json()
        profile = (await c.get("/api/tiktok-creator/profile")).json()
        showcase = (await c.get("/api/tiktok-creator/showcase")).json()
    assert status["configured"] and status["needs_reauth"] is False and status["tokens"]["refresh_valid"] is True
    assert status["last_full_sync_at"] is not None and status["coverage"]["loaded"] == 5 and status["coverage"]["total"] == 5
    assert "old-access" not in json.dumps(status)  # ni un token en la respuesta
    assert profile["profile"]["username"] == "chk"
    assert {p["code"]: p["granted"] for p in profile["permissions"]} == {"LIVE_STREAM_PERMISSION": True, "SELF_SALE_PERMISSION": True}
    assert all(p["explanation"] for p in profile["permissions"])
    assert showcase["summary"]["products"] == 1 and showcase["summary"]["by_review_status"] == {"APPROVED": 1}


@pytest.mark.asyncio
async def test_status_asks_for_reauthorization_when_last_run_needed_it_or_refresh_expired(seeded, monkeypatch, tmp_path):
    async with seeded() as s:
        s.add(SyncRun(job_type=ops.JOB_TYPE, status="partial", started_at=NOW + timedelta(days=1), stats={"needs_reauth": True, "mode": "skipped"}))
        await s.commit()
    async with _http(seeded) as c:
        flagged = (await c.get("/api/tiktok-creator/status")).json()
    assert flagged["needs_reauth"] is True and flagged["connected"] is False and "reautorizar" in flagged["message"]
    expired = tmp_path / "expired.json"
    expired.write_text(json.dumps(_tokens(refresh_token_expire_in=time.time() - 5)))
    monkeypatch.setattr(analytics, "_tokens_file", lambda: expired)
    async with seeded() as s:
        assert (await analytics.status(s))["needs_reauth"] is True


@pytest.mark.asyncio
async def test_opportunities_ranks_filters_and_relates_to_our_catalog(seeded):
    async with _http(seeded) as c:
        body = (await c.get("/api/tiktok-creator/opportunities")).json()
        ids = [i["product_id"] for i in body["items"]]
        by_id = {i["product_id"]: i for i in body["items"]}
        assert body["total"] == 5 and ids[0] == "A" and by_id["C"]["score"] == 0.0  # C sin inventario
        assert by_id["A"]["relation"]["sells_same_category"] and by_id["A"]["relation"]["equivalent"]["product_id"] == "OURS"
        assert by_id["A"]["relation"]["equivalent"]["price_difference_pct"] == 25.0
        assert by_id["A"]["affinity_groups"] and by_id["B"]["affinity_groups"] == [] and by_id["A"]["score"] > by_id["B"]["score"]
        assert by_id["N1"]["is_new"] is True and by_id["A"]["is_new"] is False
        assert [i["product_id"] for i in (await c.get("/api/tiktok-creator/opportunities?q=candle")).json()["items"]] == ["A"]
        assert (await c.get("/api/tiktok-creator/opportunities?category=haircare")).json()["total"] == 1
        assert {i["product_id"] for i in (await c.get("/api/tiktok-creator/opportunities?min_commission=15")).json()["items"]} == {"A", "C"}
        assert (await c.get("/api/tiktok-creator/opportunities?min_units=1000")).json()["total"] == 1
        by_units = (await c.get("/api/tiktok-creator/opportunities?sort=units&limit=2&offset=1")).json()
        assert [i["product_id"] for i in by_units["items"]] == ["N1", "C"] and by_units["total"] == 5
        assert (await c.get("/api/tiktok-creator/opportunities?sort=nope")).status_code == 422
        assert (await c.get("/api/tiktok-creator/opportunities?limit=999")).status_code == 422


@pytest.mark.asyncio
async def test_suspect_products_are_hidden_unless_requested(seeded):
    async with seeded() as s:
        row = tc.build_product(_raw_product("BAD", price="3875820019684.21", units=2_000_000_364))
        assert row is not None
        await ops.upsert_products(s, [row], NOW)
        await s.commit()
    async with _http(seeded) as c:
        assert "BAD" not in [i["product_id"] for i in (await c.get("/api/tiktok-creator/opportunities")).json()["items"]]
        shown = (await c.get("/api/tiktok-creator/opportunities?include_suspect=true")).json()
        assert next(i for i in shown["items"] if i["product_id"] == "BAD")["score"] == 0.0
        assert (await c.get("/api/tiktok-creator/opportunities/summary")).json()["suspect_products"] == 1


@pytest.mark.asyncio
async def test_summary_reports_categories_new_products_and_changes(seeded):
    async with seeded() as s:  # dos fotos semanales con un cambio de precio y de comisión en A
        previous = ops.week_start(TODAY) - timedelta(weeks=1)
        s.add_all([
            TikTokCreatorProductHistory(product_id="A", snapshot_date=previous, price=Decimal(20), commission_rate=Decimal("0.10"), units_sold=1),
            TikTokCreatorProductHistory(product_id="B", snapshot_date=previous, price=Decimal(20), commission_rate=Decimal("0.05"), units_sold=1),
        ])
        await s.commit()
    async with _http(seeded) as c:
        body = (await c.get("/api/tiktok-creator/opportunities/summary")).json()
    assert body["products"] == 5 and body["top"][0]["product_id"] == "A"
    assert {i["product_id"] for i in body["new_this_week"]["items"]} == {"N1", "N2"} and body["new_this_week"]["count"] == 2
    categories = {c["category"]: c for c in body["by_category"]}
    assert categories["Home & Kitchen"]["products"] == 2 and categories["Beauty"]["products"] == 1
    assert body["changes"]["available"] is True
    assert body["changes"]["price"][0]["product_id"] == "A" and body["changes"]["price"][0]["change_pct"] == 25.0
    assert body["changes"]["commission"][0]["new"] == 0.15 and body["avg_commission_rate"] > 0


@pytest.mark.asyncio
async def test_new_products_need_a_first_complete_load(maker):
    async with maker() as s:
        await ops.sync_creator(s, FakeCreator(_catalog(5)), mode=ops.MODE_LIGHT, now=NOW)  # type: ignore[arg-type]
        assert await analytics.new_products(s) == []  # sin carga completa todo parecería "nuevo"


@pytest.mark.asyncio
async def test_orders_endpoint_lists_recent_orders_without_raw_payload(maker):
    async with maker() as s:
        rows = [tc.build_order({"id": "O1", "status": "PAID", "create_time": int((NOW - timedelta(days=2)).timestamp()), "commission": {"amount": "4.2", "currency": "USD"}, "buyer": "secreto"}),
                tc.build_order({"id": "O2", "status": "PAID", "create_time": int((NOW - timedelta(days=200)).timestamp())})]
        await ops.upsert_orders(s, [r for r in rows if r], NOW)
        await s.commit()
    async with _http(maker) as c:
        body = (await c.get("/api/tiktok-creator/orders?days=30")).json()
    assert body["summary"] == {"orders": 1, "commission": 4.2} and body["orders"][0]["order_id"] == "O1"
    assert "secreto" not in json.dumps(body)


# --- Centro de acciones ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_provider_groups_best_new_products_with_documented_impact(seeded):
    assert actions.KIND in action_center.providers()
    async with seeded() as s:
        items = await actions.creator_opportunity_actions(s)
        collected, errors = await action_center.collect(s, {actions.KIND})
    assert errors == [] and len(items) == 1 and len(collected) == 1
    item = items[0]
    assert item.kind == "tiktok_creator_opportunities" and item.target == "tiktok" and item.key == "tiktok_creator_opportunities:weekly"
    assert item.meta["product_ids"] == ["N1"]  # N2 (sin comisión útil ni afinidad) queda bajo el puntaje mínimo
    expected = 4.5 * max(900 / analytics.UNITS_LIFETIME_MONTHS, actions.MIN_MONTHLY_UNITS) * actions.ACHIEVABLE_SHARE
    assert item.impact_usd == pytest.approx(expected, abs=0.01) and "demanda mensual" in item.impact_basis


@pytest.mark.asyncio
async def test_action_provider_is_empty_without_data(maker):
    async with maker() as s:
        assert await actions.creator_opportunity_actions(s) == []
