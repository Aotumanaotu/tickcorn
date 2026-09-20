"""Database bootstrap (schema, hypertables, seed) and the market-data writer.

``init_database`` creates the full schema on any backend, converts the three
time-series tables into TimescaleDB hypertables when running on PostgreSQL
(failures are logged as warnings and never fatal, so plain PostgreSQL and
SQLite keep working), upserts the product reference data from
``AppConfig.instruments.products`` and provisions a bootstrap ADMIN account
from the ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD`` environment variables.

``TimescaleWriter`` is the ingestion side: it converts core event envelopes
into ORM rows inside a bounded in-process buffer (``add_*`` never blocks)
and flushes them in single transactions from a background task.
``query_ticks`` / ``query_micro_events`` are the read side used by the API.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Optional

from argon2 import PasswordHasher
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.common.config import AppConfig
from app.common.logging import get_logger
from app.storage.db import get_session_factory
from app.storage.models import (
    Base,
    MicrostructureEvent,
    Product,
    Tick,
    User,
)

logger = get_logger("storage.timescale")

HYPERTABLE_TABLES = ("ticks", "microstructure_events", "analysis_metrics")

_PRODUCT_CATEGORY: dict[str, str] = {
    "C": "Agriculture",
    "CS": "Agriculture",
    "M": "Agriculture",
    "Y": "Agriculture",
    "P": "Agriculture",
    "SR": "Agriculture",
    "CF": "Agriculture",
    "JD": "Agriculture",
    "RB": "Ferrous",
}

_DEFAULT_CATEGORY = "Other"


def _ts_from_ns(ns: Optional[int]) -> datetime:
    """Nanosecond epoch -> UTC datetime (envelope clocks are epoch-encoded)."""
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)


def _event_ts(event: dict) -> datetime:
    """Timestamp of an envelope: exchange clock, else local clock, else now."""
    ns = event.get("exchange_ts_ns")
    if ns is None:
        ns = event.get("local_ts_ns")
    if ns is None:
        return datetime.now(timezone.utc)
    return _ts_from_ns(ns)


def _num(value: Any) -> Optional[float]:
    return None if value is None else float(value)


async def init_database(engine: AsyncEngine, config: AppConfig) -> None:
    """Create all tables, enable hypertables (PG only) and seed reference data."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if str(engine.url).startswith("postgres"):
        await _create_hypertables(engine)

    await _seed_products(engine, config)
    await _ensure_admin_user(engine)


async def _create_hypertables(engine: AsyncEngine) -> None:
    for table in HYPERTABLE_TABLES:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(
                    f"SELECT create_hypertable('{table}', 'ts', "
                    f"if_not_exists => TRUE, migrate_data => TRUE)"))
            logger.info("hypertable ready: %s", table)
        except Exception as exc:
            logger.warning("create_hypertable failed for %s: %s", table, exc)


async def _seed_products(engine: AsyncEngine, config: AppConfig) -> None:
    """Upsert the configured product reference rows (idempotent).

    Concurrent initializes (gateway + api racing at startup) may collide
    on the unique key; one retry resolves it because the winner's rows
    become visible to the loser's merges.
    """
    products = config.instruments.products
    if not products:
        return
    order = {code: i for i, code in enumerate(sorted(products))}
    factory = get_session_factory(engine)
    for attempt in (1, 2):
        try:
            async with factory() as session:
                async with session.begin():
                    for code, info in sorted(products.items()):
                        await session.merge(Product(
                            code=code,
                            name=info.name or "",
                            exchange=info.exchange,
                            category=_PRODUCT_CATEGORY.get(code, _DEFAULT_CATEGORY),
                            tick_size=info.tick_size,
                            sort_order=order[code],
                        ))
            logger.info("seeded %d products", len(products))
            return
        except IntegrityError:
            if attempt == 2:
                logger.warning("product seed raced twice; keeping existing rows")
                return
            logger.info("product seed collided with a concurrent init; retrying")


async def _ensure_admin_user(engine: AsyncEngine) -> None:
    """Create the bootstrap ADMIN account if requested and still missing."""
    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if not username or not password:
        return
    factory = get_session_factory(engine)
    async with factory() as session:
        async with session.begin():
            existing = await session.scalar(
                select(User).where(User.username == username))
            if existing is not None:
                return
            session.add(User(
                username=username,
                password_hash=PasswordHasher().hash(password),
                role="ADMIN",
                is_active=True,
            ))
    logger.info("created bootstrap admin user %r", username)


class TimescaleWriter:
    """Bounded async buffer that persists event envelopes as ORM rows.

    ``add_quote_event`` / ``add_micro_event`` are synchronous and
    non-blocking: they append ORM rows to an in-memory buffer. A background
    task started by ``start()`` flushes the buffer in a single transaction
    whenever ``flush_interval_s`` seconds elapsed or ``flush_rows`` rows are
    pending (whichever comes first). If the buffer grows beyond
    ``max_buffer`` the oldest rows are dropped and counted, so a stalled
    database can never exhaust memory on the ingestion path.
    """

    def __init__(self, engine: AsyncEngine, *, flush_interval_s: float = 2.0,
                 flush_rows: int = 2000, max_buffer: int = 100_000):
        self.engine = engine
        self.flush_interval_s = float(flush_interval_s)
        self.flush_rows = int(flush_rows)
        self.max_buffer = int(max_buffer)
        self.dropped_rows = 0
        self._buffer: list[Tick | MicrostructureEvent] = []
        self._wakeup = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None
        self._stopped = False
        self._session_factory = get_session_factory(engine)

    @property
    def pending(self) -> int:
        """Number of rows currently waiting to be flushed."""
        return len(self._buffer)

    async def start(self) -> None:
        """Start the background flush loop."""
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._flush_loop())
        logger.info(
            "TimescaleWriter started (flush_interval_s=%s, flush_rows=%d, max_buffer=%d)",
            self.flush_interval_s, self.flush_rows, self.max_buffer)

    async def stop(self) -> None:
        """Cancel the flush loop and persist everything still buffered."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()
        logger.info("TimescaleWriter stopped (dropped_rows=%d)", self.dropped_rows)

    async def _flush_loop(self) -> None:
        while not self._stopped:
            try:
                await asyncio.wait_for(self._wakeup.wait(),
                                       timeout=self.flush_interval_s)
            except asyncio.TimeoutError:
                pass
            self._wakeup.clear()
            try:
                await self.flush()
            except Exception:
                logger.exception("periodic flush failed")

    async def flush(self) -> int:
        """Write all buffered rows in one transaction; return the row count.

        On failure the rows are re-queued (newest ``max_buffer`` kept) and
        the exception propagates to the caller.
        """
        if not self._buffer:
            return 0
        rows = self._buffer
        self._buffer = []
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add_all(rows)
        except Exception:
            self._buffer = (rows + self._buffer)[-self.max_buffer:]
            raise
        return len(rows)

    def _append(self, row: Tick | MicrostructureEvent) -> None:
        self._buffer.append(row)
        if len(self._buffer) > self.max_buffer:
            self._buffer.pop(0)
            self.dropped_rows += 1
            logger.warning("TimescaleWriter buffer full: dropped oldest row "
                           "(dropped_rows=%d)", self.dropped_rows)
        if len(self._buffer) >= self.flush_rows:
            self._wakeup.set()

    def add_quote_event(self, event: dict) -> None:
        """Buffer one ``quote`` envelope (see app.core.events) as a Tick row."""
        instrument_id = event.get("instrument_id")
        if not instrument_id:
            logger.warning("dropping quote event without instrument_id "
                           "(seq=%s)", event.get("seq"))
            return
        data = event.get("data") or {}
        self._append(Tick(
            instrument_id=instrument_id,
            ts=_event_ts(event),
            exchange_ts_ns=event.get("exchange_ts_ns"),
            local_ts_ns=event.get("local_ts_ns"),
            seq=event.get("seq"),
            last=_num(data.get("last")),
            bid1=_num(data.get("bid1")),
            bid2=_num(data.get("bid2")),
            bid3=_num(data.get("bid3")),
            bid4=_num(data.get("bid4")),
            bid5=_num(data.get("bid5")),
            ask1=_num(data.get("ask1")),
            ask2=_num(data.get("ask2")),
            ask3=_num(data.get("ask3")),
            ask4=_num(data.get("ask4")),
            ask5=_num(data.get("ask5")),
            bid_volume1=_num(data.get("bid_volume1")),
            bid_volume2=_num(data.get("bid_volume2")),
            bid_volume3=_num(data.get("bid_volume3")),
            bid_volume4=_num(data.get("bid_volume4")),
            bid_volume5=_num(data.get("bid_volume5")),
            ask_volume1=_num(data.get("ask_volume1")),
            ask_volume2=_num(data.get("ask_volume2")),
            ask_volume3=_num(data.get("ask_volume3")),
            ask_volume4=_num(data.get("ask_volume4")),
            ask_volume5=_num(data.get("ask_volume5")),
            volume=_num(data.get("volume")),
            turnover=_num(data.get("turnover")),
            open_interest=_num(data.get("open_interest")),
            spread=_num(data.get("spread")),
            spread_ticks=_num(data.get("spread_ticks")),
            mid=_num(data.get("mid")),
            microprice=_num(data.get("microprice")),
            obi1=_num(data.get("obi1")),
            source=event.get("source"),
            data_mode=event.get("data_mode") or "live",
        ))

    def add_micro_event(self, event: dict) -> None:
        """Buffer one ``micro`` envelope (see app.core.events) as an event row."""
        instrument_id = event.get("instrument_id")
        if not instrument_id:
            logger.warning("dropping micro event without instrument_id "
                           "(seq=%s)", event.get("seq"))
            return
        data = event.get("data") or {}
        self._append(MicrostructureEvent(
            instrument_id=instrument_id,
            ts=_event_ts(event),
            exchange_ts_ns=event.get("exchange_ts_ns"),
            local_ts_ns=event.get("local_ts_ns"),
            seq=event.get("seq"),
            label=data.get("label"),
            family=data.get("family"),
            state5=data.get("state5"),
            direction=data.get("direction"),
            reason=data.get("reason"),
            direction_hint=data.get("direction_hint"),
            db_ticks=_num(data.get("db_ticks")),
            da_ticks=_num(data.get("da_ticks")),
            dl_ticks=_num(data.get("dl_ticks")),
            dm_ticks=_num(data.get("dm_ticks")),
            spread_prev_ticks=_num(data.get("spread_prev_ticks")),
            spread_cur_ticks=_num(data.get("spread_cur_ticks")),
            prev_quote=data.get("prev_quote"),
            cur_quote=data.get("cur_quote"),
            source=event.get("source"),
            data_mode=event.get("data_mode") or "live",
        ))


async def query_ticks(session: AsyncSession, instrument_id: str, *,
                      from_ns: Optional[int] = None, to_ns: Optional[int] = None,
                      limit: int = 1000, offset: int = 0) -> list[Tick]:
    """Ticks of one instrument, ordered by ``exchange_ts_ns`` (ascending)."""
    stmt = select(Tick).where(Tick.instrument_id == instrument_id)
    if from_ns is not None:
        stmt = stmt.where(Tick.exchange_ts_ns >= from_ns)
    if to_ns is not None:
        stmt = stmt.where(Tick.exchange_ts_ns <= to_ns)
    stmt = stmt.order_by(Tick.exchange_ts_ns.asc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


async def query_micro_events(session: AsyncSession, instrument_id: str, *,
                             from_ns: Optional[int] = None,
                             to_ns: Optional[int] = None,
                             label: Optional[str] = None,
                             limit: int = 500, offset: int = 0
                             ) -> list[MicrostructureEvent]:
    """Microstructure events of one instrument, optionally filtered by label."""
    stmt = select(MicrostructureEvent).where(
        MicrostructureEvent.instrument_id == instrument_id)
    if from_ns is not None:
        stmt = stmt.where(MicrostructureEvent.exchange_ts_ns >= from_ns)
    if to_ns is not None:
        stmt = stmt.where(MicrostructureEvent.exchange_ts_ns <= to_ns)
    if label is not None:
        stmt = stmt.where(MicrostructureEvent.label == label)
    stmt = stmt.order_by(MicrostructureEvent.exchange_ts_ns.asc()).limit(
        limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())
