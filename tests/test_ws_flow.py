"""WS hub routing / backpressure logic and realtime metrics engine."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.analysis.realtime import RealtimeMetrics
from app.core.events import EVENT_ANALYSIS, EVENT_MICRO, EVENT_QUOTE, envelope
from app.ingest.bus import EventBus
from app.ws.hub import ConnectionHub
from app.ws.ticket import TicketStore


async def _tick() -> None:
    await asyncio.sleep(0)


class FakeWebSocket:
    """Minimal stand-in for a starlette WebSocket."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed_with: int | None = None

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def _quote(instrument_id: str, seq: int) -> dict:
    return envelope(EVENT_QUOTE, instrument_id=instrument_id, seq=seq,
                    local_ts_ns=time.time_ns(), data={"last": 2300.0})


def _micro(instrument_id: str, label: str, dl_ticks: float) -> dict:
    return envelope(EVENT_MICRO, instrument_id=instrument_id,
                    local_ts_ns=time.time_ns(),
                    data={"label": label, "dl_ticks": dl_ticks,
                          "family": label})


async def _drain_pumps(times: int = 8) -> None:
    """Let queued bus subscribers / hub tasks run to completion."""
    for _ in range(times):
        await asyncio.sleep(0)


async def test_market_hub_routes_by_subscription():
    hub = ConnectionHub("market")
    subscribed = hub.register(FakeWebSocket(), 1)
    other = hub.register(FakeWebSocket(), 2)
    await hub.handle_message(subscribed,
                             {"action": "subscribe", "instrument": "c2611"})

    bus = EventBus()
    unsub = await bus.subscribe(
        hub.route, event_types={EVENT_QUOTE, EVENT_MICRO})
    await bus.publish(_quote("C2611", seq=1))
    await bus.publish(_quote("C2701", seq=2))
    await _drain_pumps()

    assert subscribed.send_queue.qsize() == 1
    assert other.send_queue.qsize() == 0
    delivered = subscribed.send_queue.get_nowait()
    assert delivered["instrument_id"] == "C2611"
    assert delivered["data"]["last"] == 2300.0
    assert subscribed.user_id == 1
    unsub()


async def test_market_hub_ignores_unrelated_event_types():
    hub = ConnectionHub("market")
    conn = hub.register(FakeWebSocket(), 1)
    conn.subscriptions.add("C2611")
    await bus_publish_only(hub, [envelope(EVENT_ANALYSIS,
                                          instrument_id="C2611")])
    assert conn.send_queue.qsize() == 0


async def bus_publish_only(hub, events):
    bus = EventBus()
    unsub = await bus.subscribe(
        hub.route, event_types={EVENT_QUOTE, EVENT_MICRO, EVENT_ANALYSIS})
    try:
        for event in events:
            await bus.publish(event)
    finally:
        unsub()


async def test_quote_conflate_drops_oldest_when_full():
    hub = ConnectionHub("market")
    conn = hub.register(FakeWebSocket(), 1)
    conn.subscriptions.add("C2611")
    for i in range(5000):
        conn.send_queue.put_nowait({"seq": i})

    await hub.route(_quote("C2611", seq=99_999))

    assert conn.send_queue.qsize() == 5000
    assert not conn.dead
    drained = [conn.send_queue.get_nowait() for _ in range(5000)]
    assert drained[0]["seq"] == 1
    assert drained[-1]["seq"] == 99_999


async def test_micro_overflow_disconnects_connection():
    hub = ConnectionHub("market")
    conn = hub.register(FakeWebSocket(), 1)
    conn.subscriptions.add("C2611")
    for i in range(5000):
        conn.send_queue.put_nowait({"seq": i})

    await hub.route(_micro("C2611", "NO_MOVE", 0.0))

    assert conn.dead


async def test_client_message_protocol():
    hub = ConnectionHub("market")
    conn = hub.register(FakeWebSocket(), 7)

    await hub.handle_message(
        conn, {"action": "subscribe", "instrument": "c2611"})
    assert "C2611" in conn.subscriptions

    await hub.handle_message(conn, {"action": "ping"})
    pong = conn.send_queue.get_nowait()
    assert pong["type"] == "pong"
    assert pong["server_time"]

    await hub.handle_message(
        conn, {"action": "unsubscribe", "instrument": "C2611"})
    assert "C2611" not in conn.subscriptions

    await hub.handle_message(conn, {"action": "dance"})
    err = conn.send_queue.get_nowait()
    assert err["type"] == "error"


async def test_sender_pumps_queue_to_socket():
    hub = ConnectionHub("market")
    ws = FakeWebSocket()
    conn = hub.register(ws, 1)
    hub.send_hello(conn)
    hub.start_sender(conn)
    hub.push(conn, {"type": "test"})
    for _ in range(100):
        if len(ws.sent) >= 2:
            break
        await _tick()
    frames = [json.loads(s) for s in ws.sent]
    assert frames[0]["type"] == "hello"
    assert frames[0]["channel"] == "market"
    assert frames[-1]["type"] == "test"
    hub.unregister(conn)


def test_ticket_store_is_single_use():
    store = TicketStore()
    ticket = store.issue(42, ttl_s=30)
    assert store.redeem(ticket) == 42
    assert store.redeem(ticket) is None
    assert store.redeem("totally-bogus") is None


def test_ticket_store_expiry():
    store = TicketStore()
    ticket = store.issue(7, ttl_s=0)
    assert store.redeem(ticket) is None


async def test_realtime_metrics_snapshot_and_regime():
    bus = EventBus()
    collected: list[dict] = []
    unsub_analysis = await bus.subscribe(
        lambda event: collected.append(event),
        event_types={EVENT_ANALYSIS}, queued=False)
    metrics = RealtimeMetrics()
    await metrics.start(bus)

    try:
        for i in range(5):
            await bus.publish(
                _micro("C2701", "LIKELY_BOUNCE_DOWN" if i % 2 else
                       "LIKELY_BOUNCE_UP", 1.0))
        early = metrics.snapshot("C2701")
        assert early["one_tick_last_changes"] == 5
        assert early["bounce_ratio"] == 1.0
        assert RealtimeMetrics.classify_regime(early) == "INSUFFICIENT_DATA"

        for i in range(25):
            await bus.publish(
                _micro("C2701", "LIKELY_BOUNCE_DOWN" if i % 2 else
                       "LIKELY_BOUNCE_UP", 1.0))
        bounce_snap = metrics.snapshot("C2701")
        assert bounce_snap["one_tick_last_changes"] == 30
        assert bounce_snap["bounce_ratio"] == 1.0
        assert bounce_snap["mean_spread_ticks"] is None
        assert RealtimeMetrics.classify_regime(bounce_snap) == "HIGH_BOUNCE"

        for i in range(20):
            await bus.publish(_micro(
                "C2701",
                "GENUINE_QUOTE_MOVE_DOWN" if i % 2 else
                "GENUINE_QUOTE_MOVE_UP", 1.0))
        final = metrics.snapshot("C2701")
        assert final["one_tick_last_changes"] == 50
        assert final["genuine_move_ratio"] == pytest.approx(20 / 50)
        assert RealtimeMetrics.classify_regime(final) == "DIRECTIONAL"

        assert metrics.snapshot("NOPE9999") is None
        assert set(metrics.snapshots()) == {"C2701"}

        assert collected
        last = collected[-1]
        assert last["type"] == EVENT_ANALYSIS
        assert last["instrument_id"] == "C2701"
        assert last["data"]["label"] == "GENUINE_QUOTE_MOVE_DOWN"
        assert last["data"]["metrics"]["one_tick_last_changes"] == 50
        assert last["data"]["regime"] == "DIRECTIONAL"
    finally:
        unsub_analysis()
        await metrics.stop()


async def test_realtime_metrics_quote_updates():
    bus = EventBus()
    metrics = RealtimeMetrics()
    await metrics.start(bus)
    try:
        for spread in (1.0, 2.0, 3.0):
            await bus.publish(envelope(
                EVENT_QUOTE, instrument_id="CS2701",
                local_ts_ns=time.time_ns(),
                data={"spread_ticks": spread, "last": 2900.0}))
        snap = metrics.snapshot("CS2701")
        assert snap["msg_count"] == 3
        assert snap["mean_spread_ticks"] == pytest.approx(2.0)
        assert snap["message_rate_per_min"] == 3
    finally:
        await metrics.stop()
