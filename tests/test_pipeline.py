"""End-to-end Phase-1 pipeline test on synthetic data (no CTP connection).

Synthetic stream structure per 10-snapshot episode (deterministic):
    3x NO_MOVE, 3x high-confidence bounce, 1x GENUINE up, 2x bounce,
    1x GENUINE down; every 6th episode adds 2 AMBIGUOUS rows.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from app.common.config import load_config
from app.common.schema import RAW_SCHEMA, PartitionKey
from app.storage.metadata_db import MetadataDB
from app.storage.parquet_store import RawParquetStore
from conftest import ROOT, build_synthetic_day


@pytest.fixture()
def e2e_env(tmp_path: Path):
    config = load_config(config_dir=ROOT / "config", data_dir_override=tmp_path)
    store = RawParquetStore(config.paths.raw_dir)
    db = MetadataDB(config.paths.metadata_db)

    df_raw = build_synthetic_day(n_base=100)  # 10 episodes -> 102 rows
    key = PartitionKey("C2701", "2026-09-18")
    table = pa.Table.from_pandas(df_raw, schema=RAW_SCHEMA, safe=True)
    store.write_staging_part(key, table, 1)
    result = store.finalize(key)
    db.register_raw_file(key.instrument_id, key.trading_day, result.path,
                         result.rows, result.sha256, result.part_count,
                         batch_id="batch-test")
    db.upsert_instrument("C2701", "C", "DCE", 1.0)
    return config, db, store


def test_end_to_end_analysis(e2e_env):
    from app.report.generator import AnalysisOptions, run_analysis
    config, db, store = e2e_env

    result = run_analysis(config, "C2701", ["2026-09-18"],
                          AnalysisOptions(strict=True))
    s = result.summary

    # ---- deterministic structural counts (see module docstring) ----
    assert s["total_snapshots"] == 102
    assert s["high_confidence_bounce"] == 50      # 5 per episode
    assert s["likely_bounce"] == 0
    assert s["genuine_moves"] == 20               # 1 up + 1 down per episode
    assert s["ambiguous"] == 2                    # ep index 5
    assert s["one_tick_last_changes"] == 62       # 6/episode + 2 ambiguous
    assert s["bounce_ratio"] == pytest.approx(50 / 62, abs=1e-9)
    assert s["genuine_move_ratio"] == pytest.approx(10 / 62, abs=1e-9)

    # ---- outputs exist ----
    out = result.output_dir
    for name in ("summary.html", "summary.md", "event_statistics.csv",
                 "transition_matrix.csv", "transition_matrix_fine.csv",
                 "obi_probability.csv", "feature_statistics.csv",
                 "preevent_features.csv", "next_move_by_state.csv",
                 "intraday_time_bins.csv", "run_metadata.json"):
        assert (out / name).exists(), name
    for name in ("price_and_quotes.png", "bounce_ratio.png",
                 "genuine_move_ratio.png", "obi_vs_direction.png",
                 "microprice_vs_direction.png", "intraday_pattern.png",
                 "obi_probability.png"):
        assert (out / "figures" / name).exists(), name

    # latest symlink
    assert (config.paths.reports_dir / "latest").exists()

    # html embeds the three headline questions + key terminology
    html = (out / "summary.html").read_text(encoding="utf-8")
    for needle in ("核心问题一", "核心问题二", "核心问题三",
                   "Bid-Ask Bounce", "Genuine Quote Move",
                   "HIGH_CONFIDENCE_BOUNCE", "AMBIGUOUS"):
        assert needle in html, needle

    # run registered in metadata db
    runs = db.list_analysis_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "ok"
    assert runs[0]["instrument_id"] == "C2701"

    # transition matrix sane: bounce_up -> bounce_down likely high
    import numpy as np
    t5 = result.transition5
    assert t5.loc["BOUNCE_UP", "BOUNCE_DOWN"] > 0.3

    # preevent comparison has rows and direction column
    assert not result.preevent.empty
    assert set(result.preevent["direction"].unique()) == {1, -1}


def test_end_to_end_replay(e2e_env):
    from app.replay.replayer import Replayer
    from app.storage.repo import StorageRepository
    config, db, store = e2e_env
    repo = StorageRepository(config, db=db)
    replayer = Replayer(repo)
    df = replayer.load("C2701", ["2026-09-18"])
    assert len(df) == 102

    seen = []

    def consumer(row):
        seen.append(row["last_price"])

    stats = replayer.run(df, consumer, speed="fast")
    assert stats.rows == 102
    assert len(seen) == 102
    assert stats.gaps >= 0


def test_clean_layer_and_processed_cache(e2e_env):
    from app.storage.repo import StorageRepository
    config, db, store = e2e_env
    repo = StorageRepository(config, db=db)
    r1 = repo.load_clean("C2701", "2026-09-18")
    assert len(r1.df) == 102
    # cache hit returns identical frame
    r2 = repo.load_clean("C2701", "2026-09-18")
    assert len(r2.df) == 102
    assert list(r2.df.columns) == list(r1.df.columns)
    # obi/microprice finite for most rows
    assert r1.df["obi1"].notna().mean() > 0.95
    assert r1.df["microprice"].notna().mean() > 0.95
