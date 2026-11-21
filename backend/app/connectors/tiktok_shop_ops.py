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


def _money(node: dict[str, Any] | None) -> float | None:
    """{'amount': '5.65', 'currency': 'USD'} -> 5.65; {} -> None (aún no liquidado)."""
    amount = (node or {}).get("amount")
    return _f(amount) if amount not in (None, "") else None


def build_sample_lines(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Productos enviados en las órdenes de muestra (no canceladas)."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for o in orders:
        if not o.get("is_sample_order") or o.get("status") in {"CANCELLED", "UNPAID"}:
            continue
        created = _ts(o.get("create_time"))
        if created is None:
            continue
        for item in o.get("line_items", []):
            sku = item.get("seller_sku") or str(item.get("sku_id") or "(sin sku)")
            row = rows.setdefault(
                (str(o["id"]), sku),
                {
                    "external_order_id": str(o["id"]),
                    "order_date": created.date(),
                    "sku": sku,
                    "product_name": item.get("product_name"),
                    "units": 0,
                },
            )
            row["units"] += 1
    return list(rows.values())


def build_sample_applications(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Solicitudes de muestra de creadores (embudo)."""
    rows = []
    for a in apps:
        creator = a.get("creator") or {}
        order_id = str(a.get("order_id") or "")
        rows.append(
            {
                "external_id": str(a["id"]),
                "creator_username": creator.get("username"),
                "follower_count": creator.get("follower_count"),
                "creator_gmv": _money(creator.get("gmv")),
                "status": a.get("status"),
                "external_order_id": order_id if order_id not in ("", "0") else None,
                "product_title": (a.get("product") or {}).get("title"),
                "commission_rate": _f(a.get("commission_rate")) if a.get("commission_rate") else None,
            }
        )
    return rows


def build_affiliate_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Una fila por producto de cada orden atribuida a un creador."""
    rows = []
    for o in orders:
        created = _ts(o.get("create_time"))
        if created is None:
            continue
        for s in o.get("skus", []):
            actual_commission = _money(s.get("actual_paid_commission"))
            actual_base = _money(s.get("actual_commission_base"))
            est_commission = _money(s.get("estimated_paid_commission"))
            est_base = _money(s.get("estimated_commission_base"))
            rate = _f(s.get("commission_rate")) / 10000  # TikTok lo manda en puntos base (1450 = 14.5%)
            rows.append(
                {
                    "external_order_id": str(o["id"]),
                    "sku_id": str(s.get("sku_id") or ""),
                    "order_date": created.date(),
                    "creator_username": s.get("creator_username"),
                    "content_type": s.get("content_type"),
                    "content_id": s.get("content_id"),
                    "sku": s.get("seller_sku"),
                    "units": int(_f(s.get("quantity")) or 1),
                    "commission_base": actual_base if actual_base is not None else (est_base or 0.0),
                    "commission": actual_commission if actual_commission is not None else (est_commission or 0.0),
                    "commission_rate": rate or None,
                    "settlement_status": s.get("settlement_status"),
                }
            )
    return rows


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
