"""Time utilities: exchange timestamps, sessions, intraday buckets.

All "exchange time" values are Beijing wall-clock time (UTC+8, no DST).
We compute nanosecond timestamps by attaching a fixed +08:00 offset, so the
values are deterministic and never depend on the host timezone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.common.config import AppConfig, TimeRange

BEIJING_TZ = timezone(timedelta(hours=8))


def normalize_ctp_day(day: str) -> str:
    """Normalize partition keys while retaining original CTP fields in raw rows."""
    fmt = "%Y%m%d" if len(day) == 8 and day.isdigit() else "%Y-%m-%d"
    return datetime.strptime(day, fmt).strftime("%Y-%m-%d")


def exchange_ts_ns(action_day: str, update_time: str, update_millisec: int) -> int:
    """Nanoseconds since epoch for an exchange snapshot timestamp.

    action_day: 'YYYY-MM-DD' (CTP ActionDay, the physical calendar date)
    update_time: 'HH:MM:SS'
    """
    if not action_day or not update_time:
        raise ValueError(f"invalid timestamp parts: {action_day!r} {update_time!r}")
    action_day = normalize_ctp_day(action_day)
    dt = datetime.strptime(f"{action_day} {update_time}", "%Y-%m-%d %H:%M:%S")
    dt = dt.replace(tzinfo=BEIJING_TZ)
    return int(dt.timestamp() * 1_000_000_000) + int(update_millisec) * 1_000_000


def hhmmss_of_ns(ts_ns: int) -> str:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=BEIJING_TZ)
    return dt.strftime("%H:%M:%S")


def local_iso(ts_ns: int) -> str:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=BEIJING_TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@dataclass(frozen=True)
class SessionWindows:
    night: tuple[TimeRange, ...]
    day: tuple[TimeRange, ...]
    open_close_window_min: int


def intraday_bucket(config: AppConfig, hhmmss: str) -> str:
    """Assign an intraday statistics bucket from an exchange time string.

    Buckets: NIGHT_OPEN_30M / NIGHT_MIDDLE / NIGHT_CLOSE_30M
             DAY_OPEN_30M / MORNING / AFTERNOON / DAY_CLOSE_30M
             OUT_OF_SESSION
    """
    minute = int(hhmmss[3:5])
    hour = int(hhmmss[0:2])
    tmin = hour * 60 + minute

    def _range_minutes(r: TimeRange) -> tuple[int, int]:
        h1, m1 = r.start.split(":")
        h2, m2 = r.end.split(":")
        return int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)

    win = config.sessions.open_close_window_min

    night_spans = [_range_minutes(r) for r in config.sessions.night]
    night_end = max((hi for lo, hi in night_spans), default=None)
    for lo, hi in night_spans:
        if lo <= tmin <= hi:
            if tmin < lo + win:
                return "NIGHT_OPEN_30M"
            if hi == night_end and tmin >= hi - win:
                return "NIGHT_CLOSE_30M"
            return "NIGHT_MIDDLE"

    day_spans = sorted(_range_minutes(r) for r in config.sessions.day)
    day_end = max((hi for lo, hi in day_spans), default=None)
    for lo, hi in day_spans:
        if lo <= tmin <= hi:
            if tmin < lo + win:
                return "DAY_OPEN_30M"
            if hi == day_end and tmin >= hi - win:
                return "DAY_CLOSE_30M"
            # 13:30 span is the afternoon; earlier spans are morning.
            return "AFTERNOON" if lo >= 13 * 60 else "MORNING"

    return "OUT_OF_SESSION"


def next_trading_day_iso(day: str) -> str:
    """Calendar next day of a 'YYYY-MM-DD' string (NOT exchange-accurate:
    only used for display/defaults; actual trading days come from CTP)."""
    d = datetime.strptime(day, "%Y-%m-%d")
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


def parse_optional_date(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
