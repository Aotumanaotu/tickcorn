"""User accounts and rotating refresh tokens.

Passwords are argon2 hashes. Refresh tokens are never stored raw: only
their SHA-256 digest is persisted, grouped into "families" so that reuse
of an already-rotated member revokes the whole family (theft detection).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, \
    VerifyMismatchError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import InvalidTokenError
from app.storage.models import RefreshToken, User

__all__ = [
    "InvalidTokenError",
    "ReuseDetectedError",
    "hash_password",
    "verify_password",
    "get_user_by_username",
    "get_user",
    "create_user",
    "authenticate",
    "issue_refresh_token",
    "rotate_refresh_token",
    "revoke_refresh_token",
    "revoke_all_for_user",
]


class ReuseDetectedError(InvalidTokenError):
    """Raised when a rotated refresh token is presented again."""


_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Argon2 hash of ``password``."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Constant-answer verification; any mismatch or corrupt hash is False."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; reinterpret them as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def get_user_by_username(session: AsyncSession,
                               username: str) -> Optional[User]:
    """Fetch one user by exact username."""
    return await session.scalar(select(User).where(User.username == username))


async def get_user(session: AsyncSession, user_id: int) -> Optional[User]:
    """Fetch one user by primary key."""
    return await session.get(User, user_id)


async def create_user(session: AsyncSession, username: str, password: str,
                      role: str, email: Optional[str] = None) -> User:
    """Create a user; raises ValueError when the username is taken."""
    existing = await get_user_by_username(session, username)
    if existing is not None:
        raise ValueError("用户名已存在")
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, username: str,
                       password: str) -> Optional[User]:
    """Username + password login; inactive accounts never authenticate."""
    user = await get_user_by_username(session, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user


async def issue_refresh_token(session: AsyncSession, user_id: int, *,
                              ttl_days: int,
                              family_id: Optional[str] = None
                              ) -> tuple[str, RefreshToken]:
    """Issue one refresh token; returns ``(raw, row)``.

    The raw token only exists in the caller's memory (response / cookie);
    the database keeps its digest. A new family is seeded from the token's
    own digest so families survive without extra round-trips.
    """
    raw = secrets.token_urlsafe(48)
    family = family_id or _hash_token(raw)
    row = RefreshToken(
        user_id=user_id,
        family_id=family,
        token_hash=_hash_token(raw),
        expires_at=_utcnow() + timedelta(days=ttl_days),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return raw, row


async def rotate_refresh_token(session: AsyncSession, raw_token: str, *,
                               ttl_days: int) -> tuple[str, User]:
    """Consume ``raw_token`` and issue its successor in the same family.

    Presenting an already-revoked member is treated as theft: every token
    of that family is revoked and :class:`ReuseDetectedError` is raised.
    Unknown or expired tokens raise :class:`InvalidTokenError`.
    """
    token_hash = _hash_token(raw_token)
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if row is None:
        raise InvalidTokenError("refresh token not found")

    if row.revoked_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_utcnow()))
        await session.commit()
        raise ReuseDetectedError("refresh token reuse detected")

    if _as_utc(row.expires_at) <= _utcnow():
        raise InvalidTokenError("refresh token expired")

    user = await get_user(session, row.user_id)
    if user is None or not user.is_active:
        raise InvalidTokenError("user is inactive")

    row.revoked_at = _utcnow()
    new_raw, _ = await issue_refresh_token(
        session, row.user_id, ttl_days=ttl_days, family_id=row.family_id)
    return new_raw, user


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    """Revoke the token matching ``raw_token`` (no-op when unknown)."""
    token_hash = _hash_token(raw_token)
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .where(RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_utcnow()))
    await session.commit()


async def revoke_all_for_user(session: AsyncSession, user_id: int) -> None:
    """Revoke every active refresh token of one user."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .where(RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_utcnow()))
    await session.commit()
