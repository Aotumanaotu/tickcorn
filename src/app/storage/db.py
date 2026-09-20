"""Async engine and session-factory construction for the relational store.

A single helper pair serves both backends described by
``WebSettings.database_url``: PostgreSQL (asyncpg, with connection-pool
health checking) in production and SQLite (aiosqlite) in development and
tests. Backend-specific tuning is decided from the URL scheme only.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_async_engine_from_url(url: str) -> AsyncEngine:
    """Create an async engine tuned for the backend named in ``url``.

    SQLite engines get ``check_same_thread=False`` (aiosqlite drives the
    connection from its own worker thread); PostgreSQL engines get a
    pre-ping'd pool of 5 connections so restarted databases do not poison
    long-lived services with stale connections.
    """
    if url.startswith("sqlite"):
        return create_async_engine(url, connect_args={"check_same_thread": False})
    return create_async_engine(url, pool_pre_ping=True, pool_size=5)


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to ``engine``.

    ``expire_on_commit=False`` keeps ORM instances readable after commit,
    which avoids surprise lazy-load IO in async request handlers.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
