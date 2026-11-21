"""Conector de Walmart Marketplace (US): órdenes + reportes de conciliación (liquidaciones).

- Órdenes (`/v3/orders`): ventas, unidades, reembolsos por línea. El número de orden es el
  `customerOrderId` (el que usa Redo en sus etiquetas), no el `purchaseOrderId`.
- Conciliación (`/v3/report/reconreport`): comisión real y cargos de etiqueta de Walmart.
  Si una orden aún no está en un reporte, su comisión se ESTIMA con la tasa por defecto
  (15 %) y se cuenta en `fees_estimated`; la siguiente sincronización la reemplaza por la real.
Solo lectura: este módulo nunca escribe en Walmart.
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
import uuid
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import polars as pl
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.persist import ImportResult, import_orders

API_BASE = "https://marketplace.walmartapis.com"
CHANNEL = "walmart"
DEFAULT_ACCOUNT = "walmart_1"
DEFAULT_COMMISSION_RATE = 0.15
EXPIRY_MARGIN_SECONDS = 60
PAGE_LIMIT = 200

OUT_SCHEMA = {
    "date": pl.Date,
    "channel": pl.Utf8,
    "account": pl.Utf8,
    "order_id": pl.Utf8,
    "sku": pl.Utf8,
    "product_name": pl.Utf8,
    "units": pl.Int64,
    "gross_sales": pl.Float64,
    "promo": pl.Float64,
    "platform_fees": pl.Float64,
    "shipping_cost": pl.Float64,
    "refund_amount": pl.Float64,
    "ordered_at": pl.Datetime(time_zone="UTC"),
}


class WalmartSettings(BaseSettings):
    """Variables WMT_* del archivo .env."""

    client_id: str
    client_secret: str

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="WMT_", extra="ignore"
    )


class WalmartError(RuntimeError):
    def __init__(self, status: int | str, message: str):
        self.status = status
        super().__init__(f"Walmart {status}: {message}")


class WalmartClient:
    """Cliente de solo lectura. El token (15 min) se renueva solo."""

    def __init__(
        self, settings: WalmartSettings | None = None, http: httpx.Client | None = None
    ) -> None:
        self.settings = settings or WalmartSettings()  # type: ignore[call-arg]
        self.http = http or httpx.Client(base_url=API_BASE, timeout=60)
        self._token = ""
        self._expires_at = 0.0

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        return {
            "WM_SVC.NAME": "CHK Profit",
            "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
            "WM_SEC.ACCESS_TOKEN": self._access_token(),
            "Accept": accept,
        }

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - EXPIRY_MARGIN_SECONDS:
            return self._token
        res = self.http.post(
            "/v3/token",
            auth=(self.settings.client_id, self.settings.client_secret),
            headers={
                "WM_SVC.NAME": "CHK Profit",
                "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
                "Accept": "application/json",
            },
            data={"grant_type": "client_credentials"},
        )
        if res.status_code != 200:
            raise WalmartError(
                res.status_code,
                "no se pudo obtener el token (revisa WMT_CLIENT_ID/SECRET)",
            )
        body = res.json()
        self._token = body["access_token"]
        self._expires_at = time.time() + int(body.get("expires_in", 900))
        return self._token

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        for attempt in range(4):
            res = self.http.get(path, params=params, headers=self._headers(accept))
            if res.status_code == 429 or res.status_code >= 500:
                time.sleep(2**attempt)
                continue
            if res.status_code != 200:
                raise WalmartError(res.status_code, res.text[:200])
            return res
        raise WalmartError(
            res.status_code, "Walmart sigue sin responder tras 4 intentos"
        )

    def iter_orders(self, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] | None = {
            "createdStartDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "createdEndDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": PAGE_LIMIT,
        }
        path = "/v3/orders"
        while True:
            body = self._get(path, params).json()["list"]
            yield from body.get("elements", {}).get("order", [])
            cursor = body.get("meta", {}).get("nextCursor")
            if not cursor:
                return
            # nextCursor ya trae la query completa ("?limit=..&nextCursor=..")
            path, params = "/v3/orders" + cursor, None

    def recon_dates(self) -> list[date]:
        body = self._get(
            "/v3/report/reconreport/availableReconFiles", {"reportVersion": "v1"}
        ).json()
        return sorted(
            datetime.strptime(d, "%m%d%Y").date()
            for d in body.get("availableApReportDates", [])
        )

    def recon_rows(self, report_date: date) -> list[dict[str, str]]:
        res = self._get(
            "/v3/report/reconreport/reconFile",
            {"reportDate": report_date.strftime("%m%d%Y"), "reportVersion": "v1"},
            accept="application/octet-stream",
        )
        with zipfile.ZipFile(io.BytesIO(res.content)) as z:
            raw = z.read(z.namelist()[0]).decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(raw)))


def _f(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _line_status_cancelled(line: dict[str, Any]) -> bool:
    statuses = line.get("orderLineStatuses", {}).get("orderLineStatus", [])
    return bool(statuses) and all(s.get("status") == "Cancelled" for s in statuses)


def _recon_index(
    rows: list[dict[str, str]],
) -> tuple[dict[tuple[str, str], float], dict[str, float], int]:
    """(comisión por (orden, sku), cargo de etiqueta por orden, otros ajustes ignorados)."""
    commission: dict[tuple[str, str], float] = defaultdict(float)
    labels: dict[str, float] = defaultdict(float)
    other = 0
    for r in rows:
        order = (r.get("Customer Order #") or "").strip()
        if not order:
            continue
        kind, amount_type = r.get("Transaction Type"), r.get("Amount Type")
        amount = _f(r.get("Amount"))
        if kind in ("Sale", "Refund") and amount_type == "Commission on Product":
            commission[(order, (r.get("Partner Item Id") or "").strip())] += -amount
        elif kind == "Adjustment" and "Shipping Label" in (
            r.get("Transaction Description") or ""
        ):
            labels[order] += -amount
        elif kind == "Adjustment":
            other += 1
    return commission, labels, other


def build_order_lines(
    orders: list[dict[str, Any]],
    recon: list[dict[str, str]],
    account: str = DEFAULT_ACCOUNT,
    commission_rate: float = DEFAULT_COMMISSION_RATE,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Devuelve (líneas canónicas, estadísticas)."""
    commission, labels, other_adjustments = _recon_index(recon)
    stats = {
        "orders_seen": 0,
        "orders_cancelled": 0,
        "lines": 0,
        "fees_actual": 0,
        "fees_estimated": 0,
        "orders_with_label_charge": 0,
        "other_adjustments_ignored": other_adjustments,
    }
    rows: list[dict[str, Any]] = []

    for order in orders:
        stats["orders_seen"] += 1
        order_id = str(order["customerOrderId"])
        ordered_at = datetime.fromtimestamp(int(order["orderDate"]) / 1000, tz=UTC)
        day = ordered_at.date()
        lines: dict[str, dict[str, Any]] = {}
        for line in order.get("orderLines", {}).get("orderLine", []):
            if _line_status_cancelled(line):
                continue
            sku = (line.get("item", {}).get("sku") or "(sin sku)").strip()
            g = lines.setdefault(
                sku,
                {
                    "name": line.get("item", {}).get("productName"),
                    "units": 0,
                    "gross": 0.0,
                    "refund": 0.0,
                },
            )
            g["units"] += int(_f(line.get("orderLineQuantity", {}).get("amount")) or 1)
            for ch in line.get("charges", {}).get("charge", []):
                if ch.get("chargeType") == "PRODUCT":
                    g["gross"] += _f(ch.get("chargeAmount", {}).get("amount"))
            refund = line.get("refund") or {}
            for rc in refund.get("refundCharges", {}).get("refundCharge", []) or []:
                charge = rc.get("charge") or {}
                if charge.get("chargeType") == "PRODUCT":
                    g["refund"] += abs(_f(charge.get("chargeAmount", {}).get("amount")))
        if not lines:
            stats["orders_cancelled"] += 1
            continue

        total_gross = sum(g["gross"] for g in lines.values())
        total_units = sum(g["units"] for g in lines.values())
        label = labels.get(order_id, 0.0)
        if label:
            stats["orders_with_label_charge"] += 1
        for sku, g in lines.items():
            share = (
                g["gross"] / total_gross if total_gross else g["units"] / total_units
            )
            key = (order_id, sku)
            if key in commission:
                fee = commission[key]
                stats["fees_actual"] += 1
            else:
                fee = g["gross"] * commission_rate
                stats["fees_estimated"] += 1
            stats["lines"] += 1
            rows.append(
                {
                    "date": day,
                    "channel": CHANNEL,
                    "account": account,
                    "order_id": order_id,
                    "sku": sku,
                    "product_name": g["name"],
                    "units": g["units"],
                    "gross_sales": g["gross"],
                    "promo": 0.0,
                    "platform_fees": max(fee, 0.0),
                    "shipping_cost": max(label, 0.0) * share,
                    "refund_amount": g["refund"],
                    "ordered_at": ordered_at,
                }
            )
    return pl.DataFrame(rows, schema=OUT_SCHEMA), stats


@dataclass
class SyncReport:
    days: int
    stats: dict[str, int] = field(default_factory=dict)
    imported: ImportResult = field(default_factory=ImportResult)


def fetch_window(
    client: WalmartClient, days: int
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Órdenes creadas en la ventana y las filas de conciliación que las cubren."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    orders = list(client.iter_orders(start, end))
    recon: list[dict[str, str]] = []
    # Una orden de hoy se liquida días después: tomamos los reportes desde antes de la ventana.
    first = (start - timedelta(days=15)).date()
    for report_date in client.recon_dates():
        if report_date >= first:
            recon.extend(client.recon_rows(report_date))
    return orders, recon


async def sync_walmart(
    session: AsyncSession,
    client: WalmartClient,
    days: int = 30,
    account: str = DEFAULT_ACCOUNT,
) -> SyncReport:
    orders, recon = await asyncio.to_thread(fetch_window, client, days)
    frame, stats = build_order_lines(orders, recon, account)
    imported = await import_orders(session, frame) if frame.height else ImportResult()
    await session.commit()
    return SyncReport(days=days, stats=stats, imported=imported)
