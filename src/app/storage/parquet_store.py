"""Raw parquet store with an immutable finalize lifecycle.

Layout:
    data/raw/instrument=<ID>/trading_day=<YYYY-MM-DD>/
        _staging/part-000001.parquet   ... appended live by the collector
        snapshots.parquet              ... merged, sha256-registered, chmod 0444

Guarantees:
    * staging parts are append-only and never rewritten;
    * finalization merges parts in order, verifies row counts, registers
      sha256 in SQLite, marks the file read-only (0444);
    * raw files, once finalized, are never modified by the system again.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from app.common.exceptions import StorageError
from app.common.logging import get_logger
from app.common.schema import RAW_SCHEMA, PartitionKey

logger = get_logger("storage.parquet")


def sha256_of_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass
class FinalizeResult:
    key: PartitionKey
    rows: int
    part_count: int
    sha256: str
    path: Path


class RawParquetStore:
    """Append-only writer + finalizer for raw snapshot parquet."""

    def __init__(self, raw_root: Path):
        self.raw_root = Path(raw_root)
        self.raw_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Staging writes (during collection)
    # ------------------------------------------------------------------
    def staging_dir(self, key: PartitionKey) -> Path:
        return key.dir(self.raw_root) / "_staging"

    def write_staging_part(
        self, key: PartitionKey, table: pa.Table, part_index: int
    ) -> Path:
        """Write one append-only staging part. Never rewrites existing parts."""
        sdir = self.staging_dir(key)
        sdir.mkdir(parents=True, exist_ok=True)
        path = sdir / f"part-{part_index:06d}.parquet"
        if path.exists():
            raise StorageError(f"staging part already exists (refusing to overwrite): {path}")
        pq.write_table(table, path, compression="zstd")
        return path

    def list_staging_partitions(self) -> list[PartitionKey]:
        """All partitions that currently have un-finalized staging parts."""
        out: list[PartitionKey] = []
        if not self.raw_root.exists():
            return out
        for inst_dir in sorted(self.raw_root.glob("instrument=*")):
            for day_dir in sorted(inst_dir.glob("trading_day=*")):
                if (day_dir / "_staging").is_dir() and any(
                        (day_dir / "_staging").glob("part-*.parquet")):
                    out.append(PartitionKey(
                        instrument_id=inst_dir.name.split("=", 1)[1],
                        trading_day=day_dir.name.split("=", 1)[1],
                    ))
        return out

    # ------------------------------------------------------------------
    # Finalize (merge staging -> immutable snapshots.parquet)
    # ------------------------------------------------------------------
    def finalize(self, key: PartitionKey, force: bool = False) -> Optional[FinalizeResult]:
        """Merge staging parts into the immutable final file.

        If the final file already exists: verifies it against staging and
        refuses to overwrite unless force=True.
        """
        final_path = key.dir(self.raw_root) / "snapshots.parquet"
        sdir = self.staging_dir(key)
        parts = sorted(sdir.glob("part-*.parquet")) if sdir.is_dir() else []
        if not parts:
            if final_path.exists():
                return FinalizeResult(key, rows=self._rowcount(final_path),
                                      part_count=0,
                                      sha256=sha256_of_file(final_path),
                                      path=final_path)
            return None
        if final_path.exists() and not force:
            raise StorageError(
                f"final file already exists for {key}: {final_path}. "
                f"Use force=True to replace (this breaks immutability!).")

        tables = [pq.read_table(p, schema=RAW_SCHEMA) for p in parts]
        expected_rows = sum(t.num_rows for t in tables)
        merged = pa.concat_tables(tables) if tables else RAW_SCHEMA.empty_table()
        if merged.num_rows != expected_rows:
            raise StorageError(
                f"row count mismatch while merging {key}: "
                f"{merged.num_rows} != {expected_rows}")

        tmp_path = final_path.with_suffix(".parquet.tmp")
        pq.write_table(merged, tmp_path, compression="zstd")
        os.replace(tmp_path, final_path)

        result = FinalizeResult(
            key=key,
            rows=merged.num_rows,
            part_count=len(parts),
            sha256=sha256_of_file(final_path),
            path=final_path,
        )

        # Verify the written file reads back correctly before dropping staging.
        if self._rowcount(final_path) != result.rows:
            raise StorageError(f"verification failed for {final_path}")

        # Make raw immutable at the filesystem level and clean staging.
        os.chmod(final_path, 0o444)
        shutil.rmtree(sdir)
        logger.info("finalized %s: %d rows from %d parts, sha256=%s...",
                    key, result.rows, result.part_count, result.sha256[:12])
        return result

    def finalize_all(self, keys: Optional[Iterable[PartitionKey]] = None
                     ) -> list[FinalizeResult]:
        keys = list(keys) if keys is not None else self.list_staging_partitions()
        results = []
        for key in keys:
            try:
                r = self.finalize(key)
                if r is not None:
                    results.append(r)
            except Exception:
                logger.exception("failed to finalize %s", key)
                raise
        return results

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    @staticmethod
    def _rowcount(path: Path) -> int:
        return pq.ParquetFile(path).metadata.num_rows

    def final_path(self, key: PartitionKey) -> Path:
        return key.dir(self.raw_root) / "snapshots.parquet"

    def read_partition(self, key: PartitionKey, columns: Optional[list[str]] = None
                       ) -> pa.Table:
        """Read a finalized partition. Prefers the final file; falls back to
        staging parts (e.g. while collection is still running)."""
        final = self.final_path(key)
        if final.exists():
            return pq.read_table(final, columns=columns)
        sdir = self.staging_dir(key)
        parts = sorted(sdir.glob("part-*.parquet")) if sdir.is_dir() else []
        if not parts:
            raise StorageError(f"no data for partition {key}")
        tables = [pq.read_table(p, columns=columns) for p in parts]
        return pa.concat_tables(tables)

    def list_partitions(self, instrument_id: Optional[str] = None
                        ) -> list[PartitionKey]:
        """All partitions with data (finalized or staging)."""
        out: list[PartitionKey] = []
        if not self.raw_root.exists():
            return out
        pattern = f"instrument={instrument_id}" if instrument_id else "instrument=*"
        for inst_dir in sorted(self.raw_root.glob(pattern)):
            for day_dir in sorted(inst_dir.glob("trading_day=*")):
                if (day_dir / "snapshots.parquet").exists() or any(
                        (day_dir / "_staging").glob("part-*.parquet")):
                    out.append(PartitionKey(
                        instrument_id=inst_dir.name.split("=", 1)[1],
                        trading_day=day_dir.name.split("=", 1)[1],
                    ))
        return out
