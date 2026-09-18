"""High-speed historical replay from raw/processed parquet.

Yields snapshots in deterministic order (exchange timestamp, then local
sequence). Supports pacing:
    fast     -- as fast as possible (analysis default)
    realtime -- sleep to reproduce exchange-time intervals (demo / dashboard)

Also feeds a LiveState for the dashboard, so `app replay --dashboard`
reproduces the real-time panel from historical data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Union

import pandas as pd

from app.common.exceptions import ReplayError
from app.common.logging import get_logger
from app.storage.repo import StorageRepository

logger = get_logger("replay")

SnapshotConsumer = Callable[[pd.Series], None]


@dataclass
class ReplayStats:
    rows: int = 0
    gaps: int = 0
    duplicates_dropped: int = 0
    invalid_dropped: int = 0
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class Replayer:
    def __init__(self, repo: StorageRepository):
        self.repo = repo

    def load(self, instrument_id: str, days: list[str]) -> pd.DataFrame:
        result = self.repo.load_clean_range(instrument_id, days)
        self._last_stats = ReplayStats(
            rows=len(result.df),
            duplicates_dropped=result.dropped_duplicates,
            invalid_dropped=result.dropped_invalid)
        if result.df.empty:
            raise ReplayError(
                f"no data to replay for {instrument_id} on {days}. "
                f"Collect data first (app collect) or check instrument/day.")
        return result.df

    def run(self, df: pd.DataFrame, consumer: SnapshotConsumer,
            speed: str = "fast", gap_threshold_s: float = 30.0,
            on_gap: Optional[Callable[[int, int], None]] = None) -> ReplayStats:
        """Iterate snapshots, calling consumer(row) for each.

        speed: 'fast' | 'realtime'
        """
        if speed not in ("fast", "realtime"):
            raise ReplayError(f"unknown speed {speed!r}")
        stats = getattr(self, "_last_stats", ReplayStats())
        stats.rows = len(df)

        ts = df["exchange_ts_ns"].to_numpy()
        t0 = time.perf_counter()
        prev_ts: Optional[int] = None
        for i in range(len(df)):
            row = df.iloc[i]
            if speed == "realtime" and prev_ts is not None:
                dt_ns = int(ts[i]) - int(prev_ts)
                if 0 < dt_ns < gap_threshold_s * 1e9:
                    time.sleep(dt_ns / 1e9)
            if (prev_ts is not None
                    and int(ts[i]) - int(prev_ts) > gap_threshold_s * 1e9):
                stats.gaps += 1
                if on_gap is not None:
                    on_gap(int(prev_ts), int(ts[i]))
            consumer(row)
            prev_ts = int(ts[i])
        stats.elapsed_s = time.perf_counter() - t0
        self._last_stats = stats
        return stats

    def replay_days(self, instrument_id: str, days: list[str],
                    consumer: SnapshotConsumer, speed: str = "fast",
                    **kwargs) -> ReplayStats:
        df = self.load(instrument_id, days)
        return self.run(df, consumer, speed=speed, **kwargs)
