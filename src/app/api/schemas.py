"""Request / response DTOs for the REST API (pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel

from app.auth.rbac import Role


class LoginRequest(BaseModel):
    """Username + password credentials."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    """Public projection of a user account."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: Optional[str] = None
    role: str
    is_active: bool
    created_at: Optional[datetime] = None
    permissions: list[str] = Field(default_factory=list)


class TokenResponse(BaseModel):
    """Access token plus the authenticated user's profile."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserCreate(BaseModel):
    """Admin-side user provisioning payload."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: Role
    email: Optional[str] = None


class GatewayConnectRequest(BaseModel):
    """CTP gateway login control payload (validated again gateway-side)."""

    fronts: list[str] = Field(min_length=1)
    broker_id: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password: str = Field(min_length=1)
    instruments: list[str] = Field(min_length=1)
    remember: bool = False


class GatewaySubscribeRequest(BaseModel):
    """Instrument (un)subscribe control payload."""

    instruments: list[str] = Field(min_length=1)


class TickOut(RootModel[dict[str, Any]]):
    """Loose passthrough DTO for one tick row."""

    model_config = ConfigDict(from_attributes=True)


class EventOut(RootModel[dict[str, Any]]):
    """Loose passthrough DTO for one microstructure event row."""

    model_config = ConfigDict(from_attributes=True)


class ProductOut(BaseModel):
    """Reference product row."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    exchange: str
    category: str
    tick_size: Optional[float] = None
    sort_order: int


class InstrumentOut(BaseModel):
    """Known instrument with static parameters."""

    model_config = ConfigDict(from_attributes=True)

    instrument_id: str
    product_code: Optional[str] = None
    exchange: str
    tick_size: Optional[float] = None
    is_main: bool = False
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


class WatchlistItemOut(BaseModel):
    """One instrument inside a watchlist."""

    model_config = ConfigDict(from_attributes=True)

    instrument_id: str
    sort_order: int = 0
    added_at: Optional[datetime] = None


class WatchlistOut(BaseModel):
    """Named watchlist with its items."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: Optional[datetime] = None
    items: list[WatchlistItemOut] = Field(default_factory=list)


class WatchlistCreate(BaseModel):
    """New watchlist name."""

    name: str = Field(min_length=1, max_length=64)


class WatchlistItemCreate(BaseModel):
    """Instrument to append to a watchlist."""

    instrument_id: str = Field(min_length=1, max_length=32)
