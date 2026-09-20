"""API-side client for the CTP gateway process.

Connects to the gateway unix socket, republishes every received event on
the EventBus, tracks the latest gateway status, and exposes a control RPC
(connect/subscribe/... -> ack) used by the system API.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any, Optional

from app.common.logging import get_logger
from app.common.runtime import WebSettings
from app.ingest.bus import EventBus
from app.ingest.protocol import (CMD_SHUTDOWN, ProtocolError, recv_frame,
                                 send_frame)

logger = get_logger("ingest.client")

_RECONNECT_MIN_S = 0.5
_RECONNECT_MAX_S = 5.0
_STATUS_TIMEOUT_S = 3.0


class GatewayUnavailableError(Exception):
    pass


class IngestClient:
    def __init__(self, settings: WebSettings, bus: EventBus):
        self._settings = settings
        self._bus = bus
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._task: Optional[asyncio.Task] = None
        self._ref = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._connected_at: Optional[float] = None
        # Latest gateway status (from "status" frames), None when offline.
        self.gateway_status: Optional[dict] = None
        self.last_event_ts_ns: Optional[int] = None
        self.last_event_at: Optional[float] = None
        self.events_received: int = 0

    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def connected_at(self) -> Optional[float]:
        return self._connected_at

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ingest-client")

    async def stop(self, shutdown_gateway: bool = False) -> None:
        if shutdown_gateway and self.connected:
            try:
                await asyncio.wait_for(
                    self.send_control(CMD_SHUTDOWN, {}), timeout=3.0)
            except Exception:  # noqa: BLE001
                logger.warning("gateway shutdown frame failed", exc_info=True)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._close_socket()

    async def _close_socket(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._writer = None
            self._reader = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(GatewayUnavailableError("gateway disconnected"))
        self._pending.clear()

    # ------------------------------------------------------------------
    async def _run(self) -> None:
        backoff = _RECONNECT_MIN_S
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(
                    str(self._settings.gateway_socket))
            except (FileNotFoundError, ConnectionError, OSError) as exc:
                self.gateway_status = None
                logger.info("gateway socket not ready (%s); retrying", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX_S)
                continue
            backoff = _RECONNECT_MIN_S
            self._reader, self._writer = reader, writer
            self._connected_at = time.time()
            logger.info("connected to gateway at %s", self._settings.gateway_socket)
            try:
                await self._read_loop()
            except (ProtocolError, asyncio.IncompleteReadError, ConnectionError,
                    OSError) as exc:
                logger.warning("gateway connection lost: %r", exc)
            finally:
                await self._close_socket()
                self.gateway_status = None
            await asyncio.sleep(backoff)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            msg = await recv_frame(self._reader)
            if msg is None:
                return
            kind = msg.get("kind")
            if kind == "event":
                event = msg.get("event") or {}
                self.events_received += 1
                self.last_event_ts_ns = event.get("exchange_ts_ns")
                self.last_event_at = time.time()
                await self._bus.publish(event)
            elif kind == "status":
                self.gateway_status = msg.get("gateway") or {}
            elif kind == "ack":
                ref = msg.get("ref")
                fut = self._pending.pop(ref, None)
                if fut is not None and not fut.done():
                    if msg.get("ok"):
                        fut.set_result(msg.get("result") or {})
                    else:
                        fut.set_exception(
                            GatewayUnavailableError(str(msg.get("error") or "error")))
            elif kind == "error":
                logger.error("gateway error frame: %s", msg)

    # ------------------------------------------------------------------
    async def send_control(self, cmd: str, payload: dict[str, Any],
                           timeout: float = 15.0) -> dict:
        """Send a control frame and await the gateway ack."""
        if not self.connected or self._writer is None:
            raise GatewayUnavailableError("gateway is not connected")
        ref = next(self._ref)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[ref] = fut
        try:
            await send_frame(self._writer, {
                "v": 1, "kind": "control", "ref": ref,
                "cmd": cmd, "payload": payload})
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(ref, None)

    async def request_status(self) -> dict:
        """Best-effort gateway status snapshot."""
        if self.gateway_status is not None:
            return dict(self.gateway_status)
        raise GatewayUnavailableError("gateway is not connected")
