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


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
        tmp = path.with_suffix(".tmp")
        pq.write_table(table, tmp, compression="zstd")
        with tmp.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        _sync_directory(sdir)
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

        Later collections append content-addressed segments. No finalized file
        is ever overwritten; interrupted publications are recovered idempotently.
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
        if force:
            raise StorageError("Replacing finalized raw data is not supported")
        tmp_path = key.dir(self.raw_root) / "snapshots.parquet.tmp"
        expected_rows = 0
        with pq.ParquetWriter(tmp_path, RAW_SCHEMA, compression="zstd") as writer:
            for part in parts:
                for batch in pq.ParquetFile(part).iter_batches(batch_size=65536):
                    table = pa.Table.from_batches([batch]).cast(RAW_SCHEMA)
                    expected_rows += table.num_rows
                    writer.write_table(table)
        if self._rowcount(tmp_path) != expected_rows:
            raise StorageError("Raw finalization row count mismatch")
        with tmp_path.open("rb") as stream:
            os.fsync(stream.fileno())
        digest = sha256_of_file(tmp_path)
        # Recover publication interrupted before staging cleanup idempotently.
        existing = next((p for p in self.final_files(key)
                         if sha256_of_file(p) == digest), None)
        if existing:
            final_path = existing
            tmp_path.unlink()
        else:
            if final_path.exists():
                final_path = key.dir(self.raw_root) / f"snapshots-{digest}.parquet"
            os.replace(tmp_path, final_path)
        result = FinalizeResult(key, expected_rows, len(parts), digest, final_path)
        os.chmod(final_path, 0o444)
        _sync_directory(final_path.parent)
        # Delete only the parts included above, never a concurrent new part.
        for part in parts:
            part.unlink()
        try:
            sdir.rmdir()
        except OSError:
            pass

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

    def final_files(self, key: PartitionKey) -> list[Path]:
        return sorted(key.dir(self.raw_root).glob("snapshots*.parquet"))

    def partition_files(self, key: PartitionKey) -> list[Path]:
        return self.final_files(key) + sorted(self.staging_dir(key).glob("part-*.parquet"))

    def read_partition(self, key: PartitionKey, columns: Optional[list[str]] = None
                       ) -> pa.Table:
        paths = self.partition_files(key)
        if not paths:
            raise StorageError(f"no data for partition {key}")
        return pa.concat_tables([pq.ParquetFile(p).read(columns=columns) for p in paths])

    def list_partitions(self, instrument_id: Optional[str] = None
                        ) -> list[PartitionKey]:
        """All partitions with data (finalized or staging)."""
        out: list[PartitionKey] = []
        if not self.raw_root.exists():
            return out
        pattern = f"instrument={instrument_id}" if instrument_id else "instrument=*"
        for inst_dir in sorted(self.raw_root.glob(pattern)):
            for day_dir in sorted(inst_dir.glob("trading_day=*")):
                if any(day_dir.glob("snapshots*.parquet")) or any(
                        (day_dir / "_staging").glob("part-*.parquet")):
                    out.append(PartitionKey(
                        instrument_id=inst_dir.name.split("=", 1)[1],
                        trading_day=day_dir.name.split("=", 1)[1],
                    ))
        return out
