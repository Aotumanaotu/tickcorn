"""Short-lived HS256 access tokens (JWT).

Only the API signs and verifies these; claims carry the identity triple
(user id, username, role) plus standard ``iat`` / ``exp`` / ``jti``. A
5-second leeway tolerates clock skew between signing and verification.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt as pyjwt

_LEEWAY_S = 5


class InvalidTokenError(Exception):
    """Raised when a token is malformed, invalid or expired."""


def create_access_token(user_id: int, username: str, role: str, *,
                        secret: str, ttl_s: int) -> str:
    """Sign one access token for the given identity and TTL."""
    now = datetime.now(tz=timezone.utc)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_s)).timestamp()),
        "jti": uuid4().hex,
    }
    return pyjwt.encode(claims, secret, algorithm="HS256")


def decode_access_token(token: str, *, secret: str) -> dict[str, Any]:
    """Verify and decode; raises :class:`InvalidTokenError` on any failure."""
    try:
        return pyjwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            leeway=_LEEWAY_S,
            options={"require": ["exp", "sub"]},
        )
    except pyjwt.InvalidTokenError as exc:
        raise InvalidTokenError(str(exc)) from exc
