"""Rolling look-back window features (uses ONLY past data, no leakage).

For each window k (in snapshots) and each snapshot t, features summarise the
behaviour over [t-k, t]:
    mid_ret_k         -- mid price change over k snapshots (ticks)
    last_dir_k        -- sign of last price change over k
    obi_mean_k        -- mean OBI1 over the window
    obi_change_k      -- OBI1 change over the window
    microprice_dev_mean_k
    volume_delta_k    -- cumulative traded volume change over window
    turnover_delta_k  -- cumulative turnover change over window
    spread_mean_k     -- mean spread (ticks) over window
    volatility_k      -- std of per-snapshot mid changes (ticks) in window
    update_speed_k    -- snapshots per second implied by the window span
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_window_features(df: pd.DataFrame, ks: list[int],
                        tick_size: float) -> pd.DataFrame:
    df = df.copy()
    mid_t = df["mid_price"] / tick_size if "mid_price" in df else None
    ts = df["exchange_ts_ns"].astype("int64") if "exchange_ts_ns" in df else None

    for k in ks:
        if "mid_price" in df:
            ret = mid_t.diff(k)
            df[f"mid_ret_{k}"] = ret
            df[f"last_dir_{k}"] = np.sign(
                pd.to_numeric(df["last_price"], errors="coerce").diff(k))
        if "obi1" in df:
            df[f"obi_mean_{k}"] = df["obi1"].rolling(k, min_periods=1).mean()
            df[f"obi_change_{k}"] = df["obi1"].diff(k)
        if "microprice_dev_ticks" in df:
            df[f"microprice_dev_mean_{k}"] = df["microprice_dev_ticks"].rolling(
                k, min_periods=1).mean()
        if "volume" in df:
            df[f"volume_delta_{k}"] = pd.to_numeric(
                df["volume"], errors="coerce").diff(k)
        if "turnover" in df:
            df[f"turnover_delta_{k}"] = pd.to_numeric(
                df["turnover"], errors="coerce").diff(k)
        if "spread_ticks" in df:
            df[f"spread_mean_{k}"] = df["spread_ticks"].rolling(
                k, min_periods=1).mean()
        if "mid_price" in df:
            step = mid_t.diff()
            df[f"volatility_{k}"] = step.rolling(k, min_periods=min(2, k)).std()
        if ts is not None and len(df) > 0:
            dt_s = (ts - ts.shift(k)) / 1e9
            df[f"update_speed_{k}"] = (k / dt_s.replace(0, np.nan)).clip(lower=0)
    return df


FEATURE_COLUMNS_BASE: list[str] = [
    "obi1", "obi3", "obi5", "spread", "spread_ticks", "mid_price",
    "microprice", "microprice_dev", "microprice_dev_ticks",
]
