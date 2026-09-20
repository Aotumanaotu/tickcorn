"""WebSocket endpoints: /ws/market, /ws/analysis, /ws/system.

Admission goes through a one-time ticket (POST /api/v1/ws/ticket) so the
JWT never appears in a URL. Market / analysis channels route envelopes by
per-connection subscriptions; the system channel broadcasts connection /
system events plus a periodic heartbeat. Hubs live on ``app.state`` and
are wired to the EventBus in the app lifespan.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.common.logging import get_logger
from app.ws.hub import ConnectionHub
from app.ws.ticket import tickets

logger = get_logger("ws.routes")

router = APIRouter()

_TICKET_CLOSE_CODE = 4401

_HUB_ATTRS = {
    "market": "market_hub",
    "analysis": "analysis_hub",
    "system": "system_hub",
}


def _get_hub(websocket: WebSocket, channel: str) -> ConnectionHub | None:
    return getattr(websocket.app.state, _HUB_ATTRS[channel], None)


async def _serve_channel(websocket: WebSocket, channel: str) -> None:
    """Shared endpoint body: ticket gate -> hub fan-out -> receive loop."""
    await websocket.accept()
    ticket = websocket.query_params.get("ticket", "")
    user_id = tickets.redeem(ticket)
    if user_id is None:
        await websocket.close(code=_TICKET_CLOSE_CODE)
        return

    hub = _get_hub(websocket, channel)
    if hub is None:
        await websocket.close(code=1011)
        return

    conn = hub.register(websocket, user_id)
    hub.send_hello(conn)
    hub.start_sender(conn)
    if channel == "system":
        hub.start_heartbeat(conn)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError("message must be a JSON object")
            except ValueError as exc:
                hub.push(conn, {"type": "error", "error": str(exc)})
                continue
            await hub.handle_message(conn, msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass
        hub.unregister(conn)


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    """Quote / micro / instrument-status stream (subscribe per instrument)."""
    await _serve_channel(websocket, "market")


@router.websocket("/ws/analysis")
async def ws_analysis(websocket: WebSocket) -> None:
    """Rolling metrics / regime stream (subscribe per instrument)."""
    await _serve_channel(websocket, "analysis")


@router.websocket("/ws/system")
async def ws_system(websocket: WebSocket) -> None:
    """Gateway connection / system events, broadcast to every client."""
    await _serve_channel(websocket, "system")
