import json
import time

import httpx
import pytest

from app.connectors.tiktok_shop import (
    TikTokShopClient,
    TikTokShopError,
    TikTokShopSettings,
    sign_request,
)
from app.connectors.tiktok_shop_sync import build_order_lines

T0 = 1_795_000_000  # 2026-11-18 aprox


def _order(oid, items, status="DELIVERED"):
    return {
        "id": oid,
        "status": status,
        "create_time": T0,
        "line_items": [
            {"seller_sku": sku, "product_name": f"P {sku}", "original_price": str(price)}
            for sku, price in items
        ],
    }


def _tx(oid, gross, seller_disc, fee, ship, refund=0):
    return {
        "order_id": oid,
        "gross_sales_amount": str(gross),
        "seller_discount_amount": str(seller_disc),
        "fee_amount": str(fee),
        "shipping_cost_amount": str(ship),
        "gross_sales_refund_amount": str(refund),
    }


# --- mapeo -------------------------------------------------------------------
def test_settled_order_maps_amounts_as_positive_costs():
    df, stats = build_order_lines(
        [_order("1", [("GRN-100", 69.99)])], [_tx("1", 137.96, -36.48, -9.64, -30.9)]
    )
    row = df.to_dicts()[0]
    assert row["gross_sales"] == pytest.approx(137.96)
    assert row["promo"] == pytest.approx(36.48)
    assert row["platform_fees"] == pytest.approx(9.64)
    assert row["shipping_cost"] == pytest.approx(30.9)
    assert row["channel"] == "tiktok_shop" and row["units"] == 1
    assert stats["orders_settled"] == 1


def test_order_amounts_are_split_by_price_across_skus():
    df, _ = build_order_lines(
        [_order("1", [("A", 30), ("B", 10), ("B", 10)])], [_tx("1", 100, 0, -10, -20)]
    )
    rows = {r["sku"]: r for r in df.to_dicts()}
    assert rows["A"]["units"] == 1 and rows["B"]["units"] == 2
    assert rows["A"]["gross_sales"] == pytest.approx(60)  # 30/50
    assert rows["B"]["gross_sales"] == pytest.approx(40)
    assert sum(r["platform_fees"] for r in rows.values()) == pytest.approx(10)
    assert sum(r["shipping_cost"] for r in rows.values()) == pytest.approx(20)


def test_pending_and_cancelled_orders_are_not_counted():
    orders = [
        _order("1", [("A", 10)]),
        _order("2", [("A", 10)], status="CANCELLED"),
        _order("3", [("A", 10)]),
    ]
    df, stats = build_order_lines(orders, [_tx("1", 10, 0, -1, -1)])
    assert df["order_id"].to_list() == ["1"]
    assert stats == {
        "orders_seen": 3,
        "orders_settled": 1,
        "orders_pending_settlement": 1,
        "orders_excluded": 1,
    }


def test_multiple_transactions_for_one_order_are_summed_including_refund():
    txs = [_tx("1", 100, 0, -10, -5), _tx("1", 0, 0, 2, 0, refund=-40)]
    df, _ = build_order_lines([_order("1", [("A", 100)])], txs)
    row = df.to_dicts()[0]
    assert row["platform_fees"] == pytest.approx(8)  # -10 + 2
    assert row["refund_amount"] == pytest.approx(40)


def test_missing_sku_falls_back_to_sku_id_then_placeholder():
    order = {
        "id": "1",
        "status": "DELIVERED",
        "create_time": T0,
        "line_items": [{"sku_id": "999", "original_price": "5"}, {"original_price": "5"}],
    }
    df, _ = build_order_lines([order], [_tx("1", 10, 0, 0, 0)])
    assert set(df["sku"]) == {"999", "(sin sku)"}


def test_empty_inputs_give_empty_typed_frame():
    df, stats = build_order_lines([], [])
    assert df.height == 0 and "gross_sales" in df.columns and stats["orders_seen"] == 0


# --- firma -------------------------------------------------------------------
def test_signature_ignores_sign_and_access_token_and_param_order():
    a = sign_request("sec", "/p", {"b": "2", "a": "1"})
    b = sign_request("sec", "/p", {"a": "1", "b": "2", "sign": "x", "access_token": "y"})
    assert a == b and len(a) == 64


def test_signature_changes_with_body_secret_and_path():
    base = sign_request("sec", "/p", {"a": "1"}, "")
    assert sign_request("sec", "/p", {"a": "1"}, '{"x":1}') != base
    assert sign_request("other", "/p", {"a": "1"}, "") != base
    assert sign_request("sec", "/q", {"a": "1"}, "") != base


# --- cliente / tokens ----------------------------------------------------------
def _client(tmp_path, handler, expire_in):
    settings = TikTokShopSettings(
        app_key="k",
        app_secret="s",
        access_token="old",
        refresh_token="r1",
        access_token_expire_in=expire_in,
        shop_cipher="cipher",
        tokens_file=tmp_path / "tokens.json",
        _env_file=None,
    )
    return TikTokShopClient(settings, httpx.Client(transport=httpx.MockTransport(handler)))


def test_expired_token_is_refreshed_and_persisted(tmp_path):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if "token/refresh" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "access_token": "new",
                        "access_token_expire_in": int(time.time()) + 86400,
                        "refresh_token": "r2",
                        "refresh_token_expire_in": int(time.time()) + 10**8,
                    },
                },
            )
        assert req.headers["x-tts-access-token"] == "new"
        assert "sign" in req.url.params and req.url.params["shop_cipher"] == "cipher"
        return httpx.Response(200, json={"code": 0, "data": {"shops": []}})

    client = _client(tmp_path, handler, expire_in=1)
    client.request("GET", "/some/path")
    assert calls[0].endswith("token/refresh")
    saved = json.loads((tmp_path / "tokens.json").read_text())
    assert saved["access_token"] == "new" and saved["refresh_token"] == "r2"


def test_valid_token_is_not_refreshed(tmp_path):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        return httpx.Response(200, json={"code": 0, "data": {}})

    client = _client(tmp_path, handler, expire_in=int(time.time()) + 86400)
    client.request("GET", "/some/path")
    assert seen == ["/some/path"]


def test_api_error_raises_with_code(tmp_path):
    def handler(req: httpx.Request):
        return httpx.Response(200, json={"code": 105002, "message": "expired credentials"})

    client = _client(tmp_path, handler, expire_in=int(time.time()) + 86400)
    with pytest.raises(TikTokShopError) as exc:
        client.request("GET", "/some/path")
    assert exc.value.code == 105002


def test_pagination_follows_next_page_token(tmp_path):
    pages = {"": ("a", "p2"), "p2": ("b", "")}

    def handler(req: httpx.Request):
        token = req.url.params.get("page_token", "")
        item, nxt = pages[token]
        return httpx.Response(
            200, json={"code": 0, "data": {"orders": [{"id": item}], "next_page_token": nxt}}
        )

    client = _client(tmp_path, handler, expire_in=int(time.time()) + 86400)
    got = list(client._paginate("GET", "/x", "orders", {}))
    assert [o["id"] for o in got] == ["a", "b"]
