"""SPA history fallback: deep links serve index.html, API keeps 404s."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import _FRONTEND_DIST, create_app
from app.common.runtime import WebSettings

pytestmark = [
    pytest.mark.filterwarnings(
        "ignore:Setting per-request cookies:DeprecationWarning"),
    pytest.mark.skipif(not _FRONTEND_DIST.is_dir(),
                       reason="frontend/dist not built"),
]


@pytest.fixture()
async def client(config, tmp_path):
    settings = WebSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
        jwt_secret="unit-test-secret-key-0123456789abcdef0123456789",
        gateway_socket=tmp_path / "g.sock",
    )
    application = create_app(config=config, settings=settings)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport,
                               base_url="http://t") as http:
            yield http


async def test_deep_link_serves_index_html(client):
    resp = await client.get("/workspace/C2611")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert b"<!doctype html>" in resp.content.lower()


async def test_api_unknown_path_stays_json_404(client):
    resp = await client.get("/api/v1/not-a-route")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


async def test_ws_unknown_path_not_index(client):
    resp = await client.get("/ws/not-a-route")
    assert resp.status_code != 200 or not resp.headers[
        "content-type"].startswith("text/html")
