"""PDF para el contador: estado de resultados mensual y resumen del periodo.

Decisiones:
- fpdf2 con fuentes core (Helvetica) solo soporta latin-1: todo texto pasa por `latin1()`, que cambia
  comillas, rayas y otros signos tipográficos por equivalentes y reemplaza lo demás por «?», así que
  generar el PDF nunca falla por un carácter raro (p. ej. en el nombre de un producto).
- Las funciones `pnl_pdf` y `summary_pdf` son puras (reciben datos ya calculados y devuelven bytes);
  `collect_summary` es la única que toca la base.
- La sección de calidad de datos es opcional: se omite si `app.data_quality` no existe o falla.
"""

from __future__ import annotations

import importlib
import logging
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from fpdf import FPDF
from fpdf.enums import Align
from fpdf.fonts import FontFace
from sqlalchemy.ext.asyncio import AsyncSession

from app.forecast import month_end
from app.models import Channel
from app.pnl import MONEY_FIELDS, pnl_report
from app.returns_cost import returns_cost_report

log = logging.getLogger(__name__)

_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": ",", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "…": "...", "•": "-", " ": " ", "€": "EUR",
    "→": "->", "←": "<-", "≥": ">=", "≤": "<=", "✓": "ok",
    "\u200b": "", "﻿": "",
}  # fmt: skip

SHORT_LABELS = {
    "gross_sales": "Ventas brutas",
    "promo": "Promoc.",
    "net_sales": "Ventas netas",
    "platform_fees": "Comisiones",
    "shipping_cost": "Envio",
    "refund_amount": "Reembolsos",
    "cogs": "Costo prod.",
    "gross_profit": "Util. bruta",
    "ad_spend": "Publicidad",
    "contribution": "Contrib.",
    "expenses": "Gastos ind.",
    "net_profit": "Util. neta",
}
CHANNEL_NAMES = {
    "amazon": "Amazon",
    "tiktok_shop": "TikTok Shop",
    "walmart": "Walmart",
    "shopify": "Shopify",
    "TOTAL": "TOTAL",
}
SEVERITY_NAMES = {"critical": "Crítico", "warning": "Aviso", "info": "Info", "high": "Alto", "medium": "Medio", "low": "Bajo"}
INK = (30, 41, 59)
MUTED = (100, 116, 139)
HEAD_FILL = (226, 232, 240)
TOTAL_FILL = (241, 245, 249)


def latin1(text: object) -> str:
    """Texto seguro para fuentes core: sin caracteres fuera de latin-1."""
    s = "".join(_REPLACEMENTS.get(ch, ch) for ch in str(text))
    return s.encode("latin-1", "replace").decode("latin-1")


def money(value: float | None) -> str:
    """$1,234.56 (negativos como -$1,234.56); vacío si no hay dato."""
    if value is None:
        return "-"
    sign = "-" if round(value, 2) < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def channel_name(code: str) -> str:
    return CHANNEL_NAMES.get(code, code)


class _Report(FPDF):
    """Hoja A4 con encabezado de la app y pie con fecha de generación y número de página."""

    def __init__(self, title: str, subtitle: str, generated: str, *, landscape: bool) -> None:
        super().__init__(orientation="L" if landscape else "P", unit="mm", format="A4")
        self.doc_title = latin1(title)
        self.subtitle = latin1(subtitle)
        self.generated = latin1(generated)
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(10, 12, 10)
        self.set_title(self.doc_title)
        self.set_creator("SellerBoard")
        self.alias_nb_pages()
        self.add_page()

    def header(self) -> None:
        self.set_text_color(*INK)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 7, self.doc_title, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*MUTED)
        self.cell(0, 5, self.subtitle, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-10)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"Generado el {self.generated}", align="L")
        self.set_x(self.l_margin)
        self.cell(0, 5, f"Pagina {self.page_no()} de {{nb}}", align="R")

    def heading(self, text: str) -> None:
        self.ln(3)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*INK)
        self.cell(0, 6, latin1(text), new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)

    def paragraph(self, text: str, *, size: int = 8, muted: bool = True) -> None:
        self.set_font("Helvetica", "I" if muted else "", size)
        self.set_text_color(*(MUTED if muted else INK))
        self.multi_cell(0, 4.2, latin1(text), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*INK)

    def grid(
        self,
        header: list[str],
        rows: list[list[str]],
        widths: list[float],
        *,
        bold_rows: set[int] | None = None,
        size: float = 7.5,
    ) -> None:
        """Tabla con encabezado que se repite en cada página; la primera columna alineada a la izquierda."""
        bold_rows = bold_rows or set()
        self.set_font("Helvetica", "", size)
        self.set_text_color(*INK)
        aligns = [Align.L] + [Align.R] * (len(header) - 1)
        with self.table(
            col_widths=widths,
            text_align=tuple(aligns),
            line_height=size * 0.5,
            borders_layout="HORIZONTAL_LINES",
            first_row_as_headings=False,
            padding=0.8,
        ) as table:
            head = table.row()
            for h in header:
                head.cell(latin1(h), style=_style("B", HEAD_FILL))
            for i, values in enumerate(rows):
                row = table.row()
                for v in values:
                    row.cell(latin1(v), style=_style("B", TOTAL_FILL) if i in bold_rows else None)
        self.ln(1)


def _style(emphasis: str, fill: tuple[int, int, int]) -> FontFace:
    return FontFace(emphasis=emphasis, fill_color=fill)


def _stamp(generated_at: datetime | None) -> str:
    return (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")


def _range_text(start: str, end: str, channel: str | None) -> str:
    where = channel_name(channel) if channel else "Todos los canales"
    return f"Periodo: {start[:7]} a {end[:7]}  |  Canal: {where}"


def pnl_pdf(report: dict[str, Any], *, channel: str | None = None, generated_at: datetime | None = None) -> bytes:
    """Estado de resultados mensual (una fila por canal y una de total por mes) en hoja horizontal."""
    pdf = _Report(
        "Estado de resultados mensual",
        _range_text(report["start"], report["end"], channel),
        _stamp(generated_at),
        landscape=True,
    )
    header = ["Mes / canal", *[SHORT_LABELS[f] for f in MONEY_FIELDS], "Margen"]
    widths = [27.0] + [20.0] * len(MONEY_FIELDS) + [10.0]
    rows: list[list[str]] = []
    bold: set[int] = set()
    for m in report["months"]:
        for r in m["channels"]:
            rows.append([f"{m['month'][:7]} {channel_name(r['channel'])}", *[money(r[f]) for f in MONEY_FIELDS], percent(r["margin"])])
        bold.add(len(rows))
        rows.append([f"{m['month'][:7]} TOTAL", *[money(m["total"][f]) for f in MONEY_FIELDS], percent(m["total"]["margin"])])
    summary = report["summary"]
    bold.add(len(rows))
    rows.append(["TOTAL DEL PERIODO", *[money(summary[f]) for f in MONEY_FIELDS], percent(summary["margin"])])
    pdf.grid(header, rows, widths, bold_rows=bold, size=6.5)

    unallocated = sum(m["unallocated_expenses"] for m in report["months"])
    if unallocated:
        pdf.paragraph(f"Gastos indirectos sin repartir por falta de ventas en el mes: {money(unallocated)}.")
    pdf.paragraph(report["note"])
    pdf.paragraph(
        "La utilidad neta de este estado de resultados INCLUYE los gastos indirectos (renta, sueldos, "
        "software...); la utilidad por producto de otras pantallas NO los incluye."
    )
    return bytes(pdf.output())


async def _quality_items(session: AsyncSession, limit: int = 10) -> list[dict[str, Any]]:
    """Las revisiones de calidad más importantes, o [] si el módulo no existe o falla (sección opcional)."""
    try:
        module = importlib.import_module("app.data_quality")
    except ImportError:
        return []
    try:
        report = await module.data_quality_report(session)
        # Ya vienen ordenadas por gravedad e impacto; las que están «ok» no aportan al resumen.
        return [c for c in report["checks"] if c["severity"] != "ok"][:limit]
    except Exception:
        log.warning("No se pudo agregar la calidad de datos al resumen", exc_info=True)
        return []


async def collect_summary(
    session: AsyncSession, *, start: date | None, end: date | None, channel: Channel | None
) -> dict[str, Any]:
    """Datos del 'Resumen del periodo': estado de resultados por canal, devoluciones y calidad de datos."""
    report = await pnl_report(session, start=start, end=end, channel=channel)
    by_channel: dict[str, dict[str, float]] = defaultdict(lambda: dict.fromkeys(MONEY_FIELDS, 0.0))
    for m in report["months"]:
        for r in m["channels"]:
            for f in MONEY_FIELDS:
                by_channel[r["channel"]][f] += r[f]
    first = date.fromisoformat(report["start"])
    last = month_end(date.fromisoformat(report["end"]))  # el estado de resultados abarca meses completos
    days = (last - first).days + 1
    returns = await returns_cost_report(session, days=days, channel=channel, today=last)
    return {
        "start": report["start"],
        "end": report["end"],
        "channel": channel.value if channel else None,
        "total": report["summary"],
        "by_channel": dict(sorted(by_channel.items())),
        "returns": returns["summary"],
        "returns_days": days,
        "note": report["note"],
        "quality": await _quality_items(session),
    }


def summary_pdf(data: dict[str, Any], *, generated_at: datetime | None = None) -> bytes:
    """'Resumen del periodo para el contador': ventas, costos, utilidad por canal, devoluciones y calidad."""
    pdf = _Report(
        "Resumen del periodo para el contador",
        _range_text(data["start"], data["end"], data["channel"]),
        _stamp(generated_at),
        landscape=False,
    )
    t = data["total"]
    pdf.heading("Totales del periodo")
    pdf.grid(
        ["Concepto", "Importe"],
        [
            ["Ventas brutas", money(t["gross_sales"])],
            ["(-) Promociones", money(t["promo"])],
            ["Ventas netas", money(t["net_sales"])],
            ["(-) Comisiones de plataforma", money(t["platform_fees"])],
            ["(-) Costo de envio", money(t["shipping_cost"])],
            ["(-) Reembolsos", money(t["refund_amount"])],
            ["(-) Costo de producto", money(t["cogs"])],
            ["Utilidad bruta", money(t["gross_profit"])],
            ["(-) Publicidad", money(t["ad_spend"])],
            ["(-) Gastos indirectos", money(t["expenses"])],
            ["Utilidad neta", money(t["net_profit"])],
            ["Margen neto sobre ventas netas", percent(t["margin"])],
        ],
        [120.0, 70.0],
        bold_rows={2, 7, 10},
        size=9,
    )

    pdf.heading("Utilidad por canal")
    channel_rows = [
        [
            channel_name(code),
            money(v["net_sales"]),
            money(v["cogs"] + v["platform_fees"] + v["shipping_cost"] + v["refund_amount"] + v["ad_spend"]),
            money(v["expenses"]),
            money(v["net_profit"]),
            percent(v["net_profit"] / v["net_sales"] if v["net_sales"] > 0 else None),
        ]
        for code, v in data["by_channel"].items()
    ]
    if channel_rows:
        pdf.grid(
            ["Canal", "Ventas netas", "Costos directos", "Gastos ind.", "Utilidad neta", "Margen"],
            channel_rows,
            [38.0, 33.0, 33.0, 29.0, 33.0, 24.0],
            size=8,
        )
    else:
        pdf.paragraph("Sin ventas registradas en el periodo.")

    r = data["returns"]
    pdf.heading(f"Devoluciones (ultimos {data['returns_days']} dias del periodo)")
    pdf.grid(
        ["Concepto", "Valor"],
        [
            ["Casos de devolucion", str(r["cases"])],
            ["Unidades devueltas", str(r["units"])],
            ["Reembolsado", money(r["refund"])],
            ["Envio perdido", money(r["shipping_lost"])],
            ["Producto perdido (estimado)", money(r["product_lost"])],
            ["Costo real de devoluciones", money(r["real_cost"])],
        ],
        [120.0, 70.0],
        bold_rows={5},
        size=9,
    )

    if data["quality"]:
        pdf.heading("Revisiones de calidad de datos (las mas importantes)")
        pdf.grid(
            ["Severidad", "Revision"],
            [
                [
                    SEVERITY_NAMES.get(str(i.get("severity")), str(i.get("severity", "-"))),
                    f"{i.get('title', '')}: {i.get('detail', '')}".strip(": "),
                ]
                for i in data["quality"]
            ],
            [28.0, 162.0],
            size=8,
        )

    pdf.paragraph(data["note"])
    pdf.paragraph("Cifras en la moneda de las plataformas (USD). Documento informativo, no sustituye la contabilidad oficial.")
    return bytes(pdf.output())
