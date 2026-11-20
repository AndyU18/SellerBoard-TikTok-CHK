import time
import uuid

import httpx
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import auth
from app.config import settings
from app.db import create_dev_schema, get_session, make_engine
from app.main import app
from app.models import Permission, Profile, Role, RolePermission, UserRole

URL = "https://proyecto.supabase.co"
PERMS = [
    "profit.view", "sales.view", "catalog.view", "costs.edit", "import.run", "sync.run",
    "ops.view", "users.view", "users.manage", "roles.manage",
]  # fmt: skip
ROLES = {
    "owner": PERMS,
    "admin": [p for p in PERMS if p != "roles.manage"],
    "analyst": ["profit.view", "sales.view", "catalog.view"],
}
OWNER, ADMIN, ANALYST, INACTIVE, NO_PROFILE = (uuid.uuid4() for _ in range(5))

PRIVATE = ec.generate_private_key(ec.SECP256R1())
OTHER_PRIVATE = ec.generate_private_key(ec.SECP256R1())


def _pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def token(sub, *, key=PRIVATE, exp=3600, aud="authenticated", iss=f"{URL}/auth/v1", alg="ES256"):
    claims = {"sub": str(sub), "email": f"{sub}@chk.test", "aud": aud, "iss": iss}
    claims["exp"] = int(time.time()) + exp
    return jwt.encode(claims, _pem(key), algorithm=alg)


def bearer(sub, **kw):
    return {"Authorization": f"Bearer {token(sub, **kw)}"}


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "supabase_url", URL)
    monkeypatch.setattr(auth, "_signing_key", lambda _token: PRIVATE.public_key())

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        perm_ids = {}
        for code in PERMS:
            p = Permission(code=code)
            s.add(p)
            perm_ids[code] = p
        role_ids = {}
        for code, perms in ROLES.items():
            r = Role(code=code, name=code)
            s.add(r)
            role_ids[code] = (r, perms)
        await s.flush()
        for r, perms in role_ids.values():
            s.add_all(RolePermission(role_id=r.id, permission_id=perm_ids[c].id) for c in perms)
        for uid, active in ((OWNER, True), (ADMIN, True), (ANALYST, True), (INACTIVE, False)):
            s.add(Profile(user_id=uid, email=f"{uid}@chk.test", is_active=active))
        await s.flush()
        for uid, role in ((OWNER, "owner"), (ADMIN, "admin"), (ANALYST, "analyst"), (INACTIVE, "owner")):
            s.add(UserRole(user_id=uid, role_id=role_ids[role][0].id))
        await s.commit()

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


# --- validación del token -------------------------------------------------------
@pytest.mark.asyncio
async def test_health_is_open_but_data_requires_a_token(client):
    assert (await client.get("/api/health")).status_code == 200
    r = await client.get("/api/profit")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_valid_token_grants_access_and_me_reports_permissions(client):
    assert (await client.get("/api/profit", headers=bearer(ANALYST))).status_code == 200
    me = (await client.get("/api/me", headers=bearer(ANALYST))).json()
    assert me["roles"] == ["analyst"]
    assert me["permissions"] == ["catalog.view", "profit.view", "sales.view"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw",
    [
        {"exp": -10},  # expirado
        {"aud": "otra-audiencia"},
        {"iss": "https://atacante.example/auth/v1"},
        {"key": OTHER_PRIVATE},  # firmado con otra clave
    ],
    ids=["expirado", "audiencia", "emisor", "firma-ajena"],
)
async def test_bad_tokens_are_rejected(client, kw):
    r = await client.get("/api/profit", headers=bearer(ANALYST, **kw))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_hs256_token_signed_with_public_data_is_rejected(client):
    """Ataque clásico de confusión de algoritmo: HS256 firmado con un secreto cualquiera."""
    forged = jwt.encode(
        {"sub": str(OWNER), "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 60},
        "x" * 32,
        algorithm="HS256",
    )
    r = await client.get("/api/profit", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_garbage_token_is_rejected(client):
    r = await client.get("/api/profit", headers={"Authorization": "Bearer no-es-un-jwt"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_user_without_profile_or_inactive_gets_403(client):
    assert (await client.get("/api/profit", headers=bearer(NO_PROFILE))).status_code == 403
    assert (await client.get("/api/profit", headers=bearer(INACTIVE))).status_code == 403


# --- permisos por rol ------------------------------------------------------------
@pytest.mark.asyncio
async def test_analyst_can_read_but_not_write(client):
    h = bearer(ANALYST)
    assert (await client.get("/api/products", headers=h)).status_code == 200
    r = await client.put("/api/products/1/cogs", headers=h, json={"cogs_unit": 5})
    assert r.status_code == 403 and "costs.edit" in r.json()["detail"]
    files = {"file": ("o.csv", b"date\n", "text/csv")}
    assert (await client.post("/api/import/orders", headers=h, files=files)).status_code == 403
    assert (await client.post("/api/gold/refresh", headers=h)).status_code == 403
    assert (await client.get("/api/users", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_owner_can_import_and_then_read_the_result(client):
    h = bearer(OWNER)
    csv = b"date,channel,account,order_id,sku,units,sales\n2026-11-01,amazon,a1,1,X,1,10\n"
    r = await client.post("/api/import/orders", headers=h, files={"file": ("o.csv", csv, "text/csv")})
    assert r.status_code == 200
    totals = (await client.get("/api/profit", headers=bearer(ANALYST))).json()["totals"]
    assert totals["gross_sales"] == 10.0


@pytest.mark.asyncio
async def test_auth_disabled_gives_dev_user_full_access(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", False)
    assert (await client.get("/api/profit")).status_code == 200
    assert (await client.get("/api/me")).json()["roles"] == ["dev"]


# --- gestión de usuarios -----------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_can_assign_roles_but_not_grant_owner(client):
    h = bearer(ADMIN)
    r = await client.put(f"/api/users/{ANALYST}/roles", headers=h, json={"roles": ["admin"]})
    assert r.status_code == 200 and r.json()["roles"] == ["admin"]
    r = await client.put(f"/api/users/{ANALYST}/roles", headers=h, json={"roles": ["owner"]})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_grant_owner_and_unknown_role_is_400(client):
    h = bearer(OWNER)
    assert (
        await client.put(f"/api/users/{ANALYST}/roles", headers=h, json={"roles": ["inventado"]})
    ).status_code == 400
    r = await client.put(f"/api/users/{ANALYST}/roles", headers=h, json={"roles": ["owner"]})
    assert r.status_code == 200
    users = {u["id"]: u for u in (await client.get("/api/users", headers=h)).json()}
    assert users[str(ANALYST)]["roles"] == ["owner"]


@pytest.mark.asyncio
async def test_last_active_owner_cannot_be_removed_or_deactivated(client):
    h = bearer(OWNER)  # INACTIVE también es owner, pero no cuenta: está desactivado
    r = await client.put(f"/api/users/{OWNER}/roles", headers=h, json={"roles": ["admin"]})
    assert r.status_code == 400
    r = await client.patch(f"/api/users/{OWNER}", headers=h, json={"is_active": False})
    assert r.status_code == 400  # ni a sí mismo


@pytest.mark.asyncio
async def test_deactivated_user_loses_access_immediately(client):
    owner = bearer(OWNER)
    assert (await client.get("/api/profit", headers=bearer(ANALYST))).status_code == 200
    r = await client.patch(f"/api/users/{ANALYST}", headers=owner, json={"is_active": False})
    assert r.status_code == 200
    assert (await client.get("/api/profit", headers=bearer(ANALYST))).status_code == 403
