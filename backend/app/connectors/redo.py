"""Conector de Redo (API GraphQL v3): costo real de las etiquetas, sin subir el CSV.

SOLO LECTURA: `RedoClient.query` rechaza cualquier operación que no sea una `query`.
Usa `billingCharges` (permiso `invoices_read`) y entrega el mismo resultado que el
importador del CSV: un total por orden, que se guarda en sales.shipping_label_cost.

Siempre se descarga TODO el historial: el total de una orden se reemplaza (no se suma),
así que una ventana parcial dejaría totales incompletos. Son ~25 páginas de 100 cargos.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import httpx
import polars as pl
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.redo import (
    LabelImportResult,
    RedoStats,
    import_label_costs,
)

API_BASE = "https://api.getredo.com"
PAGE_SIZE = 100
CHARGES_QUERY = (
    """
query ($after: String, $first: Int) {
  billingCharges(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges { node { id orderName createdAt note amount { amount currency } } }
  }
}
"""
)

PER_ORDER_SCHEMA = {
    "external_order_id": pl.Utf8,
    "amount": pl.Float64,
    "charges": pl.UInt32,
    "first_charged": pl.Date,
    "last_charged": pl.Date,
}


class RedoSettings(BaseSettings):
    """Variables REDO_* del archivo .env."""

    store_id: str
    api_key: str

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="REDO_", extra="ignore"
    )


class RedoError(RuntimeError):
    def __init__(self, status: int | str, message: str):
        self.status = status
        super().__init__(f"Redo {status}: {message}")


class RedoClient:
    def __init__(
        self, settings: RedoSettings | None = None, http: httpx.Client | None = None
    ) -> None:
        self.settings = settings or RedoSettings()  # type: ignore[call-arg]
        self.http = http or httpx.Client(base_url=API_BASE, timeout=60)

    def query(
        self, document: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not re.match(r"\s*(query\b|\{)", document):
            raise RedoError(
                "local", "este conector es de solo lectura: solo se permiten queries"
            )
        res = self.http.post(
            f"/v3/account/{self.settings.store_id}/graphql",
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
            json={"query": document, "variables": variables or {}},
        )
        if res.status_code == 401:
            raise RedoError(401, "no autorizado (revisa REDO_API_KEY y REDO_STORE_ID)")
        if res.status_code != 200:
            raise RedoError(res.status_code, res.text[:200])
        body = res.json()
        if body.get("errors"):
            raise RedoError(
                "graphql",
                "; ".join(e.get("message", "?") for e in body["errors"])[:300],
            )
        return body["data"]

    def iter_charges(self) -> Iterator[dict[str, Any]]:
        after: str | None = None
        while True:
            page = self.query(CHARGES_QUERY, {"after": after, "first": PAGE_SIZE})["billingCharges"]
            for edge in page["edges"]:
                yield edge["node"]
            if not page["pageInfo"]["hasNextPage"]:
                return
            after = page["pageInfo"]["endCursor"]


def build_label_frame(charges: list[dict[str, Any]]) -> tuple[pl.DataFrame, RedoStats]:
    """Un total por orden (cargos y reversas netos) + estadísticas de lo que no se atribuye."""
    stats = RedoStats()
    per_order: dict[str, dict[str, Any]] = {}
    for c in charges:
        amount = float(c["amount"]["amount"])
        day = date.fromisoformat(c["createdAt"][:10])
        order = (c.get("orderName") or "").strip()
        if not order:
            stats.unassigned_charges += 1
            stats.unassigned_total += amount
            continue
        rec = per_order.setdefault(
            order,
            {
                "external_order_id": order,
                "amount": 0.0,
                "charges": 0,
                "first_charged": day,
                "last_charged": day,
            },
        )
        rec["amount"] += amount
        rec["charges"] += 1
        rec["first_charged"] = min(rec["first_charged"], day)
        rec["last_charged"] = max(rec["last_charged"], day)
    stats.unassigned_total = round(stats.unassigned_total, 2)
    stats.charges_used = sum(r["charges"] for r in per_order.values())
    stats.orders = len(per_order)
    frame = pl.DataFrame(
        sorted(per_order.values(), key=lambda r: r["external_order_id"]),
        schema=PER_ORDER_SCHEMA,
    )
    return frame.with_columns(pl.col("amount").round(4)), stats


async def sync_redo(
    session: AsyncSession, client: RedoClient
) -> tuple[LabelImportResult, RedoStats]:
    charges = await asyncio.to_thread(lambda: list(client.iter_charges()))
    frame, stats = build_label_frame(charges)
    result = (
        await import_label_costs(session, frame)
        if frame.height
        else LabelImportResult()
    )
    return result, stats
