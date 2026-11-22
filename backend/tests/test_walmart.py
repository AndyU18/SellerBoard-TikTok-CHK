import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors import walmart
from app.connectors.walmart import build_order_lines, sync_walmart
from app.db import create_dev_schema, get_session, make_engine
from app.importers.canonical import read_table
from app.importers.redo import import_label_costs, normalize_redo
from app.main import app

MS = 1_795_000_000_000  # 2026-11-18 UTC


def _line(sku, qty, price, *, status="Delivered", refund=None):
    return {
        "item": {"sku": sku, "productName": f"Producto {sku}"},
        "orderLineQuantity": {"amount": str(qty)},
        "charges": {
            "charge": [{"chargeType": "PRODUCT", "chargeAmount": {"amount": price}}]
        },
        "orderLineStatuses": {"orderLineStatus": [{"status": status}]},
        "refund": refund,
    }


def _order(customer_id, lines):
    return {
        "customerOrderId": customer_id,
        "purchaseOrderId": "1" + customer_id,
        "orderDate": MS,
        "orderLines": {"orderLine": lines},
    }


def _recon(order, kind, amount_type, amount, sku="A", desc="Purchase"):
    return {
        "Customer Order #": order,
        "Transaction Type": kind,
        "Transaction Description": desc,
        "Amount Type": amount_type,
        "Amount": str(amount),
        "Partner Item Id": sku,
    }


ORDERS = [
    _order("2000A", [_line("A", 2, 100.0)]),
    _order("2000B", [_line("A", 1, 30.0), _line("B", 1, 70.0)]),
    _order("2000C", [_line("A", 1, 50.0, status="Cancelled")]),
    _order(
        "2000D",
        [
            _line(
                "B",
                1,
                40.0,
                refund={
                    "refundCharges": {
                        "refundCharge": [
                            {
                                "charge": {
                                    "chargeType": "PRODUCT",
                                    "chargeAmount": {"amount": -5.0},
                                }
                            }
                        ]
                    }
                },
            )
        ],
    ),
]
RECON = [
    _recon("2000A", "Sale", "Commission on Product", -14.0),
    _recon(
        "2000A",
        "Adjustment",
        "Fee/Reimbursement",
        -10.4,
        desc="Walmart Shipping Label Service Charge",
    ),
    _recon(
        "2000A",
        "Adjustment",
        "Fee/Reimbursement",
        0.4,
        desc="Walmart Shipping Label Service Charge",
    ),
    _recon("2000B", "Sale", "Commission on Product", -4.5, sku="A"),
    _recon(
        "2000B",
        "Adjustment",
        "Fee/Reimbursement",
        -9.0,
        desc="Walmart Shipping Label Service Charge",
    ),
]


def _by_key(df):
    return {(r["order_id"], r["sku"]): r for r in df.to_dicts()}


def test_lines_use_recon_for_fees_and_estimate_the_rest():
    df, stats = build_order_lines(ORDERS, RECON)
    rows = _by_key(df)
    assert ("2000C", "A") not in rows and stats["orders_cancelled"] == 1
    a = rows[("2000A", "A")]
    assert a["units"] == 2 and a["gross_sales"] == 100.0
    assert a["platform_fees"] == 14.0  # real, de la conciliación
    assert a["shipping_cost"] == pytest.approx(10.0)  # 10.40 - 0.40 de reversa
    # B2 sin comisión en el reporte: se estima al 15 %; A de la misma orden sí tiene la real
    assert rows[("2000B", "A")]["platform_fees"] == 4.5
    assert rows[("2000B", "B")]["platform_fees"] == pytest.approx(70 * 0.15)
    # la etiqueta de la orden se reparte 30/70
    assert rows[("2000B", "A")]["shipping_cost"] == pytest.approx(2.7)
    assert rows[("2000B", "B")]["shipping_cost"] == pytest.approx(6.3)
    assert rows[("2000D", "B")]["refund_amount"] == 5.0
    assert stats["fees_actual"] == 2 and stats["fees_estimated"] == 2
    assert set(df["order_id"]) == {"2000A", "2000B", "2000D"}  # usa customerOrderId


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class FakeClient:
    def __init__(self):
        self.calls = 0

    def iter_orders(self, start, end):
        yield from ORDERS

    def recon_dates(self):
        from datetime import date

        return [date(2099, 1, 1)]

    def recon_rows(self, report_date):
        return RECON

    def iter_returns(self, start, end):
        return iter(())

    def seller_performance(self):
        return {}

    def iter_items(self):
        return iter(())

    def iter_inventories(self):
        return iter(())


@pytest.mark.asyncio
async def test_sync_is_idempotent_and_creates_walmart_account(maker):
    async with maker() as s:
        first = await sync_walmart(s, FakeClient(), days=30)
        again = await sync_walmart(s, FakeClient(), days=30)
    assert first.imported.inserted == 4 and first.imported.accounts_created == 1
    assert again.imported.inserted == 0


@pytest.mark.asyncio
async def test_redo_label_matches_walmart_customer_order_id(maker):
    redo = (
        b"Created at,Period,Type,Category,Order,Amount,Invoice,Currency\n"
        b'"Nov 18, 2026","p",Charge,Shipping Labels,2000D,7.00,i,USD\n'
    )

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        async with maker() as s:
            await sync_walmart(s, FakeClient(), days=30)
            df, _ = normalize_redo(read_table(redo, "b.csv"))
            res = await import_label_costs(s, df)
        assert res.matched_orders == 1
    finally:
        app.dependency_overrides.clear()


def test_client_refreshes_token_and_follows_cursor():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/v3/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 900})
        assert request.headers["WM_SEC.ACCESS_TOKEN"] == "t"
        if "nextCursor" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "list": {
                        "meta": {"nextCursor": None},
                        "elements": {"order": [{"n": 2}]},
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "list": {
                    "meta": {"nextCursor": "?limit=200&nextCursor=abc"},
                    "elements": {"order": [{"n": 1}]},
                }
            },
        )

    http = httpx.Client(
        base_url=walmart.API_BASE, transport=httpx.MockTransport(handler)
    )
    client = walmart.WalmartClient(
        walmart.WalmartSettings(client_id="i", client_secret="s"), http
    )
    from datetime import UTC, datetime

    got = list(
        client.iter_orders(
            datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC)
        )
    )
    assert [o["n"] for o in got] == [1, 2]
    assert sum("/v3/token" in u for u in seen) == 1  # el token se reutiliza
