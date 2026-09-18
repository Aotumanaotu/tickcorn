"""SQLite metadata database.

SQLite stores ONLY metadata (never snapshot data):
    * instruments        -- known instruments and their tick size
    * batches            -- collection batch provenance
    * batch_instruments  -- per-batch, per-instrument counters
    * raw_files          -- finalized parquet file index (path, rows, sha256)
    * experiments        -- parameter sets (config hash + params json)
    * analysis_runs      -- executed analyses (command, inputs, outputs, status)
    * model_versions     -- placeholder for Phase 2
    * backtest_versions  -- placeholder for Phase 3
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from app.common.exceptions import StorageError
from app.common.logging import get_logger

logger = get_logger("storage.metadata")

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id TEXT PRIMARY KEY,
    product       TEXT NOT NULL,
    exchange      TEXT,
    tick_size     REAL,
    first_seen    TEXT,
    last_seen     TEXT
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id     TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT,
    app_version  TEXT,
    api_version  TEXT,
    fronts       TEXT,
    broker_id    TEXT,
    user_masked  TEXT,
    config_hash  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    note         TEXT
);
CREATE TABLE IF NOT EXISTS batch_instruments (
    batch_id      TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    first_seq     INTEGER,
    last_seq      INTEGER,
    msg_count     INTEGER NOT NULL DEFAULT 0,
    trading_days  TEXT,
    PRIMARY KEY (batch_id, instrument_id)
);
CREATE TABLE IF NOT EXISTS raw_files (
    file_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id TEXT NOT NULL,
    trading_day   TEXT NOT NULL,
    path          TEXT NOT NULL,
    rows          INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    part_count    INTEGER,
    batch_id      TEXT,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'final',
    UNIQUE (instrument_id, trading_day, batch_id)
);
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    purpose       TEXT,
    params_json   TEXT NOT NULL,
    code_version  TEXT
);
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id        TEXT PRIMARY KEY,
    experiment_id TEXT,
    command       TEXT NOT NULL,
    params_json   TEXT NOT NULL,
    instrument_id TEXT,
    trading_days  TEXT,
    input_files   TEXT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    output_dir    TEXT,
    status        TEXT NOT NULL DEFAULT 'running'
);
CREATE TABLE IF NOT EXISTS model_versions (
    model_id    TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    run_id      TEXT,
    kind        TEXT,
    params_json TEXT,
    artifact    TEXT
);
CREATE TABLE IF NOT EXISTS backtest_versions (
    backtest_id TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    model_id    TEXT,
    params_json TEXT,
    artifact    TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_files_inst ON raw_files(instrument_id, trading_day);
CREATE INDEX IF NOT EXISTS idx_runs_inst ON analysis_runs(instrument_id);
"""


class MetadataDB:
    """Thin, explicit wrapper around the metadata sqlite database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            cur = conn.execute("SELECT version FROM schema_version")
            row = cur.fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
            elif row[0] != SCHEMA_VERSION:
                raise StorageError(
                    f"metadata db schema version {row[0]} != expected {SCHEMA_VERSION}")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Instruments
    # ------------------------------------------------------------------
    def upsert_instrument(self, instrument_id: str, product: str, exchange: str,
                          tick_size: float) -> None:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO instruments
                       (instrument_id, product, exchange, tick_size, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(instrument_id) DO UPDATE SET
                       last_seen = excluded.last_seen,
                       tick_size = COALESCE(excluded.tick_size, tick_size)""",
                (instrument_id, product, exchange, tick_size, now, now))

    def get_instruments(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM instruments ORDER BY instrument_id").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Batches
    # ------------------------------------------------------------------
    def create_batch(self, batch_id: str, fronts: Iterable[str], broker_id: str,
                     user_masked: str, app_version: str, api_version: str,
                     config_hash: str, note: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO batches (batch_id, started_at, app_version, api_version,
                                        fronts, broker_id, user_masked, config_hash,
                                        status, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
                (batch_id, _now(), app_version, api_version,
                 json.dumps(list(fronts)), broker_id, user_masked, config_hash, note))

    def close_batch(self, batch_id: str, status: str = "finished",
                    note: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE batches SET ended_at = ?, status = ?, "
                "note = COALESCE(NULLIF(?, ''), note) WHERE batch_id = ?",
                (_now(), status, note, batch_id))

    def update_batch_instrument(self, batch_id: str, instrument_id: str,
                                last_seq: int, msg_count: int,
                                trading_days: set[str]) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT msg_count, trading_days FROM batch_instruments "
                "WHERE batch_id = ? AND instrument_id = ?",
                (batch_id, instrument_id)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO batch_instruments VALUES (?, ?, ?, ?, ?, ?)",
                    (batch_id, instrument_id, last_seq, last_seq, msg_count,
                     json.dumps(sorted(trading_days))))
            else:
                old_days: set[str] = set(json.loads(row["trading_days"] or "[]"))
                conn.execute(
                    """UPDATE batch_instruments
                       SET last_seq = ?, msg_count = ?, trading_days = ?
                       WHERE batch_id = ? AND instrument_id = ?""",
                    (last_seq, msg_count, json.dumps(sorted(old_days | trading_days)),
                     batch_id, instrument_id))

    def list_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM batches ORDER BY started_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Raw files
    # ------------------------------------------------------------------
    def register_raw_file(self, instrument_id: str, trading_day: str, path: Path,
                          rows: int, sha256: str, part_count: int,
                          batch_id: Optional[str]) -> int:
        with self.connect() as conn:
            existing = conn.execute("SELECT file_id, sha256 FROM raw_files WHERE path = ?",
                                    (str(path),)).fetchone()
            if existing:
                if existing["sha256"] != sha256:
                    raise ValueError("Raw file hash changed after registration")
                return int(existing["file_id"])
            cur = conn.execute(
                """INSERT INTO raw_files
                       (instrument_id, trading_day, path, rows, sha256, size_bytes,
                        part_count, batch_id, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')""",
                (instrument_id, trading_day, str(path), rows, sha256,
                 path.stat().st_size, part_count, batch_id, _now()))
            return int(cur.lastrowid or -1)

    def get_raw_files(self, instrument_id: Optional[str] = None,
                      trading_day: Optional[str] = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM raw_files WHERE 1=1"
        args: list[Any] = []
        if instrument_id:
            q += " AND instrument_id = ?"
            args.append(instrument_id)
        if trading_day:
            q += " AND trading_day = ?"
            args.append(trading_day)
        q += " ORDER BY instrument_id, trading_day"
        with self.connect() as conn:
            rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Experiments / analysis runs
    # ------------------------------------------------------------------
    def create_experiment(self, purpose: str, params: dict[str, Any],
                          code_version: str) -> str:
        experiment_id = f"exp-{uuid.uuid4().hex[:12]}"
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO experiments VALUES (?, ?, ?, ?, ?)",
                (experiment_id, _now(), purpose,
                 json.dumps(params, sort_keys=True, ensure_ascii=False), code_version))
        return experiment_id

    def start_analysis_run(self, command: str, params: dict[str, Any],
                           instrument_id: str, trading_days: list[str],
                           input_files: list[str], experiment_id: str) -> str:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO analysis_runs (run_id, experiment_id, command, params_json,"
                " instrument_id, trading_days, input_files, started_at, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')",
                (run_id, experiment_id, command,
                 json.dumps(params, sort_keys=True, ensure_ascii=False),
                 instrument_id, json.dumps(trading_days),
                 json.dumps(input_files), _now()))
        return run_id

    def finish_analysis_run(self, run_id: str, output_dir: str,
                            status: str = "ok") -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE analysis_runs SET finished_at = ?, output_dir = ?, status = ? "
                "WHERE run_id = ?", (_now(), output_dir, status, run_id))

    def list_analysis_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analysis_runs ORDER BY started_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------
    def verify_raw_files(self, instrument_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Recompute sha256 of registered raw files; return mismatches."""
        results = []
        for rec in self.get_raw_files(instrument_id=instrument_id):
            path = Path(rec["path"])
            if not path.exists():
                results.append({**rec, "check": "missing"})
                continue
            from app.storage.parquet_store import sha256_of_file
            actual = sha256_of_file(path)
            if actual != rec["sha256"]:
                results.append({**rec, "check": "sha256_mismatch",
                                "actual_sha256": actual})
        return results


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
