"""System endpoints: status, gateway control, user admin, WS tickets."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.deps import get_current_user, get_db, require_perm
from app.api.schemas import (
    GatewayConnectRequest,
    GatewaySubscribeRequest,
    UserCreate,
    UserOut,
)
from app.auth.rbac import Permission, permissions_of
from app.auth.service import create_user
from app.common.logging import get_logger
from app.ingest.client import GatewayUnavailableError
from app.ingest.protocol import CMD_CONNECT, CMD_DISCONNECT, CMD_SUBSCRIBE
from app.storage.models import User
from app.ws.ticket import tickets

logger = get_logger("api.system")

router = APIRouter(prefix="/system", tags=["system"])

_WS_TICKET_TTL_S = 30


@router.get("/status")
async def system_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_perm(Permission.VIEW_MARKET)),
) -> dict:
    """Aggregated health snapshot: gateway, ingest, db, realtime."""
    ingest = request.app.state.ingest
    realtime = request.app.state.realtime
    try:
        gateway: dict[str, Any] = await ingest.request_status()
    except GatewayUnavailableError:
        gateway = {"state": "offline"}
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "gateway": gateway,
        "gateway_connected": ingest.connected,
        "ingest": {
            "events_received": ingest.events_received,
            "last_event_at": ingest.last_event_at,
        },
        "db": {"ok": db_ok},
        "realtime": realtime.snapshots(),
        "version": __version__,
    }


async def _send_control(request: Request, cmd: str, payload: dict) -> dict:
    ingest = request.app.state.ingest
    try:
        return await ingest.send_control(cmd, payload, timeout=120.0 if cmd == CMD_DISCONNECT else 15.0)
    except GatewayUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc) if ingest.connected else "网关未连接",
        ) from exc

    except TimeoutError as exc:
        raise HTTPException(504, "网关处理超时，请刷新状态确认结果，勿重复操作") from exc


@router.post("/gateway/connect")
async def gateway_connect(
    body: GatewayConnectRequest,
    request: Request,
    _user: User = Depends(require_perm(Permission.MANAGE_USER)),
) -> dict:
    """Log the CTP gateway in and start the requested subscriptions."""
    async with request.app.state.research_lock:
        if request.app.state.reports.busy:
            raise HTTPException(409, "报告正在生成，请完成后再开始采集")
        return await _send_control(request, CMD_CONNECT, body.model_dump())


@router.post("/gateway/disconnect")
async def gateway_disconnect(
    request: Request,
    _user: User = Depends(require_perm(Permission.MANAGE_USER)),
) -> dict:
    """Log the CTP gateway out."""
    return await _send_control(request, CMD_DISCONNECT, {})


@router.post("/gateway/subscribe")
async def gateway_subscribe(
    body: GatewaySubscribeRequest,
    request: Request,
    _user: User = Depends(require_perm(Permission.RUN_ANALYSIS)),
) -> dict:
    """Subscribe the gateway to additional instruments."""
    return await _send_control(request, CMD_SUBSCRIBE, body.model_dump())


@router.get("/users", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_perm(Permission.MANAGE_USER)),
) -> list[UserOut]:
    """All user accounts."""
    rows = list((await db.execute(select(User).order_by(User.id.asc())))
                .scalars().all())
    return [
        UserOut(
            id=u.id,
            username=u.username,
            email=u.email,
            role=u.role,
            is_active=u.is_active,
            created_at=u.created_at,
            permissions=permissions_of(u.role),
        )
        for u in rows
    ]


@router.post("/users", response_model=UserOut,
             status_code=status.HTTP_201_CREATED)
async def create_user_endpoint(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_perm(Permission.MANAGE_USER)),
) -> UserOut:
    """Provision a user account (admin only)."""
    try:
        user = await create_user(
            db, body.username, body.password, body.role.value,
            email=body.email)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        permissions=permissions_of(user.role),
    )


@router.post("/ws/ticket")
async def issue_ws_ticket(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Mint a one-time WebSocket admission ticket."""
    return {"ticket": tickets.issue(current_user.id, _WS_TICKET_TTL_S),
            "expires_in": _WS_TICKET_TTL_S}
