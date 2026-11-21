"""Liquidaciones de las plataformas (flujo de caja): guardado idempotente.

Una liquidación se identifica por (cuenta, id externo); volver a sincronizar la actualiza, así un
pago que estaba "processing" pasa a "paid" sin duplicarse.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Settlement

FIELDS = (
    "statement_date",
    "payout_date",
    "status",
    "gross_amount",
    "fees_amount",
    "shipping_amount",
    "adjustment_amount",
    "net_payout",
)
MONEY_FIELDS = {"gross_amount", "fees_amount", "shipping_amount", "adjustment_amount", "net_payout"}


def _value(field: str, raw: Any) -> Any:
    return Decimal(str(round(float(raw or 0), 4))) if field in MONEY_FIELDS else raw


async def upsert_settlements(
    session: AsyncSession, channel: str, account_name: str, rows: list[dict[str, Any]]
) -> int:
    """Inserta o actualiza las liquidaciones de una cuenta. Devuelve cuántas filas procesó."""
    if not rows:
        return 0
    account = await session.scalar(
        select(Account).where(Account.channel_code == channel, Account.name == account_name)
    )
    if account is None:
        account = Account(channel_code=channel, name=account_name)
        session.add(account)
        await session.flush()

    existing = {
        s.external_id: s
        for s in await session.scalars(select(Settlement).where(Settlement.account_id == account.id))
    }
    for row in rows:
        values = {f: _value(f, row.get(f)) for f in FIELDS}
        found = existing.get(row["external_id"])
        if found is None:
            session.add(Settlement(account_id=account.id, external_id=row["external_id"], **values))
        else:
            for key, value in values.items():
                setattr(found, key, value)
    await session.flush()
    return len(rows)
