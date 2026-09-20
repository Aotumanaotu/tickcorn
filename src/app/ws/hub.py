"""WebSocket connection hub: fan-out, subscriptions and backpressure.

One :class:`ConnectionHub` instance serves one channel ("market",
"analysis", "system"). Envelopes from the EventBus are pushed onto each
connection's bounded queue by a dedicated sender task; quote-class events
conflate (drop-oldest) under pressure while micro/analysis events must be
delivered -- a connection whose queue overflows on those is disconnected
rather than silently dropping data.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

from starlette.websockets import WebSocket

from app.common.logging import get_logger
from app.core.events import EVENT_QUOTE

logger = get_logger("ws.hub")

_QUEUE_MAX = 5000
_OVERFLOW_CLOSE_CODE = 1013


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class Connection:
    """Per-client state: socket, bounded queue, subscriptions, tasks."""

    def __init__(self, websocket: WebSocket, user_id: int) -> None:
        self.websocket = websocket
        self.user_id = user_id
        self.send_queue: asyncio.Queue[Optional[dict]] = asyncio.Queue(
            maxsize=_QUEUE_MAX)
        self.subscriptions: set[str] = set()
        self.tasks: list[asyncio.Task] = []
        self.dead = False


class ConnectionHub:
    """Fan-out hub for one WS channel."""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self._connections: set[Connection] = set()

    # ------------------------------------------------------------------
    @property
    def size(self) -> int:
        """Number of live connections."""
        return len(self._connections)

    def register(self, websocket: WebSocket, user_id: int) -> Connection:
        """Track a newly accepted socket."""
        conn = Connection(websocket, user_id)
        self._connections.add(conn)
        logger.info("hub %s: connection registered (user=%s, total=%d)",
                    self.channel, user_id, self.size)
        return conn

    def unregister(self, connection: Connection) -> None:
        """Drop a connection and cancel its background tasks."""
        connection.dead = True
        self._connections.discard(connection)
        for task in connection.tasks:
            if not task.done():
                task.cancel()
        connection.tasks.clear()
        logger.info("hub %s: connection unregistered (user=%s, total=%d)",
                    self.channel, connection.user_id, self.size)

    # ------------------------------------------------------------------
    async def route(self, event: dict) -> None:
        """Deliver one envelope to connections subscribed to its instrument."""
        if self.channel == "system":
            await self.broadcast(event)
            return
        instrument = event.get("instrument_id")
        if not instrument:
            return
        key = str(instrument).upper()
        conflate = event.get("type") == EVENT_QUOTE
        for conn in list(self._connections):
            if conn.dead:
                continue
            if key in conn.subscriptions:
                self._enqueue(conn, event, conflate=conflate)

    async def broadcast(self, event: dict) -> None:
        """Deliver one envelope to every connection (system channel)."""
        conflate = event.get("type") == EVENT_QUOTE
        for conn in list(self._connections):
            if conn.dead:
                continue
            self._enqueue(conn, event, conflate=conflate)

    # ------------------------------------------------------------------
    def push(self, conn: Connection, obj: dict) -> None:
        """Queue one service frame (pong / error) with conflate semantics."""
        self._enqueue(conn, obj, conflate=True)

    def _enqueue(self, conn: Connection, obj: dict, *, conflate: bool) -> None:
        if conn.dead:
            return
        try:
            conn.send_queue.put_nowait(obj)
        except asyncio.QueueFull:
            if conflate:
                try:
                    conn.send_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    conn.send_queue.put_nowait(obj)
                except asyncio.QueueFull:
                    pass
            else:
                logger.warning(
                    "hub %s: queue overflow on must-deliver event, "
                    "disconnecting user=%s", self.channel, conn.user_id)
                self._kill(conn)

    def _kill(self, conn: Connection) -> None:
        conn.dead = True
        conn.tasks.append(asyncio.create_task(self._close(conn)))

    async def _close(self, conn: Connection) -> None:
        try:
            await conn.websocket.close(code=_OVERFLOW_CLOSE_CODE)
        except Exception:  # noqa: BLE001
            pass
        self.unregister(conn)

    async def send_json(self, connection: Connection, obj: Any) -> None:
        """Serialize and send one frame; failures mark the connection dead."""
        try:
            await connection.websocket.send_text(
                json.dumps(obj, separators=(",", ":")))
        except Exception:  # noqa: BLE001
            logger.warning("hub %s: send failed, marking dead (user=%s)",
                           self.channel, connection.user_id)
            connection.dead = True

    # ------------------------------------------------------------------
    def send_hello(self, conn: Connection) -> None:
        """Queue the first frame every client receives."""
        self._enqueue(conn, {"type": "hello", "channel": self.channel,
                             "server_time": _now_iso()}, conflate=True)

    def start_sender(self, conn: Connection) -> None:
        """Spawn the queue -> socket pump task for one connection."""
        conn.tasks.append(asyncio.create_task(self._sender(conn)))

    def start_heartbeat(self, conn: Connection,
                        interval_s: float = 10.0) -> None:
        """Spawn the periodic heartbeat task (system channel)."""
        conn.tasks.append(asyncio.create_task(self._heartbeat(conn, interval_s)))

    async def _sender(self, conn: Connection) -> None:
        while not conn.dead:
            obj = await conn.send_queue.get()
            if obj is None:
                return
            await self.send_json(conn, obj)
            if conn.dead:
                return

    async def _heartbeat(self, conn: Connection, interval_s: float) -> None:
        while not conn.dead:
            await asyncio.sleep(interval_s)
            if conn.dead:
                return
            self._enqueue(conn, {"type": "heartbeat", "channel": self.channel,
                                 "server_time": _now_iso()}, conflate=True)

    # ------------------------------------------------------------------
    async def handle_message(self, conn: Connection, msg: dict) -> None:
        """Act on one client -> server control message."""
        action = msg.get("action")
        if action == "subscribe":
            instrument = msg.get("instrument")
            if isinstance(instrument, str) and instrument.strip():
                conn.subscriptions.add(instrument.strip().upper())
        elif action == "unsubscribe":
            instrument = msg.get("instrument")
            if isinstance(instrument, str) and instrument.strip():
                conn.subscriptions.discard(instrument.strip().upper())
        elif action == "ping":
            self._enqueue(conn, {"type": "pong",
                                 "server_time": _now_iso()}, conflate=True)
        else:
            self._enqueue(conn, {"type": "error",
                                 "error": f"unknown action: {action!r}"},
                          conflate=True)
