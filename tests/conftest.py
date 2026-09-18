"""Shared test fixtures and synthetic data generation."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from app.common.config import AppConfig, load_config  # noqa: E402


@pytest.fixture(scope="session")
def config() -> AppConfig:
    return load_config(config_dir=ROOT / "config")


# ---------------------------------------------------------------------
# Synthetic snapshot stream
# ---------------------------------------------------------------------

def make_row(seq: int, ts_ns: int, bid: float, ask: float, last: float,
             trading_day: str = "2026-09-18", update_time: str = "09:30:00",
             update_millisec: int = 0, action_day: str = "2026-09-18",
             bv1: int = 50, av1: int = 50, volume: int = 1000,
             turnover: float = 2_300_000.0, oi: float = 200000.0,
             instrument: str = "C2701") -> dict:
    return {
        "sequence_id": seq,
        "local_receive_time_ns": ts_ns + 500_000,
        "batch_id": "batch-test000001",
        "trading_session": "DAY",
        "raw_source": "synthetic",
        "trading_day": trading_day,
        "action_day": action_day,
        "instrument_id": instrument,
        "exchange_id": "DCE",
        "exchange_inst_id": instrument,
        "update_time": update_time,
        "update_millisec": update_millisec,
        "last_price": last,
        "pre_settlement_price": 2298.0,
        "pre_close_price": 2297.0,
        "pre_open_interest": 199000.0,
        "open_price": 2299.0,
        "highest_price": max(2310.0, last),
        "lowest_price": min(2290.0, last),
        "volume": volume,
        "turnover": turnover,
        "open_interest": oi,
        "close_price": 1.7976931348623157e308,
        "settlement_price": 1.7976931348623157e308,
        "upper_limit_price": 2413.0,
        "lower_limit_price": 2183.0,
        "pre_delta": 0.0,
        "curr_delta": 0.0,
        "average_price": turnover / max(volume, 1),
        "banding_upper_price": 1.7976931348623157e308,
        "banding_lower_price": 1.7976931348623157e308,
        "bid_price1": bid, "bid_volume1": bv1,
        "ask_price1": ask, "ask_volume1": av1,
        "bid_price2": bid - 1, "bid_volume2": 60,
        "ask_price2": ask + 1, "ask_volume2": 60,
        "bid_price3": bid - 2, "bid_volume3": 70,
        "ask_price3": ask + 2, "ask_volume3": 70,
        "bid_price4": bid - 3, "bid_volume4": 80,
        "ask_price4": ask + 3, "ask_volume4": 80,
        "bid_price5": bid - 4, "bid_volume5": 90,
        "ask_price5": ask + 4, "ask_volume5": 90,
    }


def build_synthetic_day(instrument: str = "C2701",
                        trading_day: str = "2026-09-18",
                        n_base: int = 1200, seed: int = 7) -> pd.DataFrame:
    """Deterministic synthetic snapshot day.

    Structure per 'episode' of 10 snapshots:
        * 3 NO_MOVE (volume/oi change only)
        * 3 high-confidence bounce toggles (quote fixed, last bid<->ask)
        * 1 genuine up (whole quote +1 tick)
        * 2 bounces at the new quote
        * 1 genuine down
    Some noise rows ask-spread widening / ambiguous transitions are injected.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    seq = 0
    ts = 1_758_000_000_000_000_000  # arbitrary epoch ns (2025-09)
    # morning session timestamps: 09:30:00 base, 500ms steps
    base_ms = 9 * 3600 + 30 * 60
    ms_counter = 0

    bid, ask, last = 2300.0, 2301.0, 2300.0
    volume = 10_000
    oi = 250_000.0

    def emit(bid, ask, last, dv=0):
        nonlocal seq, ms_counter, volume, oi
        ms_counter += 500
        hh = base_ms * 1000 + ms_counter
            # hh is in ms-of-day
        ut = f"{hh // 3600000:02d}:{(hh // 60000) % 60:02d}:{(hh // 1000) % 60:02d}"
        volume += max(0, dv)
        oi += rng.integers(-20, 20)
        bv = int(rng.integers(20, 120))
        av = int(rng.integers(20, 120))
        rows.append(make_row(
            seq=seq, ts_ns=ts + ms_counter * 1_000_000, bid=bid, ask=ask,
            last=last, trading_day=trading_day, update_time=ut,
            update_millisec=ms_counter % 1000, bv1=bv, av1=av, volume=volume,
            turnover=volume * 2300.5, oi=float(oi), instrument=instrument))
        seq += 1

    episodes = max(1, n_base // 10)
    for ep in range(episodes):
        # NO_MOVE x3
        for _ in range(3):
            emit(bid, ask, last, dv=int(rng.integers(0, 5)))
        # bounce toggles x3 at fixed quote
        last = ask
        emit(bid, ask, last, dv=2)
        last = bid
        emit(bid, ask, last, dv=2)
        last = ask
        emit(bid, ask, last, dv=2)
        # genuine up
        bid, ask, last = bid + 1, ask + 1, ask + 1
        emit(bid, ask, last, dv=4)
        # bounces at new quote
        last = bid
        emit(bid, ask, last, dv=1)
        last = ask
        emit(bid, ask, last, dv=1)
        # genuine down
        bid, ask, last = bid - 1, ask - 1, bid - 1
        emit(bid, ask, last, dv=4)
        # occasional wide/ambiguous transition (1 in 6 episodes)
        if ep % 6 == 5:
            emit(bid, ask + 2, last + 1, dv=3)     # asymmetric -> AMBIGUOUS
            emit(bid, ask, last, dv=0)             # back to normal (AMBIGUOUS again)

    df = pd.DataFrame(rows)
    return df
