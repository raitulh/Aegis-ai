"""Rate limiting.

A sliding-window limiter behind a small interface. The in-memory backend is
correct for a single API process (the MVP deployment); multi-instance
deployments should plug in a shared backend (e.g. Redis) implementing
`RateLimitBackend`. Hits are counted for the admin incident summary.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Protocol

from fastapi import Request

from app.core.config import settings
from app.core.errors import RateLimited
from app.core.security import hash_identifier


class RateLimitBackend(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]: ...
    def reset(self) -> None: ...


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self.incidents: dict[str, int] = defaultdict(int)
        self.incident_last_at: dict[str, float] = {}

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            q = self._events[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                retry_after = int(q[0] + window_seconds - now) + 1
                bucket = key.split(":", 1)[0]
                self.incidents[bucket] += 1
                self.incident_last_at[bucket] = time.time()
                return False, retry_after
            q.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self.incidents.clear()
            self.incident_last_at.clear()


limiter: MemoryRateLimiter = MemoryRateLimiter()


def client_ip(request: Request) -> str:
    # Uvicorn's --proxy-headers / forwarded-allow-ips resolves request.client for trusted proxies.
    return request.client.host if request.client else "unknown"


def enforce(bucket: str, identity: str, *, limit: int, window_seconds: int) -> None:
    if not settings.RATE_LIMIT_ENABLED:
        return
    ok, retry_after = limiter.hit(f"{bucket}:{hash_identifier(identity)}", limit, window_seconds)
    if not ok:
        raise RateLimited(details={"retry_after_seconds": retry_after})


def limit_by_ip(bucket: str, limit: int, window_seconds: int):
    """FastAPI dependency factory."""

    def dependency(request: Request) -> None:
        enforce(bucket, client_ip(request), limit=limit, window_seconds=window_seconds)

    return dependency
