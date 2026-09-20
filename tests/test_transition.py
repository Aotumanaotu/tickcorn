"""Transition matrix and conditional probability tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.statistics.transition import (direction_by_state, next_genuine_direction,
                                       obi_conditional_table, state5_matrix,
                                       state8_matrix)

UP = "GENUINE_QUOTE_MOVE_UP"
DOWN = "GENUINE_QUOTE_MOVE_DOWN"
BU = "HIGH_CONFIDENCE_BOUNCE_UP"
BD = "HIGH_CONFIDENCE_BOUNCE_DOWN"
NM = "NO_MOVE"
AMB = "AMBIGUOUS"

_S5 = {UP: "QUOTE_UP", DOWN: "QUOTE_DOWN", BU: "BOUNCE_UP", BD: "BOUNCE_DOWN",
       NM: "NO_MOVE", AMB: None,
       "LIKELY_BOUNCE_UP": "BOUNCE_UP", "LIKELY_BOUNCE_DOWN": "BOUNCE_DOWN"}
_S8 = {UP: "QUOTE_UP", DOWN: "QUOTE_DOWN", BU: "HIGH_BOUNCE_UP",
       BD: "HIGH_BOUNCE_DOWN", "LIKELY_BOUNCE_UP": "LIKELY_BOUNCE_UP",
       "LIKELY_BOUNCE_DOWN": "LIKELY_BOUNCE_DOWN", NM: "NO_MOVE",
       AMB: "AMBIGUOUS"}


def _classified_df(labels: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "label": [None] + labels,
        "state5": [None] + [_S5[l] for l in labels],
        "state8": [None] + [_S8[l] for l in labels],
        "obi1": np.linspace(-1, 1, len(labels) + 1),
    })


def test_state5_matrix_simple():
    df = _classified_df([UP, BU, NM, UP, BD])
    m = state5_matrix(df)
    # transitions: UP->BU, BU->NM, NM->UP, UP->BD
    assert m.loc["QUOTE_UP", "BOUNCE_UP"] == 0.5
    assert m.loc["QUOTE_UP", "BOUNCE_DOWN"] == 0.5
    assert m.loc["BOUNCE_UP", "NO_MOVE"] == 1.0
    assert m.loc["NO_MOVE", "QUOTE_UP"] == 1.0
    # rows normalized
    sums = m.sum(axis=1)
    assert np.allclose(sums[sums > 0], 1.0)


def test_ambiguous_skipped_in_state5():
    """Do not manufacture adjacent events across an unknown observation."""
    df = _classified_df([UP, AMB, UP])
    m = state5_matrix(df)
    assert m.loc["QUOTE_UP", "QUOTE_UP"] == 0.0


def test_state8_includes_ambiguous():
    df = _classified_df([UP, AMB, UP])
    m = state8_matrix(df)
    assert m.loc["QUOTE_UP", "AMBIGUOUS"] == 1.0
    assert m.loc["AMBIGUOUS", "QUOTE_UP"] == 1.0


def test_next_genuine_direction_horizon():
    labels = [NM] * 3 + [UP] + [NM] * 10 + [DOWN]
    df = _classified_df(labels)
    # df row i carries label[i-1]; UP is labels[3] -> df row 4
    d1 = next_genuine_direction(df, horizon=1)
    assert d1.iloc[3] == 1   # genuine at row 4, distance 1
    assert d1.iloc[2] == 0   # distance 2 > horizon
    d3 = next_genuine_direction(df, horizon=3)
    assert d3.iloc[1] == 1   # distance 3
    assert d3.iloc[0] == 0   # distance 4
    assert d3.iloc[5] == 0   # after up move, down move is far
    d20 = next_genuine_direction(df, horizon=20)
    assert d20.iloc[5] == -1  # down move at row 15 within 20


def test_obi_conditional_table():
    labels = [UP] * 5 + [NM] * 10 + [DOWN] * 5
    df = _classified_df(labels)
    t = obi_conditional_table(df, edges=[-1.0, -0.5, 0.0, 0.5, 1.0], horizon=5)
    assert set(t["obi_bucket"]) == {"[-1.0,-0.5)", "[-0.5,0.0)",
                                    "[0.0,0.5)", "[0.5,1.0]"}
    low = t[t["obi_bucket"] == "[-1.0,-0.5)"].iloc[0]
    high = t[t["obi_bucket"] == "[0.5,1.0]"].iloc[0]
    assert low["p_up"] > 0.5
    assert high["p_down"] > 0.5
    assert "p_up_minus_p_down" in t.columns


def test_direction_by_state():
    df = _classified_df([BU] * 4 + [UP])
    t = direction_by_state(df, horizon=3)
    row = t[t["state"] == "BOUNCE_UP"].iloc[0]
    # BU rows 1..4; genuine UP at row 5 within horizon for rows 2,3,4 (dist 3,2,1)
    # Only rows 1 and 2 have a complete three-snapshot future window.
    assert row["p_up"] == 0.5
    assert row["n"] == 2
