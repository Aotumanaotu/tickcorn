"""Research workflow backed by the existing immutable archive/report pipeline."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import require_perm
from app.auth.rbac import Permission
from app.common.exceptions import AppError
from app.ingest.client import GatewayUnavailableError
from app.legacy.dashboard.reports import SOURCES, partitions

router = APIRouter(prefix="/research", tags=["research"])


class ReportRequest(BaseModel):
    instrument: str
    day: str
    source: str


@router.get("/archives")
async def archives(request: Request, _user=Depends(require_perm(Permission.VIEW_MARKET))):
    return {"partitions": await asyncio.to_thread(partitions, request.app.state.config),
            "sources": SOURCES}


@router.get("/reports")
async def reports(request: Request, _user=Depends(require_perm(Permission.GENERATE_REPORT))):
    store = request.app.state.reports
    return {"jobs": await asyncio.to_thread(store.list), "busy": store.busy}


@router.post("/reports", status_code=202)
async def generate(body: ReportRequest, request: Request,
                   _user=Depends(require_perm(Permission.GENERATE_REPORT))):
    async with request.app.state.research_lock:
        ingest = request.app.state.ingest
        if not ingest.connected:
            raise HTTPException(409, "无法确认采集状态，请先恢复网关连接再生成报告")
        try:
            gateway = await ingest.request_status()
        except GatewayUnavailableError as exc:
            raise HTTPException(409, "无法确认采集状态") from exc
        if gateway.get("batch_id") or gateway.get("state") in {"connecting", "connected", "streaming"}:
            raise HTTPException(409, "请先停止采集并等待归档完成，再生成报告")
        try:
            return await asyncio.to_thread(request.app.state.reports.start, body.model_dump())
        except AppError as exc:
            raise HTTPException(400, str(exc)) from exc


@router.get("/reports/{job_id}/{kind}")
async def download(job_id: str, kind: str, request: Request,
                   _user=Depends(require_perm(Permission.GENERATE_REPORT))):
    try:
        path, mime = await asyncio.to_thread(request.app.state.reports.download, job_id, kind)
    except AppError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type=mime, filename=f"{job_id}-{path.name}")
