"""Canonical enums and constants shared across modules.

Event label semantics (see classifier for the decision tree):

    NO_MOVE                    -- nothing relevant changed
    HIGH_CONFIDENCE_BOUNCE_*   -- quotes unchanged, LastPrice toggled Bid<->Ask
    LIKELY_BOUNCE_*            -- +-1 tick last move, mid ~unchanged, no
                                  same-direction whole-quote move; snapshot
                                  data insufficient for high confidence
    GENUINE_QUOTE_MOVE_*       -- best bid & ask moved together (or mid moved
                                  >= 1 tick in loose mode)
    AMBIGUOUS                  -- conservative bucket (complex / spread change /
                                  out-of-quote prints / invalid quotes ...)
"""

from __future__ import annotations

from enum import Enum


class EventLabel(str, Enum):
    """Fine-grained event label for a snapshot transition (t-1 -> t)."""

    NO_MOVE = "NO_MOVE"
    HIGH_CONFIDENCE_BOUNCE_UP = "HIGH_CONFIDENCE_BOUNCE_UP"
    HIGH_CONFIDENCE_BOUNCE_DOWN = "HIGH_CONFIDENCE_BOUNCE_DOWN"
    LIKELY_BOUNCE_UP = "LIKELY_BOUNCE_UP"
    LIKELY_BOUNCE_DOWN = "LIKELY_BOUNCE_DOWN"
    GENUINE_QUOTE_MOVE_UP = "GENUINE_QUOTE_MOVE_UP"
    GENUINE_QUOTE_MOVE_DOWN = "GENUINE_QUOTE_MOVE_DOWN"
    AMBIGUOUS = "AMBIGUOUS"


class EventFamily(str, Enum):
    """Coarse event family used in headline statistics."""

    NO_MOVE = "NO_MOVE"
    HIGH_CONFIDENCE_BOUNCE = "HIGH_CONFIDENCE_BOUNCE"
    LIKELY_BOUNCE = "LIKELY_BOUNCE"
    GENUINE_QUOTE_MOVE = "GENUINE_QUOTE_MOVE"
    AMBIGUOUS = "AMBIGUOUS"


class State5(str, Enum):
    """Canonical 5-state markov chain states (ambiguous excluded)."""

    NO_MOVE = "NO_MOVE"
    BOUNCE_UP = "BOUNCE_UP"
    BOUNCE_DOWN = "BOUNCE_DOWN"
    QUOTE_UP = "QUOTE_UP"
    QUOTE_DOWN = "QUOTE_DOWN"


class AmbiguousReason(str, Enum):
    """Why an event was conservatively labelled AMBIGUOUS."""

    QUOTE_INVALID = "QUOTE_INVALID"
    NON_TICK_ALIGNED = "NON_TICK_ALIGNED"
    LAST_OUTSIDE_UNCHANGED_QUOTE = "LAST_OUTSIDE_UNCHANGED_QUOTE"
    INSIDE_SPREAD_NON_UNIT_MOVE = "INSIDE_SPREAD_NON_UNIT_MOVE"
    ASYMMETRIC_QUOTE_MOVE = "ASYMMETRIC_QUOTE_MOVE"
    MID_MOVE_WITH_COMPLEX_QUOTE = "MID_MOVE_WITH_COMPLEX_QUOTE"
    LAST_OUTSIDE_QUOTE = "LAST_OUTSIDE_QUOTE"
    COMPLEX_QUOTE_CHANGE = "COMPLEX_QUOTE_CHANGE"


class TradingSession(str, Enum):
    NIGHT = "NIGHT"
    DAY = "DAY"


class IntradayBucket(str, Enum):
    """Buckets for intraday pattern statistics."""

    NIGHT_OPEN = "NIGHT_OPEN_30M"
    NIGHT_MIDDLE = "NIGHT_MIDDLE"
    NIGHT_CLOSE = "NIGHT_CLOSE_30M"
    DAY_OPEN = "DAY_OPEN_30M"
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    DAY_CLOSE = "DAY_CLOSE_30M"
    OUT_OF_SESSION = "OUT_OF_SESSION"


# EventLabel -> (EventFamily, State5 or None, direction)
LABEL_META: dict[EventLabel, tuple[EventFamily, State5 | None, int]] = {
    EventLabel.NO_MOVE: (EventFamily.NO_MOVE, State5.NO_MOVE, 0),
    EventLabel.HIGH_CONFIDENCE_BOUNCE_UP: (
        EventFamily.HIGH_CONFIDENCE_BOUNCE, State5.BOUNCE_UP, 1),
    EventLabel.HIGH_CONFIDENCE_BOUNCE_DOWN: (
        EventFamily.HIGH_CONFIDENCE_BOUNCE, State5.BOUNCE_DOWN, -1),
    EventLabel.LIKELY_BOUNCE_UP: (
        EventFamily.LIKELY_BOUNCE, State5.BOUNCE_UP, 1),
    EventLabel.LIKELY_BOUNCE_DOWN: (
        EventFamily.LIKELY_BOUNCE, State5.BOUNCE_DOWN, -1),
    EventLabel.GENUINE_QUOTE_MOVE_UP: (
        EventFamily.GENUINE_QUOTE_MOVE, State5.QUOTE_UP, 1),
    EventLabel.GENUINE_QUOTE_MOVE_DOWN: (
        EventFamily.GENUINE_QUOTE_MOVE, State5.QUOTE_DOWN, -1),
    EventLabel.AMBIGUOUS: (EventFamily.AMBIGUOUS, None, 0),
}

# 8-state fine chain: same as EventLabel minus NO_MOVE grouping; used for
# the detailed transition matrix.
STATE8_ORDER: list[str] = [
    "NO_MOVE",
    "HIGH_BOUNCE_UP",
    "HIGH_BOUNCE_DOWN",
    "LIKELY_BOUNCE_UP",
    "LIKELY_BOUNCE_DOWN",
    "QUOTE_UP",
    "QUOTE_DOWN",
    "AMBIGUOUS",
]

STATE5_ORDER: list[str] = [s.value for s in State5]

# CTP "no value" sentinel for double fields (DBL_MAX).
CTP_DOUBLE_SENTINEL = 1.7976931348623157e308
# Anything >= this magnitude is treated as invalid/absent price.
PRICE_SENTINEL_THRESHOLD = 1e300
