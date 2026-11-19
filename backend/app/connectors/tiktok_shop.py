"""Conector de TikTok Shop Partner API (versión 202309).

- Firma HMAC-SHA256 de cada petición.
- Renueva el access token automáticamente y guarda los tokens nuevos en
  `tts_tokens.json` (ignorado por git), porque el refresh token rota.
- Solo lectura: órdenes, tiendas y finanzas.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

API_BASE = "https://open-api.tiktokglobalshop.com"
AUTH_BASE = "https://auth.tiktok-shops.com"
EXPIRY_MARGIN_SECONDS = 300


class TikTokShopSettings(BaseSettings):
    """Variables TTS_* del archivo .env."""

    app_key: str
    app_secret: str
    access_token: str = ""
    refresh_token: str = ""
    access_token_expire_in: int = 0
    shop_cipher: str = ""
    tokens_file: Path = Path("tts_tokens.json")

    model_config = SettingsConfigDict(env_file=".env", env_prefix="TTS_", extra="ignore")


class TikTokShopError(RuntimeError):
    def __init__(self, code: int | str, message: str):
        self.code = code
        super().__init__(f"TikTok Shop {code}: {message}")


def sign_request(secret: str, path: str, params: dict[str, str], body: str = "") -> str:
    """Firma oficial: secret + path + params ordenados (sin sign/access_token) + body + secret."""
    items = sorted((k, v) for k, v in params.items() if k not in {"sign", "access_token"})
    base = secret + path + "".join(f"{k}{v}" for k, v in items) + body + secret
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


class TikTokShopClient:
    def __init__(
        self, settings: TikTokShopSettings | None = None, http: httpx.Client | None = None
    ) -> None:
        self.settings = settings or TikTokShopSettings()
        self._http = http or httpx.Client(timeout=30)
        self._tokens = self._load_tokens()

    # --- tokens -----------------------------------------------------------
    def _load_tokens(self) -> dict[str, Any]:
        file = self.settings.tokens_file
        if file.exists():
            return json.loads(file.read_text())
        s = self.settings
        return {
            "access_token": s.access_token,
            "refresh_token": s.refresh_token,
            "access_token_expire_in": s.access_token_expire_in,
            "shop_cipher": s.shop_cipher,
        }

    def _save_tokens(self) -> None:
        self.settings.tokens_file.write_text(json.dumps(self._tokens))

    def refresh(self) -> None:
        r = self._http.get(
            f"{AUTH_BASE}/api/v2/token/refresh",
            params={
                "app_key": self.settings.app_key,
                "app_secret": self.settings.app_secret,
                "refresh_token": self._tokens["refresh_token"],
                "grant_type": "refresh_token",
            },
        ).json()
        if r.get("code") != 0:
            raise TikTokShopError(r.get("code"), r.get("message", "no se pudo renovar el token"))
        d = r["data"]
        for key in ("access_token", "access_token_expire_in", "refresh_token", "refresh_token_expire_in"):
            self._tokens[key] = d[key]
        self._save_tokens()

    def _ensure_token(self) -> None:
        expires = int(self._tokens.get("access_token_expire_in") or 0)
        if not self._tokens.get("access_token") or expires - time.time() < EXPIRY_MARGIN_SECONDS:
            self.refresh()

    # --- peticiones -------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        *,
        shop: bool = True,
    ) -> dict[str, Any]:
        self._ensure_token()
        query = {
            "app_key": self.settings.app_key,
            "timestamp": str(int(time.time())),
            **{k: str(v) for k, v in (params or {}).items()},
        }
        if shop:
            query["shop_cipher"] = self.shop_cipher()
        raw = json.dumps(body, separators=(",", ":")) if body is not None else ""
        query["sign"] = sign_request(self.settings.app_secret, path, query, raw)
        resp = self._http.request(
            method,
            f"{API_BASE}{path}",
            params=query,
            content=raw.encode() if raw else None,
            headers={
                "x-tts-access-token": self._tokens["access_token"],
                "Content-Type": "application/json",
            },
        )
        payload = resp.json()
        if payload.get("code") != 0:
            raise TikTokShopError(payload.get("code"), payload.get("message", ""))
        return payload.get("data") or {}

    def shops(self) -> list[dict[str, Any]]:
        return self.request("GET", "/authorization/202309/shops", shop=False).get("shops", [])

    def shop_cipher(self) -> str:
        if not self._tokens.get("shop_cipher"):
            shops = self.shops()
            if not shops:
                raise TikTokShopError("no_shop", "No hay tiendas autorizadas para este token")
            self._tokens["shop_cipher"] = shops[0]["cipher"]
            self._save_tokens()
        return self._tokens["shop_cipher"]

    # --- datos ------------------------------------------------------------
    def _paginate(
        self,
        method: str,
        path: str,
        items_key: str,
        params: dict[str, Any],
        body: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        token = ""
        while True:
            page = {**params, **({"page_token": token} if token else {})}
            data = self.request(method, path, page, body)
            yield from data.get(items_key, [])
            token = data.get("next_page_token") or ""
            if not token:
                return

    def iter_orders(self, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
        yield from self._paginate(
            "POST",
            "/order/202309/orders/search",
            "orders",
            {"page_size": 100, "sort_field": "create_time", "sort_order": "ASC"},
            {"create_time_ge": int(start.timestamp()), "create_time_lt": int(end.timestamp())},
        )

    def iter_statements(self, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
        yield from self._paginate(
            "GET",
            "/finance/202309/statements",
            "statements",
            {
                "page_size": 100,
                "sort_field": "statement_time",
                "statement_time_ge": int(start.timestamp()),
                "statement_time_lt": int(end.timestamp()),
            },
        )

    def iter_statement_transactions(self, statement_id: str) -> Iterator[dict[str, Any]]:
        yield from self._paginate(
            "GET",
            f"/finance/202309/statements/{statement_id}/statement_transactions",
            "statement_transactions",
            {"page_size": 100, "sort_field": "order_create_time"},
        )
