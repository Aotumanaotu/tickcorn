"""Event count statistics and headline ratios.

Key definitions (paper-facing):
    one_tick_last_change  : |delta LastPrice| == 1 tick between snapshots
    bounce_ratio          = (HIGH_CONFIDENCE_BOUNCE + LIKELY_BOUNCE)
                            / one_tick_last_change
    genuine_move_ratio    = GENUINE_QUOTE_MOVE / one_tick_last_change
    ambiguous_ratio       = AMBIGUOUS / one_tick_last_change

These ratios answer the headline question: "of the observed 1-tick
LastPrice jumps, what fraction did NOT coincide with a genuine best
bid/ask relocation?"
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from app.common.timeutils import intraday_bucket

STAT_COLUMNS = [
    "total_snapshots",
    "transitions",
    "last_price_change_count",
    "one_tick_last_change_count",
    "quote_change_count",
    "genuine_quote_move_up_count",
    "genuine_quote_move_down_count",
    "high_confidence_bounce_count",
    "likely_bounce_count",
    "ambiguous_count",
    "no_move_count",
    "bounce_ratio",
    "genuine_move_ratio",
    "ambiguous_ratio",
    "high_bounce_share",
    "mean_spread_ticks",
    "mean_obi1",
    "snapshot_rate_per_min",
]


def add_intraday_bucket(df: pd.DataFrame, config) -> pd.DataFrame:
    """Add 'intraday_bucket' from exchange update_time."""
    df = df.copy()
    df["intraday_bucket"] = [
        intraday_bucket(config, t) for t in df["update_time"].fillna("00:00:00")
    ]
    return df


def _count_block(sub: pd.DataFrame) -> dict:
    n = len(sub)
    if "label" not in sub.columns or n == 0:
        return {c: 0 for c in STAT_COLUMNS}

    lab = sub["label"]
    valid = lab.notna()
    dl = pd.to_numeric(sub.get("dl_ticks"), errors="coerce")
    db = pd.to_numeric(sub.get("db_ticks"), errors="coerce")
    da = pd.to_numeric(sub.get("da_ticks"), errors="coerce")

    transitions = int(valid.sum())
    last_change = int((dl.abs() > 1e-9).sum())
    one_tick = int((dl.abs() - 1.0).abs().le(1e-9).sum())
    quote_change = int(((db.abs() > 1e-9) | (da.abs() > 1e-9)).sum())

    fam = sub["family"]
    genuine_up = int((lab == "GENUINE_QUOTE_MOVE_UP").sum())
    genuine_down = int((lab == "GENUINE_QUOTE_MOVE_DOWN").sum())
    high_b = int((fam == "HIGH_CONFIDENCE_BOUNCE").sum())
    likely_b = int((fam == "LIKELY_BOUNCE").sum())
    ambiguous = int((lab == "AMBIGUOUS").sum())
    no_move = int((lab == "NO_MOVE").sum())

    bounce_events = high_b + likely_b
    denom = one_tick if one_tick > 0 else np.nan
    ts_span_min = np.nan
    if "exchange_ts_ns" in sub.columns and n > 1:
        span = (sub["exchange_ts_ns"].iloc[-1] - sub["exchange_ts_ns"].iloc[0]) / 60e9
        if span > 0:
            ts_span_min = span

    return {
        "total_snapshots": n,
        "transitions": transitions,
        "last_price_change_count": last_change,
        "one_tick_last_change_count": one_tick,
        "quote_change_count": quote_change,
        "genuine_quote_move_up_count": genuine_up,
        "genuine_quote_move_down_count": genuine_down,
        "high_confidence_bounce_count": high_b,
        "likely_bounce_count": likely_b,
        "ambiguous_count": ambiguous,
        "no_move_count": no_move,
        "bounce_ratio": bounce_events / denom if one_tick else np.nan,
        "genuine_move_ratio": (genuine_up + genuine_down) / denom if one_tick else np.nan,
        "ambiguous_ratio": ambiguous / denom if one_tick else np.nan,
        "high_bounce_share": high_b / bounce_events if bounce_events else np.nan,
        "mean_spread_ticks": (sub["spread_ticks"].mean()
                              if "spread_ticks" in sub else np.nan),
        "mean_obi1": sub["obi1"].mean() if "obi1" in sub else np.nan,
        "snapshot_rate_per_min": n / ts_span_min if ts_span_min else np.nan,
    }


def event_statistics(df: pd.DataFrame, instrument_id: str, trading_day: str,
                     config, group_by: str = "session"
                     ) -> pd.DataFrame:
    """Per-group event statistics.

    group_by: 'session' (NIGHT/DAY/...), 'bucket' (intraday open/close
    windows), 'none' (whole day).
    """
    if "intraday_bucket" not in df.columns:
        df = add_intraday_bucket(df, config)

    if group_by == "none":
        groups = [("ALL", df)]
    elif group_by == "session":
        groups = [(s, sub) for s, sub in df.groupby("trading_session", sort=True)]
    else:
        groups = [(s, sub) for s, sub in df.groupby("intraday_bucket", sort=True)]

    rows = []
    for name, sub in groups:
        if len(sub) == 0:
            continue
        rec = {
            "instrument_id": instrument_id,
            "trading_day": trading_day,
            "group": name,
        }
        rec.update(_count_block(sub))
        rows.append(rec)
    return pd.DataFrame(rows, columns=["instrument_id", "trading_day", "group"]
                                   + STAT_COLUMNS)


def daily_summary(df: pd.DataFrame, instrument_id: str, trading_day: str,
                  config) -> dict:
    """Headline numbers for the report (whole day)."""
    return _count_block(df) | {
        "instrument_id": instrument_id,
        "trading_day": trading_day,
    }


def time_binned_stats(df: pd.DataFrame, bin_minutes: int) -> pd.DataFrame:
    """Stats per intraday time bin (for intraday pattern figures)."""
    df = df.copy()
    if "minute_of_day" not in df:
        raise ValueError("minute_of_day required; run add_ts_and_minute first")
    df["time_bin"] = (df["minute_of_day"] // bin_minutes) * bin_minutes
    rows = []
    for b, sub in df.groupby("time_bin"):
        rec = {"time_bin": int(b)}
        rec.update(_count_block(sub))
        rows.append(rec)
    out = pd.DataFrame(rows)
    if len(out):
        out["time_hhmm"] = out["time_bin"].apply(
            lambda m: f"{int(m)//60:02d}:{int(m)%60:02d}")
    return out.sort_values("time_bin").reset_index(drop=True)
