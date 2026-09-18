"""Collector / live state / dashboard tests (no CTP connection required)."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from app.collector.ctp_binding import depth_fields_to_row
from app.collector.live import (JsonlTailSource, LiveClassifier, LiveState,
                                live_feed_path)
from conftest import make_row


def _fields(bid=2300.0, ask=2301.0, last=2300.0, inst="C2701"):
    f = {ctp: None for ctp in [
        "TradingDay", "reserve1", "ExchangeID", "reserve2", "LastPrice",
        "PreSettlementPrice", "PreClosePrice", "PreOpenInterest", "OpenPrice",
        "HighestPrice", "LowestPrice", "Volume", "Turnover", "OpenInterest",
        "ClosePrice", "SettlementPrice", "UpperLimitPrice", "LowerLimitPrice",
        "PreDelta", "CurrDelta", "UpdateTime", "UpdateMillisec",
        "AveragePrice", "ActionDay", "InstrumentID", "ExchangeInstID",
        "BandingUpperPrice", "BandingLowerPrice"]}
    for i in range(1, 6):
        for side in ("Bid", "Ask"):
            f[f"{side}Price{i}"] = None
            f[f"{side}Volume{i}"] = None
    f.update({
        "TradingDay": "2026-09-18", "ExchangeID": "DCE", "LastPrice": last,
        "Volume": 100, "Turnover": 230000.0, "OpenInterest": 250000.0,
        "UpdateTime": "09:30:00", "UpdateMillisec": 500,
        "BidPrice1": bid, "BidVolume1": 30, "AskPrice1": ask, "AskVolume1": 10,
        "AveragePrice": 2300.5, "ActionDay": "2026-09-18",
        "InstrumentID": inst, "ExchangeInstID": inst,
        "BidPrice2": bid - 1, "BidVolume2": 5, "AskPrice2": ask + 1,
        "AskVolume2": 5,
    })
    return f


def test_depth_fields_to_row_schema():
    row = depth_fields_to_row(_fields(), sequence_id=7,
                              local_receive_time_ns=123, batch_id="b",
                              trading_session="DAY", raw_source="ctp:simnow")
    assert row["sequence_id"] == 7
    assert row["instrument_id"] == "C2701"
    assert row["bid_price1"] == 2300.0
    assert row["bid_volume1"] == 30
    assert row["update_millisec"] == 500
    assert row["trading_session"] == "DAY"
    # every schema column present
    from app.common.schema import RAW_COLUMN_NAMES
    missing = [c for c in RAW_COLUMN_NAMES if c not in row]
    assert not missing, missing


def test_live_classifier_toggle():
    clf = LiveClassifier(tick_size=1.0)
    lab, d, der = clf.classify("C2701", 2300.0, 2301.0, 2300.0)
    assert lab is None  # first row
    lab, d, der = clf.classify("C2701", 2300.0, 2301.0, 2301.0)
    assert lab == "HIGH_CONFIDENCE_BOUNCE_UP"
    assert d == 1
    lab, d, der = clf.classify("C2701", 2300.0, 2301.0, 2300.0)
    assert lab == "HIGH_CONFIDENCE_BOUNCE_DOWN"


def test_live_state_update_and_snapshot():
    state = LiveState(history_points=10)
    inst_state = state.instrument("C2701")
    row = make_row(0, 1, 2300.0, 2301.0, 2300.0)
    inst_state.update(row, "NO_MOVE", 0, {"spread": 1.0, "mid_price": 2300.5,
                                          "obi1": 0.5, "dl_ticks": 0.0})
    row2 = make_row(1, 2, 2300.0, 2301.0, 2301.0)
    inst_state.update(row2, "HIGH_CONFIDENCE_BOUNCE_UP", 1,
                      {"spread": 1.0, "mid_price": 2300.5, "obi1": 0.5,
                       "dl_ticks": 1.0})
    snap = inst_state.snapshot()
    assert snap["msg_count"] == 2
    assert snap["label_counts"]["HIGH_CONFIDENCE_BOUNCE_UP"] == 1
    assert snap["one_tick_last_changes"] == 1
    assert snap["bounce_ratio"] == 1.0
    assert snap["last"]["last_price"] == 2301.0
    assert len(snap["recent_events"]) == 1

    js = state.to_json()
    assert "C2701" in js["instruments"]
    assert js["instruments"]["C2701"]["msg_count"] == 2


def test_jsonl_tail_source(tmp_path: Path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    path = live_feed_path(live_dir, "2026-09-18")
    recs = [
        {"ts_recv_ns": 1, "instrument": "C2701",
         "quote": {"last_price": 2300.0, "update_time": "09:30:00",
                   "bid_price1": 2300.0, "ask_price1": 2301.0,
                   "bid_volume1": 10, "ask_volume1": 10},
         "derived": {"mid_price": 2300.5, "obi1": 0.0, "spread_ticks": 1.0,
                     "dl_ticks": 0.0},
         "label": "NO_MOVE", "direction": 0},
        {"ts_recv_ns": 2, "instrument": "C2701",
         "quote": {"last_price": 2301.0, "update_time": "09:30:01",
                   "bid_price1": 2300.0, "ask_price1": 2301.0,
                   "bid_volume1": 10, "ask_volume1": 10},
         "derived": {"mid_price": 2300.5, "obi1": 0.0, "spread_ticks": 1.0,
                     "dl_ticks": 1.0},
         "label": "HIGH_CONFIDENCE_BOUNCE_UP", "direction": 1},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")

    state = LiveState()
    tail = JsonlTailSource(live_dir, state)
    # first poll: file opened at end -> 0 rows; simulate writer appending later
    n = tail.poll()
    assert n == 0
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(recs[0]) + "\n")
        f.write(json.dumps(recs[1]) + "\n")
    n = tail.poll()
    assert n == 2
    snap = state.instrument("C2701").snapshot()
    assert snap["msg_count"] == 2
    assert snap["label_counts"]["HIGH_CONFIDENCE_BOUNCE_UP"] == 1


def test_dashboard_http(tmp_path):
    from app.dashboard.server import DashboardServer
    state = LiveState()
    state.set_connection("logged_in", "test")
    inst = state.instrument("C2701")
    inst.update(make_row(0, 1, 2300.0, 2301.0, 2300.0), "NO_MOVE", 0,
                {"spread": 1.0, "mid_price": 2300.5, "obi1": 0.5})

    server = DashboardServer(state, host="127.0.0.1", port=0)
    port = server.port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/state", timeout=5) as resp:
            data = json.loads(resp.read().decode())
        assert data["connection"]["status"] == "logged_in"
        assert "C2701" in data["instruments"]

        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=5) as resp:
            html = resp.read().decode()
        assert "微观结构" in html
        assert "__REFRESH_MS__" not in html
    finally:
        server.shutdown()
