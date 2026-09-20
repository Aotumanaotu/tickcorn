"""Shared FastAPI dependencies: DB session, auth, RBAC, settings."""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import InvalidTokenError, decode_access_token
from app.auth.rbac import Permission, role_has
from app.auth.service import get_user
from app.common.runtime import WebSettings
from app.storage.models import User

_WWW_AUTH = {"WWW-Authenticate": "Bearer"}


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield one session from the app-level factory."""
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_settings(request: Request) -> WebSettings:
    """WebSettings stored on app.state at startup."""
    return request.app.state.settings


def get_config(request: Request):
    """AppConfig stored on app.state at startup."""
    return request.app.state.config


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the bearer token to an active User row, or 401."""
    settings = request.app.state.settings
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers=_WWW_AUTH,
        )
    token = authorization[7:].strip()
    try:
        claims = decode_access_token(token, secret=settings.jwt_secret)
        user_id = int(claims["sub"])
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc) or "invalid token",
            headers=_WWW_AUTH,
        ) from exc
    user = await get_user(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found or inactive",
            headers=_WWW_AUTH,
        )
    return user


def require_perm(perm: Permission):
    """Build a dependency that enforces ``perm`` on the current user."""

    async def checker(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if not role_has(current_user.role, perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission denied: {perm.value}",
            )
        return current_user

    return checker
