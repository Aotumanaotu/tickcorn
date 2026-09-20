"""Live state shared between the collector and the real-time dashboard.

The LiveState is updated on the CTP callback thread (cheap dict/deque ops)
and read by the dashboard HTTP thread. It also supports a standalone mode
that tails the live JSONL feed file when no collector process is running.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Optional

from app.core.classifier.jump_classifier import JumpEventClassifier
from app.common.logging import get_logger

logger = get_logger("collector.live")

_LABEL_KEYS = [
    "HIGH_CONFIDENCE_BOUNCE_UP", "HIGH_CONFIDENCE_BOUNCE_DOWN",
    "LIKELY_BOUNCE_UP", "LIKELY_BOUNCE_DOWN",
    "GENUINE_QUOTE_MOVE_UP", "GENUINE_QUOTE_MOVE_DOWN",
    "AMBIGUOUS", "NO_MOVE",
]


class InstrumentLiveState:
    def __init__(self, history_points: int = 240):
        self.lock = threading.Lock()
        self.last: dict = {}
        self.history: deque = deque(maxlen=history_points)
        self.recent_events: deque = deque(maxlen=80)
        self.label_counts: Counter = Counter()
        self.one_tick_last_changes = 0
        self.unit_label_counts: Counter = Counter()
        self.rate_window: deque = deque(maxlen=600)  # recv ns of recent msgs
        self.prev_quote: Optional[tuple] = None      # (bid, ask, last)
        self.anomaly_counts: Counter = Counter()
        self.msg_count = 0
        self.last_update_ts: float = 0.0

    def update(self, row: dict, label: Optional[str], direction: int,
               derived: dict) -> None:
        recv = row.get("local_receive_time_ns") or int(time.time_ns())
        with self.lock:
            self.msg_count += 1
            self.last = {
                "ts_recv_ns": recv,
                "trading_day": row.get("trading_day"),
                "update_time": row.get("update_time"),
                "update_millisec": row.get("update_millisec"),
                "last_price": row.get("last_price"),
                "bid_price1": row.get("bid_price1"),
                "ask_price1": row.get("ask_price1"),
                "bid_volume1": row.get("bid_volume1"),
                "ask_volume1": row.get("ask_volume1"),
                "volume": row.get("volume"),
                "open_interest": row.get("open_interest"),
                "turnover": row.get("turnover"),
                "spread": derived.get("spread"),
                "mid_price": derived.get("mid_price"),
                "obi1": derived.get("obi1"),
                "microprice": derived.get("microprice"),
            }
            self.history.append({
                "t": recv,
                "last": row.get("last_price"),
                "mid": derived.get("mid_price"),
                "obi": derived.get("obi1"),
                "spread": derived.get("spread_ticks"),
            })
            self.rate_window.append(recv)
            self.last_update_ts = time.time()
            if label:
                self.label_counts[label] += 1
                dl = derived.get("dl_ticks")
                if dl is not None and abs(abs(dl) - 1.0) < 1e-9:
                    self.one_tick_last_changes += 1
                    self.unit_label_counts[label] += 1
                if label != "NO_MOVE":
                    self.recent_events.appendleft({
                        "ts": recv,
                        "update_time": row.get("update_time"),
                        "label": label,
                        "direction": direction,
                        "db": derived.get("db_ticks"),
                        "da": derived.get("da_ticks"),
                        "dl": dl,
                    })

    def note_anomaly(self, kind: str) -> None:
        with self.lock:
            self.anomaly_counts[kind] += 1

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time_ns()
            rate = 0.0
            if self.rate_window:
                recent = [t for t in self.rate_window if now - t < 60_000_000_000]
                rate = len(recent)
            counts = dict(self.label_counts)
            one_tick = self.one_tick_last_changes
            unit_counts = self.unit_label_counts
            bounce = (unit_counts.get("HIGH_CONFIDENCE_BOUNCE_UP", 0)
                      + unit_counts.get("HIGH_CONFIDENCE_BOUNCE_DOWN", 0)
                      + unit_counts.get("LIKELY_BOUNCE_UP", 0)
                      + unit_counts.get("LIKELY_BOUNCE_DOWN", 0))
            genuine = (unit_counts.get("GENUINE_QUOTE_MOVE_UP", 0)
                       + unit_counts.get("GENUINE_QUOTE_MOVE_DOWN", 0))
            return {
                "last": self.last,
                "history": list(self.history),
                "recent_events": list(self.recent_events),
                "label_counts": {k: counts.get(k, 0) for k in _LABEL_KEYS},
                "one_tick_last_changes": one_tick,
                "bounce_ratio": (bounce / one_tick) if one_tick else None,
                "genuine_move_ratio": (genuine / one_tick) if one_tick else None,
                "msg_count": self.msg_count,
                "rate_per_min": round(rate, 2),
                "anomalies": dict(self.anomaly_counts),
                "stale_s": round(time.time() - self.last_update_ts, 1)
                if self.last_update_ts else None,
            }


class LiveState:
    """Process-wide live state for the dashboard."""

    def __init__(self, history_points: int = 240):
        self.lock = threading.Lock()
        self.instruments: dict[str, InstrumentLiveState] = {}
        self.history_points = history_points
        self.connection_status = "starting"
        self.connection_detail = ""
        self.batch_id: Optional[str] = None
        self.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.login_user_masked: Optional[str] = None
        self.ctp_trading_day: Optional[str] = None
        self.api_version: Optional[str] = None
        self.subscriptions: dict[str, str] = {}

    def instrument(self, instrument_id: str) -> InstrumentLiveState:
        with self.lock:
            if instrument_id not in self.instruments:
                self.instruments[instrument_id] = InstrumentLiveState(
                    self.history_points)
            return self.instruments[instrument_id]

    def set_connection(self, status: str, detail: str = "") -> None:
        with self.lock:
            self.connection_status = status
            self.connection_detail = detail

    def clear_subscriptions(self) -> None:
        with self.lock:
            self.subscriptions.clear()

    def note_subscription(self, instrument_id: str, accepted: bool,
                          detail: str = "") -> None:
        with self.lock:
            self.subscriptions[instrument_id] = ("accepted" if accepted
                                                 else (detail or "failed"))

    def to_json(self) -> dict:
        with self.lock:
            return {
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "connection": {"status": self.connection_status,
                               "detail": self.connection_detail},
                "batch_id": self.batch_id,
                "started_at": self.started_at,
                "ctp_trading_day": self.ctp_trading_day,
                "api_version": self.api_version,
                "login_user": self.login_user_masked,
                "subscriptions": dict(self.subscriptions),
                "instruments": {
                    k: v.snapshot() for k, v in self.instruments.items()},
            }


class LiveClassifier:
    """Per-instrument pairwise classifier used on the live stream."""

    def __init__(self, tick_size: float, **kwargs):
        self.clf = JumpEventClassifier(tick_size=tick_size, **kwargs)
        self._prev: dict[str, Optional[tuple]] = {}

    def classify(self, instrument_id: str, bid: Optional[float],
                 ask: Optional[float], last: Optional[float]):
        """Return (label, direction, derived_dict). None label on first row."""
        prev = self._prev.get(instrument_id)
        self._prev[instrument_id] = (bid, ask, last)
        if prev is None:
            return None, 0, {}
        pb, pa, pl = prev
        res = self.clf.classify_pair(pb, pa, pl, bid, ask, last)
        derived = {
            "db_ticks": res.db_ticks, "da_ticks": res.da_ticks,
            "dl_ticks": res.dl_ticks, "dm_ticks": res.dm_ticks,
        }
        return res.label.value, res.direction, derived


# ----------------------------------------------------------------------
# Standalone JSONL tailing
# ----------------------------------------------------------------------

def live_feed_path(live_dir: Path, trading_day: str) -> Path:
    return Path(live_dir) / f"live_{trading_day.replace('-', '')}.jsonl"


class JsonlTailSource:
    """Feeds a LiveState from the collector's live JSONL file.

    Used by `app dashboard` when running standalone (no collector process).
    """

    def __init__(self, live_dir: Path, state: LiveState,
                 history_points: int = 240):
        self.live_dir = Path(live_dir)
        self.state = state
        self._file = None
        self._pos = 0
        self._classifiers: dict[str, LiveClassifier] = {}
        self._tick_sizes: dict[str, float] = {}

    def set_tick_sizes(self, mapping: dict[str, float]) -> None:
        self._tick_sizes = dict(mapping)

    def poll(self) -> int:
        """Read new lines; returns number of rows ingested."""
        path = _latest_live_file(self.live_dir)
        if path is None:
            return 0
        if self._file is None or getattr(self, "_path", None) != path:
            if self._file:
                self._file.close()
            self._file = open(path, "r", encoding="utf-8")
            self._path = path
            self._pos = 0
            self._file.seek(0, 2)  # start at end for tailing
        n = 0
        for line in self._file:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            inst = rec.get("instrument")
            if not inst:
                continue
            ts = LiveState.instrument(self.state, inst)
            with ts.lock:
                ts.msg_count += 1
                ts.last = rec.get("quote", {})
                ts.history.append({
                    "t": rec.get("ts_recv_ns"),
                    "last": rec.get("quote", {}).get("last_price"),
                    "mid": rec.get("derived", {}).get("mid_price"),
                    "obi": rec.get("derived", {}).get("obi1"),
                    "spread": rec.get("derived", {}).get("spread_ticks"),
                })
                ts.rate_window.append(rec.get("ts_recv_ns") or time.time_ns())
                ts.last_update_ts = time.time()
                lab = rec.get("label")
                if lab:
                    ts.label_counts[lab] += 1
                    if rec.get("derived", {}).get("dl_ticks") is not None:
                        dl = rec["derived"]["dl_ticks"]
                        if abs(abs(dl) - 1.0) < 1e-9:
                            ts.one_tick_last_changes += 1
                            ts.unit_label_counts[lab] += 1
                    if lab != "NO_MOVE":
                        ts.recent_events.appendleft({
                            "ts": rec.get("ts_recv_ns"),
                            "update_time": rec.get("quote", {}).get("update_time"),
                            "label": lab,
                            "direction": rec.get("direction", 0),
                            "db": rec.get("derived", {}).get("db_ticks"),
                            "da": rec.get("derived", {}).get("da_ticks"),
                            "dl": rec.get("derived", {}).get("dl_ticks"),
                        })
            n += 1
        return n


def _latest_live_file(live_dir: Path) -> Optional[Path]:
    if not live_dir.exists():
        return None
    files = sorted(live_dir.glob("live_*.jsonl"))
    return files[-1] if files else None
