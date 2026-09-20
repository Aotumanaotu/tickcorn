"""Feishu settings and a scoped, read-only service endpoint for the monitor."""
from __future__ import annotations

import asyncio
import hmac
import json
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.deps import require_perm
from app.auth.rbac import Permission
from app.common.exceptions import AppError
from app.legacy.dashboard.monitor_settings import apply_payload

router = APIRouter(prefix="/monitor", tags=["monitor"])


def _status(request):
    path = request.app.state.config.paths.data_dir / "monitor-status.local.json"
    try:
        value = json.loads(path.read_text())
        return {key: value.get(key) for key in ("heartbeat_at", "last_sent_at", "last_result", "test_request")}
    except (OSError, ValueError):
        return {}


@router.get("/settings")
async def settings(request: Request, _user=Depends(require_perm(Permission.MANAGE_USER))):
    return {"settings": request.app.state.monitor_settings.load().masked(), "worker": _status(request)}


@router.put("/settings")
async def save_settings(payload: dict, request: Request,
                        _user=Depends(require_perm(Permission.MANAGE_USER))):
    async with request.app.state.monitor_lock:
        store = request.app.state.monitor_settings
        try:
            settings = apply_payload(store.load(), payload)
            ZoneInfo(settings.tz)
            if settings.enabled and not all((settings.feishu_app_id, settings.feishu_app_secret, settings.feishu_receive_id)):
                raise AppError("启用推送前请填写应用 ID、密钥和接收方 ID")
            store.save(settings)
        except (AppError, ZoneInfoNotFoundError, AttributeError) as exc:
            raise HTTPException(400, str(exc) if isinstance(exc, AppError) else "时区名称无效") from exc
    return {"settings": settings.masked(), "worker": _status(request)}


@router.post("/test", status_code=202)
async def test_message(request: Request, _user=Depends(require_perm(Permission.MANAGE_USER))):
    async with request.app.state.monitor_lock:
        worker = _status(request)
        if not worker.get("heartbeat_at") or time.time() * 1000 - worker["heartbeat_at"] > 90000:
            raise HTTPException(409, "飞书推送服务未运行，请先启动 monitor 容器")
        store = request.app.state.monitor_settings
        settings = store.load()
        if not all((settings.feishu_app_id, settings.feishu_app_secret, settings.feishu_receive_id)):
            raise HTTPException(400, "请先保存完整的飞书应用与接收方配置")
        settings.test_request = max(settings.test_request + 1, int(time.time() * 1000))
        store.save(settings)
    return {"test_request": settings.test_request}


@router.get("/state")
async def service_state(request: Request, x_monitor_token: str = Header(default="")):
    # This credential grants only this safe projection, never other API access.
    if not hmac.compare_digest(x_monitor_token, request.app.state.monitor_token):
        raise HTTPException(401, "无效的监控服务凭据")
    ingest = request.app.state.ingest
    gateway = (ingest.gateway_status or {}) if ingest.connected else {}
    state = gateway.get("state", "offline")
    active = bool(gateway.get("batch_id"))
    instruments = {}
    for name, metrics in request.app.state.realtime.snapshots().items():
        instruments[name] = {**metrics, "rate_per_min": metrics.get("message_rate_per_min")}
    return {
        "manager": {"running": active, "batch_id": gateway.get("batch_id")},
        "connection": {"status": "logged_in" if state == "streaming" else state},
        "instruments": instruments, "subscriptions": gateway.get("instruments", {}),
        "source": gateway.get("raw_source", "unknown"),
        "simulate": bool(gateway.get("simulate")),
        "metrics_scope": "API 进程本次运行；比例基于最近 200 次转换中的 1-tick 子集",
    }
