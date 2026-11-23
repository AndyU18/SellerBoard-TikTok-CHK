"""Endpoints del Gestor de SKU (duplicados, fusión, alias) y de la pantalla de calidad de datos.

Mismo patrón que `analytics_api.py`: la lógica vive en `sku_manager` y `data_quality`; aquí solo se
validan parámetros, permisos y errores. La fusión reconstruye la capa oro y hace commit en la misma
transacción: si algo falla, no queda nada a medias.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require
from app.data_quality import data_quality_report
from app.db import get_session
from app.gold import refresh_daily_profit
from app.sku_manager import (
    SkuError,
    add_alias,
    delete_alias,
    duplicate_groups,
    list_aliases,
    merge_products,
)

router = APIRouter(prefix="/api")
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CATALOG_VIEW = [Depends(require("catalog.view"))]
CATALOG_EDIT = [Depends(require("catalog.edit", writes=True))]
PROFIT_VIEW = [Depends(require("profit.view"))]


class MergeBody(BaseModel):
    source_sku: str = Field(min_length=1, description="SKU que desaparece (queda como alias)")
    target_sku: str = Field(min_length=1, description="SKU maestro que conserva todo")
    reason: str | None = Field(default=None, max_length=200)


class AliasBody(BaseModel):
    alias: str = Field(min_length=1, max_length=120)
    target_sku: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=200)


def _invalid(exc: SkuError) -> HTTPException:
    return HTTPException(422, {"problems": exc.problems})


@router.get("/skus/duplicates", dependencies=CATALOG_VIEW)
async def sku_duplicates(
    session: SessionDep, days: Annotated[int, Query(ge=7, le=365)] = 90
) -> dict:
    """Grupos de productos que parecen el mismo SKU, con el maestro sugerido."""
    return await duplicate_groups(session, days=days)


@router.post("/skus/merge", dependencies=CATALOG_EDIT)
async def sku_merge(body: MergeBody, session: SessionDep) -> dict:
    """Fusiona el SKU origen en el destino (irreversible) y reconstruye la capa oro."""
    try:
        stats = await merge_products(session, body.source_sku, body.target_sku, body.reason)
    except SkuError as exc:
        await session.rollback()
        raise _invalid(exc) from exc
    stats["gold_rows"] = await refresh_daily_profit(session, None, None, reason="sku_merge")
    stats.pop("touched_from", None)
    stats.pop("touched_to", None)
    await session.commit()
    return stats


@router.get("/skus/aliases", dependencies=CATALOG_VIEW)
async def sku_aliases(session: SessionDep) -> list[dict]:
    return await list_aliases(session)


@router.post("/skus/aliases", dependencies=CATALOG_EDIT)
async def sku_alias_create(body: AliasBody, session: SessionDep) -> dict:
    """Registra un SKU alterno: las próximas importaciones lo asignan al producto maestro."""
    try:
        row = await add_alias(session, body.alias, body.target_sku, body.reason)
    except SkuError as exc:
        await session.rollback()
        raise _invalid(exc) from exc
    await session.commit()
    return row


@router.delete("/skus/aliases/{alias_id}", dependencies=CATALOG_EDIT)
async def sku_alias_delete(alias_id: int, session: SessionDep) -> dict:
    if not await delete_alias(session, alias_id):
        raise HTTPException(404, "No existe ese alias")
    await session.commit()
    return {"deleted": alias_id}


@router.get("/data-quality", dependencies=PROFIT_VIEW)
async def data_quality(session: SessionDep) -> dict:
    """Puntuación 0-100 y revisiones de calidad de datos, con su impacto y dónde corregirlas."""
    return await data_quality_report(session)
