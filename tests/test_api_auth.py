"""API auth flow: login, /me, refresh rotation + reuse, logout, RBAC."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import create_app
from app.auth.service import create_user
from app.common.runtime import WebSettings

pytestmark = pytest.mark.filterwarnings(
    "ignore:Setting per-request cookies:DeprecationWarning")


@pytest.fixture()
async def client(config, tmp_path):
    """ASGI client bound to an app whose storage is a throwaway sqlite db."""
    settings = WebSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
        jwt_secret="unit-test-secret-key-0123456789abcdef0123456789",
        gateway_socket=tmp_path / "g.sock",
    )
    application = create_app(config=config, settings=settings)
    async with application.router.lifespan_context(application):
        async with application.state.session_factory() as session:
            await create_user(session, "admin", "admin-pw-123", "ADMIN")
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport,
                               base_url="http://t") as http:
            yield http


def _refresh_raw(response) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith("mt_refresh="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError("no mt_refresh cookie in response")


async def _login(client, username="admin", password="admin-pw-123"):
    resp = await client.post("/api/v1/auth/login",
                             json={"username": username, "password": password})
    assert resp.status_code == 200
    return resp.json()


async def test_login_success(client):
    body = await _login(client)
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "ADMIN"
    assert "MANAGE_USER" in body["user"]["permissions"]
    assert "VIEW_MARKET" in body["user"]["permissions"]


async def test_login_wrong_password(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "nope-wrong"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"


async def test_login_unknown_user(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "ghost", "password": "whatever-1"})
    assert resp.status_code == 401


async def test_me_requires_token(client):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_me_with_token(client):
    body = await _login(client)
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"})
    assert resp.status_code == 200
    me = resp.json()
    assert me["username"] == "admin"
    assert me["role"] == "ADMIN"
    assert me["permissions"]


async def test_me_rejects_garbage_token(client):
    resp = await client.get("/api/v1/auth/me",
                            headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_refresh_rotation_and_reuse_detection(client):
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-pw-123"})
    old_raw = _refresh_raw(login)

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    assert first.json()["access_token"]
    new_raw = _refresh_raw(first)
    assert new_raw != old_raw

    replay = await client.post("/api/v1/auth/refresh",
                               cookies={"mt_refresh": old_raw})
    assert replay.status_code == 401

    stolen = await client.post("/api/v1/auth/refresh",
                               cookies={"mt_refresh": new_raw})
    assert stolen.status_code == 401


async def test_refresh_without_cookie(client):
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


async def test_logout_revokes_cookie_token(client):
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-pw-123"})
    raw = _refresh_raw(login)
    token = login.json()["access_token"]

    resp = await client.post("/api/v1/auth/logout",
                             headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    after = await client.post("/api/v1/auth/refresh",
                              cookies={"mt_refresh": raw})
    assert after.status_code == 401


async def test_user_management_rbac(client):
    token = (await _login(client))["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/system/users", headers=headers,
        json={"username": "watcher", "password": "viewer-pw-123",
              "role": "VIEWER"})
    assert created.status_code == 201
    assert created.json()["role"] == "VIEWER"
    assert "SUBMIT_ORDER" not in created.json()["permissions"]

    dup = await client.post(
        "/api/v1/system/users", headers=headers,
        json={"username": "watcher", "password": "viewer-pw-123",
              "role": "VIEWER"})
    assert dup.status_code == 409

    viewer = await _login(client, "watcher", "viewer-pw-123")
    vheaders = {"Authorization": f"Bearer {viewer['access_token']}"}

    forbidden_get = await client.get("/api/v1/system/users", headers=vheaders)
    assert forbidden_get.status_code == 403

    forbidden_post = await client.post(
        "/api/v1/system/users", headers=vheaders,
        json={"username": "x", "password": "password-1", "role": "ADMIN"})
    assert forbidden_post.status_code == 403
    assert forbidden_post.json()["detail"] == "permission denied: MANAGE_USER"

    unauth = await client.get("/api/v1/system/users")
    assert unauth.status_code == 401


async def test_unknown_route_is_404(client):
    resp = await client.get("/api/v1/definitely-not-here")
    assert resp.status_code == 404


async def test_ws_ticket_requires_login_then_issues(client):
    anon = await client.post("/api/v1/system/ws/ticket")
    assert anon.status_code == 401

    token = (await _login(client))["access_token"]
    resp = await client.post("/api/v1/system/ws/ticket",
                             headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket"]
    assert body["expires_in"] == 30


async def test_health_is_public(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("cache-control") == "no-store"


async def test_mutating_origin_mismatch_rejected(client):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-pw-123"},
        headers={"Origin": "http://evil.example", "Host": "t"})
    assert resp.status_code == 403
