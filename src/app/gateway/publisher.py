"""Gateway unix-socket publisher: event/status fan-out, control intake.

One unix-domain stream socket (protocol: app.ingest.protocol). Multiple
API clients may connect (in practice one). Each client gets:

    * an independent reader task -- control frames are dispatched to the
      injected on_control callback (sync or async, returns a dict or
      raises) and answered with an ack frame,
    * a bounded writer queue + writer task -- a slow consumer is
      disconnected instead of stalling the market-data hot path.

Framing note: frames we SEND use the spec-correct 4-byte byte-length
prefix; frames we RECEIVE are parsed tolerantly because
app.ingest.protocol.encode currently emits a key-count prefix (see
recv_frame below).

The shutdown control is acked first, then the on_shutdown callback is
scheduled as its own task (it typically stops the whole gateway, which
must not be cancelled together with this reader task).
"""

from __future__ import annotations

import asyncio
import inspect
import struct
from pathlib import Path
from typing import Callable, Optional

import msgpack

from app.common.logging import get_logger
from app.ingest.protocol import (CMD_SHUTDOWN, MAX_FRAME_BYTES,
                                 PROTOCOL_VERSION, ProtocolError)

logger = get_logger("gateway.publisher")

_LEN = struct.Struct(">I")

ControlHandler = Callable[[str, dict], object]
ShutdownHandler = Callable[[], object]
_CLIENT_QUEUE_SIZE = 10_000


def encode_frame(msg: dict) -> bytes:
    """Spec-correct framing: 4-byte big-endian BYTE length + msgpack body.

    app.ingest.protocol.encode packs len(dict) (the key count) as the
    prefix, which desynchronizes any recv_frame reader; we emit the
    byte-length form so spec-compliant readers parse our frames.
    """
    body = msgpack.packb(msg, use_bin_type=True)
    return _LEN.pack(len(body)) + body


async def send_frame(writer: asyncio.StreamWriter, msg: dict) -> None:
    writer.write(encode_frame(msg))
    await writer.drain()


async def recv_frame(reader: asyncio.StreamReader) -> Optional[dict]:
    """Read one frame; None on clean EOF.

    Tolerates both the byte-length prefix (spec, what we send) and the
    key-count prefix emitted by app.ingest.protocol.encode: msgpack is
    self-delimiting, so after the prefixed minimum the body is extended
    one byte at a time until it parses.
    """
    try:
        header = await reader.readexactly(_LEN.size)
    except asyncio.IncompleteReadError:
        return None
    (length,) = _LEN.unpack(header)
    if length <= 0 or length > MAX_FRAME_BYTES:
        raise ProtocolError(f"invalid frame length {length}")
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None
    while True:
        try:
            return msgpack.unpackb(body, raw=False)
        except ValueError:
            if len(body) >= MAX_FRAME_BYTES:
                raise ProtocolError("frame exceeds MAX_FRAME_BYTES")
            try:
                body += await reader.readexactly(1)
            except asyncio.IncompleteReadError:
                raise ProtocolError("connection closed mid-frame") from None


class _Client:
    __slots__ = ("reader", "writer", "queue", "reader_task", "writer_task")

    def __init__(self, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=_CLIENT_QUEUE_SIZE)
        self.reader_task: Optional[asyncio.Task] = None
        self.writer_task: Optional[asyncio.Task] = None


class GatewayPublisher:
    """Fan-out publisher on the gateway unix socket."""

    def __init__(self, on_control: ControlHandler,
                 on_shutdown: ShutdownHandler):
        self._on_control = on_control
        self._on_shutdown = on_shutdown
        self._server: Optional[asyncio.AbstractServer] = None
        self._socket_path: Optional[Path] = None
        self._clients: set[_Client] = set()
        self._last_status: Optional[dict] = None
        self._closing = False
        self._stopping = False

    # ------------------------------------------------------------------
    async def start(self, socket_path: Path | str) -> None:
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        self._socket_path = path
        self._server = await asyncio.start_unix_server(
            self._on_connection, path=str(path))
        logger.info("gateway publisher listening on %s", path)

    async def broadcast_event(self, event: dict) -> None:
        if not self._clients:
            return
        frame = {"v": PROTOCOL_VERSION, "kind": "event", "event": event}
        for client in list(self._clients):
            self._enqueue(client, frame)

    async def broadcast_status(self, status: dict) -> None:
        self._last_status = status
        if not self._clients:
            return
        frame = {"v": PROTOCOL_VERSION, "kind": "status", "gateway": status}
        for client in list(self._clients):
            self._enqueue(client, frame)

    async def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        tasks: list[asyncio.Task] = []
        for client in list(self._clients):
            for task in (client.reader_task, client.writer_task):
                if task is not None and not task.done():
                    task.cancel()
                    tasks.append(task)
            try:
                client.writer.close()
            except Exception:  # noqa: BLE001
                pass
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
        for client in list(self._clients):
            try:
                await client.writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        if self._socket_path is not None and self._socket_path.exists():
            try:
                self._socket_path.unlink()
            except OSError:
                pass
        logger.info("gateway publisher stopped")

    # ------------------------------------------------------------------
    async def _on_connection(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        client = _Client(reader, writer)
        self._clients.add(client)
        client.reader_task = asyncio.create_task(
            self._reader_loop(client), name="gw-client-reader")
        client.writer_task = asyncio.create_task(
            self._writer_loop(client), name="gw-client-writer")
        if self._last_status is not None:
            self._enqueue(client, {"v": PROTOCOL_VERSION, "kind": "status",
                                   "gateway": self._last_status})
        logger.info("api client connected (%d active)", len(self._clients))

    async def _reader_loop(self, client: _Client) -> None:
        try:
            while True:
                try:
                    msg = await recv_frame(client.reader)
                except (ProtocolError, asyncio.IncompleteReadError,
                        ConnectionError, OSError):
                    break
                if msg is None:
                    break
                await self._handle_frame(client, msg)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("client reader failed")
        finally:
            self._drop_client(client, "reader exit")

    async def _writer_loop(self, client: _Client) -> None:
        try:
            while True:
                msg = await client.queue.get()
                if msg is None:
                    break
                await send_frame(client.writer, msg)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError):
            logger.info("client writer failed; dropping client")
        except Exception:  # noqa: BLE001
            logger.exception("client writer failed")
        finally:
            self._drop_client(client, "writer exit")

    # ------------------------------------------------------------------
    async def _handle_frame(self, client: _Client, msg: dict) -> None:
        if msg.get("kind") != "control":
            logger.warning("ignoring non-control frame from api: %r",
                           msg.get("kind"))
            return
        ref = msg.get("ref")
        cmd = msg.get("cmd")
        payload = msg.get("payload") or {}
        if not isinstance(cmd, str):
            await self._send_ack(client, ref, False, "控制命令缺失")
            return
        if cmd == CMD_SHUTDOWN:
            await self._send_ack(client, ref, True, None)
            await self._trigger_shutdown()
            return
        if self._closing:
            await self._send_ack(client, ref, False, "gateway 正在关闭")
            return
        try:
            result = self._on_control(cmd, payload)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            logger.warning("control %s failed: %s", cmd, exc)
            await self._send_ack(client, ref, False,
                                 str(exc) or type(exc).__name__)
            return
        await self._send_ack(
            client, ref, True, None,
            result if isinstance(result, dict) else {})

    async def _send_ack(self, client: _Client, ref, ok: bool,
                        error: Optional[str],
                        result: Optional[dict] = None) -> None:
        frame = {"v": PROTOCOL_VERSION, "kind": "ack", "ref": ref,
                 "ok": bool(ok), "error": error, "result": result or {}}
        try:
            await send_frame(client.writer, frame)
        except (ConnectionError, OSError):
            self._drop_client(client, "ack write failed")

    async def _trigger_shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        logger.info("shutdown requested by api client")
        asyncio.create_task(self._run_on_shutdown(), name="gw-shutdown")

    async def _run_on_shutdown(self) -> None:
        try:
            result = self._on_shutdown()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001
            logger.exception("on_shutdown callback failed")

    # ------------------------------------------------------------------
    def _enqueue(self, client: _Client, frame: dict) -> bool:
        try:
            client.queue.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            self._drop_client(client, "queue overflow (slow consumer)")
            return False

    def _drop_client(self, client: _Client, reason: str) -> None:
        if client not in self._clients:
            return
        self._clients.discard(client)
        logger.info("api client dropped (%s); %d active",
                    reason, len(self._clients))
        for task in (client.reader_task, client.writer_task):
            if task is not None and not task.done():
                task.cancel()
        try:
            client.writer.close()
        except Exception:  # noqa: BLE001
            pass
