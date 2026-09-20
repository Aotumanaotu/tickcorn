"""Gateway archiver: in-memory row buffer -> staging parquet -> finalize.

Parquet is the source of truth. A parquet write failure propagates to the
caller (which stops the stream); PostgreSQL bookkeeping (GatewayBatch /
RawFile ORM from app.storage.models) and the TimescaleWriter are strictly
best-effort: any PG failure degrades to parquet-only with a warning and
never interrupts market data.

The buffering pattern follows the legacy collector's writer thread, but
runs on asyncio: sync buffering on the event loop, blocking parquet IO
via asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Any, Optional

import pyarrow as pa
from sqlalchemy import JSON as SA_JSON, update
from sqlalchemy import inspect as sa_inspect, select

from app.common.config import AppConfig
from app.common.logging import get_logger
from app.common.schema import RAW_SCHEMA, PartitionKey
from app.common.timeutils import BEIJING_TZ, normalize_ctp_day
from app.core.events import EVENT_MICRO, EVENT_QUOTE
from app.storage.parquet_store import RawParquetStore

logger = get_logger("gateway.archiver")

try:  # storage agent lands models.py in parallel; degrade if absent
    from app.storage.models import GatewayBatch, RawFile
except Exception:  # pragma: no cover - import-time degradation
    GatewayBatch = None  # type: ignore[assignment]
    RawFile = None  # type: ignore[assignment]


def _now_dt() -> datetime:
    return datetime.now(BEIJING_TZ)


def uuid12() -> str:
    """Short unique batch id (legacy collector style)."""
    return f"batch-{uuid.uuid4().hex[:12]}"


class Archiver:
    """Buffer canonical rows, flush staging parts, finalize partitions."""

    def __init__(self, config: AppConfig, engine: Any = None,
                 timescale: Any = None):
        self._config = config
        self._store = RawParquetStore(config.paths.raw_dir)
        self._flush_interval_s = float(config.collector.flush_interval_s)
        self._flush_rows = int(config.collector.flush_rows)
        self._lock = asyncio.Lock()
        self._buffers: dict[PartitionKey, list[dict]] = {}
        self._part_counters: dict[PartitionKey, int] = {}
        self._active_keys: set[PartitionKey] = set()
        self._registered_paths: set[str] = set()
        self._batch_id: Optional[str] = None
        self._batch_pg_task: Optional[asyncio.Task] = None
        self._last_flush = time.monotonic()
        self._engine = engine
        self._session_factory = None
        if engine is not None:
            try:
                from sqlalchemy.ext.asyncio import async_sessionmaker
                self._session_factory = async_sessionmaker(
                    engine, expire_on_commit=False)
            except Exception:  # pragma: no cover
                logger.warning("session factory 创建失败；仅写 parquet",
                               exc_info=True)
        self._pg_ok = engine is not None and self._session_factory is not None
        if self._pg_ok and (GatewayBatch is None or RawFile is None):
            self._pg_ok = False
            logger.warning("app.storage.models 不可用；批次元数据不写数据库")
        self._timescale = timescale
        self._timescale_started = False
        if self._timescale is None and engine is not None:
            try:
                from app.storage.timescale import TimescaleWriter
                self._timescale = TimescaleWriter(engine)
            except Exception:
                logger.warning("TimescaleWriter 不可用；事件不写时序库",
                               exc_info=True)
                self._timescale = None

    # ------------------------------------------------------------------
    # Batch lifecycle
    # ------------------------------------------------------------------
    def begin_batch(self, fronts: list[str], broker_id: str,
                    user_masked: str, instruments: list[str],
                    raw_source: str, config_hash: str) -> str:
        """Start a new collection batch; returns its id (PG write is
        scheduled in the background and fully degradable)."""
        batch_id = uuid12()
        self._batch_id = batch_id
        self._buffers.clear()
        self._part_counters.clear()
        self._active_keys.clear()
        self._batch_pg_task = None
        if self._pg_ok:
            meta = {
                "batch_id": batch_id, "started_at": _now_dt(),
                "ended_at": None, "status": "running",
                "fronts": list(fronts), "broker_id": broker_id,
                "user_masked": user_masked, "instruments": list(instruments),
                "raw_source": raw_source, "config_hash": config_hash,
            }
            self._batch_pg_task = asyncio.get_running_loop().create_task(
                self._pg_begin_batch(meta))
        logger.info("batch %s begun (source=%s, instruments=%s)",
                    batch_id, raw_source, instruments)
        return batch_id

    def add_row(self, row: dict, events: list[dict]) -> None:
        """Buffer one canonical row and its envelopes (quote/micro)."""
        if self._batch_id is None:
            raise RuntimeError("add_row called before begin_batch")
        inst = str(row.get("instrument_id") or "").upper()
        day = row.get("trading_day") or row.get("action_day")
        key = PartitionKey(inst, normalize_ctp_day(str(day or "")))
        self._buffers.setdefault(key, []).append(row)
        self._active_keys.add(key)
        if self._timescale is not None:
            for env in events or []:
                try:
                    if env.get("type") == EVENT_QUOTE:
                        self._timescale.add_quote_event(env)
                    elif env.get("type") == EVENT_MICRO:
                        self._timescale.add_micro_event(env)
                except Exception:
                    logger.warning("时序库缓冲失败；后续事件不再写入",
                                   exc_info=True)
                    self._timescale = None
                    break

    # ------------------------------------------------------------------
    # Flushing / finalization
    # ------------------------------------------------------------------
    async def flush(self, force: bool = False) -> None:
        async with self._lock:
            await self._flush_locked(force)

    async def finalize(self, status: str) -> None:
        async with self._lock:
            await self._finalize_locked(status)

    async def prepare(self) -> None:
        """Startup recovery: finalize leftover staging from crashed runs
        (config collector.finalize_on_start) and start the timescale
        writer when the database bookkeeping is healthy."""
        if self._config.collector.finalize_on_start:
            leftovers = self._store.list_staging_partitions()
            if leftovers:
                logger.info("finalizing %d leftover staging partition(s) "
                            "from previous run(s)", len(leftovers))
                for key in leftovers:
                    result = await asyncio.to_thread(self._store.finalize, key)
                    if result is not None:
                        await self._pg_register_final(result, batch_id=None)
        if self._pg_ok and RawFile is not None:
            try:
                paths = await self._pg_load_registered_paths()
                self._registered_paths.update(paths)
            except Exception:
                self._pg_degrade("读取 RawFile 已登记文件失败")
        if self._timescale is not None and self._pg_ok:
            try:
                await self._timescale.start()
                self._timescale_started = True
            except Exception:
                logger.warning("TimescaleWriter 启动失败；事件不写时序库",
                               exc_info=True)
                self._timescale = None

    # ------------------------------------------------------------------
    async def _flush_locked(self, force: bool) -> None:
        if not self._buffers:
            return
        due = force or (time.monotonic() - self._last_flush
                        >= self._flush_interval_s)
        full = any(len(rows) >= self._flush_rows
                   for rows in self._buffers.values())
        if not (due or full):
            return
        buffers, self._buffers = self._buffers, {}
        remaining, error = await asyncio.to_thread(self._write_buffers, buffers)
        for key, rows in remaining.items():
            self._buffers[key] = rows + self._buffers.get(key, [])
        if error is not None:
            raise error
        self._last_flush = time.monotonic()

    async def _finalize_locked(self, status: str) -> None:
        if self._batch_pg_task is not None:
            try:
                await self._batch_pg_task
            except Exception:  # noqa: BLE001
                pass
            self._batch_pg_task = None
        if self._batch_id is not None:
            await self._flush_locked(force=True)
            keys = sorted(self._active_keys
                          | set(self._store.list_staging_partitions()),
                          key=str)
            files = 0
            for key in keys:
                result = await asyncio.to_thread(self._store.finalize, key)
                if result is None:
                    continue
                files += 1
                await self._pg_register_final(result, batch_id=self._batch_id)
            await self._pg_finish_batch(status)
            logger.info("batch %s finalized (status=%s, %d file(s))",
                        self._batch_id, status, files)
            self._batch_id = None
            self._active_keys.clear()
        if self._timescale is not None and self._timescale_started:
            try:
                await self._timescale.stop()
            except Exception:
                logger.warning("TimescaleWriter 停止失败", exc_info=True)
            self._timescale_started = False
            self._timescale = None

    # ------------------------------------------------------------------
    # Parquet writing (blocking, runs in a worker thread)
    # ------------------------------------------------------------------
    def _write_buffers(self, buffers: dict[PartitionKey, list[dict]]):
        remaining = dict(buffers)
        error: Optional[BaseException] = None
        for key, rows in buffers.items():
            if error is not None:
                break
            try:
                self._write_part(key, rows)
                del remaining[key]
            except BaseException as exc:  # noqa: BLE001
                error = exc
        return remaining, error

    def _write_part(self, key: PartitionKey, rows: list[dict]) -> None:
        arrays = {
            name: pa.array([r.get(name) for r in rows], type=field.type)
            for name, field in zip(RAW_SCHEMA.names, RAW_SCHEMA)
        }
        table = pa.Table.from_arrays(
            [arrays[n] for n in RAW_SCHEMA.names], schema=RAW_SCHEMA)
        idx = self._next_part_index(key)
        self._store.write_staging_part(key, table, idx)
        self._part_counters[key] = idx
        logger.debug("wrote staging part %d for %s (%d rows)", idx, key,
                     len(rows))

    def _next_part_index(self, key: PartitionKey) -> int:
        previous = self._part_counters.get(key)
        if previous is None:
            previous = max(
                (int(p.stem.split("-")[1])
                 for p in self._store.staging_dir(key).glob("part-*.parquet")),
                default=0)
        return previous + 1

    # ------------------------------------------------------------------
    # PostgreSQL bookkeeping (all best-effort)
    # ------------------------------------------------------------------
    def _pg_degrade(self, message: str) -> None:
        self._pg_ok = False
        logger.warning("%s；本批次仅写 parquet，行情不受影响", message)

    @staticmethod
    def _orm_values(model: Any, payload: dict) -> dict:
        """Filter a payload to the model's column attributes, JSON-encoding
        lists/dicts unless the column itself is a JSON type."""
        try:
            known = set(sa_inspect(model).attrs.keys())
        except Exception:  # pragma: no cover - defensive
            return {}
        values: dict = {}
        for key, value in payload.items():
            if key not in known:
                continue
            if isinstance(value, (list, dict)):
                try:
                    col_type = sa_inspect(model).attrs[key].columns[0].type
                    is_json = isinstance(col_type, SA_JSON)
                except Exception:  # pragma: no cover - defensive
                    is_json = False
                if not is_json:
                    value = json.dumps(value, ensure_ascii=False)
            values[key] = value
        return values

    async def _pg_begin_batch(self, meta: dict) -> None:
        if not self._pg_ok or GatewayBatch is None:
            return
        try:
            values = self._orm_values(GatewayBatch, meta)
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(GatewayBatch(**values))
        except Exception:
            self._pg_degrade(f"GatewayBatch 写入失败 ({meta.get('batch_id')})")

    async def _pg_finish_batch(self, status: str) -> None:
        if not self._pg_ok or GatewayBatch is None or self._batch_id is None:
            return
        try:
            values = self._orm_values(
                GatewayBatch, {"status": status, "ended_at": _now_dt()})
            if not values:
                return
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
                        update(GatewayBatch).where(
                            GatewayBatch.batch_id == self._batch_id
                        ).values(**values))
        except Exception:
            self._pg_degrade(f"GatewayBatch 状态更新失败 ({self._batch_id})")

    async def _pg_load_registered_paths(self) -> set[str]:
        if not self._pg_ok or RawFile is None:
            return set()
        async with self._session_factory() as session:
            result = await session.execute(select(RawFile.path))
            return {str(row[0]) for row in result}

    async def _pg_register_final(self, result: Any,
                                 batch_id: Optional[str]) -> None:
        """Register one finalized parquet file in RawFile (idempotent by
        path; failure only degrades)."""
        if not self._pg_ok or RawFile is None:
            return
        path = str(result.path)
        if path in self._registered_paths:
            return
        payload = {
            "instrument_id": result.key.instrument_id,
            "trading_day": result.key.trading_day,
            "path": path,
            "rows": int(result.rows),
            "sha256": result.sha256,
            "size_bytes": result.path.stat().st_size,
            "part_count": int(result.part_count),
            "batch_id": batch_id,
            "registered_at": _now_dt(),
        }
        try:
            values = self._orm_values(RawFile, payload)
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(RawFile(**values))
            self._registered_paths.add(path)
        except Exception:
            self._pg_degrade(f"RawFile 注册失败 ({path})")
