"""CTP gateway process orchestration.

run_gateway() is the single entry point of the standalone gateway
process. It owns the unix-socket publisher, the archiver (parquet +
best-effort PG), the normalizer and either the real CtpMdClient or a
SimulatedFeed (dev / CI / demo without the CTP SDK).

Connection state machine (broadcast as status frames):
    idle -> connecting -> connected -> streaming
    front loss / login failure -> disconnected / failed
Every state change is broadcast; a status heartbeat goes out every 10s.

CTP callbacks arrive on CTP worker threads: they only post into a
thread-safe inbox; an asyncio drain task processes them on the loop.
"""

from __future__ import annotations

import asyncio
import os
import queue
import signal
import time
from datetime import datetime
from typing import Any, Optional

from app.common.config import AppConfig
from app.common.exceptions import ConfigError
from app.common.logging import get_logger
from app.common.private_files import ProcessLock, write_private_json
from app.common.runtime import WebSettings
from app.common.timeutils import BEIJING_TZ
from app.core.events import (CONN_CONNECTED, CONN_CONNECTING,
                             CONN_DISCONNECTED, CONN_FAILED, CONN_IDLE,
                             CONN_STREAMING, DATA_LIVE)
from app.core.normalizer import MarketDataNormalizer
from app.gateway.archiver import Archiver
from app.gateway.control import (mask_user, validate_connect_payload,
                                 validate_subscribe_payload)
from app.gateway.native.ctp_binding import CtpMdClient, depth_fields_to_row
from app.gateway.publisher import GatewayPublisher
from app.gateway.simulate import SimulatedFeed
from app.ingest.protocol import (CMD_CONNECT, CMD_DISCONNECT, CMD_SUBSCRIBE,
                                 CMD_UNSUBSCRIBE)

logger = get_logger("gateway.process")

_STATUS_INTERVAL_S = 10.0
_MAIN_LOOP_TICK_S = 1.0
_DB_INIT_RETRIES = 10
_DB_INIT_RETRY_S = 2.0
_CREDENTIALS_FILENAME = "gateway-credentials.local.json"
_SUBSCRIBE_LOWERCASE_EXCHANGES = ("DCE", "SHFE", "INE")
_SIMULATE_DEFAULT_INSTRUMENT = "C2611"


class _GatewayRuntime:
    """All mutable gateway state plus the CTP handler callbacks."""

    def __init__(self, config: AppConfig, settings: WebSettings, *,
                 simulate: bool, simulate_instruments: list[str] | None,
                 simulate_rate_hz: float, simulate_seed: int):
        self._config = config
        self._settings = settings
        self._simulate = simulate
        self._simulate_instruments = (
            [i.upper() for i in simulate_instruments]
            if simulate_instruments else [_SIMULATE_DEFAULT_INSTRUMENT])
        self._simulate_rate_hz = float(simulate_rate_hz)
        self._simulate_seed = int(simulate_seed)

        self._state = CONN_IDLE
        self._detail = ""
        self._instruments: dict[str, str] = {}
        self._batch_id: Optional[str] = None
        self._started_at = datetime.now(BEIJING_TZ).isoformat(
            timespec="seconds")
        self._last_event_ns: Optional[int] = None
        self._events_published = 0

        self._normalizer = MarketDataNormalizer(
            tick_size_resolver=config.resolve_tick_size,
            strict_symmetric_quote_move=config.classifier.strict_symmetric_quote_move,
            mid_tolerance_ticks=config.classifier.mid_tolerance_ticks,
            likely_bounce_requires_unit_move=(
                config.classifier.likely_bounce_requires_unit_move))
        self._seq: dict[str, int] = {}

        self._publisher = GatewayPublisher(
            on_control=self._on_control, on_shutdown=self._on_shutdown)
        self._archiver: Optional[Archiver] = None
        self._engine: Any = None

        self._feed: Optional[SimulatedFeed] = None
        self._feed_task: Optional[asyncio.Task] = None
        self._client: Optional[CtpMdClient] = None
        self._credentials: Optional[dict] = None

        self._inbox: queue.SimpleQueue = queue.SimpleQueue()
        self._wake: Optional[asyncio.Event] = None
        self._drain_task: Optional[asyncio.Task] = None
        self._installed_signals: list = []

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event = asyncio.Event()
        self._shutting_down = False
        self._failure = False
        self._stopped = False

    # ------------------------------------------------------------------
    async def run(self) -> int:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        for d in (self._config.paths.raw_dir, self._config.paths.processed_dir,
                  self._config.paths.features_dir, self._config.paths.reports_dir,
                  self._config.paths.models_dir, self._config.paths.live_dir,
                  self._config.paths.ctp_flow_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._engine = self._build_engine()
        await self._ensure_database()
        self._archiver = Archiver(self._config, engine=self._engine)
        await self._publisher.start(self._settings.gateway_socket)
        try:
            await self._archiver.prepare()
            self._drain_task = asyncio.create_task(
                self._drain_inbox(), name="gateway-inbox")
            if self._simulate:
                await self._start_simulate()
            else:
                await self._broadcast_status()
            self._install_signal_handlers(self._loop)
            await self._main_loop()
        finally:
            await self._shutdown()
        return 0

    # ------------------------------------------------------------------
    # Main loop: periodic archiver flush + status heartbeat
    # ------------------------------------------------------------------
    async def _main_loop(self) -> None:
        last_status = self._loop.time()
        while not self._stop_event.is_set() and not self._shutting_down:
            try:
                await asyncio.wait_for(self._stop_event.wait(),
                                       timeout=_MAIN_LOOP_TICK_S)
                break
            except asyncio.TimeoutError:
                pass
            if self._archiver is not None:
                try:
                    await self._archiver.flush()
                except Exception:
                    logger.exception("archiver flush 失败；停止行情流")
                    self._failure = True
                    break
            now = self._loop.time()
            if now - last_status >= _STATUS_INTERVAL_S:
                last_status = now
                await self._broadcast_status()

    # ------------------------------------------------------------------
    # Control handlers (invoked by the publisher; return dict or raise)
    # ------------------------------------------------------------------
    async def _on_control(self, cmd: str, payload: dict) -> dict:
        if cmd == CMD_CONNECT:
            return await self._ctl_connect(payload)
        if cmd == CMD_DISCONNECT:
            return await self._ctl_disconnect(payload)
        if cmd == CMD_SUBSCRIBE:
            return await self._ctl_subscribe(payload)
        if cmd == CMD_UNSUBSCRIBE:
            return await self._ctl_unsubscribe(payload)
        raise ValueError(f"未知控制命令: {cmd}")

    async def _ctl_connect(self, payload: dict) -> dict:
        if self._simulate:
            raise ValueError("simulate 模式不支持 connect 命令")
        if self._state not in (CONN_IDLE, CONN_DISCONNECTED, CONN_FAILED):
            raise ValueError("当前状态不允许 connect")
        params = validate_connect_payload(payload)
        instruments = params["instruments"]
        for inst in instruments:
            try:
                self._config.resolve_tick_size(inst)
            except ConfigError as exc:
                raise ValueError(f"合约 {inst} 配置无效: {exc}") from exc
        self._credentials = {
            "broker_id": params["broker_id"], "user": params["user"],
            "password": params["password"], "remember": params["remember"],
        }
        if params["remember"]:
            self._store_credentials(params)
        assert self._archiver is not None
        self._batch_id = self._archiver.begin_batch(
            fronts=params["fronts"], broker_id=params["broker_id"],
            user_masked=mask_user(params["user"]), instruments=instruments,
            raw_source="ctp", config_hash=self._config.config_hash)
        try:
            client = CtpMdClient(
                handler=self, flow_dir=self._config.paths.ctp_flow_dir,
                use_udp=self._config.ctp.use_udp,
                use_multicast=self._config.ctp.use_multicast,
                production_mode=self._config.ctp.production_mode)
            for front in params["fronts"]:
                client.register_front(front)
        except Exception:
            self._credentials = None
            self._instruments = {}
            await self._archiver.finalize("failed")
            self._batch_id = None
            raise
        self._client = client
        self._instruments = {inst: "subscribing" for inst in instruments}
        await self._set_state(CONN_CONNECTING,
                              f"连接行情前置（{len(params['fronts'])} 个）")
        client.init()
        return {"batch_id": self._batch_id, "state": self._state}

    async def _ctl_disconnect(self, payload: dict) -> dict:
        if (self._client is None and self._feed_task is None
                and self._batch_id is None):
            raise ValueError("当前未连接")
        await self._stop_stream()
        return {"state": self._state}

    async def _ctl_subscribe(self, payload: dict) -> dict:
        instruments = validate_subscribe_payload(payload)["instruments"]
        if not self._simulate and self._client is None:
            raise ValueError("尚未连接行情前置")
        ticks: dict[str, float] = {}
        for inst in instruments:
            try:
                ticks[inst] = self._config.resolve_tick_size(inst)
            except ConfigError as exc:
                raise ValueError(f"合约 {inst} 配置无效: {exc}") from exc
        added = [i for i in instruments if i not in self._instruments]
        for inst in added:
            self._instruments[inst] = (
                "streaming" if self._simulate else "subscribing")
            if self._simulate and self._feed is not None:
                self._feed.add_instrument(inst, ticks[inst])
        if not self._simulate and added:
            self._subscribe_instruments(added)
        await self._broadcast_status()
        return {"instruments": dict(self._instruments)}

    async def _ctl_unsubscribe(self, payload: dict) -> dict:
        instruments = validate_subscribe_payload(payload)["instruments"]
        removed = [i for i in instruments if i in self._instruments]
        for inst in removed:
            self._instruments.pop(inst, None)
            if self._simulate and self._feed is not None:
                self._feed.remove_instrument(inst)
        if not self._simulate and self._client is not None and removed:
            subs = [self._subscribe_form(i) for i in removed]
            self._client.unsubscribe(subs)
        await self._broadcast_status()
        return {"instruments": dict(self._instruments)}

    # ------------------------------------------------------------------
    # Simulated feed wiring
    # ------------------------------------------------------------------
    async def _start_simulate(self) -> None:
        assert self._archiver is not None
        instruments = list(dict.fromkeys(self._simulate_instruments))
        tick_sizes = {}
        for inst in instruments:
            try:
                tick_sizes[inst] = self._config.resolve_tick_size(inst)
            except ConfigError as exc:
                raise ValueError(f"合约 {inst} 配置无效: {exc}") from exc
        self._batch_id = self._archiver.begin_batch(
            fronts=[], broker_id="", user_masked="",
            instruments=instruments, raw_source="simulate",
            config_hash=self._config.config_hash)
        self._feed = SimulatedFeed(
            instruments, tick_sizes, seed=self._simulate_seed,
            rate_hz=self._simulate_rate_hz)
        self._instruments = {inst: "streaming" for inst in instruments}
        self._feed_task = asyncio.create_task(
            self._feed_loop(), name="simulate-feed")
        await self._set_state(CONN_STREAMING, "模拟行情推送中")

    async def _feed_loop(self) -> None:
        assert self._feed is not None
        try:
            await self._feed.run(self._on_simulate_row)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("模拟行情源异常")
            self._failure = True
            self._stop_event.set()

    async def _on_simulate_row(self, row: dict) -> None:
        await self._process_row(self._complete_row(row))

    def _complete_row(self, row: dict) -> dict:
        inst = str(row.get("instrument_id") or "").upper()
        seq = self._seq.get(inst, 0) + 1
        self._seq[inst] = seq
        row["instrument_id"] = inst
        row["sequence_id"] = seq
        if not row.get("batch_id"):
            row["batch_id"] = self._batch_id
        if not row.get("raw_source"):
            row["raw_source"] = "simulate" if self._simulate else "ctp"
        if not row.get("trading_session"):
            row["trading_session"] = self._config.session_of(
                str(row.get("update_time") or "00:00:00"))
        return row

    async def _process_row(self, row: dict) -> None:
        events = self._normalizer.normalize_row(
            row, source=str(row.get("raw_source") or ""), data_mode=DATA_LIVE)
        for env in events:
            await self._publisher.broadcast_event(env)
        self._events_published += len(events)
        self._last_event_ns = time.time_ns()
        self._archiver.add_row(row, events)

    # ------------------------------------------------------------------
    # CTP handler callbacks (CTP worker threads -- post to inbox only)
    # ------------------------------------------------------------------
    def _post(self, kind: str, **kw) -> None:
        self._inbox.put((kind, kw))
        wake = self._wake
        if wake is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass

    def on_front_connected(self) -> None:
        self._post("front_connected")

    def on_front_disconnected(self, reason: int) -> None:
        self._post("front_disconnected", reason=int(reason))

    def on_heartbeat_warning(self, seconds: int) -> None:
        self._post("heartbeat", seconds=int(seconds))

    def on_rsp_user_login(self, trading_day, broker, user, error_id,
                          error_msg) -> None:
        self._post("login", trading_day=trading_day, error_id=int(error_id),
                   error_msg=error_msg)

    def on_rsp_sub_market_data(self, instrument, error_id, error_msg) -> None:
        self._post("sub", instrument=instrument, error_id=int(error_id))

    def on_rsp_unsub_market_data(self, instrument, error_id, error_msg) -> None:
        self._post("unsub", instrument=instrument, error_id=int(error_id))

    def on_rsp_error(self, error_id, error_msg) -> None:
        self._post("error", error_id=int(error_id), error_msg=error_msg)

    def on_rsp_user_logout(self, error_id, error_msg) -> None:
        self._post("logout", error_id=int(error_id))

    def on_depth_market_data(self, fields: dict) -> None:
        self._post("depth", fields=fields)

    async def _drain_inbox(self) -> None:
        wake = self._wake
        while True:
            await wake.wait()
            wake.clear()
            while True:
                try:
                    kind, kw = self._inbox.get_nowait()
                except queue.Empty:
                    break
                try:
                    await self._handle_ctp_event(kind, kw)
                except Exception:
                    logger.exception("CTP 事件处理失败 (%s)", kind)
                    self._failure = True

    async def _handle_ctp_event(self, kind: str, kw: dict) -> None:
        if kind == "depth":
            await self._handle_depth(kw["fields"])
            return
        if kind == "front_connected":
            logger.info("行情前置已连接")
            if self._credentials is not None and self._client is not None:
                await self._set_state(CONN_CONNECTING, "前置已连接，正在登录")
                try:
                    self._client.login(
                        self._credentials["broker_id"],
                        self._credentials["user"],
                        self._credentials["password"])
                except Exception:
                    logger.exception("登录请求发送失败")
                    await self._set_state(CONN_FAILED, "登录请求发送失败")
            return
        if kind == "front_disconnected":
            reason = kw["reason"]
            logger.warning("行情前置断开 (reason=0x%x)；CTP 将自动重连", reason)
            await self._set_state(CONN_DISCONNECTED,
                                  f"前置断开 reason=0x{reason:x}")
            return
        if kind == "login":
            if kw["error_id"] != 0:
                logger.error("登录失败 (code=%s)", kw["error_id"])
                await self._set_state(
                    CONN_FAILED, f"登录失败，CTP 错误码 {kw['error_id']}")
                return
            logger.info("登录成功 (trading_day=%s)", kw.get("trading_day"))
            await self._set_state(CONN_CONNECTED,
                                  f"trading_day={kw.get('trading_day', '')}")
            self._subscribe_instruments(list(self._instruments))
            return
        if kind == "sub":
            inst = str(kw["instrument"]).upper()
            if kw["error_id"] != 0:
                logger.error("订阅失败 %s (code=%s)", inst, kw["error_id"])
                self._instruments[inst] = "failed"
            else:
                logger.info("已订阅: %s", inst)
                self._instruments[inst] = "streaming"
            if (self._state != CONN_STREAMING
                    and any(v == "streaming"
                            for v in self._instruments.values())):
                self._state = CONN_STREAMING
                self._detail = "行情推送中"
            await self._broadcast_status()
            return
        if kind == "unsub":
            inst = str(kw["instrument"]).upper()
            logger.info("已退订: %s (code=%s)", inst, kw["error_id"])
            self._instruments.pop(inst, None)
            await self._broadcast_status()
            return
        if kind == "heartbeat":
            logger.warning("心跳超时预警: %ss 未收到数据", kw["seconds"])
            return
        if kind == "error":
            logger.error("CTP 错误 (code=%s): %s", kw["error_id"],
                         kw.get("error_msg"))
            return
        if kind == "logout":
            logger.info("已登出 (code=%s)", kw["error_id"])
            return

    async def _handle_depth(self, fields: dict) -> None:
        inst = str(fields.get("InstrumentID") or "").upper()
        if inst not in self._instruments:
            return
        recv_ns = time.time_ns()
        seq = self._seq.get(inst, 0) + 1
        self._seq[inst] = seq
        update_time = str(fields.get("UpdateTime") or "00:00:00")
        row = depth_fields_to_row(
            fields,
            sequence_id=seq,
            local_receive_time_ns=recv_ns,
            batch_id=self._batch_id or "",
            trading_session=self._config.session_of(update_time),
            raw_source="ctp")
        row["instrument_id"] = inst
        await self._process_row(row)

    def _subscribe_form(self, instrument_id: str) -> str:
        """DCE / SHFE / INE accept lowercase instrument ids only."""
        if self._config.exchange_of(instrument_id) in _SUBSCRIBE_LOWERCASE_EXCHANGES:
            return instrument_id.lower()
        return instrument_id

    def _subscribe_instruments(self, instruments: list[str]) -> None:
        if self._client is None or not instruments:
            return
        subs = [self._subscribe_form(i) for i in instruments]
        rc = self._client.subscribe(subs)
        logger.info("订阅请求已发送 (rc=%s, instruments=%s)", rc, subs)

    # ------------------------------------------------------------------
    # Status / state machine
    # ------------------------------------------------------------------
    def _status_payload(self) -> dict:
        return {
            "state": self._state,
            "detail": self._detail,
            "instruments": dict(self._instruments),
            "batch_id": self._batch_id,
            "started_at": self._started_at,
            "last_event_ns": self._last_event_ns,
            "events_published": self._events_published,
            "simulate": self._simulate,
        }

    async def _broadcast_status(self) -> None:
        await self._publisher.broadcast_status(self._status_payload())

    async def _set_state(self, state: str, detail: str) -> None:
        self._state = state
        self._detail = detail
        await self._broadcast_status()

    # ------------------------------------------------------------------
    # Credentials (memory only unless remember=True)
    # ------------------------------------------------------------------
    def _store_credentials(self, params: dict) -> None:
        path = self._config.paths.data_dir / _CREDENTIALS_FILENAME
        try:
            write_private_json(path, {
                "fronts": params["fronts"],
                "broker_id": params["broker_id"],
                "user": params["user"],
                "password": params["password"],
                "instruments": params["instruments"],
            })
            os.chmod(path, 0o600)
        except Exception:
            logger.exception("凭据本地保存失败（仅保留在内存中）")

    # ------------------------------------------------------------------
    # Shutdown / teardown
    # ------------------------------------------------------------------
    async def _on_shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._stop_event.set()
        logger.info("收到 shutdown 控制命令")

    def _request_signal_shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._stop_event.set()
        logger.info("收到停止信号")

    def _install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_signal_shutdown)
                self._installed_signals.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                logger.warning("无法注册信号处理器 %s", getattr(sig, "name", sig))

    async def _stop_stream(self) -> None:
        """Stop feed / CTP client and finalize the running batch."""
        if self._feed_task is not None:
            self._feed_task.cancel()
            try:
                await self._feed_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("模拟行情源停止失败")
            self._feed_task = None
        if self._client is not None:
            try:
                await asyncio.to_thread(self._client.release)
            except Exception:
                logger.exception("CTP 客户端释放失败")
            self._client = None
        if self._batch_id is not None and self._archiver is not None:
            status = "failed" if self._failure else "finished"
            try:
                await self._archiver.finalize(status)
            except Exception:
                logger.exception("archiver finalize 失败；staging 保留待下次恢复")
                self._failure = True
            self._batch_id = None
        self._instruments = {}
        self._seq.clear()
        self._normalizer.reset()
        self._credentials = None
        await self._set_state(CONN_IDLE, "已断开")

    async def _shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        logger.info("gateway 停止中 (failure=%s)", self._failure)
        if self._feed_task is not None:
            self._feed_task.cancel()
            try:
                await self._feed_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            self._feed_task = None
        if self._client is not None:
            try:
                await asyncio.to_thread(self._client.release)
            except Exception:
                logger.exception("CTP 客户端释放失败")
            self._client = None
        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
            self._drain_task = None
        if self._archiver is not None:
            try:
                await self._archiver.finalize(
                    "failed" if self._failure else "finished")
            except Exception:
                logger.exception("archiver finalize 失败；staging 保留待下次恢复")
                self._failure = True
        self._state = CONN_FAILED if self._failure else CONN_DISCONNECTED
        self._detail = "gateway 已停止"
        try:
            await self._broadcast_status()
        except Exception:  # noqa: BLE001
            pass
        try:
            await self._publisher.stop()
        except Exception:  # noqa: BLE001
            logger.exception("publisher 停止失败")
        if self._loop is not None:
            for sig in self._installed_signals:
                try:
                    self._loop.remove_signal_handler(sig)
                except Exception:  # noqa: BLE001
                    pass
            self._installed_signals = []
        if self._engine is not None:
            try:
                await self._engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
        logger.info("gateway 已停止")

    # ------------------------------------------------------------------
    def _build_engine(self):
        url = (self._settings.database_url or "").strip()
        if not url:
            return None
        try:
            from sqlalchemy.ext.asyncio import create_async_engine
            return create_async_engine(url, pool_pre_ping=True)
        except Exception:
            logger.warning("数据库引擎创建失败；仅写 parquet", exc_info=True)
            return None

    async def _ensure_database(self) -> None:
        """Idempotent schema init with retries; removes startup ordering
        dependencies on the API process. Degrades to parquet-only mode
        only after the retry budget is exhausted."""
        if self._engine is None:
            return
        from app.storage.timescale import init_database
        for attempt in range(1, _DB_INIT_RETRIES + 1):
            try:
                await init_database(self._engine, self._config)
                if attempt > 1:
                    logger.info("数据库初始化成功（第 %d 次尝试）", attempt)
                return
            except Exception:
                if attempt == _DB_INIT_RETRIES:
                    logger.warning(
                        "数据库初始化失败（已重试 %d 次）；先只写 parquet，"
                        "时序库本进程内不可用", attempt, exc_info=True)
                    return
                logger.warning("数据库初始化失败，%.0fs 后重试（%d/%d）",
                               _DB_INIT_RETRY_S, attempt, _DB_INIT_RETRIES)
                await asyncio.sleep(_DB_INIT_RETRY_S)


async def run_gateway(config: AppConfig, settings: WebSettings, *,
                      simulate: bool = False,
                      simulate_instruments: list[str] | None = None,
                      simulate_rate_hz: float = 2.0,
                      simulate_seed: int = 7) -> int:
    """Run the gateway process until shutdown; returns the exit code."""
    os.umask(0o077)
    lock_path = config.paths.data_dir / "gateway.lock"
    try:
        lock = ProcessLock(lock_path)
    except Exception as exc:
        logger.error("无法获取 gateway 进程锁 (%s): %s", lock_path, exc)
        return 1
    runtime = _GatewayRuntime(
        config, settings, simulate=simulate,
        simulate_instruments=simulate_instruments,
        simulate_rate_hz=simulate_rate_hz,
        simulate_seed=simulate_seed)
    try:
        return await runtime.run()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("gateway 异常退出")
        return 1
    finally:
        lock.close()
