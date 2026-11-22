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
from app.ops_data import (
    upsert_listing_stock,
    upsert_return_cases,
    upsert_seller_performance,
)

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

    def _paged(
        self,
        path: str,
        params: dict[str, Any],
        items: Any,
        cursor: Any,
    ) -> Iterator[dict[str, Any]]:
        """Recorre las páginas. El cursor llega como query completa ("?...") o como token suelto."""
        url, query = path, dict(params)
        while True:
            body = self._get(url, query).json()
            yield from items(body)
            nxt = cursor(body)
            if not nxt:
                return
            if nxt.startswith("?"):
                url, query = path + nxt, None  # type: ignore[assignment]
            else:
                url, query = path, {**params, "nextCursor": nxt}

    def iter_returns(self, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
        yield from self._paged(
            "/v3/returns",
            {
                "returnCreationStartDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "returnCreationEndDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": PAGE_LIMIT,
            },
            lambda b: b.get("returnOrders", []),
            lambda b: b.get("meta", {}).get("nextCursor"),
        )

    def seller_performance(self) -> dict[str, Any]:
        """Indicadores del vendedor por ventana: {"7DAY": {...}, "30DAY": {...}, ...}."""
        return self._get("/v3/report/payment/performance").json().get("payload", {})

    def iter_items(self) -> Iterator[dict[str, Any]]:
        yield from self._paged(
            "/v3/items",
            {"limit": PAGE_LIMIT},
            lambda b: b.get("ItemResponse", []),
            lambda b: b.get("nextCursor"),
        )

    def iter_inventories(self) -> Iterator[dict[str, Any]]:
        yield from self._paged(
            "/v3/inventories",
            {"limit": 50},
            lambda b: b.get("elements", {}).get("inventories", []),
            lambda b: b.get("meta", {}).get("nextCursor"),
        )

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


REFUNDED_STATUSES = {"COMPLETED"}


def _return_refunds(cases: list[dict[str, Any]] | None) -> dict[tuple[str, str], float]:
    """Reembolso ya pagado por (orden, sku), según las devoluciones completadas.

    La API de órdenes casi no trae reembolsos, así que sin esto la utilidad ignora las devoluciones.
    """
    refunds: dict[tuple[str, str], float] = defaultdict(float)
    for case in cases or []:
        if case["status"] not in REFUNDED_STATUSES:
            continue
        for line in case["lines"]:
            refunds[(case["external_order_id"], line["sku"])] += line["refund_total"]
    return refunds


def build_order_lines(
    orders: list[dict[str, Any]],
    recon: list[dict[str, str]],
    account: str = DEFAULT_ACCOUNT,
    commission_rate: float = DEFAULT_COMMISSION_RATE,
    returns: list[dict[str, Any]] | None = None,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Devuelve (líneas canónicas, estadísticas). `returns` son casos de `build_return_cases`."""
    commission, labels, other_adjustments = _recon_index(recon)
    returned = _return_refunds(returns)
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
                    # Lo que informa la orden y lo de las devoluciones es lo mismo: no se suman.
                    "refund_amount": max(g["refund"], returned.get((order_id, sku), 0.0)),
                    "ordered_at": ordered_at,
                }
            )
    return pl.DataFrame(rows, schema=OUT_SCHEMA), stats


def _ts(value: Any) -> datetime | None:
    """Fecha de Walmart: epoch en milisegundos o texto ISO 8601."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _amount(node: Any) -> float:
    return _f((node or {}).get("currencyAmount"))


def build_return_cases(returns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Devoluciones de Walmart al formato de `upsert_return_cases`.

    El reembolso es el precio del artículo (sin impuesto), igual que el reembolso por línea de la
    orden. Si el comprador cancela la devolución, el estado termina en `_CANCEL` y el reporte la
    excluye.
    """
    cases: list[dict[str, Any]] = []
    for r in returns:
        order_lines = r.get("returnOrderLines") or []
        lines: dict[str, dict[str, Any]] = {}
        statuses: list[str] = []
        updated: datetime | None = None
        reason: str | None = None
        for ln in order_lines:
            item = ln.get("item") or {}
            sku = (item.get("sku") or "(sin sku)").strip()
            units = int(_f((ln.get("quantity") or {}).get("measurementValue")) or 1)
            price = sum(
                _amount(ch.get("chargePerUnit"))
                for ch in ln.get("charges") or []
                if ch.get("chargeName") == "ItemPrice"
            ) or _amount(ln.get("unitPrice"))
            g = lines.setdefault(
                sku,
                {"sku": sku, "product_name": item.get("productName"), "units": 0, "refund_total": 0.0},
            )
            g["units"] += units
            g["refund_total"] += price * units
            if ln.get("status"):
                statuses.append(str(ln["status"]))
            reason = reason or ln.get("returnReason")
            seen = _ts(ln.get("statusTime"))
            if seen and (updated is None or seen > updated):
                updated = seen
        created = _ts(r.get("returnOrderDate"))
        if created is None or not lines:
            continue
        status = statuses[-1] if statuses else None
        if status and "CANCEL" in status.upper():
            status = "BUYER_CANCEL"
        cases.append(
            {
                "external_id": str(r["returnOrderId"]),
                "kind": "return",
                "case_type": r.get("refundMode") or "RETURN",
                "status": status,
                "reason": reason,
                "reason_code": reason,
                "initiated_by": "buyer",
                "external_order_id": str(r.get("customerOrderId") or ""),
                "case_date": created.date(),
                "created_at": created,
                "updated_at": updated or created,
                "refund_total": sum(g["refund_total"] for g in lines.values()),
                "lines": list(lines.values()),
            }
        )
    return cases


def build_performance(payload: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """Una fila por ventana ("30DAY" -> 30) con la foto de hoy."""
    day = today or datetime.now(UTC).date()
    rows: list[dict[str, Any]] = []
    for key, v in payload.items():
        digits = "".join(ch for ch in key if ch.isdigit())
        if not digits or not isinstance(v, dict):
            continue
        rows.append(
            {
                "snapshot_date": day,
                "window_days": int(digits),
                "ots": v.get("ots"),
                "otd": v.get("otd"),
                "vtr": v.get("vtr"),
                "refund_rate_seller_fault": v.get("refundRateSellerFault"),
                "refund_rate_customer_fault": v.get("refundRateCustomerFault"),
                "cancellation_seller_fault": v.get("cancellationSellerFault"),
                "cancellation_customer_fault": v.get("cancellationCustomerFault"),
            }
        )
    return sorted(rows, key=lambda r: r["window_days"])


def build_listing_stock(
    items: list[dict[str, Any]], inventories: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Une catálogo (precio, publicación) con existencias sumando todos los nodos de envío."""
    stock: dict[str, dict[str, int]] = {}
    for inv in inventories:
        sku = (inv.get("sku") or "").strip()
        if not sku:
            continue
        s = stock.setdefault(sku, {"avail": 0, "reserved": 0, "input": 0})
        for node in inv.get("nodes") or []:
            s["avail"] += int(_f((node.get("availToSellQty") or {}).get("amount")))
            s["reserved"] += int(_f((node.get("reservedQty") or {}).get("amount")))
            s["input"] += int(_f((node.get("inputQty") or {}).get("amount")))

    rows: dict[str, dict[str, Any]] = {}
    for it in items:
        sku = (it.get("sku") or "").strip()
        if not sku:
            continue
        reasons = (it.get("unpublishedReasons") or {}).get("reason") or []
        rows[sku] = {
            "sku": sku,
            "product_name": it.get("productName"),
            "price": (it.get("price") or {}).get("amount"),
            "availability": it.get("availability"),
            "published_status": it.get("publishedStatus"),
            "lifecycle_status": it.get("lifecycleStatus"),
            "unpublished_reasons": "; ".join(reasons) or None,
        }
    for sku in stock.keys() | rows.keys():
        row = rows.setdefault(
            sku,
            {
                "sku": sku,
                "product_name": None,
                "price": None,
                "availability": None,
                "published_status": None,
                "lifecycle_status": None,
                "unpublished_reasons": None,
            },
        )
        s = stock.get(sku)
        row["avail_to_sell"] = s["avail"] if s else None
        row["reserved"] = s["reserved"] if s else None
        row["input_qty"] = s["input"] if s else None
    return sorted(rows.values(), key=lambda r: r["sku"])


@dataclass
class SyncReport:
    days: int
    stats: dict[str, int] = field(default_factory=dict)
    imported: ImportResult = field(default_factory=ImportResult)
    problems: list[str] = field(default_factory=list)


@dataclass
class Extras:
    returns: list[dict[str, Any]] = field(default_factory=list)
    performance: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    inventories: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def fetch_extras(client: WalmartClient, days: int) -> Extras:
    """Devoluciones, desempeño y existencias. Un fallo en uno no detiene a los demás."""
    out = Extras()
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    steps = (
        ("devoluciones", lambda: setattr(out, "returns", list(client.iter_returns(start, end)))),
        ("desempeño", lambda: setattr(out, "performance", client.seller_performance())),
        ("catálogo", lambda: setattr(out, "items", list(client.iter_items()))),
        ("inventario", lambda: setattr(out, "inventories", list(client.iter_inventories()))),
    )
    for name, step in steps:
        try:
            step()
        except WalmartError as exc:
            out.problems.append(f"{name}: {exc}")
    return out


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
    extras = await asyncio.to_thread(fetch_extras, client, days)
    cases = build_return_cases(extras.returns)
    frame, stats = build_order_lines(orders, recon, account, returns=cases)
    imported = await import_orders(session, frame) if frame.height else ImportResult()

    performance = build_performance(extras.performance)
    listings = build_listing_stock(extras.items, extras.inventories)
    stats["return_cases"] = await upsert_return_cases(session, CHANNEL, account, cases)
    stats["performance_windows"] = await upsert_seller_performance(
        session, CHANNEL, account, performance
    )
    stats["listings"] = await upsert_listing_stock(session, CHANNEL, account, listings)
    await session.commit()
    return SyncReport(days=days, stats=stats, imported=imported, problems=extras.problems)
