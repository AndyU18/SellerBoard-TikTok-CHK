"""Guarda el libro de Redo (cargos, facturas y recargas) tal como lo entrega la API de facturación.

Todo es idempotente: se actualiza por id de Redo, así sincronizar de nuevo no duplica nada.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.persist import _chunks
from app.models import RedoBill, RedoCharge, RedoTopup


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _money(node: dict[str, Any] | None) -> Decimal:
    return Decimal(str((node or {}).get("amount") or 0))


async def _existing(session: AsyncSession, model: Any, ids: list[str]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for part in _chunks(ids):
        for row in await session.scalars(select(model).where(model.id.in_(part))):
            found[row.id] = row
    return found


async def upsert_charges(session: AsyncSession, charges: list[dict[str, Any]]) -> int:
    charges = list({c["id"]: c for c in charges}.values())  # un id repetido en la entrega cuenta una vez
    existing = await _existing(session, RedoCharge, [c["id"] for c in charges])
    for c in charges:
        values = {
            "order_name": (c.get("orderName") or "").strip() or None,
            "subtype": c.get("subtype") or "Unknown",
            "type": c.get("type"),
            "product": c.get("product"),
            "amount": _money(c.get("amount")),
            "currency": (c.get("amount") or {}).get("currency") or "USD",
            "note": c.get("note"),
            "fulfillment_group_id": c.get("fulfillmentGroupId"),
            "shipment_id": c.get("shipmentId"),
            "reverses_charge_id": c.get("reversesChargeId"),
            "order_date": _dt(c.get("orderDate")),
            "created_at": _dt(c["createdAt"]),
        }
        row = existing.get(c["id"])
        if row is None:
            session.add(RedoCharge(id=c["id"], **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    await session.flush()
    return len(charges)


async def upsert_bills(session: AsyncSession, bills: list[dict[str, Any]]) -> int:
    existing = await _existing(session, RedoBill, [b["id"] for b in bills])
    for b in bills:
        values = {
            "type": b.get("type"),
            "status": b.get("status"),
            "period_start": _day(b.get("periodStart")),
            "period_end": _day(b.get("periodEnd")),
            "charge_total": _money(b.get("chargeTotal")),
            "opening_balance": _money(b.get("openingBalance")),
            "closing_balance": _money(b.get("closingBalance")),
            "billed_at": _dt(b.get("billedAt")),
            "due_at": _dt(b.get("dueAt")),
        }
        row = existing.get(b["id"])
        if row is None:
            session.add(RedoBill(id=b["id"], **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    await session.flush()
    return len(bills)


async def upsert_topups(session: AsyncSession, topups: list[dict[str, Any]]) -> int:
    existing = await _existing(session, RedoTopup, [t["id"] for t in topups])
    for t in topups:
        values = {
            "amount": _money(t.get("amount")),
            "status": t.get("status"),
            "failure_message": t.get("failureMessage"),
            "created_at": _dt(t["createdAt"]),
        }
        row = existing.get(t["id"])
        if row is None:
            session.add(RedoTopup(id=t["id"], **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    await session.flush()
    return len(topups)
