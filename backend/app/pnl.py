"""Estado de resultados mensual con gastos indirectos, y administración de esos gastos.

Por mes y canal:

    ventas netas   = ventas brutas - promociones
    utilidad bruta = ventas netas - comisiones - envío - reembolsos - costo del producto
    contribución   = utilidad bruta - publicidad
    utilidad neta  = contribución - gastos indirectos prorrateados

Los gastos indirectos (software, nómina, bodega...) viven en `finance.expense`. Uno puntual cuenta en
el mes de su fecha; uno mensual, en cada mes de su vigencia; uno anual, en 1/12 durante 12 meses. Si el
gasto tiene canal, es todo de ese canal; si no, se reparte entre los canales según sus ventas netas del mes.

La capa oro no incluye estos gastos (su utilidad es la contribución): este reporte es el único lugar donde
aparecen, para no mezclar costos fijos con la utilidad por producto.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.forecast import month_end, month_start
from app.gold import load_gold_frame
from app.models import Channel, Expense, ExpenseCategory
from app.profit import add_line_profit

RECURRENCES = ("one_time", "monthly", "annual")
INPUT_FIELDS = ("gross_sales", "promo", "platform_fees", "shipping_cost", "refund_amount", "cogs", "ad_spend", "expenses")
MONEY_FIELDS = (
    "gross_sales",
    "promo",
    "net_sales",
    "platform_fees",
    "shipping_cost",
    "refund_amount",
    "cogs",
    "gross_profit",
    "ad_spend",
    "contribution",
    "expenses",
    "net_profit",
)


class ExpenseError(ValueError):
    """Datos de un gasto no válidos."""


def add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def months_between(first: date, last: date) -> list[date]:
    out, cur = [], month_start(first)
    while cur <= month_start(last):
        out.append(cur)
        cur = add_months(cur, 1)
    return out


def expense_for_month(e: Expense, month: date) -> float:
    """Parte del gasto que cae en `month` (primer día del mes)."""
    amount = float(e.amount)
    if not e.is_active:
        return 0.0
    ms, me = month, month_end(month)
    if e.recurrence == "one_time":
        return amount if ms <= e.start_date <= me else 0.0
    if e.start_date > me or (e.end_date and e.end_date < ms):
        return 0.0
    if e.recurrence == "monthly":
        return amount
    # anual: 1/12 durante 12 meses desde el mes de inicio (o hasta que termine)
    first = month_start(e.start_date)
    last = add_months(first, 11)
    if e.end_date:
        last = min(last, month_start(e.end_date))
    return amount / 12 if first <= ms <= last else 0.0


def _blank() -> dict[str, float]:
    return dict.fromkeys(INPUT_FIELDS, 0.0)


def _row(values: dict[str, float]) -> dict[str, Any]:
    net_sales = values["gross_sales"] - values["promo"]
    gross_profit = (
        net_sales - values["platform_fees"] - values["shipping_cost"] - values["refund_amount"] - values["cogs"]
    )
    contribution = gross_profit - values["ad_spend"]
    net_profit = contribution - values["expenses"]
    return {
        **{k: values[k] for k in INPUT_FIELDS},
        "net_sales": net_sales,
        "gross_profit": gross_profit,
        "contribution": contribution,
        "net_profit": net_profit,
        "margin": (net_profit / net_sales) if net_sales > 0 else None,
    }


def _sum_rows(rows: list[dict[str, Any]], extra_expenses: float = 0.0) -> dict[str, float]:
    total = _blank()
    for r in rows:
        for k in total:
            total[k] += r[k]
    total["expenses"] += extra_expenses
    return total


async def pnl_report(
    session: AsyncSession,
    *,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    end = end or today
    start = start or add_months(month_start(end), -5)
    months = months_between(start, end)
    first, last = months[0], month_end(months[-1])

    df = await load_gold_frame(session, start=first, end=last)
    sums: dict[tuple[date, str], dict[str, float]] = defaultdict(_blank)
    if df.height:
        for r in add_line_profit(df).iter_rows(named=True):
            bucket = sums[(month_start(r["date"]), r["channel"])]
            for field in INPUT_FIELDS:
                if field != "expenses":
                    bucket[field] += r[field]

    categories = {c.id: c.name for c in await session.scalars(select(ExpenseCategory))}
    expenses = list(await session.scalars(select(Expense).where(Expense.is_active.is_(True))))

    out_months: list[dict[str, Any]] = []
    for m in months:
        channels = sorted({ch for (mm, ch) in sums if mm == m})
        net_by_channel = {ch: sums[(m, ch)]["gross_sales"] - sums[(m, ch)]["promo"] for ch in channels}
        total_net = sum(max(v, 0.0) for v in net_by_channel.values())
        by_category: dict[str, float] = defaultdict(float)
        allocated: dict[str, float] = defaultdict(float)
        unallocated = 0.0
        for e in expenses:
            amount = expense_for_month(e, m)
            if not amount:
                continue
            by_category[categories.get(e.category_id, "Otros")] += amount
            if e.channel_code:
                allocated[e.channel_code] += amount
            elif total_net > 0:
                for ch in channels:
                    allocated[ch] += amount * max(net_by_channel[ch], 0.0) / total_net
            else:
                unallocated += amount
        # Un gasto de un canal que ese mes no vendió igual cuenta: se crea su fila en ceros.
        for ch in allocated:
            if ch not in channels:
                channels.append(ch)
        rows = []
        for ch in sorted(channels):
            values = dict(sums[(m, ch)]) if (m, ch) in sums else _blank()
            values["expenses"] = allocated.get(ch, 0.0)
            rows.append({"channel": ch, **_row(values)})
        shown = [r for r in rows if channel is None or r["channel"] == channel.value]
        out_months.append(
            {
                "month": m.isoformat(),
                "channels": shown,
                "total": _row(_sum_rows(shown, unallocated if channel is None else 0.0)),
                "unallocated_expenses": unallocated if channel is None else 0.0,
                "expenses_by_category": [
                    {"category": k, "amount": v} for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])
                ],
            }
        )

    return {
        "start": first.isoformat(),
        "end": months[-1].isoformat(),
        "months": out_months,
        "summary": _row(_sum_rows([om["total"] for om in out_months])),
        "note": "La utilidad por producto de las demás pantallas no incluye gastos indirectos; este estado de resultados sí.",
    }


def pnl_csv(report: dict[str, Any]) -> str:
    """El estado de resultados como CSV (una fila por mes y canal, más una de total por mes)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["month", "channel", *MONEY_FIELDS, "margin"])
    for m in report["months"]:
        for r in [*m["channels"], {"channel": "TOTAL", **m["total"]}]:
            margin = "" if r["margin"] is None else f"{r['margin']:.4f}"
            writer.writerow([m["month"][:7], r["channel"], *[f"{r[f]:.2f}" for f in MONEY_FIELDS], margin])
    return buffer.getvalue()


# ---------------------------------------------------------------------------- gastos


def expense_dict(e: Expense, categories: dict[int, ExpenseCategory]) -> dict[str, Any]:
    cat = categories.get(e.category_id)
    return {
        "id": e.id,
        "category_id": e.category_id,
        "category": cat.name if cat else None,
        "description": e.description,
        "amount": float(e.amount),
        "recurrence": e.recurrence,
        "start_date": e.start_date.isoformat(),
        "end_date": e.end_date.isoformat() if e.end_date else None,
        "channel": e.channel_code,
        "is_active": e.is_active,
    }


async def list_categories(session: AsyncSession) -> list[ExpenseCategory]:
    query = select(ExpenseCategory).where(ExpenseCategory.is_active.is_(True)).order_by(ExpenseCategory.name)
    return list((await session.scalars(query)).all())


async def list_expenses(session: AsyncSession) -> list[dict[str, Any]]:
    categories = {c.id: c for c in await session.scalars(select(ExpenseCategory))}
    rows = await session.scalars(select(Expense).order_by(Expense.start_date.desc(), Expense.id.desc()))
    return [expense_dict(e, categories) for e in rows]


def validate_expense(recurrence: str, amount: float, start: date, end: date | None, channel: str | None) -> None:
    if recurrence not in RECURRENCES:
        raise ExpenseError(f"Frecuencia desconocida: {recurrence}")
    if amount < 0:
        raise ExpenseError("El monto no puede ser negativo")
    if end and end < start:
        raise ExpenseError("La fecha de fin no puede ser anterior a la de inicio")
    if channel and channel not in {c.value for c in Channel}:
        raise ExpenseError(f"Canal desconocido: {channel}")


async def create_expense(
    session: AsyncSession,
    *,
    category_id: int,
    description: str,
    amount: float,
    start_date: date,
    recurrence: str = "one_time",
    end_date: date | None = None,
    channel: str | None = None,
) -> Expense:
    validate_expense(recurrence, amount, start_date, end_date, channel)
    if not description.strip():
        raise ExpenseError("Falta la descripción")
    if await session.get(ExpenseCategory, category_id) is None:
        raise ExpenseError("La categoría no existe")
    expense = Expense(
        category_id=category_id,
        description=description.strip(),
        amount=Decimal(str(amount)),
        recurrence=recurrence,
        start_date=start_date,
        end_date=end_date,
        channel_code=channel,
    )
    session.add(expense)
    await session.flush()
    return expense


async def delete_expense(session: AsyncSession, expense_id: int) -> bool:
    result = await session.execute(delete(Expense).where(Expense.id == expense_id))
    return bool(result.rowcount)
