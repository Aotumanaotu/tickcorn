"""Authentication endpoints: login / refresh rotation / logout / profile."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, \
    status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_settings
from app.api.schemas import LoginRequest, TokenResponse, UserOut
from app.auth.jwt import InvalidTokenError, create_access_token
from app.auth.rbac import permissions_of
from app.auth.service import (
    ReuseDetectedError,
    authenticate,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)
from app.common.logging import get_logger
from app.common.runtime import WebSettings
from app.storage.models import User

logger = get_logger("api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "mt_refresh"
_COOKIE_PATH = "/api/v1/auth"


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        permissions=permissions_of(user.role),
    )


def _set_refresh_cookie(response: Response, raw: str,
                        settings: WebSettings) -> None:
    response.set_cookie(
        _REFRESH_COOKIE,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=_COOKIE_PATH,
        max_age=settings.refresh_token_ttl_days * 86400,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(_REFRESH_COOKIE, path=_COOKIE_PATH)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: WebSettings = Depends(get_settings),
) -> TokenResponse:
    """Exchange credentials for an access token + refresh cookie."""
    user = await authenticate(db, body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access = create_access_token(
        user.id, user.username, user.role,
        secret=settings.jwt_secret, ttl_s=settings.access_token_ttl_s)
    raw_refresh, _ = await issue_refresh_token(
        db, user.id, ttl_days=settings.refresh_token_ttl_days)
    _set_refresh_cookie(response, raw_refresh, settings)
    logger.info("login ok: user=%s", user.username)
    return TokenResponse(
        access_token=access,
        expires_in=settings.access_token_ttl_s,
        user=_user_out(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    settings: WebSettings = Depends(get_settings),
) -> TokenResponse:
    """Rotate the refresh cookie and mint a fresh access token."""
    raw: Optional[str] = request.cookies.get(_REFRESH_COOKIE)
    if not raw:
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing refresh token")
    try:
        new_raw, user = await rotate_refresh_token(
            db, raw, ttl_days=settings.refresh_token_ttl_days)
    except (ReuseDetectedError, InvalidTokenError) as exc:
        logger.warning("refresh rejected: %s", exc)
        _clear_refresh_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    access = create_access_token(
        user.id, user.username, user.role,
        secret=settings.jwt_secret, ttl_s=settings.access_token_ttl_s)
    _set_refresh_cookie(response, new_raw, settings)
    return TokenResponse(
        access_token=access,
        expires_in=settings.access_token_ttl_s,
        user=_user_out(user),
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Revoke the cookie's refresh token and clear the cookie."""
    raw: Optional[str] = request.cookies.get(_REFRESH_COOKIE)
    if raw:
        await revoke_refresh_token(db, raw)
    _clear_refresh_cookie(response)
    logger.info("logout: user=%s", current_user.username)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> UserOut:
    """Profile of the authenticated user."""
    return _user_out(current_user)
