"""High-level data access: raw -> clean processed dataframes.

The clean layer:
    * converts CTP sentinels to NaN (raw keeps them verbatim!),
    * computes exchange_ts_ns (deterministic Beijing wall-clock),
    * drops exact-duplicate snapshots (kept in raw; count logged),
    * sorts by (exchange_ts_ns, sequence_id),
    * drops rows without a usable best bid/ask/last (e.g. pre-open junk),
      counting them for the report,
    * adds basic quote features (spread / mid / OBI / microprice).

Results are cached under data/processed/instrument=X/trading_day=D/ with a
content hash of the raw input, so re-runs are free and reproducible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow.parquet as pq

from app.common.config import AppConfig
from app.common.exceptions import StorageError
from app.common.logging import get_logger
from app.common.schema import CTP_COLUMNS, RAW_SCHEMA, PartitionKey
from app.common.timeutils import normalize_ctp_day
from app.features.derived import (PRICE_COLUMNS, add_basic_quote_features,
                                  add_deltas, add_ts_and_minute, clean_prices)
from app.storage.metadata_db import MetadataDB
from app.storage.parquet_store import RawParquetStore, sha256_of_file

logger = get_logger("storage.repo")

CTP_VALUE_COLUMNS = [n for n, _ in CTP_COLUMNS]


@dataclass
class CleanResult:
    df: pd.DataFrame
    stats: dict = field(default_factory=dict)

    @property
    def dropped_duplicates(self) -> int:
        return int(self.stats.get("dropped_duplicates", 0))

    @property
    def dropped_invalid(self) -> int:
        return int(self.stats.get("dropped_invalid", 0))


class StorageRepository:
    def __init__(self, config: AppConfig, db: Optional[MetadataDB] = None,
                 store: Optional[RawParquetStore] = None):
        self.config = config
        self.db = db or MetadataDB(config.paths.metadata_db)
        self.store = store or RawParquetStore(config.paths.raw_dir)

    # ------------------------------------------------------------------
    # Raw access
    # ------------------------------------------------------------------
    def load_raw_table(self, key: PartitionKey, columns: Optional[list[str]] = None):
        return self.store.read_partition(key, columns=columns)

    def list_days(self, instrument_id: str) -> list[str]:
        return sorted(k.trading_day for k in self.store.list_partitions(instrument_id))

    def partitions(self, instrument_id: Optional[str] = None) -> list[PartitionKey]:
        return self.store.list_partitions(instrument_id)

    def input_hashes(self, keys: list[PartitionKey]) -> list[str]:
        return [sha256_of_file(path) for key in keys
                for path in self.store.partition_files(key)]

    # ------------------------------------------------------------------
    # Clean layer
    # ------------------------------------------------------------------
    def load_clean(self, instrument_id: str, trading_day: str,
                   tick_size: Optional[float] = None,
                   use_cache: bool = True, raw_source: Optional[str] = None) -> CleanResult:
        key = PartitionKey(instrument_id, trading_day)
        tick = tick_size if tick_size is not None else self.config.resolve_tick_size(
            instrument_id)

        cache_dir = (self.config.paths.processed_dir
                     / f"instrument={instrument_id}" / f"trading_day={trading_day}")
        raw_sig = hashlib.sha256("".join(self.input_hashes([key])).encode()).hexdigest()[:16]
        if any(self.store.staging_dir(key).glob("part-*.parquet")):
            use_cache = False
        param_sig = f"v3-tick{tick}"
        if raw_source:
            param_sig += "-source" + hashlib.sha256(raw_source.encode()).hexdigest()[:16]
        cache_path = cache_dir / f"clean-{raw_sig}-{param_sig}.parquet"
        stats_path = cache_dir / f"clean-{raw_sig}-{param_sig}.json"

        if use_cache and cache_path.exists() and stats_path.exists():
            df = pd.read_parquet(cache_path)
            stats = json.loads(stats_path.read_text())
            return CleanResult(df=df, stats=stats)

        table = self.load_raw_table(key)
        df = table.to_pandas()
        if raw_source:
            df = df.loc[df["raw_source"] == raw_source].copy()
        result = build_clean_dataframe(df, tick_size=tick)

        cache_dir.mkdir(parents=True, exist_ok=True)
        result.df.to_parquet(cache_path, index=False)
        stats_path.write_text(json.dumps(result.stats, ensure_ascii=False, indent=2))
        return result

    def load_clean_range(self, instrument_id: str, days: list[str],
                         tick_size: Optional[float] = None,
                         raw_source: Optional[str] = None) -> CleanResult:
        dfs, stats = [], {"dropped_duplicates": 0, "dropped_invalid": 0,
                          "days": {}}
        for day in days:
            r = self.load_clean(instrument_id, day, tick_size=tick_size,
                                raw_source=raw_source)
            dfs.append(r.df)
            stats["dropped_duplicates"] += r.dropped_duplicates
            stats["dropped_invalid"] += r.dropped_invalid
            stats["days"][day] = {
                "rows": len(r.df), "dropped_duplicates": r.dropped_duplicates,
                "dropped_invalid": r.dropped_invalid}
        if not dfs:
            return CleanResult(df=pd.DataFrame(columns=RAW_SCHEMA.names), stats=stats)
        out = pd.concat(dfs, ignore_index=True)
        out = out.sort_values(["exchange_ts_ns", "sequence_id"]).reset_index(drop=True)
        return CleanResult(df=out, stats=stats)


# ----------------------------------------------------------------------
# Pure transformation (unit-testable)
# ----------------------------------------------------------------------

def build_clean_dataframe(df: pd.DataFrame, tick_size: float) -> CleanResult:
    """Raw snapshot dataframe -> clean analysis-ready dataframe."""
    stats: dict = {"input_rows": int(len(df))}

    # 1) exact duplicates: identical CTP field content on consecutive rows.
    value_cols = [c for c in CTP_VALUE_COLUMNS if c in df.columns]
    if len(df) > 1:
        values = df[value_cols]
        previous = values.shift()
        dup = (values.eq(previous) | (values.isna() & previous.isna())).all(axis=1)
        dup.iloc[0] = False
    else:
        dup = pd.Series(False, index=df.index)
    stats["dropped_duplicates"] = int(dup.sum())
    df = df.loc[~dup].copy()

    # 2) sentinel -> NaN
    df = clean_prices(df, PRICE_COLUMNS)

    # Normalize CTP calendar strings only in the processed layer.
    for col in ("action_day", "trading_day"):
        if col in df:
            df[col] = df[col].map(normalize_ctp_day)
    # 3) deterministic exchange timestamp
    df = add_ts_and_minute(df)

    # 4) sort by exchange time, then local sequence
    if "sequence_id" in df.columns:
        df = df.sort_values(["exchange_ts_ns", "sequence_id"]).reset_index(drop=True)
    else:
        df = df.sort_values("exchange_ts_ns").reset_index(drop=True)

    # 5) drop rows without a usable best quote/last (pre-open / halted)
    need = ["bid_price1", "ask_price1", "last_price"]
    ok = df[need].notna().all(axis=1)
    ok &= df["bid_price1"] < df["ask_price1"]
    stats["dropped_invalid"] = int((~ok).sum())
    df = df.loc[ok].reset_index(drop=True)

    # 6) basic derived features + deltas
    df = add_basic_quote_features(df, tick_size)
    df = add_deltas(df)

    stats["output_rows"] = int(len(df))
    if len(df):
        stats["first_ts"] = str(df["exchange_ts_ns"].iloc[0])
        stats["last_ts"] = str(df["exchange_ts_ns"].iloc[-1])
    return CleanResult(df=df, stats=stats)
