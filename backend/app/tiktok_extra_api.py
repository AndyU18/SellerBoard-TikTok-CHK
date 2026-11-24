"""Endpoints de solo lectura del apartado TikTok Shop (datos extra). El análisis vive en `tiktok_extra_analytics`.

Todos exigen el permiso `profit.view`, aceptan `days` (7-365) y no escriben nada, ni aquí ni en TikTok.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import tiktok_extra_analytics as analytics
from app.auth import require
from app.db import get_session

router = APIRouter(prefix="/api/tiktok")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VIEW = [Depends(require("profit.view"))]
Days = Annotated[int, Query(ge=7, le=365)]
StuckDays = Annotated[int, Query(ge=1, le=60)]


@router.get("/overview", dependencies=VIEW)
async def overview(session: SessionDep, days: Days = 30) -> dict:
    """Portada: ventas, utilidad y órdenes de TikTok Shop; productos rechazados, paquetes atascados, pagos y saldo por cobrar."""
    return await analytics.overview(session, days)


@router.get("/product-performance", dependencies=VIEW)
async def product_performance(session: SessionDep, days: Days = 30) -> dict:
    """GMV, pedidos, CTR y conversión por producto, con utilidad real y productos de mucho tráfico y poca conversión."""
    return await analytics.product_performance(session, days)


@router.get("/sku-performance", dependencies=VIEW)
async def sku_performance(session: SessionDep, days: Days = 30) -> dict:
    """Rendimiento por SKU unido al SKU maestro y a su utilidad."""
    return await analytics.sku_performance(session, days)


@router.get("/catalog", dependencies=VIEW)
async def catalog(session: SessionDep, days: Days = 90) -> dict:
    """Productos con imagen, estado, auditoría y calidad; rechazados/desactivados y sus ventas de `days` días."""
    return await analytics.catalog(session, days)


@router.get("/promotions", dependencies=VIEW)
async def promotions(session: SessionDep, days: Days = 90) -> dict:
    """Actividades de promoción con productos y ROI: unidades/día durante vs. los 14 días previos."""
    return await analytics.promotions(session, days)


@router.get("/coupons", dependencies=VIEW)
async def coupons(session: SessionDep, days: Days = 90) -> dict:
    """Cupones con estado, vigencia, tipo, descuento y uso."""
    return await analytics.coupons(session, days)


@router.get("/packages", dependencies=VIEW)
async def packages(session: SessionDep, days: Days = 30, stuck_days: StuckDays = analytics.DEFAULT_STUCK_DAYS) -> dict:
    """Paquetes por estado y por día, tasa y tiempos de entrega, y los atascados (sin movimiento más de `stuck_days`)."""
    return await analytics.packages(session, days, stuck_days)


@router.get("/payments", dependencies=VIEW)
async def payments(session: SessionDep, days: Days = 90) -> dict:
    """Pagos de TikTok con retención, tipo de cambio y cuadre contra las liquidaciones."""
    return await analytics.payments(session, days)


@router.get("/withdrawals", dependencies=VIEW)
async def withdrawals(session: SessionDep, days: Days = 365) -> dict:
    """Retiros por estado y por mes."""
    return await analytics.withdrawals(session, days)


@router.get("/warehouses", dependencies=VIEW)
async def warehouses(session: SessionDep) -> dict:
    """Almacenes (nombre, tipo, estado y ciudad/estado/código postal; nunca la calle)."""
    return await analytics.warehouses(session)


@router.get("/webhooks", dependencies=VIEW)
async def webhooks(session: SessionDep) -> dict:
    """Suscripciones a eventos y los eventos documentados que faltan por suscribir."""
    return await analytics.webhooks(session)
