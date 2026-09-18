"""Transition matrices and conditional probability tables.

1. state5 transition matrix:
       P(next_state | current_state), states:
       NO_MOVE / BOUNCE_UP / BOUNCE_DOWN / QUOTE_UP / QUOTE_DOWN
       (AMBIGUOUS transitions are excluded from the chain; a fine-grained
        state8 matrix that includes AMBIGUOUS is also produced.)

2. Next-genuine-move direction by current state:
       P(next genuine QUOTE_MOVE is UP/DOWN/none-within-h | current_state)

3. OBI conditional table (first-phase headline result):
       P(QUOTE_UP / QUOTE_DOWN / none-within-h | OBI1 bucket)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.common.constants import STATE5_ORDER, STATE8_ORDER


def _pair_counts(states: pd.Series, order: list[str]) -> pd.DataFrame:
    s = states
    if len(s) < 2:
        return pd.DataFrame(0, index=order, columns=order, dtype=float)
    cur = s.iloc[:-1].to_numpy()
    nxt = s.iloc[1:].to_numpy()
    df = pd.DataFrame(0, index=order, columns=order, dtype=float)
    for c, n in zip(cur, nxt):
        if c in df.index and n in df.columns:
            df.loc[c, n] += 1
    return df


def transition_matrix(states: pd.Series, order: list[str],
                      normalize: bool = True) -> pd.DataFrame:
    counts = _pair_counts(states, order)
    if normalize:
        row_sums = counts.sum(axis=1)
        probs = counts.div(row_sums.replace(0, np.nan), axis=0)
        return probs.fillna(0.0)
    return counts


def build_state_series(df: pd.DataFrame, which: str = "state5") -> pd.Series:
    if which not in df.columns:
        raise ValueError(f"column '{which}' missing (run classifier first)")
    return df[which]


def _segmented_matrix(df, which, order):
    if "segment_id" not in df:
        return transition_matrix(build_state_series(df, which), order)
    counts = sum((_pair_counts(g[which], order) for _, g in df.groupby("segment_id")),
                 pd.DataFrame(0.0, index=order, columns=order))
    return counts.div(counts.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def state5_matrix(df: pd.DataFrame) -> pd.DataFrame:
    return _segmented_matrix(df, "state5", STATE5_ORDER)


def state8_matrix(df: pd.DataFrame) -> pd.DataFrame:
    return _segmented_matrix(df, "state8", STATE8_ORDER)


# ---------------------------------------------------------------------
# Next-genuine-move helpers (vectorized reverse scan)
# ---------------------------------------------------------------------

def next_genuine_direction(df: pd.DataFrame, horizon: int) -> pd.Series:
    """For each row t: direction (+1/-1) of the FIRST genuine quote move
    within `horizon` subsequent transitions, else 0.

    Uses only rows t+1 .. t+horizon (strictly after t).
    """
    if horizon < 1:
        raise ValueError("horizon must be positive")
    if "segment_id" in df and df["segment_id"].nunique() > 1:
        return pd.concat([next_genuine_direction(g.drop(columns="segment_id"), horizon)
                          for _, g in df.groupby("segment_id", sort=False)]).reindex(df.index)
    n = len(df)
    out = np.zeros(n, dtype=np.int8)
    if "label" not in df.columns or n == 0:
        return pd.Series(out, index=df.index)

    labels = df["label"].to_numpy()
    genuine_dir = np.zeros(n, dtype=np.int8)
    is_genuine = np.isin(labels, ["GENUINE_QUOTE_MOVE_UP",
                                  "GENUINE_QUOTE_MOVE_DOWN"])
    genuine_dir[labels == "GENUINE_QUOTE_MOVE_UP"] = 1
    genuine_dir[labels == "GENUINE_QUOTE_MOVE_DOWN"] = -1

    # next_dir[i] = direction of first genuine event at index > i (any range)
    next_dir = np.zeros(n, dtype=np.int8)
    next_dist = np.full(n, np.iinfo(np.int32).max, dtype=np.int32)
    last_dir, last_pos = 0, np.iinfo(np.int32).max
    for i in range(n - 1, -1, -1):
        next_dir[i] = last_dir
        next_dist[i] = last_pos - i if last_pos != np.iinfo(np.int32).max else last_pos
        if is_genuine[i]:
            last_dir, last_pos = genuine_dir[i], i
    out = np.where((next_dist <= horizon) & (next_dist > 0), next_dir, 0)
    return pd.Series(out, index=df.index, name="next_genuine_dir")


def _complete_horizon(df, horizon):
    if "segment_id" in df:
        return df["segment_id"].eq(df["segment_id"].shift(-horizon))
    return pd.Series(np.arange(len(df)) + horizon < len(df), index=df.index)


def direction_by_state(df: pd.DataFrame, horizon: int,
                       order: list[str] | None = None) -> pd.DataFrame:
    """P(next genuine move direction | current state) within horizon."""
    states = build_state_series(df, "state5")
    nxt = next_genuine_direction(df, horizon)
    valid = states.notna() & _complete_horizon(df, horizon)
    st = states[valid]
    nd = nxt[valid]
    order = order or STATE5_ORDER
    rows = []
    for s in order:
        m = st == s
        total = int(m.sum())
        if total == 0:
            rows.append({"state": s, "horizon": horizon, "n": 0, "p_up": np.nan, "p_down": np.nan,
                         "p_none": np.nan})
            continue
        up = int((nd[m] == 1).sum())
        down = int((nd[m] == -1).sum())
        rows.append({"state": s, "horizon": horizon, "n": total, "p_up": up / total,
                     "p_down": down / total, "p_none": (total - up - down) / total})
    return pd.DataFrame(rows)


def obi_conditional_table(df: pd.DataFrame, edges: list[float],
                          horizon: int) -> pd.DataFrame:
    """P(next genuine move direction | OBI1 bucket) within horizon.

    edges: bucket boundaries, e.g. [-1,-0.8,...,1]; buckets are
    [edges[i], edges[i+1]) with the last one closed.
    """
    if "obi1" not in df.columns:
        raise ValueError("obi1 column missing")
    obi = pd.to_numeric(df["obi1"], errors="coerce")
    nxt = next_genuine_direction(df, horizon)
    valid = obi.notna() & (obi >= edges[0]) & (obi <= edges[-1]) & _complete_horizon(df, horizon)
    obi_v, nxt_v = obi[valid], nxt[valid]

    labels = [f"[{edges[i]:.1f},{edges[i+1]:.1f}" + ("]" if i == len(edges) - 2
               else ")") for i in range(len(edges) - 1)]
    idx = np.clip(np.searchsorted(edges, obi_v.to_numpy(), side="right") - 1,
                  0, len(edges) - 2)

    rows = []
    arr = nxt_v.to_numpy()
    for b in range(len(edges) - 1):
        m = idx == b
        total = int(m.sum())
        if total == 0:
            rows.append({"obi_bucket": labels[b], "horizon": horizon, "n": total,
                         "p_up": np.nan, "p_down": np.nan, "p_none": np.nan,
                         "p_up_minus_p_down": np.nan})
            continue
        up = int((arr[m] == 1).sum())
        down = int((arr[m] == -1).sum())
        rows.append({"obi_bucket": labels[b], "horizon": horizon, "n": total,
                     "p_up": up / total, "p_down": down / total,
                     "p_none": (total - up - down) / total,
                     "p_up_minus_p_down": (up - down) / total})
    return pd.DataFrame(rows)
