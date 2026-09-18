"""Storage layer tests: staging -> finalize immutability + metadata db."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.common.schema import RAW_SCHEMA, PartitionKey
from app.storage.metadata_db import MetadataDB
from app.storage.parquet_store import RawParquetStore, sha256_of_file


def _table(rows: int, start_seq: int = 0) -> pa.Table:
    data = {
        "sequence_id": list(range(start_seq, start_seq + rows)),
        "local_receive_time_ns": [1_700_000_000_000_000_000 + i * 500_000_000
                                  for i in range(rows)],
        "batch_id": ["batch-test"] * rows,
        "trading_session": ["DAY"] * rows,
        "raw_source": ["synthetic"] * rows,
    }
    for name in RAW_SCHEMA.names[5:]:
        field = RAW_SCHEMA.field(name)
        if pa.types.is_integer(field.type):
            data[name] = [0] * rows
        elif pa.types.is_floating(field.type):
            data[name] = [2300.0] * rows
        else:
            data[name] = ["x"] * rows
    return pa.Table.from_pydict(data, schema=RAW_SCHEMA)


@pytest.fixture()
def store(tmp_path: Path):
    return RawParquetStore(tmp_path / "raw")


def test_staging_write_and_finalize(store):
    key = PartitionKey("C2701", "2026-09-18")
    store.write_staging_part(key, _table(10), 1)
    store.write_staging_part(key, _table(10, start_seq=10), 2)
    # read from staging
    t = store.read_partition(key)
    assert t.num_rows == 20

    result = store.finalize(key)
    assert result.rows == 20
    assert result.part_count == 2

    final = store.final_path(key)
    assert final.exists()
    assert not store.staging_dir(key).exists()
    # immutable: read-only permission bits
    mode = stat.S_IMODE(os.stat(final).st_mode)
    assert mode == 0o444
    # reading still works
    assert pq.read_table(final).num_rows == 20
    # sha recorded
    assert result.sha256 == sha256_of_file(final)


def test_finalize_preserves_previous_segment(store):
    key = PartitionKey("C2701", "2026-09-18")
    store.write_staging_part(key, _table(5), 1)
    first = store.finalize(key)
    original_hash = first.sha256
    store.write_staging_part(key, _table(5, start_seq=5), 2)
    assert store.read_partition(key).num_rows == 10
    second = store.finalize(key)
    assert second.path != first.path
    assert sha256_of_file(first.path) == original_hash
    assert store.read_partition(key).num_rows == 10
    assert len(store.final_files(key)) == 2


def test_staging_part_never_overwritten(store):
    key = PartitionKey("C2701", "2026-09-18")
    store.write_staging_part(key, _table(5), 1)
    with pytest.raises(Exception):
        store.write_staging_part(key, _table(5), 1)


def test_list_partitions(store):
    k1 = PartitionKey("C2701", "2026-09-18")
    k2 = PartitionKey("C2705", "2026-09-18")
    store.write_staging_part(k1, _table(3), 1)
    store.write_staging_part(k2, _table(3), 1)
    keys = store.list_partitions()
    assert {k.instrument_id for k in keys} == {"C2701", "C2705"}
    assert keys[0].instrument_id == "C2701"


def test_metadata_db_roundtrip(tmp_path: Path):
    db = MetadataDB(tmp_path / "meta.db")
    db.upsert_instrument("C2701", "C", "DCE", 1.0)
    db.create_batch("b1", ["tcp://x:1"], "9999", "12***89", "0.1.0",
                    "v6.7.13", "abc123")
    db.update_batch_instrument("b1", "C2701", 100, 100, {"2026-09-18"})
    db.update_batch_instrument("b1", "C2701", 250, 250,
                               {"2026-09-18", "2026-09-21"})
    db.close_batch("b1", "finished", "test")

    insts = db.get_instruments()
    assert insts[0]["instrument_id"] == "C2701"
    assert insts[0]["tick_size"] == 1.0

    batches = db.list_batches()
    assert batches[0]["batch_id"] == "b1"
    assert batches[0]["status"] == "finished"

    # analysis run lifecycle
    exp = db.create_experiment("test", {"a": 1}, "0.1.0")
    run = db.start_analysis_run("analyze", {"a": 1}, "C2701", ["2026-09-18"],
                                ["hash1"], exp)
    db.finish_analysis_run(run, "/tmp/out", "ok")
    runs = db.list_analysis_runs()
    assert runs[0]["run_id"] == run
    assert runs[0]["status"] == "ok"
    assert runs[0]["output_dir"] == "/tmp/out"
