"""Apartado Walmart: constructores puros, upserts idempotentes, sincronización, API, caché y acciones.

Todo con datos sintéticos y SQLite en memoria; ninguna prueba toca la red real (el cliente usa MockTransport).
"""

import io
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import sync_jobs, walmart_actions, walmart_analytics
from app.connectors import walmart_insights as wi
from app.connectors.walmart import WalmartClient, WalmartError, WalmartSettings
from app.db import create_dev_schema, get_session, make_engine
from app.models import (
    Account,
    GoldDailyProfit,
    ListingStock,
    Product,
    SellerPerformance,
    WalmartFeed,
    WalmartItemSnapshot,
    WalmartListingQuality,
    WalmartWfsInventory,
    WalmartWfsInventoryHistory,
)
from app.walmart_api import optional_walmart_client, router, walmart_client
from app.walmart_ops import (
    replace_item_snapshots,
    replace_wfs_inventory,
    upsert_feeds,
    upsert_listing_quality,
)

NOW = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
TODAY = NOW.date()
ITEM_CSV = (
    "SKU,Item ID,Product Name,Publish Status,Lifecycle Status,Status Change Reason,Fulfillment Type,Price,"
    "Buy Box Item Price,Buy Box Eligible,Repricer Strategy,Repricer Status,Minimum Seller Allowed Price,"
    "Maximum Seller Allowed Price,Reviews Count,Average Rating\n"
    "SKU-A,1,Prod A,PUBLISHED,ACTIVE,,WFS Eligible,33.97,33.97,Yes,Competitive match (external only),,4.99,100.99,3,4.5\n"
    "SKU-B,2,Prod B,UNPUBLISHED,RETIRED,Offer end date has passed,Seller Fulfilled,,,No,,,,,0,\n"
)


def _zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def _ms(delta_hours: float) -> int:
    return int((NOW + timedelta(hours=delta_hours)).timestamp() * 1000)


def _order(po: str, ship_hours: float, sku: str = "SKU-A", qty: int = 1, price: float = 20.0) -> dict:
    return {
        "purchaseOrderId": po,
        "customerOrderId": f"C{po}",
        "orderDate": _ms(-30),
        "shippingInfo": {
            "phone": "555-0100",
            "estimatedShipDate": _ms(ship_hours),
            "estimatedDeliveryDate": _ms(ship_hours + 72),
            "methodCode": "Standard",
            "postalAddress": {"name": "Persona Privada", "address1": "Calle 1", "state": "FL"},
        },
        "orderLines": {
            "orderLine": [
                {
                    "item": {"sku": sku, "productName": "Prod"},
                    "orderLineQuantity": {"amount": str(qty)},
                    "charges": {"charge": [{"chargeType": "PRODUCT", "chargeAmount": {"amount": price}}]},
                    "orderLineStatuses": {"orderLineStatus": [{"status": "Acknowledged"}]},
                }
            ]
        },
    }


class FakeWalmart:
    """Enrutador de MockTransport con las respuestas de Walmart y un contador de llamadas por ruta."""

    def __init__(self, orders: list[dict] | None = None, report_status: str = "READY") -> None:
        self.calls: dict[str, int] = {}
        self.orders = orders if orders is not None else []
        self.report_status = report_status
        self.methods: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] = self.calls.get(path, 0) + 1
        self.methods.add(request.method)
        if path == "/v3/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 900})
        if path == "/r.zip":
            return httpx.Response(200, content=_zip("ItemReport.csv", ITEM_CSV))
        body = self.route(path, dict(request.url.params))
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, text="Not Found")

    def route(self, path: str, params: dict[str, str]) -> dict | None:
        if path.endswith("/listingQuality/score"):
            return {
                "payload": {
                    "score": {
                        "offerScore": 80.0,
                        "ratingReviewScore": 10.0,
                        "contentScore": 90.0,
                        "priceScore": 60.0,
                        "shippingScore": 100.0,
                        "transactibilityScore": 67.0,
                    },
                    "postPurchaseQuality": {"itemDefectCnt": 2, "defectRatio": 0.5},
                    "listingQuality": 61.7,
                },
                "status": "OK",
            }
        if path == "/v3/fulfillment/inventory":
            nodes = [
                {"sku": "SKU-A", "offerId": "o1", "shipNodes": [{"availToSellQty": 0, "onHandQty": 0, "shipNode": None, "shipNodeType": "WFSFulfilled", "modifiedDate": "2026-11-24T04:00:00.000Z"}]},
                {"sku": "SKU-C", "offerId": "o3", "shipNodes": [{"availToSellQty": 10, "onHandQty": 14, "shipNode": None, "shipNodeType": "WFSFulfilled", "modifiedDate": "2026-11-24T04:00:00.000Z"}]},
            ]
            return {"payload": {"inventory": nodes}, "headers": {"offset": 0, "limit": 100, "totalCount": 2}}
        if path == "/v3/feeds":
            return {"totalResults": 2, "results": {"feed": [
                {"feedId": "f1", "feedType": "PRICE_AND_PROMOTION", "feedStatus": "PROCESSED", "itemsReceived": 4, "itemsSucceeded": 3, "itemsFailed": 1, "feedDate": _ms(-5)},
                {"feedId": "f2", "feedType": "MP_ITEM", "feedStatus": "PROCESSED", "itemsReceived": 2, "itemsSucceeded": 2, "itemsFailed": 0, "feedDate": _ms(-6)},
            ]}}
        if path == "/v3/settings/shipping/templates":
            return {"shippingTemplates": [{"id": "T1", "name": "Default", "type": "DEFAULT", "rateModelType": "PER_SHIPMENT_PRICING", "status": "ACTIVE"}]}
        if path == "/v3/settings/shipping/templates/T1":
            return {"id": "T1", "shippingMethods": [{"shipMethod": "STANDARD", "status": "ACTIVE", "configurations": [{
                "regions": [{"regionCode": "C"}], "transitTime": 3,
                "perShippingCharge": {"unitOfMeasure": "LB", "shippingAndHandling": {"amount": 2, "currency": "USD"}, "chargePerItem": {"amount": 1, "currency": "USD"}}}]}]}
        if path == "/v3/repricer/strategies":
            return {"strategyCollections": [{"repricerStrategy": "Competitive match (external only)", "strategyCollectionId": "s1", "enabled": True, "assignedCount": 1, "strategies": [{"strategyType": "X"}]}]}
        if path == "/v3/reports/reportRequests":
            if params.get("reportType") == "ITEM":
                return {"requests": [{"requestId": "req-item-0001", "requestStatus": self.report_status, "reportType": "ITEM", "reportVersion": "v4", "requestSubmissionDate": "2026-11-24T09:01:09Z", "reportGenerationDate": "2026-11-24T09:33:21Z"}]}
            return {"requests": []}
        if path == "/v3/reports/downloadReport":
            return {"downloadURL": "https://files.test/r.zip"}
        if path == "/v3/orders/released":
            return {"list": {"meta": {"totalCount": len(self.orders), "limit": 100}, "elements": {"order": self.orders}}}
        return None

    def client(self) -> WalmartClient:
        http = httpx.Client(base_url="https://api.test", transport=httpx.MockTransport(self.handler))
        return WalmartClient(WalmartSettings(client_id="id", client_secret="secret"), http)


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_cache():
    walmart_analytics.OPEN_ORDERS_CACHE.clear()
    yield
    walmart_analytics.OPEN_ORDERS_CACHE.clear()


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def _seed_sales(session) -> None:
    """Ventas de Walmart en la capa oro: SKU-A vende 30 unidades en 30 días (1/día)."""
    account = Account(channel_code="walmart", name="walmart_1")
    product = Product(sku="SKU-A", name="Prod A")
    session.add_all([account, product])
    await session.flush()
    session.add(
        GoldDailyProfit(
            profit_date=TODAY - timedelta(days=1), channel_code="walmart", account_id=account.id,
            product_id=product.id, orders=30, units=30, gross_sales=Decimal(600),
        )
    )
    session.add(ListingStock(account_id=account.id, sku="SKU-A", avail_to_sell=7))
    session.add(
        SellerPerformance(account_id=account.id, snapshot_date=TODAY, window_days=30, otd=Decimal("96.5"), ots=Decimal(98))
    )
    await session.commit()


# --- constructores puros -------------------------------------------------------------------------------


def test_build_listing_quality_handles_missing_payload_and_blank_scores():
    assert wi.build_listing_quality({"status": "ERR"}, TODAY) is None
    row = wi.build_listing_quality({"payload": {"score": {"offerScore": 80.09, "priceScore": ""}, "listingQuality": 61.73}}, TODAY)
    assert row and row["offer_score"] == 80.09 and row["price_score"] is None and row["item_defect_count"] is None


def test_build_wfs_inventory_uses_node_type_when_no_node_id():
    rows = wi.build_wfs_inventory([
        {"sku": "A", "offerId": "o", "shipNodes": [{"availToSellQty": 3, "onHandQty": 5, "shipNode": None, "shipNodeType": "WFSFulfilled"}]},
        {"sku": " ", "shipNodes": [{}]},
        {"sku": "B", "shipNodes": [{"availToSellQty": 1, "shipNode": "N9"}, {"availToSellQty": 2, "shipNode": "N8"}]},
    ])
    assert [(r["sku"], r["node"], r["avail_to_sell"]) for r in rows] == [("A", "WFSFulfilled", 3), ("B", "N8", 2), ("B", "N9", 1)]


def test_build_feeds_dedupes_and_converts_epoch():
    rows = wi.build_feeds([{"feedId": "f", "itemsReceived": 2, "feedDate": 1795489825104}, {"feedId": "f", "itemsReceived": 3}, {"feedType": "x"}])
    assert len(rows) == 1 and rows[0]["items_received"] == 3 and rows[0]["feed_date"] is None
    assert wi.build_feeds([{"feedId": "g", "feedDate": 1795489825104}])[0]["feed_date"].year == 2026


def test_build_repricer_template_and_report_rows():
    s = wi.build_repricer_strategies([{"strategyCollectionId": "s1", "repricerStrategy": "X", "enabled": True, "assignedCount": 2, "strategies": [{"a": 1}]}, {}])
    assert s == [{"collection_id": "s1", "strategy": "X", "enabled": True, "assigned_count": 2, "enable_for_promotion": None,
                  "restore_seller_price_without_target": None, "enable_buybox_meet_external": None,
                  "compare_with_3p_offer_only": None, "strategies": [{"a": 1}]}]
    assert wi.build_shipping_template({}, None) is None
    assert wi.build_shipping_template({"id": 5, "name": "n"}, {"x": 1})["id"] == "5"
    reqs = wi.build_report_requests([{"requestId": "r", "reportType": "ITEM", "requestStatus": "READY", "requestSubmissionDate": "2026-11-24T09:01:09Z"}])
    assert reqs[0]["submitted_at"].tzinfo is not None
    assert wi.latest_ready([{"requestId": "a", "requestStatus": "PENDING"}]) is None


def test_parse_report_handles_zip_bom_and_row_cap():
    frame = wi.parse_report_bytes(_zip("r.csv", ITEM_CSV))
    assert frame.height == 2 and "SKU" in frame.columns
    bom = wi.parse_report_bytes(b"\xef\xbb\xbfSKU,Price\nA,1\nB,2\n", max_rows=1)
    assert bom.columns == ["SKU", "Price"] and bom.height == 1
    with pytest.raises(WalmartError):
        wi.parse_report_bytes(_zip("x", "")[:0] or b"PK" + b"\x00" * 4)


def test_build_item_snapshots_parses_numbers_and_booleans():
    rows = {r["sku"]: r for r in wi.build_item_snapshots(wi.parse_report_bytes(ITEM_CSV.encode()), "req")}
    a, b = rows["SKU-A"], rows["SKU-B"]
    assert a["repricer_strategy"] and a["min_allowed_price"] == 4.99 and a["buybox_eligible"] is True and a["reviews_count"] == 3
    assert b["price"] is None and b["buybox_eligible"] is False and b["repricer_strategy"] is None
    assert wi.build_item_snapshots(wi.parse_report_bytes(b"A,B\n1,2\n"), None) == []


def test_build_open_orders_drops_personal_data_and_picks_deadline():
    rows = wi.build_open_orders([_order("P1", 5, qty=2, price=40.0), {"shippingInfo": {}}])
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"order_id", "ordered_at", "ship_by", "deliver_by", "status", "sales", "units", "lines"}
    assert row["order_id"] == "P1" and row["units"] == 2 and row["sales"] == 40.0 and row["status"] == "Acknowledged"
    flat = repr(row)
    assert "Persona Privada" not in flat and "555-0100" not in flat and "Calle 1" not in flat


def test_download_refuses_non_https_and_oversize():
    fake = FakeWalmart()
    client = fake.client()
    with pytest.raises(WalmartError, match="demasiado"):
        wi.download_report(client, "req-item-0001", max_bytes=10)
    assert len(wi.download_report(client, "req-item-0001")) > 10
    assert fake.methods <= {"GET", "POST"}  # POST solo para el token OAuth; nunca a un recurso


# --- upserts idempotentes ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upserts_are_idempotent_and_prune(maker):
    inv = wi.build_wfs_inventory([{"sku": "A", "shipNodes": [{"availToSellQty": 3, "onHandQty": 4, "shipNodeType": "WFSFulfilled"}]},
                                  {"sku": "B", "shipNodes": [{"availToSellQty": 1, "onHandQty": 1, "shipNodeType": "WFSFulfilled"}]}])
    quality = wi.build_listing_quality({"payload": {"listingQuality": 50.0}}, TODAY)
    async with maker() as s:
        for _ in range(2):
            await replace_wfs_inventory(s, inv, TODAY, NOW)
            await upsert_listing_quality(s, quality, NOW)
            await upsert_feeds(s, wi.build_feeds([{"feedId": "f", "itemsReceived": 1}]))
        assert [await _count(s, m) for m in (WalmartWfsInventory, WalmartWfsInventoryHistory, WalmartListingQuality, WalmartFeed)] == [2, 2, 1, 1]
        # B deja de informarse; un día viejo del historial se poda; otro día nuevo suma al historial
        s.add(WalmartWfsInventoryHistory(sku="A", node="WFSFulfilled", snapshot_date=TODAY - timedelta(days=200)))
        await s.flush()
        await replace_wfs_inventory(s, inv[:1], TODAY + timedelta(days=1), NOW)
        await s.commit()
        assert await _count(s, WalmartWfsInventory) == 1
        assert await _count(s, WalmartWfsInventoryHistory) == 3  # A hoy, B hoy y A mañana; el de 200 días se podó
        await replace_item_snapshots(s, [{"sku": "X"}], NOW)
        await replace_item_snapshots(s, [{"sku": "Y"}], NOW)
        assert {r for (r,) in (await s.execute(select(WalmartItemSnapshot.sku))).all()} == {"Y"}


# --- sincronización con cliente falso --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_source_with_fake_client_is_idempotent(maker, monkeypatch):
    fake = FakeWalmart()
    monkeypatch.setattr(sync_jobs, "WalmartClient", fake.client)
    assert "walmart_insights" in sync_jobs.SOURCES and sync_jobs.LABELS["walmart_insights"].startswith("Walmart")
    results = []
    for _ in range(2):
        async with maker() as s:
            results.append(await sync_jobs.run_source(s, "walmart_insights", 30, "manual"))
    first, again = results
    assert first["problems"] == [] and first["stats"] == again["stats"]
    assert first["stats"] == {"listing_quality": 1, "wfs_inventory": 2, "feeds": 2, "shipping_templates": 1,
                              "repricer_strategies": 1, "report_requests": 1, "item_snapshots": 2}
    async with maker() as s:
        assert await _count(s, WalmartWfsInventory) == 2 and await _count(s, WalmartFeed) == 2
        assert await _count(s, WalmartItemSnapshot) == 2
    assert not (fake.methods - {"GET", "POST"})


@pytest.mark.asyncio
async def test_failing_resource_is_reported_but_does_not_stop_the_rest(maker, monkeypatch):
    fake = FakeWalmart()
    original = fake.route
    fake.route = lambda path, params: None if path == "/v3/feeds" else original(path, params)  # type: ignore[method-assign]
    monkeypatch.setattr(sync_jobs, "WalmartClient", fake.client)
    async with maker() as s:
        out = await sync_jobs.run_source(s, "walmart_insights", 30, "manual")
    assert out["problems"] and out["problems"][0].startswith("feeds")
    assert out["stats"]["wfs_inventory"] == 2


# --- API -------------------------------------------------------------------------------------------------


def _app(maker, fake: FakeWalmart) -> FastAPI:
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()  # el router aún no está en main.py: se prueba montado aparte
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[walmart_client] = fake.client
    app.dependency_overrides[optional_walmart_client] = fake.client
    return app


async def _sync(maker, monkeypatch, fake: FakeWalmart) -> None:
    monkeypatch.setattr(sync_jobs, "WalmartClient", fake.client)
    async with maker() as s:
        await sync_jobs.run_source(s, "walmart_insights", 30, "manual")


def _http(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_endpoints_return_synced_data(maker, monkeypatch):
    fake = FakeWalmart(orders=[_order("LATE", -3), _order("SOON", 10), _order("OK", 72)])
    await _sync(maker, monkeypatch, fake)
    async with maker() as s:
        await _seed_sales(s)
    async with _http(_app(maker, fake)) as c:
        q = (await c.get("/api/walmart/listing-quality")).json()
        assert q["latest"]["listing_quality"] == 61.7 and q["latest"]["components"][0]["key"] == "ratingReviewScore".replace("ratingReviewScore", "rating_review_score")
        assert q["latest"]["components"][0]["gap_to_100"] == 90.0 and q["latest"]["defects"]["item_defect_count"] == 2
        assert q["item_level"]["available"] is False and len(q["history"]) == 1

        inv = (await c.get("/api/walmart/wfs-inventory")).json()
        rows = {r["sku"]: r for r in inv["rows"]}
        assert rows["SKU-A"]["status"] == "out" and rows["SKU-A"]["stocked_elsewhere"] is True and rows["SKU-A"]["seller_fulfilled_stock"] == 7
        assert rows["SKU-C"]["status"] == "no_sales" and rows["SKU-C"]["in_transit_or_reserved"] == 4
        assert inv["summary"]["out_of_stock"] == 1 and inv["rows"][0]["sku"] == "SKU-A"

        feeds = (await c.get("/api/walmart/feeds")).json()
        assert feeds["summary"]["items_received"] == 6 and feeds["summary"]["success_rate"] == pytest.approx(5 / 6)
        assert [f["feed_id"] for f in feeds["with_failures"]] == ["f1"]

        tpl = (await c.get("/api/walmart/shipping-templates")).json()
        method = tpl["templates"][0]["methods"][0]
        assert method["charged_per_single_item_order"] == 3.0 and method["absorbed_vs_label"] is None
        assert tpl["redo_label"]["note"]  # sin etiquetas de Redo: aviso claro, no un número inventado

        reports = (await c.get("/api/walmart/reports")).json()
        assert reports["requests"][0]["request_id"] == "req-item-0001"

        rep = (await c.get("/api/walmart/repricer")).json()
        assert rep["link"]["available"] is True and rep["summary"]["assigned_in_report"] == 1
        assert rep["strategies"][0]["assigned_in_report"] == 1

        ov = (await c.get("/api/walmart/overview")).json()
        assert ov["kpis"]["gross_sales"] == 600.0 and ov["kpis"]["orders"] == 30
        assert ov["listing_quality"]["score"] == 61.7 and ov["wfs_out_of_stock_skus"] == 1
        assert ov["open_orders"]["overdue"] == 1 and ov["open_orders"]["due_soon"] == 1
        assert ov["seller_performance"]["windows"][0]["otd"] == 96.5 and ov["feeds_with_failures"] == 1

        for path in ("/api/walmart/feeds?days=3", "/api/walmart/wfs-inventory?days=999", "/api/walmart/reports/x/preview?limit=0"):
            assert (await c.get(path)).status_code == 422


@pytest.mark.asyncio
async def test_repricer_cross_with_minimum_price(maker, monkeypatch):
    fake = FakeWalmart()
    await _sync(maker, monkeypatch, fake)
    async with maker() as s:
        await _seed_sales(s)

    async def fake_report(session, **kw):
        row = {"channel": "walmart", "sku": "SKU-A", "status": "ok", "min_price": 30.0, "realized_price": 33.97}
        return {"rows": [row]}

    monkeypatch.setattr(walmart_analytics, "min_price_report", fake_report)
    async with _http(_app(maker, fake)) as c:
        rep = (await c.get("/api/walmart/repricer")).json()
    sku = rep["assigned_skus"][0]
    # el piso del repricer (4.99) está muy por debajo del mínimo real (30.00)
    assert sku["issue"] == {"type": "repricer_floor_below_min", "gap": 25.01} and rep["summary"]["with_issue"] == 1


@pytest.mark.asyncio
async def test_open_orders_endpoint_has_no_personal_data_and_caches(maker):
    fake = FakeWalmart(orders=[_order("LATE", -3, sku="S1", qty=2, price=50.0), _order("OK", 72)])
    async with _http(_app(maker, fake)) as c:
        first = (await c.get("/api/walmart/open-orders")).json()
        second = (await c.get("/api/walmart/open-orders")).json()
    assert fake.calls["/v3/orders/released"] == 1  # la segunda salió de la caché
    assert first["cached"] is False and second["cached"] is True
    assert first["summary"]["overdue"] == 1 and first["summary"]["sales_overdue"] == 50.0
    late = first["orders"][0]
    assert late["overdue"] is True and late["hours_left"] < 0 and late["lines"] == [{"sku": "S1", "units": 2}]
    text = repr(first)
    assert "Persona Privada" not in text and "555-0100" not in text and "Calle 1" not in text and "customerOrderId" not in text


@pytest.mark.asyncio
async def test_open_orders_cache_expires_after_ttl():
    now = [0.0]
    cache = walmart_analytics.OpenOrdersCache(ttl_seconds=60, clock=lambda: now[0])
    calls = []

    def loader():
        calls.append(1)
        return []

    await cache.get(loader)
    now[0] = 59
    assert (await cache.get(loader))[1] is True
    now[0] = 61
    assert (await cache.get(loader))[1] is False and len(calls) == 2


@pytest.mark.asyncio
async def test_open_orders_walmart_error_is_a_502(maker):
    fake = FakeWalmart()
    fake.route = lambda path, params: None  # type: ignore[method-assign]
    async with _http(_app(maker, fake)) as c:
        res = await c.get("/api/walmart/open-orders")
    assert res.status_code == 502


@pytest.mark.asyncio
async def test_report_preview_endpoint(maker, monkeypatch):
    fake = FakeWalmart()
    await _sync(maker, monkeypatch, fake)
    async with _http(_app(maker, fake)) as c:
        res = await c.get("/api/walmart/reports/req-item-0001/preview?limit=1")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["headers"][0] == "SKU" and body["preview_rows"] == 1 and body["total_rows"] == 2
        assert body["report_type"] == "ITEM" and body["rows"][0]["SKU"] == "SKU-A"
        filled = {c_["name"]: c_["filled"] for c_ in body["columns"]}
        assert filled["Price"] == 1 and filled["SKU"] == 2
        assert (await c.get("/api/walmart/reports/bad id!/preview")).status_code in (404, 422)

    fake.report_status = "PENDING"
    await _sync(maker, monkeypatch, fake)
    async with _http(_app(maker, fake)) as c:
        assert (await c.get("/api/walmart/reports/req-item-0001/preview")).status_code == 409


def test_preview_frame_summary_counts_blanks():
    frame = wi.parse_report_bytes(b"A,B\n1,\n2,x\n")
    out = walmart_analytics.preview_frame(frame, 5)
    assert out["total_rows"] == 2 and {c["name"]: c["filled"] for c in out["columns"]} == {"A": 2, "B": 1}


# --- Centro de acciones ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orders_due_provider(maker, monkeypatch):
    fake = FakeWalmart(orders=[_order("LATE", -3, price=50.0), _order("SOON", 10, price=30.0), _order("OK", 72)])
    monkeypatch.setattr(walmart_actions, "_client", fake.client)
    async with maker() as s:
        items = await walmart_actions.orders_due_actions(s)
    by_key = {i.key: i for i in items}
    assert by_key["walmart_orders_due:overdue"].impact_usd == 50.0 and by_key["walmart_orders_due:due_soon"].impact_usd == 30.0
    assert all(i.target == "walmart" and i.kind == "walmart_orders_due" for i in items)
    monkeypatch.setattr(walmart_actions, "_client", lambda: None)
    async with maker() as s:
        assert await walmart_actions.orders_due_actions(s) == []


@pytest.mark.asyncio
async def test_quality_feed_and_repricer_providers(maker, monkeypatch):
    fake = FakeWalmart()
    await _sync(maker, monkeypatch, fake)
    async with maker() as s:
        await _seed_sales(s)

    async def fake_report(session, **kw):
        return {"rows": [{"channel": "walmart", "sku": "SKU-A", "status": "below", "min_price": 40.0, "realized_price": 33.0}]}

    monkeypatch.setattr(walmart_analytics, "min_price_report", fake_report)
    async with maker() as s:
        quality = {i.key: i for i in await walmart_actions.listing_quality_actions(s)}
        feeds = await walmart_actions.feed_failure_actions(s)
        repricer = await walmart_actions.repricer_floor_actions(s)
    # componentes bajo 70: reseñas (10), precio (60) y transaccionabilidad (67)
    assert set(quality) == {f"walmart_listing_quality:{k}" for k in ("rating_review_score", "price_score", "transactibility_score")}
    reviews = quality["walmart_listing_quality:rating_review_score"]
    assert reviews.impact_usd == pytest.approx(600 * 0.60 * walmart_actions.EXPOSURE_FACTOR) and reviews.impact_usd > 0
    assert [i.key for i in feeds] == ["walmart_feed_failures:PRICE_AND_PROMOTION"]
    assert feeds[0].impact_usd == pytest.approx(1 * 20.0)  # 1 ítem fallido x precio unitario promedio (600/30)
    assert [i.key for i in repricer] == ["walmart_repricer_floor:SKU-A"]
    assert repricer[0].impact_usd == pytest.approx(30 * 7.0)  # 30 unidades x (40 - 33)


@pytest.mark.asyncio
async def test_providers_are_registered_and_quiet_without_data(maker):
    from app import action_center
    from app.action_providers import PROVIDER_MODULES

    assert "app.walmart_actions" in PROVIDER_MODULES
    assert {"walmart_orders_due", "walmart_listing_quality", "walmart_feed_failures", "walmart_repricer_floor"} <= set(action_center.providers())
    async with maker() as s:
        assert await walmart_actions.listing_quality_actions(s) == []
        assert await walmart_actions.feed_failure_actions(s) == []
        assert await walmart_actions.repricer_floor_actions(s) == []
