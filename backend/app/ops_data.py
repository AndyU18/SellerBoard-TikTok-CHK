"""Guardado idempotente de devoluciones/cancelaciones (RMA) y envíos (logística).

Cada caso se identifica por (cuenta, id externo) y cada envío por (cuenta, orden): volver a
sincronizar actualiza el estado (p. ej. una devolución que pasa a "completa") sin duplicar.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    AffiliateOrder,
    LiveSession,
    ReturnCase,
    ReturnCaseLine,
    SampleApplication,
    SampleLine,
    Shipment,
    TrafficDaily,
    VideoPerformance,
)

CHUNK = 500
CASE_FIELDS = (
    "kind",
    "case_type",
    "status",
    "reason",
    "reason_code",
    "initiated_by",
    "external_order_id",
    "case_date",
    "created_at",
    "updated_at",
)
SHIPMENT_FIELDS = (
    "order_date",
    "status",
    "shipping_type",
    "fulfillment_type",
    "delivery_option",
    "carrier",
    "tracking_number",
    "is_sample",
    "ordered_at",
    "rts_at",
    "collected_at",
    "delivered_at",
    "due_at",
)


def _dec(value: Any) -> Decimal:
    return Decimal(str(round(float(value or 0), 4)))


async def ensure_account(session: AsyncSession, channel: str, name: str) -> Account:
    account = await session.scalar(
        select(Account).where(Account.channel_code == channel, Account.name == name)
    )
    if account is None:
        account = Account(channel_code=channel, name=name)
        session.add(account)
        await session.flush()
    return account


async def upsert_return_cases(
    session: AsyncSession, channel: str, account_name: str, cases: list[dict[str, Any]]
) -> int:
    """Inserta o actualiza los casos y reemplaza sus líneas. Devuelve cuántos procesó."""
    if not cases:
        return 0
    account = await ensure_account(session, channel, account_name)
    existing = {
        c.external_id: c
        for c in await session.scalars(select(ReturnCase).where(ReturnCase.account_id == account.id))
    }
    stored: list[tuple[ReturnCase, dict[str, Any]]] = []
    for row in cases:
        case = existing.get(row["external_id"])
        if case is None:
            case = ReturnCase(account_id=account.id, external_id=row["external_id"])
            session.add(case)
        for field in CASE_FIELDS:
            setattr(case, field, row[field])
        case.refund_total = _dec(row["refund_total"])
        stored.append((case, row))
    await session.flush()

    ids = [case.id for case, _ in stored]
    for start in range(0, len(ids), CHUNK):
        await session.execute(
            delete(ReturnCaseLine).where(ReturnCaseLine.case_id.in_(ids[start : start + CHUNK]))
        )
    for case, row in stored:
        for line in row["lines"]:
            session.add(
                ReturnCaseLine(
                    case_id=case.id,
                    sku=line["sku"],
                    product_name=line["product_name"],
                    units=int(line["units"]),
                    refund_total=_dec(line["refund_total"]),
                )
            )
    await session.flush()
    return len(cases)


async def upsert_shipments(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    account = await ensure_account(session, channel, account_name)
    existing = {
        s.external_order_id: s
        for s in await session.scalars(select(Shipment).where(Shipment.account_id == account.id))
    }
    for row in rows:
        shipment = existing.get(row["external_order_id"])
        if shipment is None:
            shipment = Shipment(account_id=account.id, external_order_id=row["external_order_id"])
            session.add(shipment)
        for field in SHIPMENT_FIELDS:
            setattr(shipment, field, row[field])
    await session.flush()
    return len(rows)


def _decimal_or_none(value: Any) -> Decimal | None:
    return None if value is None else _dec(value)


async def _upsert_keyed(
    session: AsyncSession,
    model: Any,
    account: Account,
    key: tuple[str, ...],
    rows: list[dict[str, Any]],
    money: tuple[str, ...] = (),
) -> int:
    """Upsert genérico: `key` son las columnas que identifican la fila dentro de la cuenta."""
    existing = {
        tuple(getattr(r, k) for k in key): r
        for r in await session.scalars(select(model).where(model.account_id == account.id))
    }
    for row in rows:
        values = {k: (_decimal_or_none(v) if k in money else v) for k, v in row.items()}
        found = existing.get(tuple(row[k] for k in key))
        if found is None:
            found = model(account_id=account.id)
            session.add(found)
            existing[tuple(row[k] for k in key)] = found
        for k, v in values.items():
            setattr(found, k, v)
    await session.flush()
    return len(rows)


async def upsert_sample_applications(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    account = await ensure_account(session, channel, account_name)
    return await _upsert_keyed(
        session, SampleApplication, account, ("external_id",), rows, ("creator_gmv", "commission_rate")
    )


async def upsert_sample_lines(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    account = await ensure_account(session, channel, account_name)
    return await _upsert_keyed(session, SampleLine, account, ("external_order_id", "sku"), rows)


async def upsert_traffic(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    account = await ensure_account(session, channel, account_name)
    return await _upsert_keyed(session, TrafficDaily, account, ("day", "source"), rows, ("gmv",))


async def upsert_live_sessions(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    account = await ensure_account(session, channel, account_name)
    return await _upsert_keyed(
        session,
        LiveSession,
        account,
        ("external_id",),
        rows,
        ("gmv", "gmv_24h", "click_to_order_rate", "avg_price"),
    )


async def upsert_video_performance(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    account = await ensure_account(session, channel, account_name)
    return await _upsert_keyed(
        session, VideoPerformance, account, ("video_id",), rows, ("gmv", "click_through_rate")
    )


async def upsert_affiliate_orders(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    account = await ensure_account(session, channel, account_name)
    return await _upsert_keyed(
        session,
        AffiliateOrder,
        account,
        ("external_order_id", "sku_id"),
        rows,
        ("commission_base", "commission", "commission_rate"),
    )
