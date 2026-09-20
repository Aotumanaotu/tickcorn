"""Declarative ORM models for the relational storage layer.

A single model set serves both backends: production runs on PostgreSQL /
TimescaleDB (where ``ticks`` / ``microstructure_events`` / ``analysis_metrics``
become hypertables), development and tests run on plain SQLite via
aiosqlite. Only portable column types are used -- ``DateTime(timezone=True)``,
``JSON``, ``BigInteger`` -- never PostgreSQL-only types such as JSONB.

Time-series tables carry a dual timestamp representation: a tz-aware ``ts``
datetime (the hypertable partition key on TimescaleDB) plus the raw
``*_ns`` epoch-nanosecond integers that flow through the core event
envelopes, so exact ordering survives any datetime precision loss.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

_BIGINT_PK = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    """Declarative base shared by every storage model."""


class User(Base):
    """Web-stack user account (auth layer)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class RefreshToken(Base):
    """Rotating refresh token (hashed at rest), grouped by family."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(64), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Product(Base):
    """Reference table seeded from ``config.instruments.products``."""

    __tablename__ = "products"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    exchange: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    tick_size: Mapped[Optional[float]] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Instrument(Base):
    """Known instrument with its static parameters and sighting history."""

    __tablename__ = "instruments"

    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    product_code: Mapped[Optional[str]] = mapped_column(ForeignKey("products.code"))
    exchange: Mapped[str] = mapped_column(String(16))
    tick_size: Mapped[Optional[float]] = mapped_column(Float)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Watchlist(Base):
    """Named instrument watchlist owned by a user."""

    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class WatchlistItem(Base):
    """One instrument inside a watchlist (unique per watchlist)."""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "instrument_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(String(32))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class GatewayBatch(Base):
    """Collection-batch provenance written by the gateway archiver."""

    __tablename__ = "gateway_batches"

    batch_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    fronts: Mapped[Optional[list]] = mapped_column(JSON)
    broker_id: Mapped[Optional[str]] = mapped_column(String(32))
    user_masked: Mapped[Optional[str]] = mapped_column(String(64))
    raw_source: Mapped[str] = mapped_column(String(64), default="ctp:simnow")
    app_version: Mapped[Optional[str]] = mapped_column(String(32))
    api_version: Mapped[Optional[str]] = mapped_column(String(32))
    config_hash: Mapped[Optional[str]] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running")
    note: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class RawFile(Base):
    """Registered immutable raw parquet file (path is unique)."""

    __tablename__ = "raw_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(512), unique=True)
    instrument_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    trading_day: Mapped[Optional[str]] = mapped_column(String(10))
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    rows: Mapped[Optional[int]] = mapped_column(Integer)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    batch_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("gateway_batches.batch_id"))
    registered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Tick(Base):
    """L1/L5 book snapshot; the dense time-series row (hypertable on PG)."""

    __tablename__ = "ticks"
    __table_args__ = (
        Index("ix_ticks_inst_ts", "instrument_id", "exchange_ts_ns"),
    )

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exchange_ts_ns: Mapped[Optional[int]] = mapped_column(BigInteger)
    local_ts_ns: Mapped[Optional[int]] = mapped_column(BigInteger)
    seq: Mapped[Optional[int]] = mapped_column(BigInteger)
    last: Mapped[Optional[float]] = mapped_column(Float)
    bid1: Mapped[Optional[float]] = mapped_column(Float)
    bid2: Mapped[Optional[float]] = mapped_column(Float)
    bid3: Mapped[Optional[float]] = mapped_column(Float)
    bid4: Mapped[Optional[float]] = mapped_column(Float)
    bid5: Mapped[Optional[float]] = mapped_column(Float)
    ask1: Mapped[Optional[float]] = mapped_column(Float)
    ask2: Mapped[Optional[float]] = mapped_column(Float)
    ask3: Mapped[Optional[float]] = mapped_column(Float)
    ask4: Mapped[Optional[float]] = mapped_column(Float)
    ask5: Mapped[Optional[float]] = mapped_column(Float)
    bid_volume1: Mapped[Optional[float]] = mapped_column(Float)
    bid_volume2: Mapped[Optional[float]] = mapped_column(Float)
    bid_volume3: Mapped[Optional[float]] = mapped_column(Float)
    bid_volume4: Mapped[Optional[float]] = mapped_column(Float)
    bid_volume5: Mapped[Optional[float]] = mapped_column(Float)
    ask_volume1: Mapped[Optional[float]] = mapped_column(Float)
    ask_volume2: Mapped[Optional[float]] = mapped_column(Float)
    ask_volume3: Mapped[Optional[float]] = mapped_column(Float)
    ask_volume4: Mapped[Optional[float]] = mapped_column(Float)
    ask_volume5: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[float]] = mapped_column(Float)
    turnover: Mapped[Optional[float]] = mapped_column(Float)
    open_interest: Mapped[Optional[float]] = mapped_column(Float)
    spread: Mapped[Optional[float]] = mapped_column(Float)
    spread_ticks: Mapped[Optional[float]] = mapped_column(Float)
    mid: Mapped[Optional[float]] = mapped_column(Float)
    microprice: Mapped[Optional[float]] = mapped_column(Float)
    obi1: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[Optional[str]] = mapped_column(String(64))
    data_mode: Mapped[Optional[str]] = mapped_column(String(8), default="live")


class MicrostructureEvent(Base):
    """Classifier transition record (hypertable on PG)."""

    __tablename__ = "microstructure_events"
    __table_args__ = (
        Index("ix_micro_events_inst_ts", "instrument_id", "exchange_ts_ns"),
    )

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exchange_ts_ns: Mapped[Optional[int]] = mapped_column(BigInteger)
    local_ts_ns: Mapped[Optional[int]] = mapped_column(BigInteger)
    seq: Mapped[Optional[int]] = mapped_column(BigInteger)
    label: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    family: Mapped[Optional[str]] = mapped_column(String(32))
    state5: Mapped[Optional[str]] = mapped_column(String(16))
    direction: Mapped[Optional[int]] = mapped_column(SmallInteger)
    reason: Mapped[Optional[str]] = mapped_column(String(48))
    direction_hint: Mapped[Optional[int]] = mapped_column(SmallInteger)
    db_ticks: Mapped[Optional[float]] = mapped_column(Float)
    da_ticks: Mapped[Optional[float]] = mapped_column(Float)
    dl_ticks: Mapped[Optional[float]] = mapped_column(Float)
    dm_ticks: Mapped[Optional[float]] = mapped_column(Float)
    spread_prev_ticks: Mapped[Optional[float]] = mapped_column(Float)
    spread_cur_ticks: Mapped[Optional[float]] = mapped_column(Float)
    prev_quote: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    cur_quote: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    source: Mapped[Optional[str]] = mapped_column(String(64))
    data_mode: Mapped[Optional[str]] = mapped_column(String(8), default="live")


class AnalysisMetric(Base):
    """Rolling analysis metrics per instrument (hypertable on PG)."""

    __tablename__ = "analysis_metrics"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    instrument_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exchange_ts_ns: Mapped[Optional[int]] = mapped_column(BigInteger)
    metrics: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)


class ServiceAccount(Base):
    """API key account for machine-to-machine access (hashed key)."""

    __tablename__ = "service_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    key_hash: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SystemConfig(Base):
    """Key/value runtime configuration store."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class Report(Base):
    """Generated research report record."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    trading_days: Mapped[Optional[list]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    document: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    artifact_dir: Mapped[Optional[str]] = mapped_column(String(512))
