"""One-time tickets bridging authenticated REST to WebSocket channels.

A client that already holds an access token calls POST /api/v1/ws/ticket
and receives a short-lived opaque string; opening the WebSocket with
``?ticket=...`` redeems it exactly once, keeping tokens out of URLs.
"""

from __future__ import annotations

import secrets
import time
from typing import Optional

_TICKET_TTL_S = 30


class TicketStore:
    """In-memory single-use ticket registry (single-process deployments)."""

    def __init__(self) -> None:
        self._tickets: dict[str, tuple[int, float]] = {}

    def issue(self, user_id: int, ttl_s: float = _TICKET_TTL_S) -> str:
        """Mint a ticket bound to ``user_id`` expiring in ``ttl_s``."""
        self._purge()
        ticket = secrets.token_urlsafe(24)
        self._tickets[ticket] = (user_id, time.monotonic() + ttl_s)
        return ticket

    def redeem(self, ticket: str) -> Optional[int]:
        """Consume ``ticket``; returns the user id, or None if invalid."""
        self._purge()
        entry = self._tickets.pop(ticket, None)
        if entry is None:
            return None
        user_id, expires_at = entry
        if time.monotonic() >= expires_at:
            return None
        return user_id

    def _purge(self) -> None:
        now = time.monotonic()
        for key in [k for k, (_, exp) in self._tickets.items() if exp <= now]:
            del self._tickets[key]


tickets = TicketStore()
