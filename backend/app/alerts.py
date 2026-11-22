"""Alertas: lo que requiere atención hoy, calculado al vuelo con los reportes que ya existen.

No se guardan: siempre reflejan el estado actual de los datos. Cada alerta trae una severidad
(critical | warning | info), una categoría y, cuando aplica, el canal y el SKU afectados.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.gold import load_gold_frame
from app.profit import summarize
from app.reports import (
    cashflow_report,
    inventory_report,
    logistics_report,
    returns_report,
    seller_performance_report,
)
from app.sync_jobs import sync_status

LOSS_MIN_UNITS = 3  # una pérdida con menos unidades vendidas es ruido
LOSS_CRITICAL = -50.0  # pérdida acumulada desde la que es crítica
RETURN_RATE_ALERT = 0.20
RETURN_MIN_UNITS = 2
MAX_PER_CATEGORY = 8
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _money(value: float) -> str:
    return f"${value:,.2f}" if value >= 0 else f"-${abs(value):,.2f}"


def _alert(
    key: str,
    severity: str,
    category: str,
    title: str,
    detail: str,
    channel: str | None = None,
    sku: str | None = None,
) -> dict[str, Any]:
    return {
        "id": key,
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
        "channel": channel,
        "sku": sku,
    }


async def _profit_alerts(session: AsyncSession, today: date) -> list[dict[str, Any]]:
    df = await load_gold_frame(session, start=today - timedelta(days=30), end=today)
    if not df.height:
        return []
    names = df.group_by("sku").agg(pl.col("product_name").drop_nulls().first())
    rows = summarize(df, ["channel", "sku"]).join(names, on="sku", how="left").to_dicts()
    out: list[dict[str, Any]] = []

    losers = sorted(
        (r for r in rows if r["net_profit"] < 0 and r["units"] >= LOSS_MIN_UNITS),
        key=lambda r: r["net_profit"],
    )
    for r in losers[:MAX_PER_CATEGORY]:
        out.append(
            _alert(
                f"loss:{r['channel']}:{r['sku']}",
                "critical" if r["net_profit"] <= LOSS_CRITICAL else "warning",
                "profit",
                f"{r['sku']} pierde dinero en {r['channel']}",
                f"{r['units']} unidades en 30 días: utilidad {_money(r['net_profit'])} "
                f"({_money(r['unit_profit'] or 0)} por unidad). Revisa precio, promoción y costos.",
                r["channel"],
                r["sku"],
            )
        )
    if len(losers) > MAX_PER_CATEGORY:
        rest = losers[MAX_PER_CATEGORY:]
        out.append(
            _alert(
                "loss:more",
                "warning",
                "profit",
                f"{len(rest)} productos más pierden dinero",
                f"Pérdida acumulada de {_money(sum(r['net_profit'] for r in rest))} en 30 días. "
                "Filtra Pareto ABC por utilidad para verlos todos.",
            )
        )

    by_sku = summarize(df, ["sku"]).to_dicts()
    sold = [r for r in by_sku if r["units"] > 0 and r["gross_sales"] > 0]
    missing = [r for r in sold if r["cogs"] == 0]
    if missing:
        total = sum(r["gross_sales"] for r in sold)
        share = sum(r["gross_sales"] for r in missing) / total if total else 0
        out.append(
            _alert(
                "cogs:missing",
                "warning",
                "costs",
                f"{len(missing)} productos vendidos sin costo",
                f"Representan {share:.0%} de las ventas de 30 días; su utilidad aparece inflada. "
                "Cárgalos en Costos (COGS) o importa el CSV.",
            )
        )
    return out


async def _inventory_alerts(session: AsyncSession, today: date) -> list[dict[str, Any]]:
    inv = await inventory_report(session, today=today)
    rows = inv["rows"]
    out: list[dict[str, Any]] = []
    for r in [x for x in rows if x["status"] == "out_of_stock"][:MAX_PER_CATEGORY]:
        out.append(
            _alert(
                f"stock:out:{r['account']}:{r['sku']}",
                "critical",
                "inventory",
                f"{r['sku']} agotado y se estaba vendiendo",
                f"{r['units_sold']} unidades en {inv['days']} días (~{_money(r['sales'] / inv['days'])} de ventas al día "
                "que se pierden sin existencias).",
                r["channel"],
                r["sku"],
            )
        )
    for r in [x for x in rows if x["status"] == "low"][:MAX_PER_CATEGORY]:
        out.append(
            _alert(
                f"stock:low:{r['account']}:{r['sku']}",
                "warning",
                "inventory",
                f"{r['sku']} se agota en {r['days_of_cover']:.0f} días",
                f"{r['avail_to_sell']} unidades disponibles y se venden ~{r['daily_units']:.1f} al día. "
                "Programa la reposición.",
                r["channel"],
                r["sku"],
            )
        )
    blocked = [x for x in rows if x["status"] == "unpublished" and (x["avail_to_sell"] or 0) > 0]
    for r in blocked[:MAX_PER_CATEGORY]:
        out.append(
            _alert(
                f"stock:blocked:{r['account']}:{r['sku']}",
                "warning",
                "inventory",
                f"{r['sku']} tiene {r['avail_to_sell']} unidades pero no está publicado",
                f"Estado {r['published_status'] or 'desconocido'}"
                + (f" ({r['unpublished_reasons']})" if r["unpublished_reasons"] else "")
                + ". No se puede comprar hasta corregirlo en el Seller Center.",
                r["channel"],
                r["sku"],
            )
        )
    if len(blocked) > MAX_PER_CATEGORY:
        out.append(
            _alert(
                "stock:blocked:more",
                "warning",
                "inventory",
                f"{len(blocked) - MAX_PER_CATEGORY} publicaciones más bloqueadas con existencias",
                "Ábrelas en Inventario, filtro “Sin publicar”.",
            )
        )
    return out


async def _performance_alerts(session: AsyncSession) -> list[dict[str, Any]]:
    perf = await seller_performance_report(session)
    t = perf["targets"]
    out: list[dict[str, Any]] = []
    for acc in perf["accounts"]:
        w = next((x for x in acc["windows"] if x["window_days"] == 30), None)
        if not w:
            continue
        checks = (
            ("otd", "Entrega a tiempo", "min", 10.0),
            ("ots", "Envío a tiempo", "min", 5.0),
            ("vtr", "Guía de rastreo válida", "min", 5.0),
            ("cancellation_seller_fault", "Cancelaciones por tu culpa", "max", 2.0),
        )
        for key, label, kind, margin in checks:
            value, target = w.get(key), t.get(key)
            if value is None or target is None:
                continue
            bad = value < target if kind == "min" else value > target
            if not bad:
                continue
            gap = (target - value) if kind == "min" else (value - target)
            out.append(
                _alert(
                    f"perf:{acc['account']}:{key}",
                    "critical" if gap >= margin else "warning",
                    "performance",
                    f"{label} en {value:.1f}% ({acc['account']})",
                    f"Meta de referencia {target:.0f}%{' o más' if kind == 'min' else ' como máximo'}, ventana de 30 días. "
                    "Walmart puede limitar la cuenta si se mantiene.",
                    acc["channel"],
                )
            )
    return out


async def _operations_alerts(session: AsyncSession, today: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ret = await returns_report(session, today - timedelta(days=90), today)
    risky = [
        p
        for p in ret["by_product"]
        if p["returned_units"] >= RETURN_MIN_UNITS and (p["return_rate"] or 0) >= RETURN_RATE_ALERT
    ]
    for p in sorted(risky, key=lambda p: p["return_rate"], reverse=True)[:MAX_PER_CATEGORY]:
        out.append(
            _alert(
                f"returns:{p['sku']}",
                "warning",
                "returns",
                f"{p['sku']} se devuelve mucho ({p['return_rate']:.0%})",
                f"{p['returned_units']} de {p['units_sold']} unidades devueltas en 90 días "
                f"({_money(p['returned_refund'])} reembolsados). Revisa descripción, fotos y empaque.",
                sku=p["sku"],
            )
        )
    overdue = (await logistics_report(session, today - timedelta(days=30), today))["summary"]["overdue_now"]
    if overdue:
        out.append(
            _alert(
                "logistics:overdue",
                "critical",
                "logistics",
                f"{overdue} órdenes por enviar ya pasaron su fecha límite",
                "La plataforma puede cancelarlas y penalizarte. Envíalas hoy.",
            )
        )
    failed = (await cashflow_report(session, today - timedelta(days=90), today))["summary"]["failed"]
    if failed:
        out.append(
            _alert(
                "cashflow:failed",
                "critical",
                "cashflow",
                f"Pagos fallidos por {_money(failed)}",
                "La plataforma no pudo depositar. Revisa la cuenta bancaria en su panel de vendedor.",
            )
        )
    return out


async def _sync_alerts(session: AsyncSession) -> list[dict[str, Any]]:
    status = await sync_status(session)
    out: list[dict[str, Any]] = []
    for s in status["sources"]:
        if not s["configured"]:
            continue
        if s["status"] == "failed":
            out.append(
                _alert(
                    f"sync:failed:{s['source']}",
                    "critical",
                    "sync",
                    f"La última sincronización de {s['label']} falló",
                    (s["error"] or "Sin detalle") + ". Los datos de esta fuente pueden estar desactualizados.",
                )
            )
        elif s["status"] is None or s["overdue"]:
            out.append(
                _alert(
                    f"sync:stale:{s['source']}",
                    "warning",
                    "sync",
                    f"{s['label']} no se sincroniza desde hace tiempo"
                    if s["status"]
                    else f"{s['label']} nunca se ha sincronizado",
                    "Sincroniza desde Importar o revisa que la sincronización automática esté activa.",
                )
            )
    return out


async def build_alerts(session: AsyncSession, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    alerts: list[dict[str, Any]] = []
    for part in (
        await _sync_alerts(session),
        await _profit_alerts(session, today),
        await _inventory_alerts(session, today),
        await _performance_alerts(session),
        await _operations_alerts(session, today),
    ):
        alerts.extend(part)
    alerts.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], a["category"], a["id"]))
    counts = {s: sum(1 for a in alerts if a["severity"] == s) for s in SEVERITY_ORDER}
    return {"generated_at": datetime.now(UTC), "counts": counts, "alerts": alerts}
