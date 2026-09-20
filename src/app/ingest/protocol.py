"""Wire protocol between the CTP gateway process and the API process.

A single unix-domain stream socket carries length-prefixed msgpack frames
in both directions:

    gateway -> api : {"v":1,"kind":"event","event":<envelope>}
                     {"v":1,"kind":"status","gateway":{...}}
                     {"v":1,"kind":"ack","ref":n,"ok":bool,"error":str|null}
    api -> gateway : {"v":1,"kind":"control","ref":n,"cmd":str,"payload":{...}}

Control commands:
    connect     {fronts:[str], broker_id, user, password, remember:bool,
                 instruments:[str]}
    disconnect  {}
    subscribe   {instruments:[str]}
    unsubscribe {instruments:[str]}
    shutdown    {}
"""

from __future__ import annotations

import asyncio
import struct

import msgpack

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8 * 1024 * 1024

_LEN = struct.Struct(">I")

CMD_CONNECT = "connect"
CMD_DISCONNECT = "disconnect"
CMD_SUBSCRIBE = "subscribe"
CMD_UNSUBSCRIBE = "unsubscribe"
CMD_SHUTDOWN = "shutdown"


class ProtocolError(Exception):
    pass


def encode(msg: dict) -> bytes:
    return _LEN.pack(len(msg)) + msgpack.packb(msg, use_bin_type=True)


async def send_frame(writer: asyncio.StreamWriter, msg: dict) -> None:
    writer.write(encode(msg))
    await writer.drain()


async def recv_frame(reader: asyncio.StreamReader) -> dict | None:
    """Read one frame; returns None on clean EOF."""
    header = await reader.readexactly(_LEN.size)
    (length,) = _LEN.unpack(header)
    if length <= 0 or length > MAX_FRAME_BYTES:
        raise ProtocolError(f"invalid frame length {length}")
    body = await reader.readexactly(length)
    return msgpack.unpackb(body, raw=False)
