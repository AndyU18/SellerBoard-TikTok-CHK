import json

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.redo import (
    RedoClient,
    RedoError,
    RedoSettings,
    build_label_frame,
    sync_redo,
)
from app.db import create_dev_schema, make_engine
from app.importers.canonical import normalize_orders, read_table
from app.importers.persist import import_orders


def _charge(order, amount, day="2026-11-17", note="Charging label"):
    return {
        "id": "1",
        "orderName": order,
        "createdAt": f"{day}T10:00:00.000Z",
        "note": note,
        "amount": {"amount": amount, "currency": "USD"},
    }


CHARGES = [
    _charge("T-100", "10.41"),
    _charge("T-100", "3.05", "2026-11-18"),
    _charge("T-100", "-0.50", "2026-11-19", "Refund"),
    _charge("T-200", "12.00"),
    _charge(None, "30.00", note="Credit card processing fee"),
]


def test_frame_nets_charges_per_order_and_reports_unassigned():
    df, stats = build_label_frame(CHARGES)
    per = {r["external_order_id"]: r for r in df.to_dicts()}
    assert (
        per["T-100"]["amount"] == pytest.approx(12.96) and per["T-100"]["charges"] == 3
    )
    assert (
        str(per["T-100"]["first_charged"]) == "2026-11-17"
        and str(per["T-100"]["last_charged"]) == "2026-11-19"
    )
    assert (
        stats.orders == 2
        and stats.unassigned_charges == 1
        and stats.unassigned_total == 30.0
    )


def _client(handler):
    http = httpx.Client(
        base_url="https://x.test", transport=httpx.MockTransport(handler)
    )
    return RedoClient(RedoSettings(store_id="s1", api_key="redo_sk_x"), http)


def test_client_pages_and_sends_bearer_only_queries():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append((request.url.path, request.headers["authorization"], body["query"]))
        first = body["variables"]["after"] is None
        edges = [{"node": CHARGES[0] if first else CHARGES[3]}]
        page = {"hasNextPage": first, "endCursor": "c1"}
        return httpx.Response(
            200, json={"data": {"billingCharges": {"pageInfo": page, "edges": edges}}}
        )

    got = list(_client(handler).iter_charges())
    assert [c["orderName"] for c in got] == ["T-100", "T-200"]
    assert all(
        p == "/v3/account/s1/graphql" and a == "Bearer redo_sk_x" for p, a, _ in sent
    )
    assert all(q.lstrip().startswith("query") for _, _, q in sent)


def test_mutations_are_refused_locally():
    client = _client(lambda r: pytest.fail("no debe llegar a la red"))
    with pytest.raises(RedoError, match="solo lectura"):
        client.query("mutation { x }")


def test_unauthorized_gives_a_clear_error():
    client = _client(
        lambda r: httpx.Response(401, json={"errors": [{"message": "Unauthorized"}]})
    )
    with pytest.raises(RedoError, match="no autorizado"):
        list(client.iter_charges())


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class FakeRedo:
    def iter_charges(self):
        yield from CHARGES


@pytest.mark.asyncio
async def test_sync_matches_orders_and_is_idempotent(maker):
    orders = (
        b"date,channel,account,order_id,sku,units,sales\n"
        b"2026-11-17,tiktok_shop,tt1,T-100,A,1,100\n"
    )
    async with maker() as s:
        await import_orders(s, normalize_orders(read_table(orders, "o.csv")))
        first, stats = await sync_redo(s, FakeRedo())
        again, _ = await sync_redo(s, FakeRedo())
    assert (
        first.inserted == 2
        and first.matched_orders == 1
        and first.unmatched_orders == 1
    )
    assert again.inserted == 0 and again.updated == 0
    assert stats.unassigned_total == 30.0
