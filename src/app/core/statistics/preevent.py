"""Pre-event analysis: what happened BEFORE a genuine quote move?

For every GENUINE_QUOTE_MOVE at transition t (snapshot rows t-1 -> t), we
collect features from the snapshots strictly BEFORE the move (window ending
at t-1), for look-back depths k in {1,2,3,5,10}:

    obi1 / obi3 / obi5, microprice deviation (ticks), bid/ask volume1,
    d_bid_volume1 / d_ask_volume1, volume_delta_k, turnover_delta_k,
    spread_ticks, mid_ret_k (short momentum), volatility_k, update_speed_k.

Feature rows are aligned at t-1 (NOT t), so no information from the move
itself leaks into the pre-event feature set.

Output: per-direction means, Welch t-tests and Mann-Whitney U tests
(up events vs down events), with effect sizes.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as sps

from app.common.logging import get_logger

logger = get_logger("statistics.preevent")

BASE_FEATURES = [
    "obi1", "obi3", "obi5", "microprice_dev_ticks", "bid_volume1",
    "ask_volume1", "d_bid_volume1", "d_ask_volume1", "spread_ticks",
]


def preevent_feature_rows(df: pd.DataFrame, lookbacks: list[int]) -> pd.DataFrame:
    """One row per genuine quote move with pre-move features."""
    if "label" not in df.columns:
        raise ValueError("classification columns missing")
    event_mask = df["label"].isin(
        ["GENUINE_QUOTE_MOVE_UP", "GENUINE_QUOTE_MOVE_DOWN"])
    event_idx = df.index[event_mask]
    if len(event_idx) == 0:
        return pd.DataFrame()

    # features strictly before the move: row t-1
    prior_idx = event_idx - 1
    prior_idx = prior_idx[prior_idx >= 0]

    feats = df.loc[prior_idx, BASE_FEATURES].copy()
    feats["direction"] = df.loc[prior_idx + 1, "label"].map(
        {"GENUINE_QUOTE_MOVE_UP": 1, "GENUINE_QUOTE_MOVE_DOWN": -1}).values
    feats["event_exchange_ts_ns"] = df.loc[prior_idx + 1, "exchange_ts_ns"].values
    feats["event_index"] = prior_idx + 1

    for k in lookbacks:
        for col in (f"volume_delta_{k}", f"turnover_delta_{k}", f"mid_ret_{k}",
                    f"obi_mean_{k}", f"obi_change_{k}", f"microprice_dev_mean_{k}",
                    f"volatility_{k}", f"update_speed_{k}"):
            if col in df.columns:
                feats[col] = df.loc[prior_idx, col].values
    return feats.reset_index(drop=True)


def compare_directions(feats: pd.DataFrame, min_samples: int = 30
                       ) -> pd.DataFrame:
    """UP vs DOWN pre-event feature comparison with significance tests."""
    if feats.empty or "direction" not in feats:
        return pd.DataFrame()

    up = feats[feats["direction"] == 1]
    down = feats[feats["direction"] == -1]
    rows = []
    for col in feats.columns:
        if col in ("direction", "event_exchange_ts_ns", "event_index"):
            continue
        x = pd.to_numeric(up[col], errors="coerce").dropna()
        y = pd.to_numeric(down[col], errors="coerce").dropna()
        if len(x) < min_samples or len(y) < min_samples:
            rows.append({
                "feature": col, "n_up": len(x), "n_down": len(y),
                "mean_up": x.mean(), "mean_down": y.mean(),
                "mean_diff": x.mean() - y.mean(),
                "t_stat": np.nan, "p_ttest": np.nan,
                "u_stat": np.nan, "p_mannwhitney": np.nan,
                "cohens_d": np.nan, "significant": False,
            })
            continue
        t_stat, p_t = sps.ttest_ind(x, y, equal_var=False)
        try:
            u_stat, p_u = sps.mannwhitneyu(x, y, alternative="two-sided")
        except ValueError:
            u_stat, p_u = np.nan, np.nan
        nx, ny = len(x), len(y)
        pooled_sd = np.sqrt(((nx - 1) * x.var(ddof=1) + (ny - 1) * y.var(ddof=1))
                            / (nx + ny - 2))
        d = (x.mean() - y.mean()) / pooled_sd if pooled_sd > 0 else np.nan
        rows.append({
            "feature": col, "n_up": nx, "n_down": ny,
            "mean_up": x.mean(), "mean_down": y.mean(),
            "mean_diff": x.mean() - y.mean(),
            "t_stat": t_stat, "p_ttest": p_t,
            "u_stat": u_stat, "p_mannwhitney": p_u,
            "cohens_d": d,
            "significant": bool((p_t == p_t and p_t < 0.05)
                                or (p_u == p_u and p_u < 0.05)),
        })
    return pd.DataFrame(rows)


def headline_preevent_summary(comp: pd.DataFrame, lookbacks: list[int]) -> dict:
    """Extract the paper-facing numbers."""
    out: dict = {}
    if comp.empty:
        return out
    for _, r in comp.iterrows():
        f = r["feature"]
        if f == "obi1":
            out["mean_obi_before_up"] = r["mean_up"]
            out["mean_obi_before_down"] = r["mean_down"]
            out["obi_p_value"] = r["p_ttest"]
        if f == "microprice_dev_ticks":
            out["mean_microprice_dev_before_up"] = r["mean_up"]
            out["mean_microprice_dev_before_down"] = r["mean_down"]
            out["microprice_dev_p_value"] = r["p_ttest"]
    return out
