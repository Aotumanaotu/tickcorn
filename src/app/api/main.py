"""FastAPI application factory and process wiring.

``create_app`` assembles the whole web process: relational storage
(engine + schema bootstrap), the gateway ingest client, the realtime
metrics engine, one connection hub per WebSocket channel and the REST
routers. Everything long-lived is pinned on ``app.state`` so request
handlers and WS endpoints share single instances.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.analysis.realtime import RealtimeMetrics
from app.api.routers import auth as auth_router
from app.api.routers import instruments as instruments_router
from app.api.routers import market as market_router
from app.api.routers import system as system_router
from app.common.config import AppConfig, load_config
from app.common.logging import get_logger
from app.common.runtime import WebSettings, load_web_settings
from app.core.events import (
    EVENT_ANALYSIS,
    EVENT_CONNECTION,
    EVENT_INSTRUMENT_STATUS,
    EVENT_MICRO,
    EVENT_QUOTE,
    EVENT_SYSTEM,
)
from app.ingest.bus import EventBus
from app.ingest.client import IngestClient
from app.storage.db import create_async_engine_from_url, get_session_factory
from app.storage.timescale import init_database
from app.ws import routes as ws_routes
from app.ws.hub import ConnectionHub

logger = get_logger("api.main")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def create_app(config: Optional[AppConfig] = None,
               settings: Optional[WebSettings] = None) -> FastAPI:
    """Build the API application (storage, ingest, hubs, routers)."""
    config = config or load_config()
    settings = settings or load_web_settings(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_async_engine_from_url(settings.database_url)
        session_factory = get_session_factory(engine)
        await init_database(engine, config)

        bus = EventBus()
        ingest = IngestClient(settings, bus)
        await ingest.start()

        realtime = RealtimeMetrics()
        await realtime.start(bus)

        market_hub = ConnectionHub("market")
        analysis_hub = ConnectionHub("analysis")
        system_hub = ConnectionHub("system")
        unsubs = [
            await bus.subscribe(
                market_hub.route,
                event_types={EVENT_QUOTE, EVENT_MICRO,
                             EVENT_INSTRUMENT_STATUS}),
            await bus.subscribe(analysis_hub.route,
                                event_types={EVENT_ANALYSIS}),
            await bus.subscribe(system_hub.broadcast,
                                event_types={EVENT_CONNECTION, EVENT_SYSTEM}),
        ]

        app.state.config = config
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.bus = bus
        app.state.ingest = ingest
        app.state.realtime = realtime
        app.state.market_hub = market_hub
        app.state.analysis_hub = analysis_hub
        app.state.system_hub = system_hub
        logger.info("app lifespan started (version=%s, db=%s)",
                    __version__, settings.database_url.split("://")[0])
        try:
            yield
        finally:
            for unsub in unsubs:
                unsub()
            await realtime.stop()
            await ingest.stop()
            await bus.close()
            await engine.dispose()
            logger.info("app lifespan stopped")

    app = FastAPI(
        title="MicroTerm API",
        version=__version__,
        docs_url="/api/docs",
        lifespan=lifespan,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def harden_middleware(request, call_next):
        method = request.method.upper()
        if method in _MUTATING_METHODS:
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("host", "")
                if urlsplit(origin).netloc != host:
                    return JSONResponse(
                        {"detail": "origin not allowed"},
                        status_code=403)
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    async def health() -> dict:
        """Unauthenticated liveness probe."""
        return {"status": "ok", "version": __version__}

    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(instruments_router.router, prefix="/api/v1")
    app.include_router(market_router.router, prefix="/api/v1")
    app.include_router(system_router.router, prefix="/api/v1")
    app.include_router(ws_routes.router)

    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True),
                  name="frontend")
    else:
        @app.get("/")
        async def root() -> dict:
            return {"name": "MicroTerm API"}

    return app


app = create_app()
