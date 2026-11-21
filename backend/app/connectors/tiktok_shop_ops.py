"""TikTok Shop -> devoluciones/cancelaciones (RMA) y envíos (logística).

Funciones puras: reciben los JSON de la API y devuelven filas listas para guardar
(ver app/ops_data.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Las órdenes sin pagar no son ventas ni envíos.
SKIP_STATUSES = {"UNPAID"}


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _refund(node: dict[str, Any] | None) -> float:
    return _f((node or {}).get("refund_total"))


def sku_by_order_line(orders: list[dict[str, Any]]) -> dict[str, str]:
    """id de la línea de orden -> SKU del vendedor (las cancelaciones solo traen el id de línea)."""
    return {
        str(item["id"]): item.get("seller_sku") or str(item.get("sku_id") or "(sin sku)")
        for order in orders
        for item in order.get("line_items", [])
        if item.get("id")
    }


def build_return_cases(
    returns: list[dict[str, Any]],
    cancellations: list[dict[str, Any]],
    sku_lookup: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Devoluciones y cancelaciones -> una fila por caso, con sus líneas."""
    sku_lookup = sku_lookup or {}
    cases: list[dict[str, Any]] = []

    for r in returns:
        created = _ts(r.get("create_time"))
        if created is None:
            continue
        cases.append(
            {
                "external_id": str(r["return_id"]),
                "kind": "return",
                "case_type": r.get("return_type"),
                "status": r.get("return_status"),
                "reason": r.get("return_reason_text") or r.get("return_reason"),
                "reason_code": r.get("return_reason"),
                "initiated_by": str(r.get("role") or "").lower() or None,
                "external_order_id": str(r["order_id"]),
                "created_at": created,
                "updated_at": _ts(r.get("update_time")),
                "case_date": created.date(),
                "refund_total": _refund(r.get("refund_amount")),
                "lines": [
                    {
                        "sku": li.get("seller_sku")
                        or sku_lookup.get(str(li.get("order_line_item_id")))
                        or str(li.get("sku_id") or "(sin sku)"),
                        "product_name": li.get("product_name"),
                        "units": 1,
                        "refund_total": _refund(li.get("refund_amount")),
                    }
                    for li in r.get("return_line_items", [])
                ],
            }
        )

    for c in cancellations:
        created = _ts(c.get("create_time"))
        if created is None:
            continue
        cases.append(
            {
                "external_id": str(c["cancel_id"]),
                "kind": "cancellation",
                "case_type": c.get("cancel_type"),
                "status": c.get("cancel_status") or "complete",
                "reason": c.get("cancel_reason_text") or c.get("cancel_reason"),
                "reason_code": c.get("cancel_reason"),
                "initiated_by": str(c.get("role") or "").lower() or None,
                "external_order_id": str(c["order_id"]),
                "created_at": created,
                "updated_at": _ts(c.get("update_time")),
                "case_date": created.date(),
                "refund_total": _refund(c.get("refund_amount")),
                "lines": [
                    {
                        "sku": sku_lookup.get(str(li.get("order_line_item_id")))
                        or str(li.get("sku_id") or "(sin sku)"),
                        "product_name": li.get("product_name"),
                        "units": 1,
                        "refund_total": _refund(li.get("refund_amount")),
                    }
                    for li in c.get("cancel_line_items", [])
                ],
            }
        )
    return cases


def build_shipments(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Una fila por orden con sus tiempos de envío."""
    rows: list[dict[str, Any]] = []
    for o in orders:
        if o.get("status") in SKIP_STATUSES:
            continue
        ordered_at = _ts(o.get("create_time"))
        if ordered_at is None:
            continue
        first = (o.get("line_items") or [{}])[0]
        rows.append(
            {
                "external_order_id": str(o["id"]),
                "order_date": ordered_at.date(),
                "status": o.get("status"),
                "shipping_type": o.get("shipping_type"),
                "fulfillment_type": o.get("fulfillment_type"),
                "delivery_option": o.get("delivery_option_name"),
                "carrier": first.get("shipping_provider_name"),
                "tracking_number": first.get("tracking_number"),
                "is_sample": bool(o.get("is_sample_order")),
                "ordered_at": ordered_at,
                "rts_at": _ts(o.get("rts_time")),
                "collected_at": _ts(o.get("collection_time")),
                "delivered_at": _ts(o.get("delivery_time")),
                "due_at": _ts(o.get("shipping_due_time")),
            }
        )
    return rows
