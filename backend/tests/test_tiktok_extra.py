"""TikTok Shop (datos extra): constructores puros, upserts idempotentes, sincronización, API y acciones.

Todo con datos sintéticos y SQLite en memoria; ninguna prueba toca la red (el cliente es un doble).
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

from app import action_center, sync_jobs, tiktok_actions
from app import tiktok_extra_analytics as analytics
from app.connectors import tiktok_shop_extra as tse
from app.connectors.tiktok_shop import TikTokShopError
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    Account,
    GoldDailyProfit,
    OrderLine,
    Product,
    SalesOrder,
    Settlement,
    TikTokShopPackage,
    TikTokShopPayment,
    TikTokShopProduct,
    TikTokShopProductPerformance,
    TikTokShopWarehouse,
)
from app.tiktok_extra_api import router

NOW = datetime.now(UTC)
TODAY = NOW.date()


def _epoch(days_ago: float) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def _search_product(pid: str, seller: str, status: str = "ACTIVATE", audit: str = "APPROVED", tier: str = "GOOD") -> dict:
    return {
        "id": pid,
        "title": f"Producto {pid}",
        "status": status,
        "audit": {"status": audit, "pre_approved_reasons": []},
        "listing_quality_tier": tier,
        "is_not_for_sale": status != "ACTIVATE",
        "sales_regions": ["US"],
        "create_time": _epoch(100),
        "update_time": _epoch(2),
        "skus": [{"id": f"sku-{pid}", "seller_sku": seller, "price": {"currency": "USD", "tax_exclusive_price": "20.00"},
                  "inventory": [{"quantity": 10, "warehouse_id": "w1"}]}],
    }


class FakeShop:
    """Doble de TikTokShopClient: enruta por ruta y registra los métodos usados."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_paths: set[str] = set()

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None, **_: Any) -> dict:
        self.calls.append((method, path))
        if any(path.startswith(p) for p in self.fail_paths):
            raise TikTokShopError(40006, "sin permiso")
        return self.route(method, path, params or {})

    def iter_statements(self, start: datetime, end: datetime):
        yield {"id": "ST1", "payment_id": "PAY1"}
        yield {"id": "ST2", "payment_id": ""}

    def route(self, method: str, path: str, params: dict) -> dict:
        if path == "/product/202312/products/search":
            if params.get("page_token") == "t2":
                return {"products": [_search_product("3", "SKU-C")]}
            return {"products": [_search_product("1", "SKU-A"), _search_product("2", "SKU-B", "FAILED", "FAILED", "POOR")],
                    "next_page_token": "t2"}
        if path.startswith("/product/202309/products/"):
            return {"main_images": [{"urls": ["https://img/x.jpg"], "thumb_urls": ["https://img/t.jpg"]}],
                    "brand": {"name": "Marca"}, "audit": {"status": "APPROVED"},
                    "category_chains": [{"local_name": "Hogar", "is_leaf": False}, {"local_name": "Velas", "is_leaf": True}],
                    "skus": [{"id": f"sku-{path.rsplit('/', 1)[1]}", "seller_sku": {"1": "SKU-A", "2": "SKU-B", "3": "SKU-C"}[path.rsplit('/', 1)[1]],
                              "price": {"currency": "USD", "sale_price": "19.50"}, "inventory": [{"quantity": 4}]}]}
        if path == "/analytics/202405/shop_products/performance":
            return {"products": [
                {"id": 1, "gmv": {"amount": "1000.00"}, "orders": 50, "units_sold": 60, "click_through_rate": "0.0300"},
                {"id": 2, "gmv": {"amount": "10.00"}, "orders": 1, "units_sold": 1, "click_through_rate": "0.0100"},
                {"id": 3, "gmv": {"amount": "40.00"}, "orders": 2, "units_sold": 2, "click_through_rate": "0.0050"},
            ]}
        if path.startswith("/analytics/202405/shop_products/"):
            pid = path.split("/")[4]
            views, visitors = {"1": (5000, 2000), "2": (100, 60), "3": (9000, 4000)}[pid]
            return {"performance": {"intervals": [{
                "impressions": views * 20, "page_views": views, "avg_page_visitors": visitors,
                "gmv_breakdowns": [{"type": "VIDEO", "amount": "5.0"}, {"type": "LIVE", "amount": "0"}, {"type": "PRODUCT_CARD", "amount": "1"}],
            }]}}
        if path == "/analytics/202406/shop_skus/performance":
            return {"skus": [{"id": "sku-1", "product_id": 1, "gmv": {"amount": "1000"}, "sku_orders": 50, "units_sold": 60},
                             {"id": "sku-2", "product_id": 2, "gmv": {"amount": "10"}, "sku_orders": 1, "units_sold": 1}]}
        if path == "/promotion/202309/activities/search":
            return {"activities": [{"id": "A1", "title": "Flash", "activity_type": "FLASHSALE", "product_level": "PRODUCT",
                                    "status": "ONGOING", "duration_type": "FIXED", "begin_time": _epoch(20), "end_time": _epoch(-5),
                                    "create_time": _epoch(21), "update_time": _epoch(21)},
                                   {"id": "A2", "title": "Envío", "activity_type": "SHIPPING_DISCOUNT", "product_level": "SHOP",
                                    "status": "ONGOING", "begin_time": _epoch(10), "end_time": _epoch(-5)}]}
        if path.startswith("/promotion/202309/activities/"):
            if path.endswith("A1"):
                return {"products": [{"id": "1", "activity_price": {"amount": "9.00", "currency": "USD"}, "quantity_limit": 5}]}
            return {"products": [], "discount": {"type": "FREE"}}
        if path == "/promotion/202406/coupons/search":
            return {"coupons": [{"id": "C1", "title": "10% off", "status": "ONGOING", "discount": {"type": "PERCENT_OFF", "percentage": "10", "max_discount": {"amount": "20"}},
                                 "redemption_duration": {"start_time": _epoch(5), "end_time": _epoch(-5)}, "create_time": _epoch(5)}]}
        if path.startswith("/promotion/202406/coupons/"):
            return {"coupon": {"id": "C1", "product_ids": ["1"], "usage_stats": {"claimed_count": 20, "redeemed_count": 5}}}
        if path == "/fulfillment/202309/packages/search":
            def pkg(i: str, status: str, created: float, updated: float, order: str) -> dict:
                return {"id": i, "status": status, "tracking_number": f"TRK{i}", "create_time": _epoch(created), "update_time": _epoch(updated),
                        "orders": [{"id": order, "skus": [{"id": "s", "quantity": 2}]}]}
            return {"packages": [pkg("K1", "COMPLETED", 10, 8, "O1"), pkg("K2", "FULFILLING", 12, 9, "O2"),
                                 pkg("K3", "PROCESSING", 1, 0.5, "O3"), pkg("K4", "CANCELLED", 5, 5, "O4")]}
        if path.startswith("/fulfillment/202309/orders/") and path.endswith("/tracking"):
            return {"tracking": [{"action_code": 21001, "description": "Recibido por el transportista", "update_time_millis": _epoch(9) * 1000}]}
        if path == "/finance/202309/payments":
            return {"payments": [
                {"id": "PAY1", "status": "PAID", "amount": {"currency": "USD", "value": "95.00"}, "settlement_amount": {"currency": "USD", "value": "95.00"},
                 "reserve_amount": {"currency": "USD", "value": "0"}, "exchange_rate": "1", "create_time": _epoch(10), "paid_time": _epoch(8),
                 "bank_account": "CHK****4321"},
                {"id": "PAY2", "status": "FAILED", "amount": {"currency": "USD", "value": "30.00"}, "settlement_amount": {"currency": "USD", "value": "30.00"},
                 "reserve_amount": {"currency": "USD", "value": "5"}, "create_time": _epoch(3), "bank_account": "CHK****4321"}]}
        if path == "/finance/202309/withdrawals":
            return {"withdrawals": [{"id": "W1", "amount": "50.5", "currency": "USD", "status": "SUCCESS", "type": "WITHDRAW", "create_time": _epoch(20)}]}
        if path == "/logistics/202309/warehouses":
            return {"warehouses": [{"id": "WH1", "name": "Bodega", "type": "SALES_WAREHOUSE", "sub_type": "DOMESTIC_WAREHOUSE", "effect_status": "ENABLED",
                                    "is_default": True, "address": {"city": "Miami", "state": "Florida", "region_code": "US", "postal_code": "33101",
                                                                    "address_line1": "Calle Privada 1", "phone_number": "555", "contact_person": "Ana"}}]}
        if path == "/event/202309/webhooks":
            return {"webhooks": [{"event_type": "PACKAGE_UPDATE", "address": "https://hook.example/x", "create_time": _epoch(30)}]}
        raise AssertionError(f"ruta inesperada {path}")


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def _seed_sales(session) -> None:
    """Ventas de TikTok: SKU-A rentable y con promoción (1 u/día antes, 3 u/día durante); SKU-B (rechazado) vendía bien."""
    account = Account(channel_code="tiktok_shop", name="tiktok_shop_us")
    a, b = Product(sku="SKU-A", name="Vela A"), Product(sku="SKU-B", name="Vela B")
    session.add_all([account, a, b])
    await session.flush()
    session.add(GoldDailyProfit(profit_date=TODAY - timedelta(days=3), channel_code="tiktok_shop", account_id=account.id, product_id=a.id,
                                orders=25, units=25, gross_sales=Decimal(500), cogs=Decimal(200)))
    session.add(GoldDailyProfit(profit_date=TODAY - timedelta(days=40), channel_code="tiktok_shop", account_id=account.id, product_id=b.id,
                                orders=60, units=60, gross_sales=Decimal(900)))
    for offset in range(35):  # 0..20 días atrás = durante la promoción; 21..34 = línea base
        order = SalesOrder(account_id=account.id, external_order_id=f"SO{offset}", order_date=TODAY - timedelta(days=offset))
        session.add(order)
        await session.flush()
        session.add(OrderLine(order_id=order.id, account_id=account.id, order_date=order.order_date, product_id=a.id, channel_sku="SKU-A",
                              units=3 if offset <= 20 else 1, gross_sales=Decimal(60 if offset <= 20 else 20)))
    session.add(Settlement(account_id=account.id, external_id="ST1", statement_date=TODAY - timedelta(days=11), net_payout=Decimal(100)))
    session.add(Settlement(account_id=account.id, external_id="ST9", statement_date=TODAY - timedelta(days=2), net_payout=Decimal(40)))
    await session.commit()


async def _sync(maker, monkeypatch, fake: FakeShop | None = None) -> tuple[FakeShop, dict]:
    fake = fake or FakeShop()
    monkeypatch.setattr(sync_jobs, "TikTokShopClient", lambda: fake)
    async with maker() as s:
        out = await sync_jobs.run_source(s, "tiktok_shop_extra", 90, "manual")
    return fake, out


@pytest_asyncio.fixture
async def synced(maker, monkeypatch):
    async with maker() as s:
        await _seed_sales(s)
    fake, out = await _sync(maker, monkeypatch)
    assert out["problems"] == []
    return maker, fake, out


# --- constructores puros -----------------------------------------------------------------------------


def test_build_product_takes_image_and_prices_from_detail():
    row, skus = tse.build_product(_search_product("1", "SKU-A"), FakeShop().route("GET", "/product/202309/products/1", {}))
    assert row["main_image_url"] == "https://img/x.jpg" and row["thumb_url"] == "https://img/t.jpg" and row["image_count"] == 1
    assert row["categories"] == ["Hogar", "Velas"] and row["min_price"] == 19.5 and row["sku_count"] == 1
    assert skus == [{"sku_id": "sku-1", "product_id": "1", "seller_sku": "SKU-A", "price": 19.5, "currency": "USD", "quantity": 4}]


def test_build_product_without_detail_keeps_search_data():
    row, skus = tse.build_product(_search_product("2", "SKU-B", "FAILED", "FAILED", "POOR"), None)
    assert row["main_image_url"] is None and row["audit_status"] == "FAILED" and skus[0]["price"] == 20.0 and skus[0]["quantity"] == 10


def test_build_product_performance_conversion_needs_visitors():
    listed = {"id": 1, "gmv": {"amount": "100"}, "orders": 10, "units_sold": 12, "click_through_rate": "0.0272"}
    window = (TODAY - timedelta(days=30), TODAY)
    full = tse.build_product_performance(listed, {"impressions": 1000, "page_views": 400, "avg_page_visitors": 200,
                                                  "gmv_breakdowns": [{"type": "VIDEO", "amount": "7"}]}, window, TODAY)
    assert full["conversion_rate"] == 0.05 and full["gmv_video"] == 7.0 and full["click_through_rate"] == 0.0272
    bare = tse.build_product_performance(listed, None, window, TODAY)
    assert bare["conversion_rate"] is None and bare["impressions"] is None  # sin visitantes no hay conversión (no un cero)


def test_build_coupon_percent_and_amount_and_symbol_currency():
    pct = tse.build_coupon({"id": "1", "discount": {"type": "PERCENT_OFF", "percentage": "50", "max_discount": {"amount": "60", "currency": "$"}}}, None)
    assert pct["discount_percent"] == 50 and pct["max_discount"] == 60
    amt = tse.build_coupon({"id": "2", "discount": {"type": "AMOUNT_OFF", "reduction_amount": {"amount": "6", "currency": "$"}},
                            "threshold": {"type": "MIN_SPEND", "min_spend": {"amount": "30", "currency": "$"}}}, {"product_ids": [1, 2]})
    assert amt["discount_amount"] == 6 and amt["min_spend"] == 30 and amt["currency"] == "USD" and amt["product_ids"] == ["1", "2"]


def test_build_package_infers_delivery_and_last_event():
    done = tse.build_package({"id": "K", "status": "COMPLETED", "create_time": 1000, "update_time": 5000, "orders": [{"id": "O", "skus": [{"quantity": 3}]}]})
    assert done["delivered_at"] == datetime.fromtimestamp(5000, tz=UTC) and done["unit_count"] == 3 and done["order_ids"] == ["O"]
    moving = tse.build_package({"id": "M", "status": "FULFILLING", "orders": []},
                               [{"description": "a", "update_time_millis": 1_000_000}, {"description": "b", "update_time_millis": 2_000_000}])
    assert moving["delivered_at"] is None and moving["last_event"] == "b"


def test_build_payment_keeps_only_last_four_of_bank_account():
    row = tse.build_payment({"id": "P", "status": "PAID", "bank_account": "CHK-0099887766", "amount": {"currency": "USD", "value": "5"},
                             "settlement_amount": {"value": "5"}, "reserve_amount": {"value": "0"}}, ["S1"])
    assert row["bank_last4"] == "7766" and "0099" not in str(row.values()) and row["statement_ids"] == ["S1"]
    assert tse.build_payment({"id": "Q", "bank_account": "12"}, [])["bank_last4"] is None


def test_statements_by_payment_ignores_unlinked():
    grouped = tse.statements_by_payment([{"id": "b", "payment_id": "P"}, {"id": "a", "payment_id": "P"}, {"id": "c", "payment_id": ""}])
    assert grouped == {"P": ["a", "b"]}


def test_build_warehouse_drops_street_and_contact():
    row = tse.build_warehouse(FakeShop().route("GET", "/logistics/202309/warehouses", {})["warehouses"][0])
    assert row["city"] == "Miami" and row["postal_code"] == "33101"
    assert "Calle" not in str(row.values()) and "Ana" not in str(row.values()) and "555" not in str(row.values())


def test_build_webhook_requires_event_and_address():
    assert tse.build_webhook({"event_type": "X"}) is None
    assert tse.build_webhook({"event_type": "X", "address": "https://a"})["event_type"] == "X"


def test_pure_rules_stuck_bucket_and_quantile():
    assert analytics.is_problem_product("ACTIVATE", "FAILED") and analytics.is_problem_product("DELETED", "NONE")
    assert not analytics.is_problem_product("ACTIVATE", "APPROVED")
    old = NOW - timedelta(days=9)
    assert analytics.is_stuck("FULFILLING", old, NOW, 5) and not analytics.is_stuck("COMPLETED", old, NOW, 5)
    assert not analytics.is_stuck("FULFILLING", None, NOW, 5)  # sin fecha no se acusa a nadie
    assert [analytics.delivery_bucket(d) for d in (0.5, 3, 6, 10, 30)] == ["0-2 días", "3-4 días", "5-7 días", "8-14 días", "15+ días"]
    assert analytics.quantile([], 0.5) == 0.0 and analytics.quantile([1, 2, 3, 4], 0.75) == 4


def test_promotion_roi_compares_against_baseline_and_handles_no_baseline():
    start = date(2026, 11, 10)
    daily = {start - timedelta(days=i): (1.0, 10.0) for i in range(1, 15)}
    daily.update({start + timedelta(days=i): (3.0, 30.0) for i in range(5)})
    roi = analytics.promotion_roi(daily, (start, start + timedelta(days=4)))
    assert roi["units_per_day_during"] == 3.0 and roi["units_per_day_before"] == 1.0 and roi["uplift"] == 2.0
    assert analytics.promotion_roi({start: (2.0, 20.0)}, (start, start))["uplift"] is None  # sin línea base: no se inventa


# --- fetch / sincronización --------------------------------------------------------------------------


def test_fetch_snapshot_is_read_only_and_follows_pagination():
    fake = FakeShop()
    snap = tse.fetch_extra_snapshot(fake, NOW, 90)  # type: ignore[arg-type]
    assert {m for m, _ in fake.calls} <= {"GET", "POST"}
    assert all(p.endswith("/search") for m, p in fake.calls if m == "POST")  # los POST son solo consultas
    assert [p["product_id"] for p in snap.products] == ["1", "2", "3"]  # siguió next_page_token
    assert len(snap.packages) == 4 and len(snap.promotion_products) == 1 and snap.problems == []
    k2 = next(p for p in snap.packages if p["package_id"] == "K2")
    assert k2["last_event"] == "Recibido por el transportista"  # seguimiento solo de los no terminados
    assert next(p for p in snap.packages if p["package_id"] == "K1")["last_event"] is None


def test_failing_section_is_reported_without_stopping_others():
    fake = FakeShop()
    fake.fail_paths = {"/promotion/202406/coupons"}
    snap = tse.fetch_extra_snapshot(fake, NOW, 90)  # type: ignore[arg-type]
    assert snap.coupons == [] and snap.problems and snap.problems[0].startswith("cupones")
    assert "cupones" in snap.failed and len(snap.products) == 3 and len(snap.payments) == 2


@pytest.mark.asyncio
async def test_run_source_is_idempotent_and_registered(maker, monkeypatch):
    assert "tiktok_shop_extra" in sync_jobs.SOURCES and sync_jobs.LABELS["tiktok_shop_extra"].startswith("TikTok Shop (")
    _, first = await _sync(maker, monkeypatch)
    _, again = await _sync(maker, monkeypatch)
    assert first["problems"] == [] and first["stats"] == again["stats"]
    assert first["stats"] == {"products": 3, "product_skus": 3, "product_performance": 3, "sku_performance": 2, "promotions": 2,
                              "promotion_products": 1, "coupons": 1, "packages": 4, "payments": 2, "withdrawals": 1, "warehouses": 1, "webhooks": 1}
    async with maker() as s:
        assert await _count(s, TikTokShopProduct) == 3 and await _count(s, TikTokShopPackage) == 4
        assert await _count(s, TikTokShopProductPerformance) == 3 and await _count(s, TikTokShopPayment) == 2


@pytest.mark.asyncio
async def test_sync_keeps_tracking_and_prunes_snapshots_but_not_on_failure(maker, monkeypatch):
    await _sync(maker, monkeypatch)
    again = FakeShop()
    again.fail_paths = {"/fulfillment/202309/orders"}
    _, out = await _sync(maker, monkeypatch, again)
    assert out["problems"] == []  # el seguimiento es opcional: sin él no hay problema
    async with maker() as s:
        k2 = await s.get(TikTokShopPackage, "K2")
        assert k2.last_event == "Recibido por el transportista"  # se conservó el último evento
    broken = FakeShop()
    broken.fail_paths = {"/logistics/202309/warehouses"}
    _, out = await _sync(maker, monkeypatch, broken)
    assert out["problems"][0].startswith("almacenes")
    async with maker() as s:
        assert await _count(s, TikTokShopWarehouse) == 1  # un fallo de lectura no borra la última foto


@pytest.mark.asyncio
async def test_payment_stores_no_full_bank_account(synced):
    maker, *_ = synced
    async with maker() as s:
        assert (await s.get(TikTokShopPayment, "PAY1")).bank_last4 == "4321"


# --- API ---------------------------------------------------------------------------------------------


def _client(maker) -> httpx.AsyncClient:
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()  # el router se prueba montado aparte
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_empty_database_endpoints_do_not_fail(maker):
    async with _client(maker) as c:
        for path in ("product-performance", "sku-performance", "catalog", "promotions", "coupons", "packages", "payments",
                     "withdrawals", "warehouses", "webhooks", "overview"):
            assert (await c.get(f"/api/tiktok/{path}")).status_code == 200, path
        assert (await c.get("/api/tiktok/product-performance")).json()["available"] is False


@pytest.mark.asyncio
async def test_days_is_validated(maker):
    async with _client(maker) as c:
        assert (await c.get("/api/tiktok/packages?days=3")).status_code == 422
        assert (await c.get("/api/tiktok/packages?days=999")).status_code == 422


@pytest.mark.asyncio
async def test_product_performance_endpoint(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        body = (await c.get("/api/tiktok/product-performance")).json()
    assert body["available"] and body["summary"]["products"] == 3 and body["summary"]["gmv"] == 1050.0
    first = body["products"][0]
    assert first["rank"] == 1 and first["product_id"] == "1"
    by_id = {p["product_id"]: p for p in body["products"]}
    assert by_id["1"]["profit"] == 300.0 and by_id["1"]["linked_to_master"] and by_id["1"]["conversion_rate"] == 0.025
    assert by_id["1"]["gmv_by_source"]["video"] == 5.0
    assert [p["product_id"] for p in body["traffic_low_conversion"]] == ["3"]  # mucho tráfico, casi nada de conversión
    assert by_id["3"]["linked_to_master"] is False and by_id["3"]["profit"] is None


@pytest.mark.asyncio
async def test_sku_performance_endpoint(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        body = (await c.get("/api/tiktok/sku-performance")).json()
    a = next(s for s in body["skus"] if s["seller_sku"] == "SKU-A")
    assert a["master_sku"] == "SKU-A" and a["profit"] == 300.0 and a["profit_per_unit"] == 12.0 and a["margin"] == 0.6
    assert body["summary"]["skus"] == 2


@pytest.mark.asyncio
async def test_catalog_endpoint_lists_images_and_rejected_with_sales(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        body = (await c.get("/api/tiktok/catalog?days=90")).json()
    assert body["summary"]["by_status"] == {"ACTIVATE": 2, "FAILED": 1} and body["summary"]["problems"] == 1
    rejected = body["problem_products"][0]
    assert rejected["product_id"] == "2" and rejected["settled_sales"] == 900.0 and rejected["audit_status"] == "FAILED"
    assert all(p["image_url"] == "https://img/x.jpg" for p in body["products"])


@pytest.mark.asyncio
async def test_promotions_endpoint_measures_uplift(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        body = (await c.get("/api/tiktok/promotions")).json()
    flash = next(a for a in body["activities"] if a["activity_id"] == "A1")
    assert flash["products"][0]["activity_price"] == 9.0 and flash["roi_scope"] == "products"
    assert flash["roi"]["units_per_day_during"] == 3.0 and flash["roi"]["units_per_day_before"] == 1.0 and flash["roi"]["uplift"] == 2.0
    shipping = next(a for a in body["activities"] if a["activity_id"] == "A2")
    assert shipping["roi_scope"] == "shop" and shipping["roi"] is not None


@pytest.mark.asyncio
async def test_coupons_endpoint(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        body = (await c.get("/api/tiktok/coupons")).json()
    coupon = body["coupons"][0]
    assert coupon["discount"] == "10 % de descuento (máx. $20)" and coupon["redemption_rate"] == 0.25 and coupon["product_count"] == 1


@pytest.mark.asyncio
async def test_packages_endpoint_flags_stuck_and_rates(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        body = (await c.get("/api/tiktok/packages?days=30&stuck_days=5")).json()
        loose = (await c.get("/api/tiktok/packages?days=30&stuck_days=60")).json()
    assert body["summary"]["by_status"] == {"COMPLETED": 1, "FULFILLING": 1, "PROCESSING": 1, "CANCELLED": 1}
    assert [s["package_id"] for s in body["stuck"]] == ["K2"] and loose["summary"]["stuck"] == 0
    assert body["summary"]["delivery_rate"] == pytest.approx(1 / 3) and body["summary"]["avg_delivery_days"] == pytest.approx(2.0, abs=0.01)
    assert {d["bucket"]: d["count"] for d in body["delivery_distribution"]}["0-2 días"] == 1
    assert sum(d["total"] for d in body["by_day"]) == 4


@pytest.mark.asyncio
async def test_payments_endpoint_reconciles_against_settlements(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        body = (await c.get("/api/tiktok/payments")).json()
    by_id = {p["payment_id"]: p for p in body["payments"]}
    assert by_id["PAY1"]["settled_amount"] == 100.0 and by_id["PAY1"]["difference"] == -5.0 and by_id["PAY1"]["reconciled"] is False
    assert by_id["PAY2"]["checkable"] is False and by_id["PAY2"]["difference"] is None  # sin liquidaciones no se inventa diferencia
    assert body["summary"]["pending_amount"] == 30.0 and body["summary"]["reconciliation"]["difference"] == -5.0
    assert by_id["PAY1"]["bank_last4"] == "4321"


@pytest.mark.asyncio
async def test_withdrawals_warehouses_webhooks_endpoints(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        w = (await c.get("/api/tiktok/withdrawals")).json()
        wh = (await c.get("/api/tiktok/warehouses")).json()
        hooks = (await c.get("/api/tiktok/webhooks")).json()
    assert w["summary"]["total_amount"] == 50.5 and w["by_month"][0]["count"] == 1
    assert wh["warehouses"][0]["city"] == "Miami" and "address" not in str(wh["warehouses"][0]).lower()
    assert hooks["subscriptions"][0]["event_type"] == "PACKAGE_UPDATE"
    assert "PACKAGE_UPDATE" not in hooks["unsubscribed"] and "ORDER_STATUS_CHANGE" in hooks["unsubscribed"]


@pytest.mark.asyncio
async def test_overview_endpoint(synced):
    maker, *_ = synced
    async with _client(maker) as c:
        body = (await c.get("/api/tiktok/overview?days=30")).json()
    assert body["kpis"]["sales"] == 500.0 and body["kpis"]["net_profit"] == 300.0 and body["kpis"]["orders"] == 25
    assert body["rejected_products"]["count"] == 1 and body["stuck_packages"]["count"] == 1
    assert body["payments"]["pending"] == 1 and body["receivable"]["unpaid_settlements_amount"] == 40.0
    assert body["receivable"]["pending_payments_amount"] == 30.0 and body["last_sync_at"] is not None


# --- Centro de acciones ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_providers_registered_and_produce_actions(synced):
    maker, *_ = synced
    kinds = set(action_center.providers())
    assert {"tiktok_listing_rejected", "tiktok_packages_stuck", "tiktok_payment_issue"} <= kinds
    async with maker() as s:
        listing = await tiktok_actions.listing_rejected_actions(s)
        stuck = await tiktok_actions.packages_stuck_actions(s)
        pay = await tiktok_actions.payment_issue_actions(s)
    assert [a.entity for a in listing] == ["2"] and listing[0].impact_usd == pytest.approx(900 / 90 * 30) and listing[0].target == "tiktok"
    assert [a.entity for a in stuck] == ["K2"] and stuck[0].impact_usd > 0
    assert {a.entity for a in pay} == {"PAY1", "PAY2"} and {a.target for a in pay} == {"tiktok"}
    assert all(a.key.startswith(f"{a.kind}:") for a in listing + stuck + pay)
    async with maker() as s:
        items, errors = await action_center.collect(s, {"tiktok_listing_rejected", "tiktok_packages_stuck", "tiktok_payment_issue"})
    assert errors == [] and len(items) == 4


@pytest.mark.asyncio
async def test_actions_are_empty_without_data(maker):
    async with maker() as s:
        assert await tiktok_actions.listing_rejected_actions(s) == []
        assert await tiktok_actions.packages_stuck_actions(s) == []
        assert await tiktok_actions.payment_issue_actions(s) == []
