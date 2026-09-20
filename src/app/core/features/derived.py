"""Derived market microstructure variables.

All functions are vectorized over a snapshot dataframe and only use the
CURRENT row (no look-ahead): they are safe to compute at collection time
for the live dashboard and for later prediction features.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.common.constants import PRICE_SENTINEL_THRESHOLD


def clean_prices(df: pd.DataFrame, price_cols: list[str]) -> pd.DataFrame:
    """Convert CTP sentinel values (DBL_MAX) and non-positive prices to NaN."""
    df = df.copy()
    for c in price_cols:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce").astype(float)
            df[c] = s.where((s > 0) & (s < PRICE_SENTINEL_THRESHOLD))
    return df


PRICE_COLUMNS: list[str] = (
    ["last_price", "pre_settlement_price", "pre_close_price", "open_price",
     "highest_price", "lowest_price", "close_price", "settlement_price",
     "upper_limit_price", "lower_limit_price", "average_price",
     "banding_upper_price", "banding_lower_price"]
    + [f"bid_price{i}" for i in range(1, 6)]
    + [f"ask_price{i}" for i in range(1, 6)]
)


def add_basic_quote_features(df: pd.DataFrame, tick_size: float) -> pd.DataFrame:
    """Add spread / mid / OBI / microprice columns (in-place on a copy).

    Requires (already NaN-cleaned): bid_price1, ask_price1, bid_volume1,
    ask_volume1; optional: levels 2-5 for OBI3/OBI5.
    """
    df = df.copy()
    b1 = df["bid_price1"].astype(float)
    a1 = df["ask_price1"].astype(float)
    bv1 = pd.to_numeric(df["bid_volume1"], errors="coerce").astype(float)
    av1 = pd.to_numeric(df["ask_volume1"], errors="coerce").astype(float)

    valid_quote = b1.notna() & a1.notna() & (a1 > b1)

    df["spread"] = (a1 - b1).where(valid_quote)
    df["mid_price"] = ((a1 + b1) / 2.0).where(valid_quote)
    df["spread_ticks"] = (df["spread"] / tick_size).where(valid_quote)

    denom1 = (bv1 + av1).where(bv1.notna() & av1.notna())
    df["obi1"] = ((bv1 - av1) / denom1.replace(0, np.nan)).where(valid_quote)

    # microprice = (ask * bid_vol + bid * ask_vol) / (bid_vol + ask_vol)
    mp_denom = denom1.replace(0, np.nan)
    df["microprice"] = ((a1 * bv1 + b1 * av1) / mp_denom).where(valid_quote)
    df["microprice_dev"] = (df["microprice"] - df["mid_price"]).where(valid_quote)
    # deviation in ticks, signed: >0 means microprice above mid (buy pressure)
    df["microprice_dev_ticks"] = (df["microprice_dev"] / tick_size).where(valid_quote)

    # OBI3 / OBI5 (require deeper levels to be present and non-null)
    for k, name in ((3, "obi3"), (5, "obi5")):
        bid_cols = [f"bid_volume{i}" for i in range(1, k + 1)]
        ask_cols = [f"ask_volume{i}" for i in range(1, k + 1)]
        if not set(bid_cols + ask_cols).issubset(df.columns):
            continue
        bv = sum(pd.to_numeric(df[c], errors="coerce").astype(float)
                 for c in bid_cols)
        av = sum(pd.to_numeric(df[c], errors="coerce").astype(float)
                 for c in ask_cols)
        ok = valid_quote & bv.notna() & av.notna()
        df[name] = ((bv - av) / (bv + av).replace(0, np.nan)).where(ok)

    return df


def add_deltas(df: pd.DataFrame) -> pd.DataFrame:
    """First differences of key fields (in snapshot order)."""
    df = df.copy()
    for col, name in (
        ("last_price", "d_last"),
        ("bid_price1", "d_bid1"),
        ("ask_price1", "d_ask1"),
        ("mid_price", "d_mid"),
        ("microprice", "d_microprice"),
        ("volume", "d_volume"),
        ("turnover", "d_turnover"),
        ("open_interest", "d_open_interest"),
        ("bid_volume1", "d_bid_volume1"),
        ("ask_volume1", "d_ask_volume1"),
    ):
        if col in df.columns:
            df[name] = pd.to_numeric(df[col], errors="coerce").diff()
    return df


def add_ts_and_minute(df: pd.DataFrame) -> pd.DataFrame:
    """Add exchange_ts_ns and minute_of_day from action_day/update_time."""
    df = df.copy()
    if "exchange_ts_ns" not in df.columns:
        from app.common.timeutils import exchange_ts_ns
        df["exchange_ts_ns"] = [
            exchange_ts_ns(ad, ut, int(ms) if ms is not None else 0)
            for ad, ut, ms in zip(df["action_day"], df["update_time"],
                                  df.get("update_millisec", 0))
        ]
    ts = df["exchange_ts_ns"]
    df["minute_of_day"] = ((ts // 60_000_000_000 + 8 * 60) % 1440).astype(int)
    return df
