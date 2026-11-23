"""Conector de TikTok Ads (Marketing API de TikTok for Business): gasto publicitario diario.

SOLO LECTURA: únicamente hace GET (informe de gasto e información del anunciante); no crea ni modifica
campañas. El gasto se guarda en sales.ad_spend_daily del canal TikTok Shop (cuenta tiktok_shop_us),
así entra a la utilidad neta (columna ad_spend) igual que un CSV de publicidad.

El token de acceso de TikTok Ads no caduca mientras la autorización siga vigente; se obtiene una vez
canjeando el auth_code del anunciante (ver docs/GUIA_LOCAL.md).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx
import polars as pl
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.persist import ImportResult, import_ad_spend

API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
CHANNEL = "tiktok_shop"
DEFAULT_ACCOUNT = "tiktok_shop_us"
MAX_DAYS_PER_REQUEST = 30  # límite de TikTok para informes por día
PAGE_SIZE = 200

SPEND_SCHEMA = {
    "date": pl.Date,
    "channel": pl.Utf8,
    "account": pl.Utf8,
    "sku": pl.Utf8,
    "amount": pl.Float64,
}


class TikTokAdsSettings(BaseSettings):
    """Variables TTADS_* del archivo .env. Sin token o sin anunciantes la fuente cuenta como no configurada."""

    access_token: str = Field(min_length=1)
    advertiser_ids: str = Field(min_length=1)  # separados por coma
    app_id: str = ""
    app_secret: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="TTADS_", extra="ignore")

    @property
    def advertisers(self) -> list[str]:
        return [a.strip() for a in self.advertiser_ids.split(",") if a.strip()]


class TikTokAdsError(RuntimeError):
    def __init__(self, code: int | str, message: str):
        self.code = code
        super().__init__(f"TikTok Ads {code}: {message}")


class TikTokAdsClient:
    def __init__(
        self, settings: TikTokAdsSettings | None = None, http: httpx.Client | None = None
    ) -> None:
        self.settings = settings or TikTokAdsSettings()  # type: ignore[call-arg]
        self.http = http or httpx.Client(base_url=API_BASE, timeout=60)

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        res = self.http.get(path, params=params, headers={"Access-Token": self.settings.access_token})
        if res.status_code != 200:
            raise TikTokAdsError(res.status_code, res.text[:200])
        body = res.json()
        if body.get("code") != 0:
            raise TikTokAdsError(body.get("code", "?"), str(body.get("message", ""))[:300])
        return body.get("data") or {}

    def advertiser_info(self, advertiser_id: str) -> dict[str, Any]:
        data = self.get("/advertiser/info/", {"advertiser_ids": json.dumps([advertiser_id])})
        rows = data.get("list") or []
        return rows[0] if rows else {}

    def iter_daily_spend(self, advertiser_id: str, start: date, end: date) -> list[dict[str, Any]]:
        """Filas {date, amount} del anunciante entre `start` y `end` (en tramos de 30 días)."""
        out: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(cursor + timedelta(days=MAX_DAYS_PER_REQUEST - 1), end)
            page = 1
            while True:
                data = self.get(
                    "/report/integrated/get/",
                    {
                        "advertiser_id": advertiser_id,
                        "report_type": "BASIC",
                        "data_level": "AUCTION_ADVERTISER",
                        "dimensions": json.dumps(["advertiser_id", "stat_time_day"]),
                        "metrics": json.dumps(["spend"]),
                        "start_date": cursor.isoformat(),
                        "end_date": chunk_end.isoformat(),
                        "page": page,
                        "page_size": PAGE_SIZE,
                    },
                )
                for row in data.get("list") or []:
                    out.append(
                        {
                            "date": date.fromisoformat(row["dimensions"]["stat_time_day"][:10]),
                            "amount": float(row["metrics"].get("spend") or 0),
                        }
                    )
                total_pages = int((data.get("page_info") or {}).get("total_page") or 1)
                if page >= total_pages:
                    break
                page += 1
            cursor = chunk_end + timedelta(days=1)
        return out


def build_spend_frame(rows: list[dict[str, Any]], account: str = DEFAULT_ACCOUNT) -> pl.DataFrame:
    """Un gasto por día (se suman varios anunciantes); los días sin gasto se omiten."""
    per_day: dict[date, float] = {}
    for r in rows:
        per_day[r["date"]] = per_day.get(r["date"], 0.0) + r["amount"]
    records = [
        {"date": d, "channel": CHANNEL, "account": account, "sku": None, "amount": round(a, 4)}
        for d, a in sorted(per_day.items())
        if a > 0
    ]
    return pl.DataFrame(records, schema=SPEND_SCHEMA)


@dataclass
class AdsReport:
    days: int
    imported: ImportResult
    stats: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


async def sync_tiktok_ads(
    session: AsyncSession, client: TikTokAdsClient, days: int = 14, today: date | None = None
) -> AdsReport:
    end = today or date.today()
    start = end - timedelta(days=max(days, 1) - 1)
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    advertisers: list[dict[str, str]] = []

    for advertiser_id in client.settings.advertisers:
        info = await asyncio.to_thread(client.advertiser_info, advertiser_id)
        currency = info.get("currency")
        name = info.get("name") or advertiser_id
        if currency and currency != "USD":
            problems.append(f"{name}: moneda {currency} (solo se admite USD); se omite")
            continue
        rows.extend(await asyncio.to_thread(client.iter_daily_spend, advertiser_id, start, end))
        advertisers.append({"id": advertiser_id, "name": str(name)})

    frame = build_spend_frame(rows)
    imported = await import_ad_spend(session, frame) if frame.height else ImportResult()
    stats = {
        "advertisers": advertisers,
        "days_with_spend": frame.height,
        "spend_total": round(float(frame["amount"].sum() or 0), 2) if frame.height else 0.0,
        "window": [start.isoformat(), end.isoformat()],
    }
    return AdsReport(days=days, imported=imported, stats=stats, problems=problems)
