"""Reference data (products / instruments) and per-user watchlists."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_config, get_db, require_perm
from app.api.schemas import (
    InstrumentOut,
    ProductOut,
    WatchlistCreate,
    WatchlistItemCreate,
    WatchlistItemOut,
    WatchlistOut,
)
from app.auth.rbac import Permission
from app.common.config import AppConfig, parse_product
from app.common.logging import get_logger
from app.storage.models import (
    Instrument,
    Product,
    Watchlist,
    WatchlistItem,
    User,
)

logger = get_logger("api.instruments")

router = APIRouter(tags=["instruments"])

_DEFAULT_WATCHLIST_NAME = "Default"


async def _load_products(db: AsyncSession) -> list[Product]:
    stmt = select(Product).order_by(Product.sort_order.asc(), Product.code.asc())
    return list((await db.execute(stmt)).scalars().all())


async def _merge_instruments(db: AsyncSession, config: AppConfig) -> list[dict]:
    """DB instruments first, then config-only overrides (DB rows win)."""
    rows = list((await db.execute(select(Instrument))).scalars().all())
    merged: dict[str, dict] = {}
    for row in rows:
        merged[row.instrument_id] = {
            "instrument_id": row.instrument_id,
            "product_code": row.product_code,
            "exchange": row.exchange,
            "tick_size": row.tick_size,
            "is_main": bool(row.is_main),
            "first_seen": row.first_seen,
            "last_seen": row.last_seen,
        }
    for instrument_id, override in (
            config.instruments.instrument_overrides.items()):
        if instrument_id in merged:
            merged[instrument_id]["tick_size"] = override.get(
                "tick_size", merged[instrument_id]["tick_size"])
            merged[instrument_id]["is_main"] = bool(override.get(
                "is_main", merged[instrument_id]["is_main"]))
            continue
        try:
            product_code = parse_product(instrument_id)
            exchange = config.exchange_of(instrument_id)
            tick_size = config.resolve_tick_size(instrument_id)
        except Exception:  # noqa: BLE001
            continue
        merged[instrument_id] = {
            "instrument_id": instrument_id,
            "product_code": product_code,
            "exchange": exchange,
            "tick_size": tick_size,
            "is_main": bool(override.get("is_main", False)),
            "first_seen": None,
            "last_seen": None,
        }
    return list(merged.values())


@router.get("/products", response_model=list[ProductOut])
async def list_products(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_perm(Permission.VIEW_MARKET)),
) -> list[ProductOut]:
    """All seeded products, ordered by sort_order."""
    return [ProductOut.model_validate(p) for p in await _load_products(db)]


@router.get("/instruments", response_model=list[InstrumentOut])
async def list_instruments(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_perm(Permission.VIEW_MARKET)),
    exchange: Optional[str] = Query(default=None),
    product: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    config: AppConfig = Depends(get_config),
) -> list[InstrumentOut]:
    """Known instruments: DB rows merged with config overrides (DB first)."""
    items = await _merge_instruments(db, config)
    if exchange:
        items = [i for i in items if i["exchange"] == exchange]
    if product:
        key = product.strip().upper()
        items = [i for i in items
                 if (i["product_code"] or "").upper() == key]
    if search:
        needle = search.strip().lower()
        items = [i for i in items if needle in i["instrument_id"].lower()]
    items.sort(key=lambda i: i["instrument_id"])
    return [InstrumentOut.model_validate(i) for i in items[:limit]]


# ----------------------------------------------------------------------
# Watchlists
# ----------------------------------------------------------------------

async def _owned_watchlist(db: AsyncSession, user: User,
                           watchlist_id: int) -> Watchlist:
    row = await db.get(Watchlist, watchlist_id)
    if row is None or row.owner_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="watchlist not found")
    return row


async def _watchlist_out(db: AsyncSession, row: Watchlist) -> WatchlistOut:
    stmt = (select(WatchlistItem).where(WatchlistItem.watchlist_id == row.id)
            .order_by(WatchlistItem.sort_order.asc(),
                      WatchlistItem.id.asc()))
    items = list((await db.execute(stmt)).scalars().all())
    return WatchlistOut(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        items=[WatchlistItemOut.model_validate(i) for i in items],
    )


async def _ensure_default_watchlist(db: AsyncSession, user: User) -> None:
    stmt = select(Watchlist).where(Watchlist.owner_user_id == user.id)
    if (await db.execute(stmt)).scalars().first() is None:
        db.add(Watchlist(owner_user_id=user.id, name=_DEFAULT_WATCHLIST_NAME))
        await db.commit()


@router.get("/watchlists", response_model=list[WatchlistOut])
async def list_watchlists(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm(Permission.VIEW_MARKET)),
) -> list[WatchlistOut]:
    """The caller's watchlists (a default one is created on first use)."""
    await _ensure_default_watchlist(db, user)
    stmt = (select(Watchlist).where(Watchlist.owner_user_id == user.id)
            .order_by(Watchlist.id.asc()))
    rows = list((await db.execute(stmt)).scalars().all())
    return [await _watchlist_out(db, r) for r in rows]


@router.post("/watchlists", response_model=WatchlistOut,
             status_code=status.HTTP_201_CREATED)
async def create_watchlist(
    body: WatchlistCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm(Permission.VIEW_MARKET)),
) -> WatchlistOut:
    """Create an empty watchlist owned by the caller."""
    row = Watchlist(owner_user_id=user.id, name=body.name)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return await _watchlist_out(db, row)


@router.get("/watchlists/{watchlist_id}", response_model=WatchlistOut)
async def get_watchlist(
    watchlist_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm(Permission.VIEW_MARKET)),
) -> WatchlistOut:
    """One owned watchlist with its items."""
    row = await _owned_watchlist(db, user, watchlist_id)
    return await _watchlist_out(db, row)


async def _instrument_known(db: AsyncSession, config: AppConfig,
                            instrument_id: str) -> bool:
    row = await db.get(Instrument, instrument_id)
    if row is not None:
        return True
    try:
        return parse_product(instrument_id) in config.instruments.products
    except Exception:  # noqa: BLE001
        return False


@router.post("/watchlists/{watchlist_id}/items", response_model=WatchlistOut,
             status_code=status.HTTP_201_CREATED)
async def add_watchlist_item(
    watchlist_id: int,
    body: WatchlistItemCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm(Permission.VIEW_MARKET)),
    config: AppConfig = Depends(get_config),
) -> WatchlistOut:
    """Append an instrument to an owned watchlist (idempotent)."""
    row = await _owned_watchlist(db, user, watchlist_id)
    instrument_id = body.instrument_id.strip().upper()
    if not await _instrument_known(db, config, instrument_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown instrument: {instrument_id}")
    stmt = select(WatchlistItem).where(
        WatchlistItem.watchlist_id == row.id,
        WatchlistItem.instrument_id == instrument_id)
    existing = (await db.execute(stmt)).scalars().first()
    if existing is None:
        db.add(WatchlistItem(watchlist_id=row.id, instrument_id=instrument_id))
        await db.commit()
    return await _watchlist_out(db, row)


@router.delete("/watchlists/{watchlist_id}/items/{instrument_id}")
async def delete_watchlist_item(
    watchlist_id: int,
    instrument_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_perm(Permission.VIEW_MARKET)),
) -> dict:
    """Remove an instrument from an owned watchlist."""
    row = await _owned_watchlist(db, user, watchlist_id)
    stmt = select(WatchlistItem).where(
        WatchlistItem.watchlist_id == row.id,
        WatchlistItem.instrument_id == instrument_id.upper())
    item = (await db.execute(stmt)).scalars().first()
    if item is not None:
        await db.delete(item)
        await db.commit()
    return {"ok": True}
