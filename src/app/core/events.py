"""Standard market event contract (single source of truth).

Every market-data consumer in the system -- websocket hubs, realtime
metrics, the timescale writer, the replay engine -- consumes these
envelopes. Nobody outside app.gateway.native may touch raw CTP field
structures.

Envelopes are plain dicts with msgpack-safe values (str/int/float/bool/
None/list/dict) so the same payload flows unchanged over the gateway unix
socket (msgpack) and the browser websockets (JSON).
"""

from __future__ import annotations

import time

PROTOCOL_VERSION = 1

# --- event types -------------------------------------------------------
EVENT_QUOTE = "quote"                  # L1/L5 book snapshot (per update)
EVENT_MICRO = "micro"                  # microstructure classification (per transition)
EVENT_INSTRUMENT_STATUS = "instrument_status"   # subscribe/unsubscribe results
EVENT_CONNECTION = "connection"        # gateway connection state machine
EVENT_ANALYSIS = "analysis"            # rolling metrics / regime updates
EVENT_REPLAY_STATUS = "replay_status"  # replay task lifecycle
EVENT_SYSTEM = "system"                # heartbeat / health

# --- data modes --------------------------------------------------------
DATA_LIVE = "live"
DATA_REPLAY = "replay"

# --- connection states (EVENT_CONNECTION.data["state"]) ----------------
CONN_IDLE = "idle"
CONN_CONNECTING = "connecting"
CONN_CONNECTED = "connected"          # logged in, streaming possible
CONN_STREAMING = "streaming"          # subscribed to >= 1 instrument
CONN_DISCONNECTED = "disconnected"
CONN_FAILED = "failed"


def now_ns() -> int:
    return time.time_ns()


def envelope(event_type: str, *, instrument_id: str | None = None,
             seq: int | None = None, exchange_ts_ns: int | None = None,
             local_ts_ns: int | None = None, source: str = "",
             data_mode: str = DATA_LIVE, data: dict | None = None) -> dict:
    """Build a standard event envelope."""
    return {
        "v": PROTOCOL_VERSION,
        "type": event_type,
        "instrument_id": instrument_id,
        "seq": seq,
        "exchange_ts_ns": exchange_ts_ns,
        "local_ts_ns": local_ts_ns if local_ts_ns is not None else now_ns(),
        "source": source,
        "data_mode": data_mode,
        "data": data if data is not None else {},
    }


def quote_data(row: dict, derived: dict) -> dict:
    """Quote payload: 5-level book + last + session stats + derived values.

    CTP sentinels (DBL_MAX etc.) are already converted to None by the
    normalizer before this is called.
    """
    return {
        "trading_day": row.get("trading_day"),
        "update_time": row.get("update_time"),
        "update_millisec": row.get("update_millisec"),
        "last": derived.get("last"),
        "bid1": derived.get("bid1"), "ask1": derived.get("ask1"),
        "bid_volume1": row.get("bid_volume1"), "ask_volume1": row.get("ask_volume1"),
        "bid2": derived.get("bid2"), "ask2": derived.get("ask2"),
        "bid_volume2": row.get("bid_volume2"), "ask_volume2": row.get("ask_volume2"),
        "bid3": derived.get("bid3"), "ask3": derived.get("ask3"),
        "bid_volume3": row.get("bid_volume3"), "ask_volume3": row.get("ask_volume3"),
        "bid4": derived.get("bid4"), "ask4": derived.get("ask4"),
        "bid_volume4": row.get("bid_volume4"), "ask_volume4": row.get("ask_volume4"),
        "bid5": derived.get("bid5"), "ask5": derived.get("ask5"),
        "bid_volume5": row.get("bid_volume5"), "ask_volume5": row.get("ask_volume5"),
        "volume": row.get("volume"),
        "turnover": row.get("turnover"),
        "open_interest": row.get("open_interest"),
        "spread": derived.get("spread"),
        "spread_ticks": derived.get("spread_ticks"),
        "mid": derived.get("mid"),
        "microprice": derived.get("microprice"),
        "obi1": derived.get("obi1"),
    }


def micro_data(result, prev_quote: dict, cur_quote: dict) -> dict:
    """Microstructure classification payload from a ClassificationResult.

    `result` is a classifier jump_classifier.ClassificationResult; prev/cur
    quotes carry the (bid, ask, last) triples so the Microstructure Lab can
    render full before/after context without re-querying ticks.
    """
    return {
        "label": result.label.value,
        "family": result.family.value,
        "state5": result.state5.value if result.state5 else None,
        "direction": result.direction,
        "reason": result.reason.value if result.reason else None,
        "direction_hint": result.direction_hint,
        "db_ticks": result.db_ticks,
        "da_ticks": result.da_ticks,
        "dl_ticks": result.dl_ticks,
        "dm_ticks": result.dm_ticks,
        "spread_prev_ticks": result.spread_prev_ticks,
        "spread_cur_ticks": result.spread_cur_ticks,
        "prev_quote": prev_quote,
        "cur_quote": cur_quote,
    }
