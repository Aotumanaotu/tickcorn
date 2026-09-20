"""Historical market-data queries: ticks and microstructure events."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_perm
from app.api.schemas import EventOut, TickOut
from app.auth.rbac import Permission
from app.storage.models import MicrostructureEvent, Tick
from app.storage.timescale import query_micro_events, query_ticks

router = APIRouter(prefix="/market", tags=["market"])

_MAX_LIMIT = 5000


def _row_to_dict(row) -> dict:
    """ORM row -> JSON dict; datetimes become ISO strings."""
    out: dict = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        out[column.name] = value
    return out


@router.get("/ticks", response_model=list[TickOut])
async def list_ticks(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_perm(Permission.VIEW_MARKET)),
    instrument_id: str = Query(min_length=1),
    from_ns: Optional[int] = Query(default=None, ge=0),
    to_ns: Optional[int] = Query(default=None, ge=0),
    limit: int = Query(default=1000, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[TickOut]:
    """Ticks of one instrument ordered by exchange timestamp."""
    rows = await query_ticks(
        db, instrument_id, from_ns=from_ns, to_ns=to_ns,
        limit=limit, offset=offset)
    return [TickOut(_row_to_dict(r)) for r in rows]


@router.get("/events", response_model=list[EventOut])
async def list_events(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_perm(Permission.VIEW_MARKET)),
    instrument_id: str = Query(min_length=1),
    from_ns: Optional[int] = Query(default=None, ge=0),
    to_ns: Optional[int] = Query(default=None, ge=0),
    label: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[EventOut]:
    """Microstructure events of one instrument, optionally by label."""
    rows = await query_micro_events(
        db, instrument_id, from_ns=from_ns, to_ns=to_ns, label=label,
        limit=limit, offset=offset)
    return [EventOut(_row_to_dict(r)) for r in rows]
