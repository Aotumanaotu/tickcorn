"""Relational storage tests: ORM models, init/seed, TimescaleWriter round-trips.

Runs entirely on SQLite (aiosqlite) as the portable compatibility baseline
for the PostgreSQL/TimescaleDB production backend. Envelopes are built with
the real constructors from ``app.core.events`` so the writer is exercised
against the exact contract the gateway emits.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.common.config import AppConfig
from app.core.events import envelope, micro_data, quote_data
from app.storage.db import create_async_engine_from_url, get_session_factory
from app.storage.models import (
    Product,
    RefreshToken,
    User,
    Watchlist,
    WatchlistItem,
)
from app.storage.timescale import (
    TimescaleWriter,
    init_database,
    query_micro_events,
    query_ticks,
)

BASE_NS = 1_758_000_000_000_000_000
STEP_NS = 500_000_000


@pytest.fixture()
def no_admin_env(monkeypatch) -> None:
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)


@pytest.fixture()
async def engine(tmp_path: Path):
    eng = create_async_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    try:
        yield eng
    finally:
        await eng.dispose()


def _naive_utc(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).replace(tzinfo=None)


def _quote_event(seq: int, bid: float = 2300.0, ask: float = 2301.0,
                 instrument: str = "C2701") -> dict:
    row = {
        "trading_day": "2026-09-18",
        "update_time": "09:30:00",
        "update_millisec": 500,
        "bid_volume1": 50, "ask_volume1": 55,
        "bid_volume2": 60, "ask_volume2": 65,
        "bid_volume3": 70, "ask_volume3": 75,
        "bid_volume4": 80, "ask_volume4": 85,
        "bid_volume5": 90, "ask_volume5": 95,
        "volume": 1234,
        "turnover": 2_844_534.0,
        "open_interest": 250_000.0,
    }
    derived = {
        "last": bid,
        "bid1": bid, "ask1": ask,
        "bid2": bid - 1, "ask2": ask + 1,
        "bid3": bid - 2, "ask3": ask + 2,
        "bid4": bid - 3, "ask4": ask + 3,
        "bid5": bid - 4, "ask5": ask + 4,
        "spread": ask - bid,
        "spread_ticks": 1.0,
        "mid": (bid + ask) / 2,
        "microprice": (bid + ask) / 2,
        "obi1": (50 - 55) / 105.0,
    }
    return envelope(
        "quote",
        instrument_id=instrument,
        seq=seq,
        exchange_ts_ns=BASE_NS + seq * STEP_NS,
        local_ts_ns=BASE_NS + seq * STEP_NS + 250_000,
        source="ctp:simnow",
        data_mode="live",
        data=quote_data(row, derived),
    )


def _micro_event(seq: int, label: str = "LIKELY_BOUNCE_UP") -> dict:
    family = "GENUINE_QUOTE_MOVE" if label.startswith("GENUINE") else "LIKELY_BOUNCE"
    state5 = "BOUNCE_UP" if label.endswith("UP") else "BOUNCE_DOWN"
    result = SimpleNamespace(
        label=SimpleNamespace(value=label),
        family=SimpleNamespace(value=family),
        state5=SimpleNamespace(value=state5),
        direction=1,
        reason=None,
        direction_hint=1,
        db_ticks=0.0,
        da_ticks=0.0,
        dl_ticks=1.0,
        dm_ticks=1.0,
        spread_prev_ticks=1.0,
        spread_cur_ticks=1.0,
    )
    return envelope(
        "micro",
        instrument_id="C2701",
        seq=seq,
        exchange_ts_ns=BASE_NS + seq * STEP_NS,
        local_ts_ns=BASE_NS + seq * STEP_NS + 300_000,
        source="ctp:simnow",
        data_mode="live",
        data=micro_data(
            result,
            {"bid": 2300.0, "ask": 2301.0, "last": 2300.0},
            {"bid": 2300.0, "ask": 2301.0, "last": 2301.0},
        ),
    )


# ---------------------------------------------------------------------
# init_database: schema, product seed, admin bootstrap
# ---------------------------------------------------------------------

async def test_init_database_seeds_products_and_admin(engine, config: AppConfig,
                                                      monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret-pass")
    await init_database(engine, config)

    factory = get_session_factory(engine)
    async with factory() as session:
        products = list((await session.execute(select(Product))).scalars())
        codes = [p.code for p in products]
        assert sorted(codes) == sorted(config.instruments.products)

        by_code = {p.code: p for p in products}
        assert by_code["C"].category == "Agriculture"
        assert by_code["CS"].category == "Agriculture"
        assert by_code["M"].category == "Agriculture"
        assert by_code["CF"].category == "Agriculture"
        assert by_code["RB"].category == "Ferrous"
        assert by_code["C"].exchange == "DCE"
        assert by_code["C"].tick_size == 1.0
        assert by_code["C"].name == config.instruments.products["C"].name
        ordered = [p.code for p in sorted(products, key=lambda p: p.sort_order)]
        assert ordered == sorted(codes)

        admin = await session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        assert admin.role == "ADMIN"
        assert admin.is_active is True
        assert admin.created_at is not None
        assert admin.id is not None
        assert PasswordHasher().verify(admin.password_hash, "s3cret-pass")


async def test_init_database_is_idempotent_and_keeps_admin(engine,
                                                           config: AppConfig,
                                                           monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "first-pass")
    await init_database(engine, config)
    monkeypatch.setenv("ADMIN_PASSWORD", "second-pass")
    await init_database(engine, config)

    factory = get_session_factory(engine)
    async with factory() as session:
        users = list((await session.execute(select(User))).scalars())
        assert len(users) == 1
        assert users[0].username == "admin"
        assert PasswordHasher().verify(users[0].password_hash, "first-pass")
        with pytest.raises(VerifyMismatchError):
            PasswordHasher().verify(users[0].password_hash, "other-pass")


async def test_init_database_without_admin_env_creates_no_user(engine,
                                                               config: AppConfig,
                                                               no_admin_env):
    await init_database(engine, config)
    factory = get_session_factory(engine)
    async with factory() as session:
        users = list((await session.execute(select(User))).scalars())
        assert users == []


# ---------------------------------------------------------------------
# TimescaleWriter: buffered writes + queries
# ---------------------------------------------------------------------

async def test_writer_quote_and_micro_roundtrip(engine, config: AppConfig,
                                                no_admin_env):
    await init_database(engine, config)
    writer = TimescaleWriter(engine, flush_interval_s=60.0, flush_rows=500)
    await writer.start()
    try:
        for seq in range(5):
            writer.add_quote_event(_quote_event(seq))
        for seq in range(3):
            writer.add_micro_event(_micro_event(seq, "LIKELY_BOUNCE_UP"))
        for seq in range(3, 5):
            writer.add_micro_event(_micro_event(seq, "GENUINE_QUOTE_MOVE_UP"))
        assert writer.pending == 10
    finally:
        await writer.stop()
    assert writer.pending == 0
    assert writer.dropped_rows == 0

    factory = get_session_factory(engine)
    async with factory() as session:
        ticks = await query_ticks(session, "C2701")
        assert len(ticks) == 5
        t0 = ticks[0]
        assert t0.id is not None
        assert t0.instrument_id == "C2701"
        assert t0.seq == 0
        assert t0.exchange_ts_ns == BASE_NS
        assert t0.local_ts_ns == BASE_NS + 250_000
        assert t0.ts == _naive_utc(BASE_NS)
        assert t0.last == 2300.0
        assert t0.bid1 == 2300.0
        assert t0.ask1 == 2301.0
        assert t0.bid2 == 2299.0
        assert t0.ask5 == 2305.0
        assert t0.bid_volume1 == 50.0
        assert t0.ask_volume5 == 95.0
        assert t0.volume == 1234.0
        assert t0.turnover == 2_844_534.0
        assert t0.open_interest == 250_000.0
        assert t0.spread == 1.0
        assert t0.spread_ticks == 1.0
        assert t0.mid == 2300.5
        assert t0.microprice == 2300.5
        assert t0.obi1 == pytest.approx(-5.0 / 105.0)
        assert t0.source == "ctp:simnow"
        assert t0.data_mode == "live"

        assert [t.seq for t in await query_ticks(
            session, "C2701", from_ns=BASE_NS + 3 * STEP_NS)] == [3, 4]
        assert [t.seq for t in await query_ticks(
            session, "C2701", to_ns=BASE_NS + 2 * STEP_NS + 1)] == [0, 1, 2]
        assert [t.seq for t in await query_ticks(
            session, "C2701", from_ns=BASE_NS + STEP_NS,
            to_ns=BASE_NS + 3 * STEP_NS)] == [1, 2, 3]
        assert [t.seq for t in await query_ticks(
            session, "C2701", limit=2, offset=1)] == [1, 2]
        assert await query_ticks(session, "RB2601") == []

        events = await query_micro_events(session, "C2701")
        assert len(events) == 5
        e0 = events[0]
        assert e0.seq == 0
        assert e0.exchange_ts_ns == BASE_NS
        assert e0.ts == _naive_utc(BASE_NS)
        assert e0.label == "LIKELY_BOUNCE_UP"
        assert e0.family == "LIKELY_BOUNCE"
        assert e0.state5 == "BOUNCE_UP"
        assert e0.direction == 1
        assert e0.direction_hint == 1
        assert e0.reason is None
        assert e0.db_ticks == 0.0
        assert e0.dl_ticks == 1.0
        assert e0.spread_cur_ticks == 1.0
        assert e0.prev_quote == {"bid": 2300.0, "ask": 2301.0, "last": 2300.0}
        assert e0.cur_quote == {"bid": 2300.0, "ask": 2301.0, "last": 2301.0}
        assert e0.source == "ctp:simnow"
        assert e0.data_mode == "live"

        bounces = await query_micro_events(session, "C2701",
                                           label="LIKELY_BOUNCE_UP")
        assert [e.seq for e in bounces] == [0, 1, 2]
        moves = await query_micro_events(session, "C2701",
                                         from_ns=BASE_NS + 2 * STEP_NS + 1,
                                         label="GENUINE_QUOTE_MOVE_UP")
        assert [e.seq for e in moves] == [3, 4]
        assert await query_micro_events(session, "C2701", label="NO_MOVE") == []


async def test_writer_autoflush_by_rows_then_by_interval(engine,
                                                         config: AppConfig,
                                                         no_admin_env):
    await init_database(engine, config)
    factory = get_session_factory(engine)

    writer = TimescaleWriter(engine, flush_interval_s=60.0, flush_rows=3)
    await writer.start()
    try:
        for seq in range(3):
            writer.add_quote_event(_quote_event(seq))
        await asyncio.sleep(0.5)
        async with factory() as session:
            assert [t.seq for t in await query_ticks(session, "C2701")] == [0, 1, 2]
    finally:
        await writer.stop()

    writer = TimescaleWriter(engine, flush_interval_s=0.2, flush_rows=10_000)
    await writer.start()
    try:
        writer.add_quote_event(_quote_event(10))
        await asyncio.sleep(0.7)
        async with factory() as session:
            assert [t.seq for t in await query_ticks(session, "C2701")] == [0, 1, 2, 10]
    finally:
        await writer.stop()


async def test_writer_drops_oldest_when_buffer_full(engine, config: AppConfig,
                                                    no_admin_env):
    await init_database(engine, config)
    writer = TimescaleWriter(engine, flush_interval_s=60.0, flush_rows=100,
                             max_buffer=5)
    for seq in range(8):
        writer.add_quote_event(_quote_event(seq))
    assert writer.pending == 5
    assert writer.dropped_rows == 3
    await writer.flush()

    factory = get_session_factory(engine)
    async with factory() as session:
        assert [t.seq for t in await query_ticks(session, "C2701")] == [3, 4, 5, 6, 7]


async def test_writer_drops_events_without_instrument_id(engine,
                                                         config: AppConfig,
                                                         no_admin_env):
    await init_database(engine, config)
    writer = TimescaleWriter(engine)
    quote = _quote_event(0)
    quote["instrument_id"] = None
    writer.add_quote_event(quote)
    micro = _micro_event(0)
    micro["instrument_id"] = None
    writer.add_micro_event(micro)
    assert writer.pending == 0


# ---------------------------------------------------------------------
# users / refresh_tokens / watchlists CRUD
# ---------------------------------------------------------------------

async def test_user_refresh_token_watchlist_crud(engine, config: AppConfig,
                                                 no_admin_env):
    await init_database(engine, config)
    factory = get_session_factory(engine)

    async with factory() as session:
        async with session.begin():
            session.add(User(username="alice", email="alice@example.com",
                             password_hash="x" * 60, role="RESEARCHER"))
            session.add(User(username="bob", password_hash="y" * 60, role="VIEWER"))

    async with factory() as session:
        alice = await session.scalar(select(User).where(User.username == "alice"))
        bob = await session.scalar(select(User).where(User.username == "bob"))
        assert alice is not None and bob is not None
        assert alice.email == "alice@example.com"
        assert bob.email is None
        assert alice.is_active is True
        assert alice.created_at is not None
        alice_id = alice.id

    async with factory() as session:
        async with session.begin():
            session.add(RefreshToken(
                user_id=alice_id,
                family_id="fam-1",
                token_hash="t" * 64,
                expires_at=datetime.now(timezone.utc) + timedelta(days=14)))
            session.add(Watchlist(owner_user_id=alice_id, name="main"))

    async with factory() as session:
        token = await session.scalar(
            select(RefreshToken).where(RefreshToken.user_id == alice_id))
        watchlist = await session.scalar(
            select(Watchlist).where(Watchlist.owner_user_id == alice_id))
        assert token is not None
        assert token.family_id == "fam-1"
        assert token.revoked_at is None
        assert token.created_at is not None
        assert watchlist is not None
        assert watchlist.name == "main"
        assert watchlist.created_at is not None
        watchlist_id = watchlist.id

    async with factory() as session:
        async with session.begin():
            session.add(WatchlistItem(watchlist_id=watchlist_id,
                                      instrument_id="C2701", sort_order=0))
            session.add(WatchlistItem(watchlist_id=watchlist_id,
                                      instrument_id="RB2601", sort_order=1))

    async with factory() as session:
        items = list((await session.execute(
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id)
            .order_by(WatchlistItem.sort_order))).scalars())
        assert [i.instrument_id for i in items] == ["C2701", "RB2601"]
        assert items[0].added_at is not None

    with pytest.raises(IntegrityError):
        async with factory() as session:
            async with session.begin():
                session.add(WatchlistItem(watchlist_id=watchlist_id,
                                          instrument_id="C2701"))

    with pytest.raises(IntegrityError):
        async with factory() as session:
            async with session.begin():
                session.add(User(username="alice", password_hash="z" * 60,
                                 role="VIEWER"))

    async with factory() as session:
        async with session.begin():
            bob = await session.scalar(select(User).where(User.username == "bob"))
            bob.is_active = False

    async with factory() as session:
        bob = await session.scalar(select(User).where(User.username == "bob"))
        assert bob.is_active is False

    async with factory() as session:
        async with session.begin():
            token = await session.scalar(
                select(RefreshToken).where(RefreshToken.user_id == alice_id))
            await session.delete(token)

    async with factory() as session:
        tokens = list((await session.execute(
            select(RefreshToken).where(RefreshToken.user_id == alice_id))).scalars())
        assert tokens == []
        users = list((await session.execute(select(User))).scalars())
        assert {u.username for u in users} == {"alice", "bob"}
