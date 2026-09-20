"""Realtime per-instrument metrics and regime classification.

Subscribes to the EventBus inline (``queued=False``) and keeps only cheap
in-memory state per instrument: a rolling window of microstructure
transitions tagged by their one-tick status, the latest quote, a message
rate window and a spread sampler. Expensive ratios are computed lazily in
:snapshot:. After every micro event a throttled ``analysis`` envelope is
republished on the bus (>= 1s per instrument, or immediately on a label
change), so websocket subscribers and REST clients see the same numbers.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from statistics import fmean
from typing import Any, Optional

from app.common.logging import get_logger
from app.core.events import EVENT_ANALYSIS, EVENT_MICRO, EVENT_QUOTE, envelope
from app.ingest.bus import EventBus

logger = get_logger("ws.realtime")

_LABEL_WINDOW = 200
_RATE_WINDOW = 600
_SPREAD_WINDOW = 100
_RATE_WINDOW_S_NS = 60_000_000_000
_PUBLISH_MIN_INTERVAL_S = 1.0
_MIN_ONE_TICK = 30
_EPS = 1e-9

_BOUNCE_LABELS = frozenset({
    "HIGH_CONFIDENCE_BOUNCE_UP", "HIGH_CONFIDENCE_BOUNCE_DOWN",
    "LIKELY_BOUNCE_UP", "LIKELY_BOUNCE_DOWN",
})
_GENUINE_LABELS = frozenset({
    "GENUINE_QUOTE_MOVE_UP", "GENUINE_QUOTE_MOVE_DOWN",
})


def _is_one_tick(dl_ticks: Any) -> bool:
    """Legacy live-collector criterion: |dl_ticks| == 1 exactly."""
    try:
        return abs(abs(float(dl_ticks)) - 1.0) < _EPS
    except (TypeError, ValueError):
        return False


class _InstrumentState:
    """Rolling per-instrument state updated inline on the bus."""

    __slots__ = ("transitions", "last_quote", "rate_window", "spreads",
                 "msg_count", "last_event_ns", "last_publish_monotonic",
                 "last_published_label")

    def __init__(self) -> None:
        self.transitions: deque[tuple[str, bool]] = deque(maxlen=_LABEL_WINDOW)
        self.last_quote: Optional[dict] = None
        self.rate_window: deque[int] = deque(maxlen=_RATE_WINDOW)
        self.spreads: deque[float] = deque(maxlen=_SPREAD_WINDOW)
        self.msg_count = 0
        self.last_event_ns: Optional[int] = None
        self.last_publish_monotonic: float = 0.0
        self.last_published_label: Optional[str] = None


class RealtimeMetrics:
    """Maintains live microstructure metrics for every streamed instrument."""

    def __init__(self) -> None:
        self._states: dict[str, _InstrumentState] = {}
        self._bus: Optional[EventBus] = None
        self._unsub: Any = None

    # ------------------------------------------------------------------
    async def start(self, bus: EventBus) -> None:
        """Subscribe inline to quote + micro envelopes."""
        self._bus = bus
        self._unsub = await bus.subscribe(
            self._on_event, event_types={EVENT_QUOTE, EVENT_MICRO},
            queued=False)
        logger.info("RealtimeMetrics started")

    async def stop(self) -> None:
        """Unsubscribe from the bus (idempotent)."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._bus = None

    def _state(self, instrument_id: str) -> _InstrumentState:
        state = self._states.get(instrument_id)
        if state is None:
            state = _InstrumentState()
            self._states[instrument_id] = state
        return state

    # ------------------------------------------------------------------
    async def _on_event(self, event: dict) -> None:
        instrument_id = event.get("instrument_id")
        if not instrument_id:
            return
        event_type = event.get("type")
        if event_type == EVENT_QUOTE:
            self._on_quote(str(instrument_id), event)
        elif event_type == EVENT_MICRO:
            await self._on_micro(str(instrument_id), event)

    def _on_quote(self, instrument_id: str, event: dict) -> None:
        state = self._state(instrument_id)
        state.msg_count += 1
        state.last_quote = event.get("data") or {}
        local_ns = event.get("local_ts_ns")
        if isinstance(local_ns, int):
            state.rate_window.append(local_ns)
        spread_ticks = (state.last_quote or {}).get("spread_ticks")
        if isinstance(spread_ticks, (int, float)):
            state.spreads.append(float(spread_ticks))
        state.last_event_ns = local_ns or event.get("exchange_ts_ns")

    async def _on_micro(self, instrument_id: str, event: dict) -> None:
        data = event.get("data") or {}
        label = data.get("label")
        if not label:
            return
        state = self._state(instrument_id)
        one_tick = _is_one_tick(data.get("dl_ticks"))
        state.transitions.append((label, one_tick))
        state.last_event_ns = (event.get("exchange_ts_ns")
                               or event.get("local_ts_ns"))

        now = time.monotonic()
        if (now - state.last_publish_monotonic >= _PUBLISH_MIN_INTERVAL_S
                or label != state.last_published_label):
            state.last_publish_monotonic = now
            state.last_published_label = label
            await self._publish(instrument_id, label)

    async def _publish(self, instrument_id: str, label: str) -> None:
        if self._bus is None:
            return
        metrics = self._metrics(self._state(instrument_id))
        await self._bus.publish(envelope(
            EVENT_ANALYSIS,
            instrument_id=instrument_id,
            source="realtime",
            data={"metrics": metrics,
                  "regime": self.classify_regime(metrics),
                  "label": label},
        ))

    # ------------------------------------------------------------------
    def _metrics(self, state: _InstrumentState) -> dict:
        one_tick = [lab for lab, is_unit in state.transitions if is_unit]
        one_tick_count = len(one_tick)
        counts = Counter(one_tick)
        bounce = sum(counts.get(lab, 0) for lab in _BOUNCE_LABELS)
        genuine = sum(counts.get(lab, 0) for lab in _GENUINE_LABELS)

        now_ns = time.time_ns()
        rate = sum(1 for ts in state.rate_window
                   if now_ns - ts < _RATE_WINDOW_S_NS)
        mean_spread = (fmean(state.spreads) if state.spreads else None)
        return {
            "bounce_ratio": (bounce / one_tick_count) if one_tick_count else None,
            "genuine_move_ratio": (genuine / one_tick_count) if one_tick_count else None,
            "one_tick_last_changes": one_tick_count,
            "message_rate_per_min": rate,
            "mean_spread_ticks": mean_spread,
            "msg_count": state.msg_count,
            "last_event_ns": state.last_event_ns,
        }

    @staticmethod
    def classify_regime(snapshot: dict) -> str:
        """Map a metrics snapshot to a coarse market regime label."""
        one_tick = snapshot.get("one_tick_last_changes") or 0
        if one_tick < _MIN_ONE_TICK:
            return "INSUFFICIENT_DATA"
        bounce_ratio = snapshot.get("bounce_ratio")
        genuine_ratio = snapshot.get("genuine_move_ratio")
        bounce_ratio = bounce_ratio if bounce_ratio is not None else 0.0
        genuine_ratio = genuine_ratio if genuine_ratio is not None else 0.0
        if bounce_ratio >= 0.65 and genuine_ratio < 0.2:
            return "HIGH_BOUNCE"
        if genuine_ratio >= 0.3:
            return "DIRECTIONAL"
        return "MIXED"

    # ------------------------------------------------------------------
    def snapshot(self, instrument_id: str) -> Optional[dict]:
        """Metrics snapshot for one instrument (None when never seen)."""
        state = self._states.get(instrument_id)
        if state is None:
            return None
        return self._metrics(state)

    def snapshots(self) -> dict[str, dict]:
        """Metrics snapshots for every tracked instrument."""
        return {inst: self._metrics(state)
                for inst, state in self._states.items()}
