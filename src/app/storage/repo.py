"""Raw-side data access (immutable parquet partitions + metadata).

The clean-layer pipeline moved to app.analysis.pipeline: storage no longer
depends on the feature layer. Use pipeline.load_clean / load_clean_range for
analysis-ready dataframes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.common.config import AppConfig
from app.common.schema import PartitionKey
from app.storage.metadata_db import MetadataDB
from app.storage.parquet_store import RawParquetStore, sha256_of_file


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
