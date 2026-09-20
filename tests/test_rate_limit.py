"""Failed-login throttling on the public login endpoint."""

from __future__ import annotations

import pytest

from app.api.main import create_app
from app.api.ratelimit import LoginThrottle, throttle
from app.auth.service import create_user
from app.common.runtime import WebSettings


def test_throttle_window_mechanics():
    t = LoginThrottle(max_failures=3, window_s=300)
    t.check("1.2.3.4")
    t.record_failure("1.2.3.4")
    t.record_failure("1.2.3.4")
    t.check("1.2.3.4")
    t.record_failure("1.2.3.4")
    with pytest.raises(Exception):
        t.check("1.2.3.4")
    t.check("5.6.7.8")
    t.reset("1.2.3.4")
    t.check("1.2.3.4")


@pytest.fixture()
async def api_app(config, tmp_path):
    settings = WebSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
        jwt_secret="unit-test-secret-key-0123456789abcdef0123456789",
        gateway_socket=tmp_path / "g.sock",
    )
    application = create_app(config=config, settings=settings)
    throttle._failures.clear()
    async with application.router.lifespan_context(application):
        async with application.state.session_factory() as session:
            await create_user(session, "admin", "admin-pw-123", "ADMIN")
        yield application


async def test_login_endpoint_returns_429_after_budget(api_app):
    import httpx

    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://t") as client:
        payload = {"username": "admin", "password": "definitely-wrong"}
        for _ in range(5):
            r = await client.post("/api/v1/auth/login", json=payload)
            assert r.status_code == 401
        r = await client.post("/api/v1/auth/login", json=payload)
        assert r.status_code == 429
        assert int(r.headers.get("Retry-After", "0")) >= 1
