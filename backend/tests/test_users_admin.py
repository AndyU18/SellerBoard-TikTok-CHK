"""Administración de usuarios: vista general, estados y protecciones del Dueño.

Reutiliza las claves y los tokens de `test_auth.py`; la app es mínima (solo los routers de usuarios).
"""

import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_auth import ADMIN, ANALYST, INACTIVE, OWNER, PERMS, ROLES, URL, bearer

from app import auth
from app.config import settings
from app.db import create_dev_schema, get_session, make_engine
from app.models import Permission, Profile, Role, RolePermission, UserRole
from app.users_admin_api import router as admin_router
from app.users_admin_api import user_status
from app.users_api import router as users_router

PENDING = uuid.uuid4()

app = FastAPI()
app.include_router(users_router)
app.include_router(admin_router)


@pytest_asyncio.fixture
async def client(monkeypatch):
    from test_auth import PRIVATE

    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "supabase_url", URL)
    monkeypatch.setattr(auth, "_signing_key", lambda _token: PRIVATE.public_key())

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(create_dev_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        perms = {code: Permission(code=code) for code in PERMS}
        roles = {code: Role(code=code, name=code.title()) for code in ROLES}
        s.add_all([*perms.values(), *roles.values()])
        await s.flush()
        s.add_all(
            RolePermission(role_id=roles[r].id, permission_id=perms[c].id) for r, codes in ROLES.items() for c in codes
        )
        for uid, active in ((OWNER, True), (ADMIN, True), (ANALYST, True), (INACTIVE, False), (PENDING, True)):
            s.add(Profile(user_id=uid, email=f"{uid}@chk.test", is_active=active))
        await s.flush()
        for uid, role in ((OWNER, "owner"), (ADMIN, "admin"), (ANALYST, "analyst"), (INACTIVE, "owner")):
            s.add(UserRole(user_id=uid, role_id=roles[role].id))
        await s.commit()

    async def override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


async def overview(client, who=OWNER) -> dict:
    r = await client.get("/api/users/overview", headers=bearer(who))
    assert r.status_code == 200
    return r.json()


def by_id(data: dict) -> dict[str, dict]:
    return {u["id"]: u for u in data["users"]}


def test_user_status_rules():
    assert user_status(True, ["analyst"]) == "active"
    assert user_status(True, []) == "pending"
    assert user_status(False, ["owner"]) == "disabled"
    assert user_status(False, []) == "disabled"


@pytest.mark.asyncio
async def test_overview_lists_users_with_status_roles_and_permissions(client):
    data = await overview(client)
    users = by_id(data)
    assert users[str(OWNER)]["status"] == "active" and users[str(OWNER)]["roles"] == ["owner"]
    assert users[str(PENDING)]["status"] == "pending" and users[str(PENDING)]["roles"] == []
    assert users[str(INACTIVE)]["status"] == "disabled"
    assert data["active_owners"] == 1  # el otro Dueño está desactivado
    roles = {r["code"]: r for r in data["roles"]}
    assert set(roles) == set(ROLES) and roles["analyst"]["permissions"] == ["catalog.view", "profit.view", "sales.view"]
    assert data["me"] == {"id": str(OWNER), "can_manage": True, "can_manage_owner": True}


@pytest.mark.asyncio
async def test_overview_reports_what_each_actor_can_do(client):
    assert (await overview(client, ADMIN))["me"] == {"id": str(ADMIN), "can_manage": True, "can_manage_owner": False}


@pytest.mark.asyncio
async def test_overview_requires_users_view(client):
    assert (await client.get("/api/users/overview", headers=bearer(ANALYST))).status_code == 403
    assert (await client.get("/api/users/overview")).status_code == 401


@pytest.mark.asyncio
async def test_giving_and_removing_roles_moves_user_between_pending_and_active(client):
    h = bearer(ADMIN)
    r = await client.put(f"/api/users/{PENDING}/roles", headers=h, json={"roles": ["analyst"]})
    assert r.status_code == 200
    assert by_id(await overview(client))[str(PENDING)]["status"] == "active"
    r = await client.put(f"/api/users/{PENDING}/roles", headers=h, json={"roles": []})
    assert r.status_code == 200
    assert by_id(await overview(client))[str(PENDING)]["status"] == "pending"


@pytest.mark.asyncio
async def test_admin_cannot_grant_owner_to_pending_user(client):
    r = await client.put(f"/api/users/{PENDING}/roles", headers=bearer(ADMIN), json={"roles": ["owner"]})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_owner_cannot_remove_their_own_owner_access_even_with_another_owner(client):
    h = bearer(OWNER)
    assert (await client.put(f"/api/users/{ANALYST}/roles", headers=h, json={"roles": ["owner"]})).status_code == 200
    r = await client.put(f"/api/users/{OWNER}/roles", headers=h, json={"roles": ["admin"]})
    assert r.status_code == 400 and "propio" in r.json()["detail"]
    assert by_id(await overview(client))[str(OWNER)]["roles"] == ["owner"]
    # Otro Dueño sí puede quitárselo: queda uno activo.
    r = await client.put(f"/api/users/{OWNER}/roles", headers=bearer(ANALYST), json={"roles": ["admin"]})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_last_owner_cannot_lose_access_when_demoted_by_someone_else(client):
    # El único Dueño activo es OWNER; INACTIVE está desactivado. Un Dueño recién nombrado lo degrada...
    h = bearer(OWNER)
    await client.put(f"/api/users/{ANALYST}/roles", headers=h, json={"roles": ["owner"]})
    await client.patch(f"/api/users/{OWNER}", headers=bearer(ANALYST), json={"is_active": False})
    # ...y ahora ANALYST es el único Dueño activo: nadie puede quitárselo ni desactivarlo.
    r = await client.put(f"/api/users/{ANALYST}/roles", headers=bearer(ANALYST), json={"roles": ["admin"]})
    assert r.status_code == 400
    r = await client.patch(f"/api/users/{ANALYST}", headers=bearer(ANALYST), json={"is_active": False})
    assert r.status_code == 400
