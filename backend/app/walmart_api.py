"""Endpoints de solo lectura del apartado Walmart.

Calidad de listados, inventario WFS, feeds, repricer, plantillas de envío, reportes, pedidos por surtir en
vivo y resumen de portada. Nada escribe en Walmart ni en la base. Router delgado: la lógica vive en
`app.walmart_analytics`. Aún no está registrado en `main.py`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.connectors.walmart import WalmartClient, WalmartError
from app.db import get_session
from app.walmart_analytics import (
    REPORT_PREVIEW_DEFAULT_ROWS,
    REPORT_PREVIEW_MAX_ROWS,
    ReportError,
    feeds_report,
    listing_quality,
    open_orders,
    overview,
    report_preview,
    reports_list,
    repricer_report,
    shipping_templates_report,
    wfs_inventory,
)

router = APIRouter(prefix="/api/walmart")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=365)]
BAD_GATEWAY = 502
UNAVAILABLE = 503


def walmart_client() -> WalmartClient:
    """Cliente de Walmart para las rutas en vivo. Se sustituye en las pruebas con `dependency_overrides`."""
    try:
        return WalmartClient()
    except ValidationError as exc:
        raise HTTPException(UNAVAILABLE, "Walmart no está configurado (WMT_CLIENT_ID/SECRET)") from exc


def optional_walmart_client() -> WalmartClient | None:
    """Como `walmart_client`, pero None si falta la credencial: la portada debe abrir igual sin datos en vivo."""
    try:
        return WalmartClient()
    except ValidationError:
        return None


ClientDep = Annotated[WalmartClient, Depends(walmart_client)]
OptionalClientDep = Annotated[WalmartClient | None, Depends(optional_walmart_client)]


@router.get("/overview", dependencies=VIEW)
async def get_overview(session: SessionDep, client: OptionalClientDep, days: Days = 30) -> dict[str, Any]:
    """Portada: KPIs de la capa oro, calidad, desempeño del vendedor, pedidos por vencer, feeds y WFS agotados."""
    live: dict[str, Any] = {"available": False, "error": "Walmart no está configurado"}
    if client is not None:
        try:
            live = {"available": True, **(await open_orders(client))["summary"]}
        except WalmartError as exc:
            live = {"available": False, "error": str(exc)[:200]}
    return await overview(session, days=days, open_orders_summary=live)


@router.get("/listing-quality", dependencies=VIEW)
async def get_listing_quality(session: SessionDep, days: Annotated[int, Query(ge=7, le=365)] = 90) -> dict[str, Any]:
    """Último puntaje, historial diario, componentes con su distancia a 100 y defectos."""
    return await listing_quality(session, days=days)


@router.get("/wfs-inventory", dependencies=VIEW)
async def get_wfs_inventory(
    session: SessionDep,
    days: Days = 30,
    low_cover_days: Annotated[int, Query(ge=1, le=120)] = 14,
) -> dict[str, Any]:
    """Existencias WFS por SKU y nodo, cobertura en días y contraste con el inventario normal."""
    return await wfs_inventory(session, days=days, low_cover_days=low_cover_days)


@router.get("/feeds", dependencies=VIEW)
async def get_feeds(
    session: SessionDep, days: Days = 30, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> dict[str, Any]:
    """Cargas recientes, tasa de éxito y las que traen fallos."""
    return await feeds_report(session, days=days, limit=limit)


@router.get("/repricer", dependencies=VIEW)
async def get_repricer(session: SessionDep, days: Annotated[int, Query(ge=7, le=365)] = 90) -> dict[str, Any]:
    """Estrategias del repricer y SKU asignados cruzados con el precio mínimo real."""
    return await repricer_report(session, days=days)


@router.get("/shipping-templates", dependencies=VIEW)
async def get_shipping_templates(session: SessionDep, days: Annotated[int, Query(ge=7, le=365)] = 90) -> dict[str, Any]:
    """Plantillas de envío y su tarifa contra el costo real de etiqueta de Redo."""
    return await shipping_templates_report(session, days=days)


@router.get("/reports", dependencies=VIEW)
async def get_reports(session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    """Solicitudes de reporte (ITEM, BUYBOX) y su estado."""
    return await reports_list(session, limit=limit)


@router.get("/reports/{request_id}/preview", dependencies=VIEW)
async def get_report_preview(
    request_id: str,
    session: SessionDep,
    client: ClientDep,
    limit: Annotated[int, Query(ge=1, le=REPORT_PREVIEW_MAX_ROWS)] = REPORT_PREVIEW_DEFAULT_ROWS,
) -> dict[str, Any]:
    """Descarga el reporte ya generado en memoria y devuelve encabezados, filas de muestra y resumen."""
    try:
        return await report_preview(session, client, request_id, limit)
    except ReportError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except WalmartError as exc:
        raise HTTPException(BAD_GATEWAY, str(exc)[:200]) from exc


@router.get("/open-orders", dependencies=VIEW)
async def get_open_orders(client: ClientDep) -> dict[str, Any]:
    """Pedidos por surtir EN VIVO (caché de 60 s), sin datos personales; marca vencidos y por vencer en 24 h."""
    try:
        return await open_orders(client)
    except WalmartError as exc:
        raise HTTPException(BAD_GATEWAY, str(exc)[:200]) from exc
