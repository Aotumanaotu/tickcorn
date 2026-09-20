"""In-memory failed-login throttle (per client IP, sliding window).

The API is designed to sit behind the Caddy reverse proxy on the public
internet, so brute-force protection on the credential endpoint is
mandatory. Only FAILED attempts count; a successful login clears the
caller's window. Single-process (uvicorn runs one worker), no shared
state needed.

Config via env:
    LOGIN_RATE_LIMIT       max failures per window (default 5)
    LOGIN_RATE_WINDOW_S    window seconds (default 300)
"""

from __future__ import annotations

import os
import time
from collections import deque

from fastapi import HTTPException, Request, status

from app.common.logging import get_logger

logger = get_logger("api.ratelimit")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class LoginThrottle:
    def __init__(self, max_failures: int = 5, window_s: float = 300.0):
        self.max_failures = max(1, max_failures)
        self.window_s = float(window_s)
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, ip: str, now: float) -> deque[float]:
        dq = self._failures.setdefault(ip, deque())
        while dq and now - dq[0] > self.window_s:
            dq.popleft()
        if not dq:
            self._failures.pop(ip, None)
            return deque()
        return dq

    def check(self, ip: str) -> None:
        """Raise 429 when the IP has exhausted its failure budget."""
        dq = self._prune(ip, time.monotonic())
        if len(dq) >= self.max_failures:
            retry = int(self.window_s - (time.monotonic() - dq[0])) + 1
            logger.warning("login throttle tripped for %s (%d failures)",
                           ip, len(dq))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="尝试次数过多，请稍后再试",
                headers={"Retry-After": str(max(retry, 1))},
            )

    def record_failure(self, ip: str) -> None:
        dq = self._failures.setdefault(ip, deque())
        dq.append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)


throttle = LoginThrottle(
    max_failures=_env_int("LOGIN_RATE_LIMIT", 5),
    window_s=_env_int("LOGIN_RATE_WINDOW_S", 300),
)


def client_ip(request: Request) -> str:
    """Best-effort client IP (uvicorn rewrites this from X-Forwarded-For
    when the proxy is trusted via FORWARDED_ALLOW_IPS)."""
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"
