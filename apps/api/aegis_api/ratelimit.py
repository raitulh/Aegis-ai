"""Fixed-window rate limiting with three tiers: public, authenticated and expensive (AI evaluation).

Uses Redis when configured (shared across processes); otherwise an in-process window store.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Literal

import structlog
from fastapi import Request

from aegis_api.config import get_settings
from aegis_api.errors import RateLimited

log = structlog.get_logger("aegis.ratelimit")
Tier = Literal["public", "authenticated", "expensive"]


class _MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, window: int) -> tuple[int, int]:
        now = time.time()
        with self._lock:
            count, reset = self._counts.get(key, (0, now + window))
            if now >= reset:
                count, reset = 0, now + window
            count += 1
            self._counts[key] = (count, reset)
            if len(self._counts) > 50_000:
                self._counts = {k: v for k, v in self._counts.items() if v[1] > now}
            return count, int(reset - now)


class RateLimiter:
    def __init__(self) -> None:
        self._memory = _MemoryStore()
        self._redis = None
        url = get_settings().redis_url
        if url:
            try:
                import redis

                self._redis = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
            except Exception:  # pragma: no cover - redis optional
                self._redis = None

    def limit_for(self, tier: Tier) -> int:
        s = get_settings()
        return {
            "public": s.rate_limit_public_per_min,
            "authenticated": s.rate_limit_auth_per_min,
            "expensive": s.rate_limit_expensive_per_min,
        }[tier]

    def check(self, tier: Tier, identity: str) -> None:
        self.check_custom(tier, identity, self.limit_for(tier))

    def check_custom(self, name: str, identity: str, limit: int, window: int = 60) -> None:
        """Fixed-window limit for an arbitrary named bucket (lab tiers, per-tool, per-endpoint…)."""
        if not get_settings().rate_limit_enabled:
            return
        tier = name
        key = f"rl:{tier}:{identity}:{int(time.time() // window)}"
        count, retry = 0, window
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, window + 5)
                count = int(pipe.execute()[0])
                retry = window - int(time.time() % window)
            except Exception:
                count, retry = self._memory.hit(key, window)
        else:
            count, retry = self._memory.hit(key, window)
        if count > limit:
            raise RateLimited(f"Rate limit exceeded for {tier} requests ({limit}/min)", retry_after=max(retry, 1))


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def reset_limiter() -> None:
    global _limiter
    _limiter = None


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def public_rate_limit(request: Request) -> None:
    get_limiter().check("public", client_ip(request))


def expensive_rate_limit_for(identity_fn: Callable[[Request], str]) -> Callable[[Request], None]:
    def _dep(request: Request) -> None:
        get_limiter().check("expensive", identity_fn(request))

    return _dep
