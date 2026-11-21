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
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.tiktok_shop import TikTokShopClient
from app.connectors.tiktok_shop_ops import (
    build_affiliate_orders,
    build_return_cases,
    build_sample_applications,
    build_sample_lines,
    build_shipments,
    sku_by_order_line,
)
from app.importers.persist import ImportResult, import_orders
from app.ops_data import (
    upsert_affiliate_orders,
    upsert_return_cases,
    upsert_sample_applications,
    upsert_sample_lines,
    upsert_shipments,
)
from app.settlements import upsert_settlements

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
    "platform_discount": pl.Float64,
    "list_amount": pl.Float64,
    "ordered_at": pl.Datetime(time_zone="UTC"),
}


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sum_transactions(
    transactions: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
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
            g = groups.setdefault(
                sku,
                {
                    "units": 0,
                    "weight": 0.0,
                    "platform": 0.0,
                    "name": item.get("product_name"),
                },
            )
            g["units"] += 1
            g["weight"] += _f(item.get("original_price"))
            # El descuento de plataforma viene por unidad, así que no hace falta repartirlo.
            g["platform"] += _f(item.get("platform_discount"))
        if not groups:
            continue
        total_weight = sum(g["weight"] for g in groups.values())
        total_units = sum(g["units"] for g in groups.values())
        ordered_at = datetime.fromtimestamp(int(order["create_time"]), tz=UTC)
        day = ordered_at.date()

        for sku, g in groups.items():
            share = (
                g["weight"] / total_weight if total_weight else g["units"] / total_units
            )
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
                    "platform_discount": g["platform"],
                    "list_amount": g["weight"],
                    "ordered_at": ordered_at,
                }
            )
    return pl.DataFrame(rows, schema=OUT_SCHEMA), stats


def _epoch_date(value: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).date()
    except (TypeError, ValueError, OverflowError):
        return None


def build_settlements(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Estados de cuenta de TikTok -> filas de liquidación (montos como positivos, estado en minúscula)."""
    return [
        {
            "external_id": str(s["id"]),
            "statement_date": _epoch_date(s.get("statement_time")),
            "payout_date": _epoch_date(s.get("payment_time")),
            "status": str(s.get("payment_status") or "").lower() or None,
            "gross_amount": _f(s.get("revenue_amount")),
            "fees_amount": abs(_f(s.get("fee_amount"))),
            "shipping_amount": abs(_f(s.get("shipping_cost_amount"))),
            "adjustment_amount": _f(s.get("adjustment_amount")),
            "net_payout": _f(s.get("settlement_amount")),
        }
        for s in statements
    ]


@dataclass
class SyncReport:
    days: int
    stats: dict[str, int] = field(default_factory=dict)
    imported: ImportResult = field(default_factory=ImportResult)


@dataclass
class Window:
    """Todo lo que se descarga de TikTok Shop en una sincronización."""

    orders: list[dict[str, Any]] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    statements: list[dict[str, Any]] = field(default_factory=list)
    returns: list[dict[str, Any]] = field(default_factory=list)
    cancellations: list[dict[str, Any]] = field(default_factory=list)
    applications: list[dict[str, Any]] = field(default_factory=list)
    affiliate_orders: list[dict[str, Any]] = field(default_factory=list)


def fetch_window(client: TikTokShopClient, days: int) -> Window:
    """Descarga órdenes, liquidaciones, devoluciones, muestras y ventas de afiliados de la ventana."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    w = Window()
    w.orders = list(client.iter_orders(start, end))
    w.returns = list(client.iter_returns(start, end))
    w.cancellations = list(client.iter_cancellations(start, end))
    w.applications = list(client.iter_sample_applications())
    w.affiliate_orders = list(client.iter_affiliate_orders(start, end))
    window = {o["id"] for o in w.orders}
    w.statements = list(client.iter_statements(start, end))
    for statement in w.statements:
        for tx in client.iter_statement_transactions(statement["id"]):
            if tx.get("order_id") in window:
                w.transactions.append(tx)
    return w


async def sync_tiktok_shop(
    session: AsyncSession,
    client: TikTokShopClient,
    days: int = 30,
    account: str = DEFAULT_ACCOUNT,
) -> SyncReport:
    w = await asyncio.to_thread(fetch_window, client, days)
    frame, stats = build_order_lines(w.orders, w.transactions, account)
    imported = await import_orders(session, frame) if frame.height else ImportResult()
    stats["settlements"] = await upsert_settlements(
        session, CHANNEL, account, build_settlements(w.statements)
    )
    cases = build_return_cases(w.returns, w.cancellations, sku_by_order_line(w.orders))
    stats["return_cases"] = await upsert_return_cases(session, CHANNEL, account, cases)
    stats["shipments"] = await upsert_shipments(session, CHANNEL, account, build_shipments(w.orders))
    stats["sample_lines"] = await upsert_sample_lines(
        session, CHANNEL, account, build_sample_lines(w.orders)
    )
    stats["sample_applications"] = await upsert_sample_applications(
        session, CHANNEL, account, build_sample_applications(w.applications)
    )
    stats["affiliate_orders"] = await upsert_affiliate_orders(
        session, CHANNEL, account, build_affiliate_orders(w.affiliate_orders)
    )
    await session.commit()
    return SyncReport(days=days, stats=stats, imported=imported)
