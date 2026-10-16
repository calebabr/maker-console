"""In-memory per-IP sliding-window rate limiter for login, 2FA and step-up.

One limiter per app, stored on ``app.state.auth_ratelimiter`` (created lazily), so tests
that build fresh apps get fresh limiters. The account lockout (store-backed) is separate.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable

from fastapi import HTTPException, Request

# bucket -> (max attempts, window seconds)
DEFAULT_LIMITS: dict[str, tuple[int, float]] = {
    "login": (10, 300.0),
    "2fa": (10, 300.0),
    "stepup": (20, 300.0),
}

_MAX_KEYS = 10_000  # memory guard (512 MB Render instance)


class SlidingWindowLimiter:
    def __init__(self, limits: dict[str, tuple[int, float]] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.limits = dict(limits or DEFAULT_LIMITS)
        self._clock = clock
        self._hits: dict[tuple[str, str], deque] = {}

    def hit(self, bucket: str, key: str) -> tuple[bool, int]:
        """Record an attempt. Returns (allowed, retry_after_seconds)."""
        limit, window = self.limits[bucket]
        now = self._clock()
        q = self._hits.get((bucket, key))
        if q is None:
            if len(self._hits) >= _MAX_KEYS:
                self._sweep(now)
            q = self._hits[(bucket, key)] = deque()
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            return False, max(1, math.ceil(window - (now - q[0])))
        q.append(now)
        return True, 0

    def reset(self) -> None:
        self._hits.clear()

    def _sweep(self, now: float) -> None:
        for k in list(self._hits):
            window = self.limits[k[0]][1]
            q = self._hits[k]
            while q and now - q[0] >= window:
                q.popleft()
            if not q:
                del self._hits[k]
        if len(self._hits) >= _MAX_KEYS:  # still full: drop everything rather than grow
            self._hits.clear()


def get_limiter(request: Request) -> SlidingWindowLimiter:
    state = request.app.state
    lim = getattr(state, "auth_ratelimiter", None)
    if lim is None:
        lim = SlidingWindowLimiter()
        state.auth_ratelimiter = lim
    return lim


def client_ip(request: Request) -> str:
    """LAST X-Forwarded-For hop, else the peer address.

    The client controls everything before the last hop; Render's proxy (and Caddy on the VM path)
    appends the real peer address at the end, so only the last hop can be trusted (SECURITY_REVIEW S-1)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last[:64]
    return request.client.host if request.client else "unknown"


def enforce(request: Request, bucket: str) -> None:
    """Raise 429 ``rate_limited`` (with Retry-After) if this IP exceeded ``bucket``."""
    ok, retry = get_limiter(request).hit(bucket, client_ip(request))
    if not ok:
        raise HTTPException(status_code=429, detail="rate_limited",
                            headers={"Retry-After": str(retry)})
