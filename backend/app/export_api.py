"""Exportaciones para el contador (CSV y PDF).

- GET /api/export/catalog         lista de exportaciones y los parámetros que acepta cada una
- GET /api/export/{nombre}.csv    CSV de una exportación del registro (`app.exports.EXPORTS`)
- GET /api/export/pnl.pdf         estado de resultados mensual
- GET /api/export/summary.pdf     resumen del periodo para el contador

Todas exigen `profit.view`. Los nombres de archivo salen del registro y de la fecha del día, nunca de
texto del usuario, así que no hay forma de inyectar cabeceras.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.db import get_session
from app.exports import EXPORTS, ExportParams, to_csv
from app.models import Channel
from app.pdf_reports import collect_summary, pnl_pdf, summary_pdf
from app.pnl import pnl_report

router = APIRouter(prefix="/api/export")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]


def _attachment(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _check_range(start: date | None, end: date | None) -> None:
    if start and end and start > end:
        raise HTTPException(422, "La fecha inicial no puede ser posterior a la final")


@router.get("/catalog", dependencies=VIEW)
async def catalog() -> list[dict]:
    """Exportaciones disponibles (CSV) y los parámetros que usa cada una."""
    csv_items = [
        {"name": name, "title": e.title, "description": e.description, "params": list(e.params), "formats": ["csv"]}
        for name, e in EXPORTS.items()
    ]
    pdf_items = [
        {
            "name": "pnl",
            "title": "Estado de resultados (PDF)",
            "description": "Estado de resultados mensual por canal, listo para imprimir o enviar al contador.",
            "params": ["start", "end", "channel"],
            "formats": ["pdf"],
        },
        {
            "name": "summary",
            "title": "Resumen del periodo para el contador (PDF)",
            "description": "Ventas, costos, utilidad por canal, devoluciones y revisiones de calidad de datos.",
            "params": ["start", "end", "channel"],
            "formats": ["pdf"],
        },
    ]
    return [*csv_items, *pdf_items]


@router.get("/pnl.pdf", dependencies=VIEW)
async def pnl_pdf_download(
    session: SessionDep, start: date | None = None, end: date | None = None, channel: Channel | None = None
) -> Response:
    _check_range(start, end)
    report = await pnl_report(session, start=start, end=end, channel=channel)
    return Response(
        pnl_pdf(report, channel=channel.value if channel else None),
        media_type="application/pdf",
        headers=_attachment(f"estado_de_resultados_{_today()}.pdf"),
    )


@router.get("/summary.pdf", dependencies=VIEW)
async def summary_pdf_download(
    session: SessionDep, start: date | None = None, end: date | None = None, channel: Channel | None = None
) -> Response:
    _check_range(start, end)
    data = await collect_summary(session, start=start, end=end, channel=channel)
    return Response(
        summary_pdf(data),
        media_type="application/pdf",
        headers=_attachment(f"resumen_contador_{_today()}.pdf"),
    )


@router.get("/{name}.csv", dependencies=VIEW)
async def export_csv(
    name: str,
    session: SessionDep,
    start: date | None = None,
    end: date | None = None,
    channel: Channel | None = None,
    account: str | None = None,
    days: Annotated[int | None, Query(ge=7, le=365)] = None,
) -> Response:
    export = EXPORTS.get(name)
    if export is None:
        raise HTTPException(404, "Exportación no encontrada")
    _check_range(start, end)
    columns, rows = await export.build(
        session, ExportParams(start=start, end=end, channel=channel, account=account, days=days)
    )
    return Response(
        to_csv(columns, rows),
        media_type="text/csv; charset=utf-8",
        headers=_attachment(f"{name}_{_today()}.csv"),
    )
