"""Resumen diario o semanal del negocio: construcción de los datos y renderizado a texto, HTML y Slack.

Decisiones:
- `build_digest` devuelve un dict de tipos simples (str, int, float, None, listas, dicts): se puede
  serializar tal cual a JSON para el webhook genérico y para la vista previa de la API.
- Los periodos se calculan en la zona horaria `settings.report_timezone`: el diario es "ayer" y el
  semanal son los 7 días que terminan ayer. El periodo anterior equivalente se usa para las variaciones.
- Los renderizadores son funciones puras. El HTML escapa todo con `html.escape` y no lleva scripts.
- El resumen no incluye datos de clientes: solo cifras agregadas, nombres de producto y títulos de alertas.
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import build_alerts
from app.cashflow_forecast import cash_projection
from app.config import settings
from app.gold import load_gold_frame
from app.profit import summarize
from app.sync_jobs import sync_status

Period = Literal["daily", "weekly"]

CASH_HORIZON_DAYS = 14
MAX_CRITICAL_ALERTS = 5
TOP_PRODUCTS = 3
LABELS: dict[str, str] = {"daily": "Resumen diario", "weekly": "Resumen semanal"}
CHANNEL_NAMES = {"tiktok_shop": "TikTok Shop", "walmart": "Walmart", "amazon": "Amazon"}
SYNC_STATUS_TEXT = {
    "success": "correcta",
    "partial": "con avisos",
    "failed": "falló",
    "running": "en curso",
}
_METRICS = ("gross_sales", "net_profit", "units")


def local_today(now: datetime | None = None) -> date:
    """Fecha de hoy en la zona horaria de los reportes."""
    now = now or datetime.now(UTC)
    return now.astimezone(ZoneInfo(settings.report_timezone)).date()


def period_ranges(period: Period, today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """((inicio, fin), (inicio_anterior, fin_anterior)); ambos extremos incluidos."""
    days = 1 if period == "daily" else 7
    end = today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    return (start, end), (prev_end - timedelta(days=days - 1), prev_end)


def _pct_change(current: float, previous: float) -> float | None:
    """Variación relativa; None si no hay base de comparación."""
    return None if previous == 0 else round((current - previous) / abs(previous), 4)


def _window(frame: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    return frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))


def _totals(frame: pl.DataFrame) -> dict[str, float | int | None]:
    """Ventas, utilidad, unidades y margen de un DataFrame (ceros si está vacío)."""
    if frame.is_empty():
        return {"gross_sales": 0.0, "net_profit": 0.0, "units": 0, "margin": None}
    row = summarize(frame.with_columns(pl.lit("all").alias("_all")), ["_all"]).to_dicts()[0]
    return {
        "gross_sales": float(row["gross_sales"]),
        "net_profit": float(row["net_profit"]),
        "units": int(row["units"]),
        "margin": row["margin"],
    }


def _channels(frame: pl.DataFrame) -> list[dict[str, Any]]:
    if frame.is_empty():
        return []
    rows = summarize(frame, ["channel"]).sort("gross_sales", descending=True).to_dicts()
    return [
        {
            "channel": r["channel"],
            "name": CHANNEL_NAMES.get(r["channel"], r["channel"]),
            "gross_sales": float(r["gross_sales"]),
            "net_profit": float(r["net_profit"]),
            "units": int(r["units"]),
            "margin": r["margin"],
        }
        for r in rows
    ]


def _products(frame: pl.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(mejores, peores) por utilidad; los peores no repiten a los mejores."""
    if frame.is_empty():
        return [], []
    rows = [
        {
            "sku": r["sku"],
            "name": r["product_name"] or r["sku"],
            "units": int(r["units"]),
            "gross_sales": float(r["gross_sales"]),
            "net_profit": float(r["net_profit"]),
        }
        for r in summarize(frame, ["sku", "product_name"]).sort("net_profit", descending=True).to_dicts()
    ]
    # Con pocos productos se reparte la lista a la mitad para que los peores no queden vacíos.
    cut = min(TOP_PRODUCTS, (len(rows) + 1) // 2)
    best = rows[:cut]
    worst = list(reversed(rows[cut:]))[:TOP_PRODUCTS]
    return best, worst


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def build_digest(
    session: AsyncSession, period: Period, today: date | None = None
) -> dict[str, Any]:
    """Arma el resumen del periodo. `today` es la fecha local de referencia (por defecto, hoy)."""
    today = today or local_today()
    (start, end), (prev_start, prev_end) = period_ranges(period, today)

    frame = await load_gold_frame(session, start=prev_start, end=end)
    current = _window(frame, start, end)
    totals = _totals(current)
    previous = _totals(_window(frame, prev_start, prev_end))
    change: dict[str, float | None] = {m: _pct_change(float(totals[m] or 0), float(previous[m] or 0)) for m in _METRICS}
    margin, prev_margin = totals["margin"], previous["margin"]
    change["margin_points"] = (
        round((margin - prev_margin) * 100, 2) if margin is not None and prev_margin is not None else None
    )
    best, worst = _products(current)

    alerts = await build_alerts(session, today)
    critical = [a["title"] for a in alerts["alerts"] if a["severity"] == "critical"]
    cash = await cash_projection(session, horizon_days=CASH_HORIZON_DAYS, today=today)
    sync = await sync_status(session)

    return {
        "period": period,
        "label": LABELS[period],
        "timezone": settings.report_timezone,
        "generated_at": datetime.now(UTC).isoformat(),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_range": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "totals": totals,
        "previous": previous,
        "change": change,
        "channels": _channels(current),
        "best_products": best,
        "worst_products": worst,
        "alerts": {
            "counts": alerts["counts"],
            "critical_titles": critical[:MAX_CRITICAL_ALERTS],
            "critical_more": max(len(critical) - MAX_CRITICAL_ALERTS, 0),
        },
        "cash": {
            "horizon_days": CASH_HORIZON_DAYS,
            "expected": float(cash["summary"]["expected_in_horizon"]),
            "pending": float(cash["summary"]["pending_total"]),
            "note": cash["note"],
        },
        "sync": [
            {
                "label": s["label"],
                "status": s["status"],
                "overdue": bool(s["overdue"]),
                "last_run": _iso(s["finished_at"] or s["started_at"]),
            }
            for s in sync["sources"]
            if s["configured"]
        ],
    }


# ---------------------------------------------------------------------------- formato


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}" if value >= 0 else f"-${abs(value):,.2f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _delta(value: float | None) -> str:
    """Variación contra el periodo anterior, con signo."""
    return "sin base de comparación" if value is None else f"{value * 100:+.1f}% vs. periodo anterior"


def _range_text(digest: dict[str, Any]) -> str:
    start, end = digest["range"]["start"], digest["range"]["end"]
    return start if start == end else f"{start} al {end}"


def _alert_summary(digest: dict[str, Any]) -> str:
    c = digest["alerts"]["counts"]
    return f"{c['critical']} críticas, {c['warning']} de atención, {c['info']} avisos"


def _sync_lines(digest: dict[str, Any]) -> list[str]:
    lines = []
    for s in digest["sync"]:
        state = SYNC_STATUS_TEXT.get(s["status"] or "", "sin ejecuciones")
        lines.append(f"{s['label']}: {state}" + (" (atrasada)" if s["overdue"] else ""))
    return lines


def _cash_line(digest: dict[str, Any]) -> str:
    cash = digest["cash"]
    if cash["note"]:
        return f"Dinero esperado en {cash['horizon_days']} días: sin datos de liquidaciones"
    return f"Dinero esperado en {cash['horizon_days']} días: {_money(cash['expected'])}"


def _product_line(p: dict[str, Any]) -> str:
    return f"{p['name']} ({p['sku']}): {_money(p['net_profit'])} en {p['units']} uds."


def render_text(digest: dict[str, Any]) -> str:
    """Versión en texto plano (cuerpo del correo y mensaje del webhook)."""
    t, ch = digest["totals"], digest["change"]
    lines = [
        f"{digest['label']} - {_range_text(digest)} ({digest['timezone']})",
        "",
        f"Ventas brutas: {_money(t['gross_sales'])} ({_delta(ch['gross_sales'])})",
        f"Utilidad neta: {_money(t['net_profit'])} ({_delta(ch['net_profit'])})",
        f"Unidades: {t['units']} ({_delta(ch['units'])})",
        f"Margen: {_pct(t['margin'])}",
    ]
    if digest["channels"]:
        lines += ["", "Por canal:"]
        lines += [
            f"- {c['name']}: ventas {_money(c['gross_sales'])}, utilidad {_money(c['net_profit'])}, {c['units']} uds."
            for c in digest["channels"]
        ]
    if digest["best_products"]:
        lines += ["", "Mejores productos por utilidad:"]
        lines += [f"- {_product_line(p)}" for p in digest["best_products"]]
    if digest["worst_products"]:
        lines += ["", "Peores productos por utilidad:"]
        lines += [f"- {_product_line(p)}" for p in digest["worst_products"]]
    lines += ["", f"Alertas: {_alert_summary(digest)}"]
    lines += [f"- {title}" for title in digest["alerts"]["critical_titles"]]
    if digest["alerts"]["critical_more"]:
        lines.append(f"- y {digest['alerts']['critical_more']} críticas más")
    lines += ["", _cash_line(digest)]
    if digest["sync"]:
        lines += ["", "Sincronización:"] + [f"- {s}" for s in _sync_lines(digest)]
    return "\n".join(lines)


def _slack_escape(text: str) -> str:
    """Slack solo interpreta &, < y > en su mrkdwn."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_slack(digest: dict[str, Any]) -> str:
    """Versión en mrkdwn de Slack."""
    t, ch = digest["totals"], digest["change"]
    lines = [
        f"*{digest['label']}* - {_range_text(digest)}",
        f"• Ventas brutas: *{_money(t['gross_sales'])}* ({_delta(ch['gross_sales'])})",
        f"• Utilidad neta: *{_money(t['net_profit'])}* ({_delta(ch['net_profit'])})",
        f"• Unidades: *{t['units']}* ({_delta(ch['units'])})",
        f"• Margen: *{_pct(t['margin'])}*",
    ]
    for c in digest["channels"]:
        lines.append(
            f"  ◦ {_slack_escape(c['name'])}: {_money(c['gross_sales'])} / utilidad {_money(c['net_profit'])}"
        )
    if digest["best_products"]:
        lines += ["", "*Mejores productos*"]
        lines += [f"• {_slack_escape(_product_line(p))}" for p in digest["best_products"]]
    if digest["worst_products"]:
        lines += ["", "*Peores productos*"]
        lines += [f"• {_slack_escape(_product_line(p))}" for p in digest["worst_products"]]
    lines += ["", f"*Alertas:* {_alert_summary(digest)}"]
    lines += [f"• {_slack_escape(title)}" for title in digest["alerts"]["critical_titles"]]
    lines += ["", _cash_line(digest)]
    return "\n".join(lines)


def render_html(digest: dict[str, Any]) -> str:
    """HTML simple (sin scripts ni recursos externos). Todo el contenido variable se escapa."""

    def esc(value: object) -> str:
        return html.escape(str(value), quote=True)

    def items(rows: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{esc(r)}</li>" for r in rows) + "</ul>" if rows else ""

    t, ch = digest["totals"], digest["change"]
    metrics = [
        ("Ventas brutas", _money(t["gross_sales"]), _delta(ch["gross_sales"])),
        ("Utilidad neta", _money(t["net_profit"]), _delta(ch["net_profit"])),
        ("Unidades", str(t["units"]), _delta(ch["units"])),
        ("Margen", _pct(t["margin"]), ""),
    ]
    rows = "".join(
        f"<tr><td style='padding:4px 12px 4px 0'>{esc(name)}</td>"
        f"<td style='padding:4px 12px 4px 0'><strong>{esc(value)}</strong></td>"
        f"<td style='padding:4px 0;color:#64748b'>{esc(note)}</td></tr>"
        for name, value, note in metrics
    )
    parts = [
        "<div style='font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#0f172a'>",
        f"<h2 style='margin:0 0 4px'>{esc(digest['label'])}</h2>",
        f"<p style='margin:0 0 12px;color:#64748b'>{esc(_range_text(digest))} ({esc(digest['timezone'])})</p>",
        f"<table style='border-collapse:collapse'>{rows}</table>",
    ]
    if digest["channels"]:
        parts.append("<h3>Por canal</h3>")
        parts.append(
            items(
                [
                    f"{c['name']}: ventas {_money(c['gross_sales'])}, utilidad {_money(c['net_profit'])}, {c['units']} uds."
                    for c in digest["channels"]
                ]
            )
        )
    if digest["best_products"]:
        parts += ["<h3>Mejores productos por utilidad</h3>", items([_product_line(p) for p in digest["best_products"]])]
    if digest["worst_products"]:
        parts += ["<h3>Peores productos por utilidad</h3>", items([_product_line(p) for p in digest["worst_products"]])]
    extra = digest["alerts"]["critical_more"]
    critical = list(digest["alerts"]["critical_titles"]) + ([f"y {extra} críticas más"] if extra else [])
    parts += [f"<h3>Alertas</h3><p>{esc(_alert_summary(digest))}</p>", items(critical)]
    parts.append(f"<p>{esc(_cash_line(digest))}</p>")
    if digest["sync"]:
        parts += ["<h3>Sincronización</h3>", items(_sync_lines(digest))]
    parts.append("</div>")
    return "".join(parts)
