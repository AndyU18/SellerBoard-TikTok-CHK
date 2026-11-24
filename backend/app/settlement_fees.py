"""Desglose por concepto de las liquidaciones de TikTok Shop: construcción y guardado idempotente.

Cada transacción del estado de cuenta trae montos separados (comisión de referencia, de afiliado,
de pago, envío, descuentos...). Aquí se convierten en filas (transacción, concepto) con el monto
firmado de TikTok: ingresos positivos, costos negativos.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Settlement, SettlementFee

# código -> (nombre en español, tipo). El orden es el de la cascada del depósito.
CONCEPTS: dict[str, tuple[str, str]] = {
    "gross_sales": ("Ventas brutas", "revenue"),
    "refunds": ("Reembolsos", "revenue"),
    "seller_discount": ("Descuentos del vendedor", "revenue"),
    "referral_fee": ("Comisión de referencia", "fee"),
    "platform_commission": ("Comisión de plataforma", "fee"),
    "transaction_fee": ("Comisión de pago", "fee"),
    "affiliate_commission": ("Comisión de afiliado", "fee"),
    "affiliate_ads_commission": ("Comisión de afiliado (anuncios)", "fee"),
    "affiliate_partner_commission": ("Comisión de socio afiliado", "fee"),
    "refund_admin_fee": ("Cargo administrativo de reembolso", "fee"),
    # Hallazgo: aquí cae lo que la API 202309 no desglosa (Smart Promotion, campañas: ver 202501).
    "other_fees": ("Promoción y campañas (otras comisiones)", "fee"),
    "shipping": ("Envío", "shipping"),
    "adjustment": ("Ajustes", "adjustment"),
    "sales_tax": ("Impuesto de venta (informativo)", "memo"),
    "platform_discount": ("Descuento de plataforma (subsidio, informativo)", "memo"),
}

# concepto -> campos de la transacción que se suman.
_SIMPLE: dict[str, tuple[str, ...]] = {
    "gross_sales": ("gross_sales_amount",),
    "refunds": ("gross_sales_refund_amount",),
    "seller_discount": ("seller_discount_amount", "seller_discount_refund_amount"),
    "referral_fee": ("referral_fee_amount",),
    "platform_commission": ("platform_commission_amount",),
    "transaction_fee": ("transaction_fee_amount",),
    "affiliate_commission": ("affiliate_commission_amount",),
    "affiliate_ads_commission": ("affiliate_ads_commission_amount",),
    "affiliate_partner_commission": ("affiliate_partner_commission_amount",),
    "refund_admin_fee": ("refund_administration_fee_amount",),
    "shipping": ("shipping_cost_amount",),
    "adjustment": ("adjustment_amount",),
    "sales_tax": ("sales_tax_amount",),
    "platform_discount": ("platform_discount_amount", "platform_discount_refund_amount"),
}
_KNOWN_FEES = (
    "referral_fee",
    "platform_commission",
    "transaction_fee",
    "affiliate_commission",
    "affiliate_ads_commission",
    "affiliate_partner_commission",
    "refund_admin_fee",
)


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def transaction_concepts(tx: dict[str, Any]) -> dict[str, float]:
    """Monto firmado por concepto de una transacción (omite los ceros)."""
    out = {code: sum(_f(tx.get(k)) for k in keys) for code, keys in _SIMPLE.items()}
    # fee_amount es el total de comisiones: lo que no cae en un concepto conocido va a "otras".
    out["other_fees"] = _f(tx.get("fee_amount")) - sum(out[c] for c in _KNOWN_FEES)
    return {code: round(v, 4) for code, v in out.items() if abs(v) >= 0.005}


def build_settlement_fees(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transacciones (cada una con `statement_id`) -> filas de settlement_fee."""
    rows: list[dict[str, Any]] = []
    for tx in transactions:
        try:
            fee_date = datetime.fromtimestamp(int(tx["order_create_time"]), tz=UTC).date()
        except (KeyError, TypeError, ValueError, OverflowError):
            fee_date = None
        for code, amount in transaction_concepts(tx).items():
            rows.append(
                {
                    "statement_id": str(tx["statement_id"]),
                    "transaction_id": str(tx.get("id") or tx.get("order_id")),
                    "external_order_id": tx.get("order_id"),
                    "concept": code,
                    "kind": CONCEPTS[code][1],
                    "amount": amount,
                    "fee_date": fee_date,
                }
            )
    return rows


async def upsert_settlement_fees(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    """Guarda el desglose; vuelve a sincronizar reemplaza el de cada liquidación (sin duplicar)."""
    if not rows:
        return 0
    account = await session.scalar(
        select(Account).where(Account.channel_code == channel, Account.name == account_name)
    )
    if account is None:
        return 0
    settlements = {
        s.external_id: s.id
        for s in await session.scalars(select(Settlement).where(Settlement.account_id == account.id))
    }
    by_statement: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["statement_id"] in settlements:
            by_statement.setdefault(row["statement_id"], []).append(row)
    saved = 0
    for statement_id, items in by_statement.items():
        await session.execute(
            delete(SettlementFee).where(
                SettlementFee.account_id == account.id, SettlementFee.statement_id == statement_id
            )
        )
        session.add_all(
            SettlementFee(
                settlement_id=settlements[statement_id],
                account_id=account.id,
                statement_id=statement_id,
                transaction_id=r["transaction_id"],
                external_order_id=r["external_order_id"],
                concept=r["concept"],
                kind=r["kind"],
                amount=Decimal(str(r["amount"])),
                fee_date=r["fee_date"],
            )
            for r in items
        )
        saved += len(items)
    await session.flush()
    return saved
