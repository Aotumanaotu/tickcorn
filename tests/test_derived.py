"""Derived feature tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.features.derived import add_basic_quote_features, add_deltas
from app.analysis.pipeline import build_clean_dataframe


def _mini_df():
    return pd.DataFrame({
        "bid_price1": [2300.0, 2300.0, 2301.0],
        "ask_price1": [2301.0, 2301.0, 2302.0],
        "bid_volume1": [30, 40, 50],
        "ask_volume1": [10, 10, 20],
        "bid_volume2": [5, 5, 5], "ask_volume2": [5, 5, 5],
        "bid_volume3": [5, 5, 5], "ask_volume3": [5, 5, 5],
        "last_price": [2300.0, 2301.0, 2301.0],
        "volume": [100, 110, 125],
    })


def test_spread_mid_obi_microprice():
    df = add_basic_quote_features(_mini_df(), tick_size=1.0)
    assert np.allclose(df["spread"], [1.0, 1.0, 1.0])
    assert np.allclose(df["mid_price"], [2300.5, 2300.5, 2301.5])
    assert np.allclose(df["spread_ticks"], [1.0, 1.0, 1.0])
    # OBI1 = (bv-av)/(bv+av)
    assert np.allclose(df["obi1"], [(30 - 10) / 40, (40 - 10) / 50,
                                    (50 - 20) / 70])
    # microprice row0 = (ask*bv + bid*av)/(bv+av)
    mp0 = (2301 * 30 + 2300 * 10) / 40
    assert np.isclose(df["microprice"].iloc[0], mp0)
    assert np.isclose(df["microprice_dev_ticks"].iloc[0], (mp0 - 2300.5) / 1.0)
    # OBI3 = (30+5+5 - 10-5-5)/(60) = 20/60
    assert np.isclose(df["obi3"].iloc[0], 20 / 60)


def test_sentinel_prices_become_nan():
    df = _mini_df()
    df.loc[0, "bid_price1"] = 1.7976931348623157e308
    df.loc[0, "ask_price1"] = 1.7976931348623157e308
    df = add_basic_quote_features(df, tick_size=1.0)
    assert pd.isna(df.loc[0, "spread"])
    assert pd.isna(df.loc[0, "mid_price"])
    assert pd.isna(df.loc[0, "obi1"])
    assert not pd.isna(df.loc[1, "mid_price"])


def test_zero_volumes_obi_nan():
    df = _mini_df()
    df.loc[0, ["bid_volume1", "ask_volume1"]] = 0
    df = add_basic_quote_features(df, tick_size=1.0)
    assert pd.isna(df.loc[0, "obi1"])
    assert pd.isna(df.loc[0, "microprice"])


def test_deltas():
    df = add_basic_quote_features(_mini_df(), tick_size=1.0)
    df = add_deltas(df)
    assert pd.isna(df["d_last"].iloc[0])
    assert df["d_last"].iloc[1] == 1.0
    assert df["d_bid1"].iloc[2] == 1.0
    assert df["d_volume"].iloc[1] == 10


def test_build_clean_dataframe(config):
    from conftest import build_synthetic_day
    raw = build_synthetic_day(n_base=100)
    result = build_clean_dataframe(raw, tick_size=1.0)
    df = result.df
    assert len(df) > 50
    # sorted timestamps
    assert df["exchange_ts_ns"].is_monotonic_increasing
    # required derived columns
    for col in ("spread", "mid_price", "obi1", "microprice", "d_last",
                "spread_ticks", "microprice_dev"):
        assert col in df.columns
    # duplicates were injected? our synthetic has none -> count 0
    assert result.stats["dropped_duplicates"] >= 0


def test_exact_duplicate_dropping():
    from conftest import make_row
    rows = [make_row(0, 1, 2300, 2301, 2300, update_time="09:30:00"),
            make_row(1, 2, 2300, 2301, 2300, update_time="09:30:00"),  # dup
            make_row(2, 3, 2300, 2301, 2301, update_time="09:30:01")]
    df = pd.DataFrame(rows)
    result = build_clean_dataframe(df, tick_size=1.0)
    assert result.stats["dropped_duplicates"] == 1
    assert len(result.df) == 2


def test_invalid_quote_rows_dropped():
    from conftest import make_row
    rows = [make_row(0, 1, 2300, 2301, 2300),
            make_row(1, 2, 0.0, 1.7976931348623157e308, 2300),  # invalid
            make_row(2, 3, 2300, 2301, 2301)]
    df = pd.DataFrame(rows)
    result = build_clean_dataframe(df, tick_size=1.0)
    assert result.stats["dropped_invalid"] == 1
    assert len(result.df) == 2


def test_exchange_ts_and_sessions(config):
    from app.common.timeutils import exchange_ts_ns, intraday_bucket
    ts = exchange_ts_ns("2026-09-18", "21:30:15", 500)
    assert ts % 1_000_000_000 == 500 * 1_000_000
    assert intraday_bucket(config, "21:10:00") == "NIGHT_OPEN_30M"
    assert intraday_bucket(config, "22:00:00") == "NIGHT_MIDDLE"
    assert intraday_bucket(config, "22:45:00") == "NIGHT_CLOSE_30M"
    assert intraday_bucket(config, "09:05:00") == "DAY_OPEN_30M"
    assert intraday_bucket(config, "10:00:00") == "MORNING"
    assert intraday_bucket(config, "14:00:00") == "AFTERNOON"
    assert intraday_bucket(config, "14:45:00") == "DAY_CLOSE_30M"
    assert intraday_bucket(config, "12:00:00") == "OUT_OF_SESSION"
    assert config.session_of("21:30:00") == "NIGHT"
    assert config.session_of("10:00:00") == "DAY"
    assert config.session_of("12:00:00") == "OUT_OF_SESSION"
