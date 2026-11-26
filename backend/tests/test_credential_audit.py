"""Seguridad de credenciales: ningún valor secreto sale JAMÁS; clasificación, inventario, rotación, captura, API y acciones.

Todo con un backend sintético en una carpeta temporal cuyos valores llevan el marcador `SECRETO-`: si ese texto
aparece en una respuesta, un log o una salida estándar, la prueba falla. Ninguna prueba toca la red ni los
archivos reales de credenciales.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import action_center, action_providers, credential_actions
from app import credential_audit as audit
from app import credential_inventory as inventory
from app.auth import CurrentUser, current_user
from app.connectors.tiktok_creator import atomic_write_text
from app.connectors.tiktok_shop import TikTokShopSettings
from app.credential_audit_api import get_capturer, get_source, router
from app.credential_catalog import (
    CREDENTIAL_SPECS,
    EXPOSED_IN_CHAT,
    PLATFORMS,
    ROTATION_GUIDES,
    TIKTOK_ADS,
    TIKTOK_CREATOR,
    TIKTOK_ORGANIC,
    TIKTOK_SHOP,
    TOKEN_FILES,
)
from app.credential_ops import (
    load_scope_snapshots,
    mark_rotated,
    reopen_rotation,
    replace_scope_snapshot,
)
from app.credential_scope_capture import (
    ScopeCaptureError,
    ScopeRecorder,
    capture_seller_scopes,
    parse_scope_names,
)
from app.credential_service import InventorySource
from app.db import create_dev_schema, get_session, make_engine
from app.models import CredentialRotation, CredentialScopeSnapshot

SECRET = "SECRETO"  # prefijo común de todos los valores sintéticos
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
FAR_FUTURE = int((NOW + timedelta(days=3650)).timestamp())
HAS_GIT = shutil.which("git") is not None

# Los 31 permisos que TikTok devolvió al renovar el token real del vendedor (solo nombres).
SELLER_SCOPES: tuple[str, ...] = (
    "data.bestselling.public.read", "data.shop_analytics.public.read", "seller.affiliate_collaboration.read",
    "seller.affiliate_collaboration.write", "seller.affiliate_messages.write", "seller.authorization.info",
    "seller.creator_marketplace.read", "seller.customer_service", "seller.delivery.status.write", "seller.fbt.info",
    "seller.finance.info", "seller.fulfillment.basic", "seller.fulfillment.package.write",
    "seller.global_product.category.info", "seller.global_product.delete", "seller.global_product.image_trans_task",
    "seller.global_product.info", "seller.global_product.write", "seller.logistics", "seller.order.ext_ref.read",
    "seller.order.ext_ref.write", "seller.order.info", "seller.product.basic", "seller.product.delete",
    "seller.product.write", "seller.promotion.info", "seller.promotion.write", "seller.redeem_info.write",
    "seller.return_refund.basic", "seller.shop.info", "serller.product.optimize",
)


# --- un backend sintético -----------------------------------------------------------------------------------


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _tokens(tag: str, *, access_exp: int = FAR_FUTURE, refresh_exp: int = FAR_FUTURE) -> dict[str, Any]:
    return {
        "access_token": f"{SECRET}-ACCESS-{tag}",
        "access_token_expire_in": access_exp,
        "refresh_token": f"{SECRET}-REFRESH-{tag}",
        "refresh_token_expire_in": refresh_exp,
        "shop_cipher": f"{SECRET}-CIPHER",
    }


@pytest.fixture
def backend(tmp_path: Path) -> Path:
    """Carpeta con .env, tres archivos de tokens y un repositorio git que solo ignora algunos."""
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# TTADS_APP_SECRET=SECRETO-COMENTADO",
                f"TTS_APP_SECRET={SECRET}-APP",
                f'TTADS_ACCESS_TOKEN="{SECRET}-ADS"',
                f"export WMT_CLIENT_SECRET={SECRET}-WMT",
                f"CHK_DATABASE_URL=postgresql://user:{SECRET}-DB@host:5432/db",
                "REDO_API_KEY=",  # vacío: cuenta como ausente
                "CHK_REPORT_TIMEZONE=America/New_York",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env.bak").write_text(f"TTS_APP_SECRET={SECRET}-VIEJO", encoding="utf-8")
    (tmp_path / ".env.example").write_text("TTS_APP_SECRET=", encoding="utf-8")
    _write_json(tmp_path / "tts_tokens.json", _tokens("SHOP", access_exp=int((NOW + timedelta(days=6)).timestamp())))
    _write_json(tmp_path / "tokens_creator.json", _tokens("CREATOR", refresh_exp=int((NOW + timedelta(days=10)).timestamp())))
    _write_json(
        tmp_path / "tiktok_organic_tokens.json",
        {
            "access_token": f"{SECRET}-ORG-ACCESS",
            "refresh_token": f"{SECRET}-ORG-REFRESH",
            "access_expires_at": (NOW + timedelta(days=1)).isoformat(),
            "refresh_expires_at": (NOW + timedelta(days=300)).isoformat(),
            "open_id": f"{SECRET}-OPENID",
            "scope": ["user.info.basic", "video.list"],
        },
    )
    (tmp_path / ".gitignore").write_text(".env\n.env.*\n!.env.example\ntts_tokens*.json\ntiktok_organic_tokens*.json\n", encoding="utf-8")
    if HAS_GIT:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _source(backend: Path) -> InventorySource:
    return InventorySource(base_dir=backend, environ={})


def assert_clean(payload: Any) -> None:
    """Falla si el marcador de secreto aparece en cualquier parte de la salida."""
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    assert SECRET not in text, "se filtró un valor secreto"


# --- .env y archivos de tokens: solo nombres y fechas -------------------------------------------------------


def test_env_names_only_and_empty_or_commented_excluded(backend):
    names = inventory.read_env_names(backend / ".env")
    assert {"TTS_APP_SECRET", "TTADS_ACCESS_TOKEN", "WMT_CLIENT_SECRET", "CHK_DATABASE_URL"} <= names
    assert "REDO_API_KEY" not in names and "TTADS_APP_SECRET" not in names
    assert all(isinstance(n, str) and SECRET not in n for n in names)
    assert inventory.read_env_names(backend / "no-existe") == frozenset()


def test_token_facts_keep_names_and_dates_not_values(backend):
    facts = inventory.read_token_facts(backend / "tts_tokens.json", frozenset({"access_token_expire_in"}))
    assert {"access_token", "refresh_token"} <= facts.fields_present
    assert facts.expiries["access_token_expire_in"] == datetime.fromtimestamp(NOW.timestamp() + 6 * 86400, tz=UTC)
    assert_clean(repr(facts))
    (backend / "roto.json").write_text("{no es json", encoding="utf-8")
    assert inventory.read_token_facts(backend / "roto.json", frozenset()).fields_present == frozenset()


def test_parse_expiry_formats():
    assert inventory.parse_expiry(1_800_000_000) == inventory.parse_expiry(1_800_000_000_000)  # segundos y milisegundos
    assert inventory.parse_expiry("2026-12-01T00:00:00Z".replace("Z", "+00:00")) == datetime(2026, 12, 1, tzinfo=UTC)
    assert inventory.parse_expiry("2026-12-01T00:00:00").tzinfo is UTC
    assert all(inventory.parse_expiry(v) is None for v in (None, "", "mañana", 0, -5, True, {}))


@pytest.mark.skipif(not HAS_GIT, reason="git no está instalado")
def test_git_ignore_detection_in_temp_repo(backend, tmp_path_factory):
    assert inventory.git_ignores(backend / ".env", backend) is True
    assert inventory.git_ignores(backend / "tokens_creator.json", backend) is False  # no está en el .gitignore sintético
    assert inventory.git_ignores(backend / ".env.example", backend) is False
    outside = tmp_path_factory.mktemp("sin-repo")
    assert inventory.git_ignores(outside / ".env", outside) is None


@pytest.mark.skipif(not HAS_GIT, reason="git no está instalado")
def test_real_gitignore_covers_token_backups_and_locks():
    """Regresión: la renovación atómica crea .bak/.lock junto al archivo; antes no estaban ignorados."""
    for name in ("tts_tokens.json", "tokens_creator.json", "tiktok_organic_tokens.json", ".env", ".env.bak"):
        for suffix in ("", ".bak", ".lock") if name.endswith(".json") else ("",):
            assert inventory.git_ignores(inventory.BACKEND_DIR / (name + suffix), inventory.BACKEND_DIR) is True, name + suffix


# --- inventario ---------------------------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_GIT, reason="git no está instalado")
def test_inventory_facts_and_no_secret_in_any_field(backend):
    items = inventory.build_inventory(base_dir=backend, environ={}, now=NOW)
    assert_clean(items)
    by_name = {i["name"]: i for i in items}
    assert by_name["TTS_APP_SECRET"]["present"] and by_name["TTS_APP_SECRET"]["git_ignored"] is True
    assert by_name["REDO_API_KEY"]["present"] is False
    assert by_name["tts_tokens.json:access_token"]["days_left"] == 6.0
    assert by_name["tiktok_organic_tokens.json:access_token"]["days_left"] == 1.0
    creator_refresh = by_name["tokens_creator.json:refresh_token"]
    assert creator_refresh["days_left"] == 10.0 and creator_refresh["git_ignored"] is False
    assert by_name["TTS_APP_SECRET"]["copies"] == [{"file": ".env.bak", "git_ignored": True}]  # .env.example no es copia
    assert all(i["in_app_backup"] is False for i in items)
    assert {i["platform"] for i in items} == {s.platform for s in CREDENTIAL_SPECS}


def test_present_platforms_without_git_and_environ_names(backend):
    assert inventory.present_platforms(backend, {}) == {TIKTOK_SHOP, TIKTOK_CREATOR, TIKTOK_ORGANIC, TIKTOK_ADS, "walmart", "supabase"}
    assert "anthropic" in inventory.present_platforms(backend, {"ANTHROPIC_API_KEY": "algo"})
    assert "anthropic" not in inventory.present_platforms(backend, {"ANTHROPIC_API_KEY": "  "})


def test_backup_module_never_includes_credential_files():
    source = (inventory.BACKEND_DIR / "app" / "backup.py").read_text(encoding="utf-8")
    assert not any(name in source for name in (*TOKEN_FILES, ".env"))
    assert inventory.FILES_IN_APP_BACKUP == frozenset()


# --- clasificación de permisos --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "level"),
    [
        ("seller.product.write", "write"),
        ("seller.product.delete", "delete"),
        ("seller.finance.info", "read"),
        ("data.bestselling.public.read", "read"),
        ("video.publish", "write"),
    ],
)
def test_known_scope_levels(scope, level):
    result = audit.classify_scope(scope)
    assert (result.level, result.documented) == (level, True)


@pytest.mark.parametrize(
    ("scope", "level"),
    [("seller.new.write", "write"), ("seller.new.delete", "delete"), ("post.publish", "write"), ("profile.edit", "write"),
     ("seller.new.read", "read"), ("seller.overwrite.info", "read"), ("algo.raro", "read")],
)
def test_unknown_scopes_use_pattern_and_are_flagged(scope, level):
    result = audit.classify_scope(scope, TIKTOK_SHOP)
    assert result.level == level and result.documented is False and result.used_by_app is False
    assert result.allows.startswith("Sin documentar")


def test_app_never_uses_a_write_scope_except_its_own_database():
    """La app solo lee: ningún permiso de escritura conocido está marcado como usado (salvo su propia base)."""
    used_writes = {s for s, i in audit.KNOWN_SCOPES.items() if i.level != "read" and i.used_by_app}
    assert used_writes == {"supabase.database_connection"}


def test_connectors_only_issue_reads_and_searches():
    """Verificación de código: ningún conector usa PUT/PATCH/DELETE; los POST son búsquedas o canje de token."""
    forbidden = ('"PUT"', '"PATCH"', '"DELETE"', ".put(", ".patch(", ".delete(")
    for path in (inventory.BACKEND_DIR / "app" / "connectors").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(marker in text for marker in forbidden), path.name


def test_declared_platform_scopes_are_verified_constants():
    assert audit.DECLARED_SCOPES[TIKTOK_ORGANIC] == ("user.info.basic", "user.info.profile", "user.info.stats", "video.list")
    assert audit.DECLARED_SCOPES[TIKTOK_CREATOR] == ("LIVE_STREAM_PERMISSION", "SELF_SALE_PERMISSION")
    for platform, scopes in audit.DECLARED_SCOPES.items():
        for scope in scopes:
            info = audit.KNOWN_SCOPES[scope]
            assert info.platform == platform
            if scope != "supabase.database_connection":
                assert info.level == "read" and info.used_by_app, scope


def test_seller_scope_report_matches_real_capture():
    report = audit.platform_scopes(TIKTOK_SHOP, SELLER_SCOPES, NOW)
    assert report["granted"] == 31 and report["undocumented_count"] == 0
    assert (report["write_count"], report["delete_count"]) == (14, 2)
    assert report["unused_write_count"] == 14  # todos los de escritura sobran
    rec = report["recommendation"]
    assert rec["action"] == "remove" and not rec["can_revoke_from_app"]
    assert "seller.product.write" in rec["remove_now"] and "serller.product.optimize" in rec["remove_now"]
    assert "seller.order.info" not in rec["remove_now"] + rec["remove_optional"]  # lo que la app usa se queda
    assert "seller.fbt.info" in rec["remove_optional"]
    assert "Partner Center" in " ".join(rec["steps"]) and "no puede quitar permisos" in rec["summary"]
    assert [s["level"] for s in report["scopes"]][:14] != ["read"] * 14  # primero lo más peligroso


def test_scope_report_covers_every_platform_with_permissions():
    report = {r["platform"]: r for r in audit.scope_report({})}
    assert set(report) == {TIKTOK_SHOP, TIKTOK_CREATOR, TIKTOK_ORGANIC, TIKTOK_ADS, "walmart", "redo", "supabase"}
    assert report[TIKTOK_SHOP]["source"] == "not_captured" and report[TIKTOK_SHOP]["recommendation"]["action"] == "capture"
    assert report[TIKTOK_ORGANIC]["write_count"] == 0 and report[TIKTOK_ORGANIC]["recommendation"]["action"] == "none"
    assert "acceso TOTAL" in report["supabase"]["recommendation"]["summary"]
    assert report["supabase"]["unused_write_count"] == 0  # la usa el backend: no sobra, pero se advierte


# --- rotación y riesgo ----------------------------------------------------------------------------------------


def test_pending_rotation_includes_exposed_and_detected_extras():
    assert set(audit.pending_rotation(set(), frozenset())) == EXPOSED_IN_CHAT
    assert "anthropic" in audit.pending_rotation(set(), frozenset({"anthropic"}))
    assert TIKTOK_SHOP not in audit.pending_rotation({TIKTOK_SHOP}, frozenset())
    assert set(PLATFORMS) >= set(ROTATION_GUIDES) == set(PLATFORMS)


def test_upcoming_expirations_only_refresh_tokens():
    rows = [
        {"platform": "a", "name": "x:refresh", "kind": "refresh_token", "present": True, "expires_at": "d", "days_left": 5.0},
        {"platform": "a", "name": "x:access", "kind": "access_token", "present": True, "expires_at": "d", "days_left": 1.0},
        {"platform": "b", "name": "y:refresh", "kind": "refresh_token", "present": True, "expires_at": "d", "days_left": 900.0},
        {"platform": "c", "name": "z:refresh", "kind": "refresh_token", "present": False, "expires_at": "d", "days_left": 2.0},
    ]
    assert [r["name"] for r in audit.upcoming_expirations(rows)] == ["x:refresh"]


def test_risk_levels():
    high = audit.assess_risk(pending=[TIKTOK_SHOP], write_platforms=[TIKTOK_SHOP], unignored_files=[], expiring=[])
    assert high["level"] == "high" and "TikTok Shop" in high["reason"]
    assert audit.assess_risk(pending=[], write_platforms=[], unignored_files=["tokens.json"], expiring=[])["level"] == "high"
    assert audit.assess_risk(pending=["walmart"], write_platforms=[], unignored_files=[], expiring=[])["level"] == "medium"
    assert audit.assess_risk(pending=[], write_platforms=[TIKTOK_SHOP], unignored_files=[], expiring=[])["level"] == "medium"
    assert audit.assess_risk(pending=[], write_platforms=[], unignored_files=[], expiring=[])["level"] == "low"


# --- captura de permisos --------------------------------------------------------------------------------------


def _refresh_handler(calls: list[httpx.Request], *, code: int = 0, scopes: Any = SELLER_SCOPES):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/api/v2/token/refresh"
        data: dict[str, Any] = {
            "access_token": f"{SECRET}-NEW-ACCESS",
            "access_token_expire_in": FAR_FUTURE,
            "refresh_token": f"{SECRET}-NEW-REFRESH",
            "refresh_token_expire_in": FAR_FUTURE,
        }
        if scopes is not None:
            data["granted_scopes"] = list(scopes)
        return httpx.Response(200, json={"code": code, "message": "x", "data": data})

    return handler


def _shop_settings(backend: Path) -> TikTokShopSettings:
    return TikTokShopSettings(app_key="k", app_secret=f"{SECRET}-APPSECRET", tokens_file=backend / "tts_tokens.json")


def _mock_http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_capture_forces_atomic_renewal_and_returns_only_scope_names(backend, monkeypatch, capsys, caplog):
    path = backend / "tts_tokens.json"
    _write_json(path, _tokens("OLD"))  # access token vigente 10 años: una renovación normal se omitiría
    replaced: list[str] = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda src, dst: (replaced.append(str(dst)), real_replace(src, dst))[1])
    calls: list[httpx.Request] = []
    with caplog.at_level("DEBUG"):
        result = capture_seller_scopes(_shop_settings(backend), _mock_http(_refresh_handler(calls)))
    assert len(calls) == 1 and result.scopes == tuple(sorted(SELLER_SCOPES))
    assert_clean(repr(result))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["access_token"] == f"{SECRET}-NEW-ACCESS" and saved["shop_cipher"] == f"{SECRET}-CIPHER"  # conserva el resto
    assert json.loads((backend / "tts_tokens.json.bak").read_text(encoding="utf-8"))["access_token"] == f"{SECRET}-ACCESS-OLD"
    assert str(path) in replaced  # escritura atómica: temporal + os.replace
    assert not (backend / "tts_tokens.json.lock").exists() and not list(backend.glob("*.tmp"))
    out = capsys.readouterr()
    assert_clean(out.out + out.err + caplog.text)


def test_capture_is_repeatable(backend):
    calls: list[httpx.Request] = []
    http = _mock_http(_refresh_handler(calls))
    first = capture_seller_scopes(_shop_settings(backend), http)
    second = capture_seller_scopes(_shop_settings(backend), http)
    assert first == second and len(calls) == 2


def test_capture_failure_leaves_token_file_untouched(backend):
    path = backend / "tts_tokens.json"
    before = path.read_bytes()
    with pytest.raises(ScopeCaptureError) as err:
        capture_seller_scopes(_shop_settings(backend), _mock_http(_refresh_handler([], code=105002)))
    assert path.read_bytes() == before and not (backend / "tts_tokens.json.lock").exists()
    assert_clean(str(err.value))


def test_capture_without_scopes_is_an_error(backend):
    with pytest.raises(ScopeCaptureError, match="no trajo permisos"):
        capture_seller_scopes(_shop_settings(backend), _mock_http(_refresh_handler([], scopes=None)))


def test_capture_network_failure_is_reported_without_details(backend):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"sin red {SECRET}")

    with pytest.raises(ScopeCaptureError) as err:
        capture_seller_scopes(_shop_settings(backend), _mock_http(boom))
    assert_clean(str(err.value))


def test_missing_configuration_does_not_leak_pydantic_input(monkeypatch):
    for name in ("TTS_APP_KEY", "TTS_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(Path(__file__).parent)  # sin .env a la vista
    with pytest.raises(ScopeCaptureError) as err:
        capture_seller_scopes()
    assert err.value.__cause__ is None and "TTS_" in str(err.value)


def test_parse_scope_names_rejects_non_scope_values():
    assert parse_scope_names(["b.read", "a.write", "a.write", " c.info "]) == ("a.write", "b.read", "c.info")
    assert parse_scope_names("x.read,y.write") == ("x.read", "y.write")
    assert parse_scope_names(["ok.read", "con espacios", "<html>", 5, None]) == ("ok.read",)
    assert parse_scope_names({"a": 1}) == () and parse_scope_names(["a.b"] * 1000) == ()


def test_recorder_notifies_on_natural_renewal_and_ignores_other_urls():
    seen: list[tuple[str, ...]] = []
    calls: list[httpx.Request] = []
    recorder = ScopeRecorder(_mock_http(_refresh_handler(calls, scopes=("a.read", "b.write"))), seen.append)
    other = ScopeRecorder(_mock_http(lambda request: httpx.Response(200, json={"data": {"granted_scopes": ["z.write"]}})))
    other.get("https://auth.example/otra/ruta")  # no es la renovación: no se observa
    assert other.scopes == ()
    recorder.get("https://auth.example/api/v2/token/refresh")
    assert recorder.scopes == ("a.read", "b.write") and seen == [("a.read", "b.write")]
    assert hasattr(recorder, "close")  # el resto del cliente se reenvía


def test_atomic_write_never_leaves_partial_file(tmp_path):
    target = tmp_path / "t.json"
    atomic_write_text(target, "uno")
    assert target.read_text() == "uno" and [p.name for p in tmp_path.iterdir()] == ["t.json"]


# --- base de datos (sqlite en memoria) -----------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_rotation_is_idempotent_and_reopenable(maker):
    async with maker() as s:
        first = await mark_rotated(s, TIKTOK_SHOP, "a@x.com", "  hecho  ", NOW)
        again = await mark_rotated(s, TIKTOK_SHOP, "b@x.com", "otra", NOW + timedelta(days=1))
        await s.commit()
        assert again is first and again.rotated_by_email == "a@x.com" and again.note == "hecho"
        assert len((await s.scalars(select(CredentialRotation))).all()) == 1
        assert await reopen_rotation(s, TIKTOK_SHOP) is True
        assert await reopen_rotation(s, TIKTOK_SHOP) is False
        assert (await mark_rotated(s, TIKTOK_SHOP, None, "x" * 900, NOW)).note == "x" * 500


@pytest.mark.asyncio
async def test_scope_snapshot_replaces_previous_capture_and_stores_levels(maker):
    async with maker() as s:
        assert await replace_scope_snapshot(s, TIKTOK_SHOP, ("seller.product.write", "seller.order.info", "seller.order.info"), NOW) == 2
        await replace_scope_snapshot(s, TIKTOK_SHOP, ("seller.product.delete",), NOW + timedelta(days=1))
        await replace_scope_snapshot(s, "otra", ("x.read",), NOW)
        await s.commit()
        rows = {r.scope: r.level for r in (await s.scalars(select(CredentialScopeSnapshot).where(CredentialScopeSnapshot.platform == TIKTOK_SHOP))).all()}
        assert rows == {"seller.product.delete": "delete"}
        snaps = await load_scope_snapshots(s)
        assert snaps[TIKTOK_SHOP].scopes == ("seller.product.delete",) and snaps[TIKTOK_SHOP].captured_at.tzinfo is not None


# --- API ------------------------------------------------------------------------------------------------------


def _app(maker, backend: Path, *, user: CurrentUser | None = None, capturer=None) -> FastAPI:
    async def override():
        async with maker() as s:
            yield s

    app = FastAPI()  # el router se prueba montado aparte
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_source] = lambda: _source(backend)
    if capturer is not None:
        app.dependency_overrides[get_capturer] = lambda: capturer
    if user is not None:
        app.dependency_overrides[current_user] = lambda: user
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _fake_capture(backend: Path):
    return lambda: capture_seller_scopes(_shop_settings(backend), _mock_http(_refresh_handler([])))


@pytest.mark.asyncio
async def test_no_endpoint_ever_returns_a_secret_value(maker, backend, caplog):
    app = _app(maker, backend, capturer=_fake_capture(backend))
    with caplog.at_level("DEBUG"):
        async with _client(app) as c:
            assert (await c.post("/api/security/scopes/capture/seller")).status_code == 200
            assert (await c.put(f"/api/security/rotation/{TIKTOK_ADS}", json={"note": "listo"})).status_code == 200
            for path in ("overview", "scopes", "credentials", "rotation"):
                response = await c.get(f"/api/security/{path}")
                assert response.status_code == 200, path
                assert_clean(response.text)
            assert_clean((await c.delete(f"/api/security/rotation/{TIKTOK_ADS}")).text)
    assert_clean(caplog.text)


@pytest.mark.asyncio
async def test_overview_scopes_and_rotation_flow(maker, backend):
    app = _app(maker, backend, capturer=_fake_capture(backend))
    async with _client(app) as c:
        before = (await c.get("/api/security/overview")).json()
        assert before["risk"]["level"] == "high"  # secretos expuestos sin rotar y (tras capturar) con escritura sobrante
        captured = (await c.post("/api/security/scopes/capture/seller")).json()
        assert captured == {"platform": TIKTOK_SHOP, "granted": 31, "write": 14, "delete": 2, "unused_write": 14, "undocumented": 0}

        overview = (await c.get("/api/security/overview")).json()
        assert overview["unused_write_scopes"] == 14 and overview["platforms_with_unused_write"] == [TIKTOK_SHOP]
        assert set(overview["pending_rotation"]) == EXPOSED_IN_CHAT
        assert [e["name"] for e in overview["upcoming_expirations"]] == ["tokens_creator.json:refresh_token"]

        shop = next(p for p in (await c.get("/api/security/scopes")).json()["platforms"] if p["platform"] == TIKTOK_SHOP)
        assert shop["source"] == "captured" and shop["write_count"] == 14

        # marcar todas como rotadas: idempotente y el riesgo baja
        for platform in sorted(EXPOSED_IN_CHAT):
            first = (await c.put(f"/api/security/rotation/{platform}", json={"note": "ok"})).json()
            second = (await c.put(f"/api/security/rotation/{platform}", json={})).json()
            assert first == second and first["rotated_by_email"] == "dev@local"
        board = (await c.get("/api/security/rotation")).json()
        assert board["pending_count"] == 0 and all(r["rotated"] and r["steps"] and r["portal_url"].startswith("https://") for r in board["platforms"])
        after = (await c.get("/api/security/overview")).json()
        assert after["pending_rotation_count"] == 0
        assert after["risk"]["level"] == "high" and "tokens_creator.json" in after["risk"]["reason"]  # git no lo ignora
        assert "tts_tokens.json.bak" in after["risk"]["reason"]  # ni el respaldo que dejó la captura
        with (backend / ".gitignore").open("a", encoding="utf-8") as handle:
            handle.write("\ntokens_creator.json*\ntts_tokens*.json.*\n")
        assert (await c.get("/api/security/overview")).json()["risk"]["level"] == "medium"  # aún sobran permisos de escritura

        reopened = (await c.delete(f"/api/security/rotation/{TIKTOK_SHOP}")).json()
        assert reopened["reopened"] is True and (await c.get("/api/security/overview")).json()["pending_rotation"] == [TIKTOK_SHOP]


@pytest.mark.asyncio
async def test_credentials_endpoint_lists_names_and_expiries_without_values(maker, backend):
    async with _client(_app(maker, backend)) as c:
        body = (await c.get("/api/security/credentials")).json()
    assert body["backup_includes_credential_files"] is False
    ads = next(p for p in body["platforms"] if p["platform"] == TIKTOK_ADS)
    assert ads["present"] and {c["name"] for c in ads["credentials"]} == {"TTADS_APP_SECRET", "TTADS_ACCESS_TOKEN"}
    redo = next(p for p in body["platforms"] if p["platform"] == "redo")
    assert redo["present"] is False


@pytest.mark.asyncio
async def test_unknown_platform_and_invalid_note(maker, backend):
    async with _client(_app(maker, backend)) as c:
        assert (await c.put("/api/security/rotation/nada", json={})).status_code == 404
        assert (await c.delete("/api/security/rotation/nada")).status_code == 404
        assert (await c.put(f"/api/security/rotation/{TIKTOK_SHOP}", json={"note": "x" * 501})).status_code == 422


@pytest.mark.asyncio
async def test_writes_need_settings_edit_but_reads_only_settings_view(maker, backend):
    viewer = CurrentUser(None, "v@x.com", frozenset({"settings.view"}))
    nobody = CurrentUser(None, "n@x.com", frozenset({"profit.view"}))
    async with _client(_app(maker, backend, user=viewer, capturer=_fake_capture(backend))) as c:
        assert (await c.get("/api/security/overview")).status_code == 200
        assert (await c.put(f"/api/security/rotation/{TIKTOK_SHOP}", json={})).status_code == 403
        assert (await c.delete(f"/api/security/rotation/{TIKTOK_SHOP}")).status_code == 403
        assert (await c.post("/api/security/scopes/capture/seller")).status_code == 403
    async with _client(_app(maker, backend, user=nobody)) as c:
        assert (await c.get("/api/security/credentials")).status_code == 403


@pytest.mark.asyncio
async def test_capture_failure_returns_502_without_details(maker, backend):
    def failing() -> None:
        raise ScopeCaptureError("TikTok Shop no respondió (ConnectError)")

    async with _client(_app(maker, backend, capturer=failing)) as c:  # type: ignore[arg-type]
        response = await c.post("/api/security/scopes/capture/seller")
    assert response.status_code == 502 and "ConnectError" in response.json()["detail"]


# --- proveedor del Centro de acciones ---------------------------------------------------------------------------


def test_provider_is_registered_and_listed():
    assert "app.credential_actions" in action_providers.PROVIDER_MODULES
    assert credential_actions.KIND in action_center.providers()


@pytest.mark.asyncio
async def test_actions_one_per_pending_platform_and_one_per_unused_write(maker, backend, monkeypatch):
    monkeypatch.setattr(credential_actions, "default_source", lambda: _source(backend))
    async with maker() as s:
        await replace_scope_snapshot(s, TIKTOK_SHOP, SELLER_SCOPES, NOW)
        await s.commit()
        items, errors = await action_center.collect(s, {credential_actions.KIND})
        assert errors == []
        keys = {i.key for i in items}
        assert keys == {f"security_rotate:rotate:{p}" for p in EXPOSED_IN_CHAT} | {f"security_rotate:scopes:{TIKTOK_SHOP}"}
        assert all(i.impact_usd == credential_actions.SYMBOLIC_IMPACT_USD > 0 and "simbólico" in i.impact_basis for i in items)
        assert all(i.target == "security" and i.kind == "security_rotate" for i in items)
        scope_item = next(i for i in items if ":scopes:" in i.key)
        assert scope_item.meta["unused_write"] == 14 and "Quita 14 permisos" in scope_item.title
        assert_clean([asdict(i) for i in items])

        await mark_rotated(s, TIKTOK_SHOP, "a@x.com", None, NOW)
        await s.commit()
        again, _ = await action_center.collect(s, {credential_actions.KIND})
        assert f"security_rotate:rotate:{TIKTOK_SHOP}" not in {i.key for i in again}


@pytest.mark.asyncio
async def test_actions_without_captured_scopes_only_ask_for_rotation(maker, backend):
    async with maker() as s:
        items = await credential_actions.security_actions(s, _source(backend))
    assert {i.key for i in items} == {f"security_rotate:rotate:{p}" for p in EXPOSED_IN_CHAT}

