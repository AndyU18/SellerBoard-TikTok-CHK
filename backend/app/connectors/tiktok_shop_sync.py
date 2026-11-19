"""Convierte órdenes + liquidaciones de TikTok Shop al esquema canónico y las guarda.

Solo se cuentan órdenes LIQUIDADAS (con transacción de finanzas): ahí las comisiones,
el envío y los reembolsos son exactos. Las pendientes de liquidar se reportan aparte
para no inflar la utilidad con ventas sin sus costos.
Los montos de la orden se reparten entre sus SKUs en proporción al precio original.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_shop import TikTokShopClient
from app.importers.persist import ImportResult, import_orders

CHANNEL = "tiktok_shop"
DEFAULT_ACCOUNT = "tiktok_shop_us"
EXCLUDED_STATUSES = {"UNPAID", "CANCELLED"}

OUT_SCHEMA = {
    "date": pl.Date,
    "channel": pl.Utf8,
    "account": pl.Utf8,
    "order_id": pl.Utf8,
    "sku": pl.Utf8,
    "product_name": pl.Utf8,
    "units": pl.Int64,
    "gross_sales": pl.Float64,
    "promo": pl.Float64,
    "platform_fees": pl.Float64,
    "shipping_cost": pl.Float64,
    "refund_amount": pl.Float64,
}


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sum_transactions(transactions: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Suma por orden (una orden puede tener varias transacciones: venta + reembolso)."""
    totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for tx in transactions:
        order = totals[tx["order_id"]]
        for key in (
            "gross_sales_amount",
            "seller_discount_amount",
            "fee_amount",
            "shipping_cost_amount",
            "gross_sales_refund_amount",
        ):
            order[key] += _f(tx.get(key))
    return totals


def build_order_lines(
    orders: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    account: str = DEFAULT_ACCOUNT,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Devuelve (líneas canónicas, estadísticas)."""
    tx_by_order = _sum_transactions(transactions)
    rows: list[dict[str, Any]] = []
    stats = {
        "orders_seen": 0,
        "orders_settled": 0,
        "orders_pending_settlement": 0,
        "orders_excluded": 0,
    }

    for order in orders:
        stats["orders_seen"] += 1
        if order.get("status") in EXCLUDED_STATUSES:
            stats["orders_excluded"] += 1
            continue
        tx = tx_by_order.get(order["id"])
        if tx is None:
            stats["orders_pending_settlement"] += 1
            continue
        stats["orders_settled"] += 1

        groups: dict[str, dict[str, Any]] = {}
        for item in order.get("line_items", []):
            sku = item.get("seller_sku") or item.get("sku_id") or "(sin sku)"
            g = groups.setdefault(sku, {"units": 0, "weight": 0.0, "name": item.get("product_name")})
            g["units"] += 1
            g["weight"] += _f(item.get("original_price"))
        if not groups:
            continue
        total_weight = sum(g["weight"] for g in groups.values())
        total_units = sum(g["units"] for g in groups.values())
        day = datetime.fromtimestamp(int(order["create_time"]), tz=UTC).date()

        for sku, g in groups.items():
            share = g["weight"] / total_weight if total_weight else g["units"] / total_units
            rows.append(
                {
                    "date": day,
                    "channel": CHANNEL,
                    "account": account,
                    "order_id": order["id"],
                    "sku": sku,
                    "product_name": g["name"],
                    "units": g["units"],
                    "gross_sales": tx["gross_sales_amount"] * share,
                    "promo": abs(tx["seller_discount_amount"]) * share,
                    "platform_fees": abs(tx["fee_amount"]) * share,
                    "shipping_cost": abs(tx["shipping_cost_amount"]) * share,
                    "refund_amount": abs(tx["gross_sales_refund_amount"]) * share,
                }
            )
    return pl.DataFrame(rows, schema=OUT_SCHEMA), stats


@dataclass
class SyncReport:
    days: int
    stats: dict[str, int] = field(default_factory=dict)
    imported: ImportResult = field(default_factory=ImportResult)


def fetch_window(
    client: TikTokShopClient, days: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Órdenes creadas en la ventana y sus transacciones de liquidación."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    orders = list(client.iter_orders(start, end))
    window = {o["id"] for o in orders}
    transactions: list[dict[str, Any]] = []
    for statement in client.iter_statements(start, end):
        for tx in client.iter_statement_transactions(statement["id"]):
            if tx.get("order_id") in window:
                transactions.append(tx)
    return orders, transactions


async def sync_tiktok_shop(
    session: AsyncSession,
    client: TikTokShopClient,
    days: int = 30,
    account: str = DEFAULT_ACCOUNT,
) -> SyncReport:
    orders, transactions = await asyncio.to_thread(fetch_window, client, days)
    frame, stats = build_order_lines(orders, transactions, account)
    imported = await import_orders(session, frame) if frame.height else ImportResult()
    await session.commit()
    return SyncReport(days=days, stats=stats, imported=imported)
