"""Dinero atorado en liquidaciones: antigüedad de saldos y depósitos que no cuadran.

Qué se puede medir con los datos (y qué no):
  * TikTok solo importa a `sales_order` las órdenes YA liquidadas (ver tiktok_shop_sync). Una orden enviada o
    entregada que no está en `sales_order` es, por construcción, una orden sin liquidar. De ella solo se
    conocen las fechas (`shipment`), NO el monto: se estima con el ticket promedio de las órdenes liquidadas
    menos la comisión efectiva histórica. Es una estimación y la respuesta expone sus supuestos.
  * Las ventas de afiliados traen su estado de liquidación (`To-SETTLE`) y la base de comisión, que sí es un
    monto real de la venta; se usa como base del estimado. Las que ya están en `sales_order` se descartan
    (TikTok a veces tarda en actualizar su estado).
  * Una orden de afiliado pendiente también es una orden de TikTok pendiente: para no contar el dinero dos
    veces, el origen "afiliados" tiene prioridad y "TikTok Shop" son el resto.
  * Una liquidación "con diferencia" es aquella cuyo depósito no es la suma de sus transacciones (sin los
    conceptos informativos). TikTok no desglosa esos ajustes (p. ej. recuperaciones de saldo): no se puede
    saber QUÉ concepto falta, solo cuánto.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, case, distinct, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.action_center import ActionItem, action_provider
from app.models import (
    Account,
    AffiliateOrder,
    OrderLine,
    SalesOrder,
    Settlement,
    SettlementFee,
    Shipment,
)

CHANNEL = "tiktok_shop"
ORIGIN_TIKTOK = "tiktok_shop"
ORIGIN_AFFILIATE = "affiliate"
ORIGIN_LABELS = {ORIGIN_TIKTOK: "TikTok Shop", ORIGIN_AFFILIATE: "Afiliados"}

# Plazo normal entre la entrega y el depósito. En los datos reales ~93% de las órdenes se liquidan dentro de 8 días.
EXPECTED_SETTLEMENT_DAYS = 7

# Cubetas de antigüedad en días (límites inclusivos). La última no tiene tope.
AGE_BUCKETS: tuple[tuple[str, str, int, int | None], ...] = (
    ("0_7", "0-7", 0, 7),
    ("8_14", "8-14", 8, 14),
    ("15_30", "15-30", 15, 30),
    ("31_60", "31-60", 31, 60),
    ("60_plus", "60+", 61, None),
)

# Estados de TikTok de una orden que ya salió del almacén; las anteriores no pueden estar liquidadas todavía.
SHIPPED_STATUSES = ("IN_TRANSIT", "DELIVERED", "COMPLETED")
NOT_SHIPPED_STATUSES = ("CANCELLED", "AWAITING_SHIPMENT", "AWAITING_COLLECTION", "ON_HOLD", "UNPAID")
AFFILIATE_PENDING_STATUS = "To-SETTLE"

# Diferencia depósito vs transacciones: por debajo de la tolerancia es redondeo; solo las grandes generan acción.
DIFFERENCE_TOLERANCE_USD = 0.50
ACTION_MIN_DIFFERENCE_USD = 25.0
MAX_DIFFERENCES = 50
DEFAULT_OLDEST_LIMIT = 50


@dataclass(frozen=True, slots=True)
class PendingOrder:
    """Una orden sin liquidar, con la fecha desde la que se espera el dinero."""

    order_id: str
    origin: str
    anchor_date: date
    anchor_kind: str  # "entrega" | "envío" | "orden"
    estimated_usd: float
    creator: str | None = None


def bucket_for(age_days: int) -> tuple[str, str, int, int | None]:
    """Cubeta que corresponde a una antigüedad (días negativos, por fechas a futuro, cuentan como 0)."""
    age = max(age_days, 0)
    for bucket in AGE_BUCKETS:
        _key, _label, low, high = bucket
        if age >= low and (high is None or age <= high):
            return bucket
    return AGE_BUCKETS[-1]


def is_overdue(age_days: int) -> bool:
    return age_days > EXPECTED_SETTLEMENT_DAYS


def _r(value: float) -> float:
    return round(float(value), 2)


def _anchor(delivered: datetime | None, collected: datetime | None, ordered: date) -> tuple[date, str]:
    """Desde cuándo corre el plazo: la entrega; si no hay, el envío; si no, la fecha de la orden."""
    if delivered is not None:
        return delivered.date(), "entrega"
    if collected is not None:
        return collected.date(), "envío"
    return ordered, "orden"


# --------------------------------------------------------------------------------------------------
# Supuestos de la estimación
# --------------------------------------------------------------------------------------------------


async def _effective_fee_rate(session: AsyncSession) -> float | None:
    """Comisión efectiva histórica = comisiones / ventas de las liquidaciones con ventas. None sin historial."""
    row = (
        await session.execute(
            select(func.sum(Settlement.fees_amount), func.sum(Settlement.gross_amount))
            .join(Account, Account.id == Settlement.account_id)
            .where(Account.channel_code == CHANNEL, Settlement.gross_amount > 0)
        )
    ).one()
    fees, gross = float(row[0] or 0), float(row[1] or 0)
    return fees / gross if gross > 0 else None


async def _average_order_value(session: AsyncSession) -> tuple[float | None, int]:
    """Venta neta de promos por orden liquidada (ticket promedio) y cuántas órdenes la sustentan."""
    row = (
        await session.execute(
            select(func.sum(OrderLine.gross_sales - OrderLine.promo), func.count(distinct(OrderLine.order_id)))
            .join(Account, Account.id == OrderLine.account_id)
            .where(Account.channel_code == CHANNEL)
        )
    ).one()
    total, orders = float(row[0] or 0), int(row[1] or 0)
    return (total / orders if orders else None), orders


def _net_estimate(gross: float, fee_rate: float | None) -> float:
    """Monto a cobrar estimado: venta menos la comisión efectiva histórica (sin historial, la venta completa)."""
    return gross * (1 - (fee_rate or 0.0))


# --------------------------------------------------------------------------------------------------
# Órdenes pendientes
# --------------------------------------------------------------------------------------------------


def _not_settled(account_col: Any, order_col: Any) -> Any:
    """Condición: la orden NO existe todavía en sales_order (es decir, TikTok no la ha liquidado)."""
    return ~exists().where(and_(SalesOrder.account_id == account_col, SalesOrder.external_order_id == order_col))


async def _pending_affiliate(
    session: AsyncSession, *, avg_order: float | None, fee_rate: float | None
) -> dict[str, PendingOrder]:
    """Ventas de afiliados por liquidar, una por orden, ancladas a la entrega/envío de su paquete."""
    stmt = (
        select(
            AffiliateOrder.external_order_id,
            func.sum(AffiliateOrder.commission_base),
            func.max(AffiliateOrder.creator_username),
            func.min(AffiliateOrder.order_date),
            Shipment.delivered_at,
            Shipment.collected_at,
        )
        .join(Account, Account.id == AffiliateOrder.account_id)
        .outerjoin(
            Shipment,
            and_(
                Shipment.account_id == AffiliateOrder.account_id,
                Shipment.external_order_id == AffiliateOrder.external_order_id,
            ),
        )
        .where(
            Account.channel_code == CHANNEL,
            AffiliateOrder.settlement_status == AFFILIATE_PENDING_STATUS,
            _not_settled(AffiliateOrder.account_id, AffiliateOrder.external_order_id),
            # Un paquete cancelado o aún sin enviar no es dinero atorado: sin envío registrado sí se conserva.
            (Shipment.status.is_(None)) | (Shipment.status.not_in(NOT_SHIPPED_STATUSES)),
        )
        .group_by(AffiliateOrder.external_order_id, Shipment.delivered_at, Shipment.collected_at)
    )
    out: dict[str, PendingOrder] = {}
    for order_id, base, creator, ordered, delivered, collected in (await session.execute(stmt)).all():
        anchor, kind = _anchor(delivered, collected, ordered)
        gross = float(base or 0) or (avg_order or 0.0)  # base 0 = TikTok aún no la calcula
        out[order_id] = PendingOrder(order_id, ORIGIN_AFFILIATE, anchor, kind, _net_estimate(gross, fee_rate), creator)
    return out


async def _pending_tiktok(
    session: AsyncSession, *, avg_order: float | None, fee_rate: float | None, exclude: set[str]
) -> list[PendingOrder]:
    """Órdenes enviadas o entregadas sin liquidar (sin muestras: nunca se cobran) que no son de afiliados."""
    stmt = (
        select(Shipment.external_order_id, Shipment.delivered_at, Shipment.collected_at, Shipment.order_date)
        .join(Account, Account.id == Shipment.account_id)
        .where(
            Account.channel_code == CHANNEL,
            Shipment.status.in_(SHIPPED_STATUSES),
            Shipment.is_sample.is_(False),
            _not_settled(Shipment.account_id, Shipment.external_order_id),
        )
    )
    estimate = _net_estimate(avg_order or 0.0, fee_rate)
    orders: list[PendingOrder] = []
    for order_id, delivered, collected, ordered in (await session.execute(stmt)).all():
        if order_id in exclude:
            continue
        anchor, kind = _anchor(delivered, collected, ordered)
        orders.append(PendingOrder(order_id, ORIGIN_TIKTOK, anchor, kind, estimate))
    return orders


# --------------------------------------------------------------------------------------------------
# Agregación (pura: se prueba sin base de datos)
# --------------------------------------------------------------------------------------------------


def _empty_origin() -> dict[str, float]:
    return {"orders": 0, "pending_usd": 0.0}


def build_buckets(orders: list[PendingOrder], today: date) -> list[dict[str, Any]]:
    """Órdenes y monto estimado por cubeta de antigüedad, con el desglose por origen."""
    acc: dict[str, dict[str, dict[str, float]]] = {
        key: {ORIGIN_TIKTOK: _empty_origin(), ORIGIN_AFFILIATE: _empty_origin()} for key, *_ in AGE_BUCKETS
    }
    for order in orders:
        key = bucket_for((today - order.anchor_date).days)[0]
        cell = acc[key][order.origin]
        cell["orders"] += 1
        cell["pending_usd"] += order.estimated_usd
    out: list[dict[str, Any]] = []
    for key, label, low, high in AGE_BUCKETS:
        cells = acc[key]
        out.append(
            {
                "key": key,
                "label": label,
                "min_days": low,
                "max_days": high,
                "overdue": low > EXPECTED_SETTLEMENT_DAYS,
                "orders": int(sum(c["orders"] for c in cells.values())),
                "pending_usd": _r(sum(c["pending_usd"] for c in cells.values())),
                "tiktok_shop_orders": int(cells[ORIGIN_TIKTOK]["orders"]),
                "tiktok_shop_usd": _r(cells[ORIGIN_TIKTOK]["pending_usd"]),
                "affiliate_orders": int(cells[ORIGIN_AFFILIATE]["orders"]),
                "affiliate_usd": _r(cells[ORIGIN_AFFILIATE]["pending_usd"]),
            }
        )
    return out


def build_origins(orders: list[PendingOrder], today: date) -> list[dict[str, Any]]:
    """Total y atrasado por origen (TikTok Shop / afiliados)."""
    totals: dict[str, dict[str, float]] = {
        o: {"orders": 0, "pending_usd": 0.0, "overdue_orders": 0, "overdue_usd": 0.0} for o in ORIGIN_LABELS
    }
    for order in orders:
        t = totals[order.origin]
        t["orders"] += 1
        t["pending_usd"] += order.estimated_usd
        if is_overdue((today - order.anchor_date).days):
            t["overdue_orders"] += 1
            t["overdue_usd"] += order.estimated_usd
    return [
        {
            "origin": origin,
            "label": ORIGIN_LABELS[origin],
            "orders": int(t["orders"]),
            "pending_usd": _r(t["pending_usd"]),
            "overdue_orders": int(t["overdue_orders"]),
            "overdue_usd": _r(t["overdue_usd"]),
        }
        for origin, t in totals.items()
    ]


def oldest_orders(orders: list[PendingOrder], today: date, limit: int) -> list[dict[str, Any]]:
    """Las órdenes que llevan más tiempo esperando su dinero (desempata por monto y por id, para ser estable)."""
    ranked = sorted(orders, key=lambda o: (o.anchor_date, -o.estimated_usd, o.order_id))[:limit]
    return [
        {
            "order_id": o.order_id,
            "origin": o.origin,
            "origin_label": ORIGIN_LABELS[o.origin],
            "creator": o.creator,
            "anchor_date": o.anchor_date,
            "anchor_kind": o.anchor_kind,
            "age_days": max((today - o.anchor_date).days, 0),
            "bucket": bucket_for((today - o.anchor_date).days)[1],
            "overdue": is_overdue((today - o.anchor_date).days),
            "estimated_usd": _r(o.estimated_usd),
        }
        for o in ranked
    ]


# --------------------------------------------------------------------------------------------------
# Liquidaciones con diferencia
# --------------------------------------------------------------------------------------------------


async def settlement_differences(session: AsyncSession) -> tuple[list[dict[str, Any]], float, int]:
    """Liquidaciones cuyo depósito no es la suma de sus transacciones. Devuelve (lista, suma, total)."""
    tx_sum = func.coalesce(func.sum(case((SettlementFee.kind != "memo", SettlementFee.amount), else_=0)), 0)
    stmt = (
        select(Settlement, tx_sum)
        .outerjoin(SettlementFee, SettlementFee.settlement_id == Settlement.id)
        .group_by(Settlement.id)
    )
    flagged: list[tuple[Settlement, float, float]] = []
    for settlement, tx in (await session.execute(stmt)).all():
        diff = float(settlement.net_payout) - float(tx)
        if abs(diff) > DIFFERENCE_TOLERANCE_USD:
            flagged.append((settlement, float(tx), diff))
    flagged.sort(key=lambda t: (-abs(t[2]), t[0].id))
    shown = flagged[:MAX_DIFFERENCES]
    concepts = await _concepts_by_settlement(session, [s.id for s, _, _ in shown])
    rows = [
        {
            "settlement_id": s.id,
            "external_id": s.external_id,
            "statement_date": s.statement_date,
            "payout_date": s.payout_date,
            "status": s.status,
            "deposit_usd": _r(float(s.net_payout)),
            "transactions_usd": _r(tx),
            "difference_usd": _r(diff),
            "adjustment_usd": _r(float(s.adjustment_amount)),
            "concepts": concepts.get(s.id, []),
        }
        for s, tx, diff in shown
    ]
    return rows, _r(sum(d for _, _, d in flagged)), len(flagged)


async def _concepts_by_settlement(session: AsyncSession, ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Conceptos que sí traen las transacciones de cada liquidación (sin los informativos)."""
    if not ids:
        return {}
    stmt = (
        select(SettlementFee.settlement_id, SettlementFee.concept, SettlementFee.kind, func.sum(SettlementFee.amount))
        .where(SettlementFee.settlement_id.in_(ids), SettlementFee.kind != "memo")
        .group_by(SettlementFee.settlement_id, SettlementFee.concept, SettlementFee.kind)
    )
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for sid, concept, kind, amount in (await session.execute(stmt)).all():
        out[sid].append({"concept": concept, "kind": kind, "amount_usd": _r(float(amount))})
    for items in out.values():
        items.sort(key=lambda c: -abs(c["amount_usd"]))
    return out


# --------------------------------------------------------------------------------------------------
# Reporte
# --------------------------------------------------------------------------------------------------


async def settlement_aging_report(
    session: AsyncSession, *, today: date, oldest_limit: int = DEFAULT_OLDEST_LIMIT
) -> dict[str, Any]:
    """Antigüedad del dinero pendiente de liquidar y liquidaciones que no cuadran."""
    fee_rate = await _effective_fee_rate(session)
    avg_order, sample_orders = await _average_order_value(session)
    affiliate = await _pending_affiliate(session, avg_order=avg_order, fee_rate=fee_rate)
    tiktok = await _pending_tiktok(session, avg_order=avg_order, fee_rate=fee_rate, exclude=set(affiliate))
    orders = [*affiliate.values(), *tiktok]
    buckets = build_buckets(orders, today)
    differences, difference_usd, difference_count = await settlement_differences(session)
    overdue = [b for b in buckets if b["overdue"]]
    return {
        "as_of": today,
        "expected_days": EXPECTED_SETTLEMENT_DAYS,
        "assumptions": {
            "fee_rate": None if fee_rate is None else round(fee_rate, 4),
            "avg_order_usd": None if avg_order is None else _r(avg_order),
            "sample_orders": sample_orders,
            "note": (
                "Las órdenes de TikTok sin liquidar no traen monto: se estiman con el ticket promedio de las "
                "liquidadas menos la comisión efectiva histórica. Las de afiliados usan su base de comisión."
            ),
        },
        "totals": {
            "orders": len(orders),
            "pending_usd": _r(sum(o.estimated_usd for o in orders)),
            "overdue_orders": sum(b["orders"] for b in overdue),
            "overdue_usd": _r(sum(b["pending_usd"] for b in overdue)),
            "oldest_days": max((max((today - o.anchor_date).days, 0) for o in orders), default=0),
            "difference_count": difference_count,
            "difference_usd": difference_usd,
        },
        "buckets": buckets,
        "by_origin": build_origins(orders, today),
        "oldest_orders": oldest_orders(orders, today, oldest_limit),
        "differences": differences,
    }


# --------------------------------------------------------------------------------------------------
# Proveedor del Centro de acciones
# --------------------------------------------------------------------------------------------------


def _bucket_action(bucket: dict[str, Any], expected_days: int) -> ActionItem:
    orders, usd = bucket["orders"], bucket["pending_usd"]
    return ActionItem(
        key=f"settlement_aging:bucket_{bucket['key']}",
        kind="settlement_aging",
        title=f"{orders} órdenes llevan {bucket['label']} días sin liquidarse",
        detail=(
            f"Pasaron más de {expected_days} días desde la entrega o el envío y TikTok no las ha pagado "
            f"(TikTok Shop: {bucket['tiktok_shop_orders']}, afiliados: {bucket['affiliate_orders']}). "
            "Revisa su estado en Seller Center y abre un caso si no hay motivo."
        ),
        impact_usd=usd,
        impact_basis="Ticket promedio liquidado (o base de comisión de afiliados) menos la comisión efectiva histórica.",
        effort="medium",
        target="settlements",
        entity=bucket["key"],
        meta={"orders": orders, "bucket": bucket["label"]},
    )


def _difference_action(row: dict[str, Any]) -> ActionItem:
    diff = row["difference_usd"]
    sign = "menos" if diff < 0 else "más"
    return ActionItem(
        key=f"settlement_aging:diff_{row['settlement_id']}",
        kind="settlement_aging",
        title=f"La liquidación {row['external_id']} depositó ${abs(diff):,.2f} {sign} que sus transacciones",
        detail=(
            f"Depósito ${row['deposit_usd']:,.2f} contra ${row['transactions_usd']:,.2f} en transacciones"
            f" (fecha {row['statement_date']}). TikTok no desglosa el ajuste: pide el detalle en Seller Center."
        ),
        impact_usd=abs(diff),
        impact_basis="Valor absoluto de depósito menos la suma de las transacciones de la liquidación.",
        effort="medium",
        target="settlements",
        entity=str(row["settlement_id"]),
        meta={"settlement_id": row["settlement_id"], "difference_usd": diff},
    )


@action_provider("settlement_aging", "Dinero atorado")
async def settlement_aging_actions(session: AsyncSession) -> list[ActionItem]:
    """Una acción por cubeta atrasada con dinero y una por liquidación con diferencia relevante."""
    report = await settlement_aging_report(session, today=datetime.now(UTC).date())
    items = [_bucket_action(b, report["expected_days"]) for b in report["buckets"] if b["overdue"] and b["orders"]]
    items += [_difference_action(d) for d in report["differences"] if abs(d["difference_usd"]) >= ACTION_MIN_DIFFERENCE_USD]
    return items
