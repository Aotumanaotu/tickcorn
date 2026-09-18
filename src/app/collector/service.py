"""CTP snapshot collection service.

Responsibilities:
    * own the CtpMdClient lifecycle (connect / login / subscribe / release),
    * auto re-login and re-subscribe on reconnect,
    * normalize every DepthMarketData into the canonical raw schema
      (with sequence ids, receive timestamps, session tags),
    * run the LIVE pairwise classifier for the dashboard,
    * buffer and flush rows to staging parquet (append-only),
    * write the live JSONL feed,
    * rotate + finalize partitions when the CTP TradingDay changes,
    * register everything (batch, instruments, raw files) in SQLite.

Threading:
    CTP callback thread -> queue -> writer thread -> parquet staging parts.
    The callback path is kept minimal (normalize + enqueue + live update).
"""

from __future__ import annotations

import json
import os
import queue
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pyarrow as pa

from app import __version__
from app.classifier.jump_classifier import is_valid_price
from app.collector.ctp_binding import CtpMdClient
from app.collector.live import LiveClassifier, LiveState, live_feed_path
from app.common.config import AppConfig
from app.common.exceptions import CollectorError
from app.common.logging import get_logger
from app.common.schema import RAW_SCHEMA, PartitionKey
from app.storage.metadata_db import MetadataDB
from app.storage.parquet_store import RawParquetStore

logger = get_logger("collector.service")

_DBL_MAX = 1.7976931348623157e308


@dataclass
class _InstrumentState:
    sequence_id: int = 0
    msg_count: int = 0
    trading_days: set = field(default_factory=set)
    last_recv_ns: int = 0
    last_exch_ts: str = ""


class CollectorService:
    def __init__(self, config: AppConfig, instruments: list[str],
                 user: str, password: str,
                 raw_source: str = "ctp:simnow",
                 with_dashboard: bool = False,
                 dashboard_port: Optional[int] = None,
                 note: str = ""):
        if not instruments:
            raise CollectorError("no instruments to collect")
        self.config = config
        self.instruments = list(dict.fromkeys(i.upper() for i in instruments))
        instruments = self.instruments
        self.user = user
        self.password = password
        self.raw_source = raw_source
        self.note = note

        self.batch_id = f"batch-{uuid.uuid4().hex[:12]}"
        self.db = MetadataDB(config.paths.metadata_db)
        self.store = RawParquetStore(config.paths.raw_dir)
        self.live = LiveState(history_points=config.dashboard.history_points)
        self.live.batch_id = self.batch_id

        self._states = {i: _InstrumentState() for i in self.instruments}
        self._tick_sizes = {i: config.resolve_tick_size(i)
                            for i in self.instruments}
        self._live_clf = {
            i: LiveClassifier(
                tick_size=self._tick_sizes[i],
                strict_symmetric_quote_move=config.classifier.strict_symmetric_quote_move,
                mid_tolerance_ticks=config.classifier.mid_tolerance_ticks,
                likely_bounce_requires_unit_move=(
                    config.classifier.likely_bounce_requires_unit_move),
            ) for i in self.instruments
        }
        self._queue: queue.Queue = queue.Queue(maxsize=200_000)
        self._stop = threading.Event()
        self._writer_started = threading.Event()
        self._writer_error = None
        self._writer_done = threading.Event()
        self._part_counters: dict[PartitionKey, int] = {}
        self._live_feed_file = None
        self._live_feed_path: Optional[Path] = None
        self._live_feed_rows = 0

        # Per-partition buffered rows for parquet writing.
        self._buffers: dict[PartitionKey, list[dict]] = {}
        self._buffer_lock = threading.Lock()

        self._dashboard = None
        self._dashboard_thread = None
        self._with_dashboard = with_dashboard
        self._dashboard_port = dashboard_port
        self._client_ref: Optional[CtpMdClient] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Blocking collection loop (Ctrl+C or SIGTERM to stop)."""
        from app.common.private_files import ProcessLock
        os.umask(0o077)
        lock = ProcessLock(self.config.paths.data_dir / "collector.lock")
        client = None
        writer_thread = None
        batch_created = False
        old_handlers = {}
        failure = None
        try:
            self._prepare()
            if not self.config.ctp.fronts or not self.config.ctp.broker_id:
                raise CollectorError("请先填写行情前置地址和 BrokerID")
            client = CtpMdClient(
                handler=self, flow_dir=self.config.paths.ctp_flow_dir,
                use_udp=self.config.ctp.use_udp,
                use_multicast=self.config.ctp.use_multicast,
                production_mode=self.config.ctp.production_mode)
            self._client_ref = client
            self.live.api_version = client.api_version
            for front in self.config.ctp.fronts:
                client.register_front(front)
            self.db.create_batch(
                batch_id=self.batch_id, fronts=self.config.ctp.fronts,
                broker_id=self.config.ctp.broker_id, user_masked="",
                app_version=__version__, api_version=client.api_version,
                config_hash=self.config.config_hash, note=self.raw_source)
            batch_created = True
            for inst in self.instruments:
                self.db.upsert_instrument(inst, product=_product_of(inst),
                    exchange=self.config.exchange_of(inst),
                    tick_size=self.config.resolve_tick_size(inst))
            writer_thread = threading.Thread(target=self._writer_loop, name="writer", daemon=True)
            writer_thread.start()
            if self._with_dashboard:
                self._start_dashboard()
            if threading.current_thread() is threading.main_thread():
                for sig in (signal.SIGINT, signal.SIGTERM):
                    old_handlers[sig] = signal.signal(sig, lambda *_: self.stop())
            self.live.set_connection("waiting_front", "connecting to front")
            client.init()
            logger.info("collecting %s (batch %s)", self.instruments, self.batch_id)
            while not self._stop.wait(0.5):
                if self._writer_error:
                    raise CollectorError("raw writer failed")
        except BaseException as exc:
            failure = exc
        finally:
            self.live.set_connection("stopping")
            try:
                # Release callbacks before asking the writer to drain its queue.
                if client is not None:
                    client.release()
                self._writer_done.set()
                if writer_thread:
                    writer_thread.join(timeout=60)
                    if writer_thread.is_alive():
                        raise CollectorError("writer did not stop; staging retained")
                if self._writer_error:
                    raise CollectorError("writer failed; check disk and staging")
                self._finalize_all()
            except BaseException as exc:
                failure = failure or exc
            finally:
                self._stop.set()
                self._close_live_feed()
                self.password = ""
                self.user = ""
                if self._dashboard:
                    self._dashboard.shutdown()
                for sig, handler in old_handlers.items():
                    signal.signal(sig, handler)
                try:
                    if batch_created:
                        self.db.close_batch(self.batch_id, status="failed" if failure else "finished")
                finally:
                    lock.close()
            self.live.set_connection("failed" if failure else "stopped")
        if failure:
            raise failure
        logger.info("batch %s closed; data finalized", self.batch_id)

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # CTP handler callbacks (called on CTP threads -- keep fast)
    # ------------------------------------------------------------------
    def on_front_connected(self) -> None:
        logger.info("front connected; logging in")
        self.live.set_connection("connected", "logging in")
        try:
            self._client_ref.login(self.config.ctp.broker_id, self.user,
                                   self.password)
        except Exception:
            logger.exception("login request failed")

    def on_front_disconnected(self, reason: int) -> None:
        logger.warning("front disconnected (reason=0x%x); CTP will auto-reconnect",
                       reason)
        self.live.set_connection("disconnected", f"reason=0x{reason:x}")

    def on_heartbeat_warning(self, seconds: int) -> None:
        logger.warning("heartbeat warning: %ss since last message", seconds)

    def on_rsp_user_login(self, trading_day, broker, user, error_id, error_msg):
        if error_id != 0:
            logger.error("login failed (code=%s)", error_id)
            self.live.set_connection("login_failed", f"登录失败，CTP 错误码 {error_id}")
            return
        logger.info("login succeeded; trading_day=%s", trading_day)
        self.live.ctp_trading_day = trading_day
        self.live.login_user_masked = ""
        self.live.set_connection("logged_in", f"trading_day={trading_day}")
        subscriptions = [i.lower() if self.config.exchange_of(i) in ("DCE", "SHFE", "INE")
                         else i for i in self.instruments]
        rc = self._client_ref.subscribe(subscriptions)
        logger.info("subscribe request sent (rc=%s)", rc)

    def on_rsp_sub_market_data(self, instrument, error_id, error_msg):
        if error_id != 0:
            logger.error("subscription failed (code=%s)", error_id)
            self.live.set_connection("subscription_failed", f"CTP 错误码 {error_id}")
        else:
            logger.info("subscribed: %s", instrument)

    def on_rsp_unsub_market_data(self, instrument, error_id, error_msg):
        logger.info("unsubscribed: %s (code=%s)", instrument, error_id)

    def on_rsp_error(self, error_id, error_msg):
        logger.error("CTP response error (code=%s)", error_id)

    def on_rsp_user_logout(self, error_id, error_msg):
        logger.info("logout (code=%s)", error_id)

    def on_depth_market_data(self, fields: dict) -> None:
        inst = fields.get("InstrumentID", "").upper()
        state = self._states.get(inst)
        if state is None:  # not requested but delivered
            return
        recv_ns = time.time_ns()
        state.sequence_id += 1
        state.msg_count += 1
        state.last_recv_ns = recv_ns
        trading_day = fields.get("TradingDay", "")
        state.trading_days.add(trading_day)

        from app.collector.ctp_binding import depth_fields_to_row
        row = depth_fields_to_row(
            fields,
            sequence_id=state.sequence_id,
            local_receive_time_ns=recv_ns,
            batch_id=self.batch_id,
            trading_session=self.config.session_of(fields.get("UpdateTime",
                                                              "00:00:00")),
            raw_source=self.raw_source,
        )

        bid = row.get("bid_price1")
        ask = row.get("ask_price1")
        last = row.get("last_price")
        spread = (ask - bid) if (is_valid_price(bid) and is_valid_price(ask)
                                 and ask > bid) else None
        mid = (bid + ask) / 2.0 if spread is not None else None
        bv, av = row.get("bid_volume1"), row.get("ask_volume1")
        obi1 = ((bv - av) / (bv + av)) if (bv is not None and av is not None
                                           and (bv + av) > 0) else None
        micro = None
        if spread is not None and bv is not None and av is not None and (
                bv + av) > 0:
            micro = (ask * bv + bid * av) / (bv + av)

        label, direction, derived = self._live_clf[inst].classify(inst, bid,
                                                                  ask, last)
        if label is None:
            derived = {}
        derived = dict(derived)
        if spread is not None:
            derived["spread"] = spread
            derived["spread_ticks"] = spread / self._tick_sizes[inst]
        if mid is not None:
            derived["mid_price"] = mid
        if obi1 is not None:
            derived["obi1"] = obi1
        if micro is not None:
            derived["microprice"] = micro

        live_state = self.live.instrument(inst)
        live_state.update(row, label, direction, derived)

        # cheap anomaly counters (logged at flush time)
        if is_valid_price(bid) and is_valid_price(ask) and bid > ask:
            live_state.note_anomaly("crossed_quote")
        if (is_valid_price(bid) and is_valid_price(ask) and is_valid_price(last)
                and not (bid - 1e-9 <= last <= ask + 1e-9)):
            live_state.note_anomaly("last_outside_quote")

        # live feed record (derived data only)
        self._enqueue(("live", {
            "ts_recv_ns": recv_ns,
            "instrument": inst,
            "batch_id": self.batch_id,
            "quote": {
                "trading_day": trading_day,
                "update_time": row.get("update_time"),
                "update_millisec": row.get("update_millisec"),
                "last_price": _clean_f(last), "bid_price1": _clean_f(bid),
                "ask_price1": _clean_f(ask), "bid_volume1": bv,
                "ask_volume1": av, "volume": row.get("volume"),
                "open_interest": row.get("open_interest"),
            },
            "derived": {k: _clean_f(v) for k, v in derived.items()},
            "label": label,
            "direction": direction,
        }))

        # raw row (canonical schema; values verbatim incl. sentinels)
        self._enqueue(("raw", (PartitionKey(inst, trading_day), row)))

    # ------------------------------------------------------------------
    # Writer thread
    # ------------------------------------------------------------------
    def _enqueue(self, item):
        while not self._writer_error:
            try:
                self._queue.put(item, timeout=0.5)
                return
            except queue.Full:
                continue
        self.stop()

    def _writer_loop(self) -> None:
        self._writer_started.set()
        last_flush = time.monotonic()
        try:
            while not (self._writer_done.is_set() and self._queue.empty()):
                try:
                    kind, payload = self._queue.get(timeout=0.2)
                except queue.Empty:
                    kind = None
                if kind == "live":
                    self._write_live(payload)
                elif kind == "raw":
                    key, row = payload
                    with self._buffer_lock:
                        self._buffers.setdefault(key, []).append(row)
                due = time.monotonic() - last_flush >= self.config.collector.flush_interval_s
                full = any(len(rows) >= self.config.collector.flush_rows for rows in self._buffers.values())
                if due or full:
                    self._flush()
                    last_flush = time.monotonic()
            self._flush()
        except Exception as exc:
            self._writer_error = exc
            logger.error("raw writer failed (%s); stopping collection", type(exc).__name__)
            self.stop()

    def _flush(self, keys=None) -> None:
        with self._buffer_lock:
            keys = [k for k in self._buffers if self._buffers[k]]
            buffers = {k: self._buffers.pop(k) for k in keys}
        pending = list(buffers.items())
        for pos, (key, rows) in enumerate(pending):
            try:
                self._write_part(key, rows)
            except Exception:
                with self._buffer_lock:
                    for remaining_key, remaining_rows in pending[pos + 1:]:
                        self._buffers[remaining_key] = remaining_rows + self._buffers.get(remaining_key, [])
                raise
        for inst, state in self._states.items():
            if state.msg_count:
                self.db.update_batch_instrument(
                    self.batch_id, inst, state.sequence_id, state.msg_count,
                    state.trading_days)

    def _write_part(self, key: PartitionKey, rows: list[dict]) -> None:
        try:
            arrays = {
                name: pa.array(
                    [r.get(name) for r in rows],
                    type=field.type,
                )
                for name, field in zip(RAW_SCHEMA.names, RAW_SCHEMA)
            }
            table = pa.Table.from_arrays(
                [arrays[n] for n in RAW_SCHEMA.names], schema=RAW_SCHEMA)
            previous = self._part_counters.get(key)
            if previous is None:
                previous = max((int(p.stem.split("-")[1]) for p in
                                self.store.staging_dir(key).glob("part-*.parquet")), default=0)
            idx = previous + 1
            self.store.write_staging_part(key, table, idx)
            self._part_counters[key] = idx
            logger.debug("wrote %s part %d (%d rows)", key, idx, len(rows))
        except Exception:
            logger.exception("failed writing staging part for %s; keeping rows",
                             key)
            with self._buffer_lock:
                self._buffers[key] = rows + self._buffers.get(key, [])
            raise

    # ------------------------------------------------------------------
    # Live feed
    # ------------------------------------------------------------------
    def _write_live(self, rec: dict) -> None:
        try:
            day = (rec.get("quote", {}).get("trading_day") or "").replace("-", "")
            if not day:
                return
            if (self._live_feed_path is None
                    or self._live_feed_path.name != f"live_{day}.jsonl"):
                self._close_live_feed()
                self.config.paths.live_dir.mkdir(parents=True, exist_ok=True)
                self._live_feed_path = live_feed_path(
                    self.config.paths.live_dir, rec["quote"]["trading_day"])
                self._live_feed_file = open(self._live_feed_path, "a",
                                            encoding="utf-8", buffering=1)
                with open(self._live_feed_path, encoding="utf-8") as stream:
                    self._live_feed_rows = sum(1 for _ in stream)
            self._live_feed_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._live_feed_rows += 1
            if self._live_feed_rows >= self.config.collector.live_feed_rows:
                self._close_live_feed()
                os.replace(self._live_feed_path,
                           self._live_feed_path.with_suffix(".1.jsonl"))
                self._live_feed_file = open(self._live_feed_path, "a",
                                            encoding="utf-8", buffering=1)
                self._live_feed_rows = 0
        except Exception:
            logger.exception("live feed write failed")

    def _close_live_feed(self) -> None:
        if self._live_feed_file:
            try:
                self._live_feed_file.flush()
                self._live_feed_file.close()
            except Exception:
                pass
            self._live_feed_file = None

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------
    def _finalize_all(self) -> None:
        self._flush()
        results = self.store.finalize_all()
        for r in results:
            self.db.register_raw_file(
                instrument_id=r.key.instrument_id,
                trading_day=r.key.trading_day,
                path=r.path,
                rows=r.rows,
                sha256=r.sha256,
                part_count=r.part_count,
                batch_id=self.batch_id)

    def _finalize_previous_staging(self) -> None:
        """Finalize staging left over from a crashed previous run."""
        leftovers = self.store.list_staging_partitions()
        if not leftovers:
            return
        logger.info("finalizing %d leftover staging partition(s) from previous "
                    "run(s)", len(leftovers))
        for key in leftovers:
            try:
                r = self.store.finalize(key)
                if r is not None:
                    self.db.register_raw_file(
                        instrument_id=key.instrument_id,
                        trading_day=key.trading_day, path=r.path, rows=r.rows,
                        sha256=r.sha256, part_count=r.part_count,
                        batch_id=None)
            except Exception:
                logger.exception("could not finalize leftover %s", key)
                raise

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _prepare(self) -> None:
        for d in (self.config.paths.raw_dir, self.config.paths.processed_dir,
                  self.config.paths.features_dir, self.config.paths.reports_dir,
                  self.config.paths.models_dir, self.config.paths.live_dir,
                  self.config.paths.ctp_flow_dir):
            d.mkdir(parents=True, exist_ok=True)
        if self.config.collector.finalize_on_start:
            self._finalize_previous_staging()
        # Recover files published before a crash interrupted SQLite registration.
        known = {r["path"] for r in self.db.get_raw_files()}
        from app.storage.parquet_store import sha256_of_file
        for key in self.store.list_partitions():
            for path in self.store.final_files(key):
                if str(path) not in known:
                    self.db.register_raw_file(key.instrument_id, key.trading_day, path,
                        self.store._rowcount(path), sha256_of_file(path), 0, batch_id=None)

    def _start_dashboard(self) -> None:
        from app.dashboard.server import DashboardServer
        port = self._dashboard_port or self.config.dashboard.port
        self._dashboard = DashboardServer(self.live, host=self.config.dashboard.host,
                                          port=port)
        self._dashboard_thread = threading.Thread(
            target=self._dashboard.serve_forever, daemon=True,
            name="dashboard")
        self._dashboard_thread.start()

def _mask_user(user: str) -> str:
    if len(user) <= 2:
        return user[:1] + "*"
    return user[:2] + "***" + user[-2:]


def _product_of(instrument_id: str) -> str:
    import re
    m = re.match(r"^([A-Za-z]+)", instrument_id)
    return m.group(1).upper() if m else "?"


def _clean_f(v):
    """For the live feed: sentinels -> None (JSON friendly)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or abs(f) >= 1e300:
        return None
    return f
