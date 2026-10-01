"""Fixed-window rate limiting with three tiers: public, authenticated and expensive (AI evaluation).

Uses Redis when configured (shared across processes); otherwise an in-process window store.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from typing import Any, Literal

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
        if not get_settings().rate_limit_enabled:
            return
        limit = self.limit_for(tier)
        window = 60
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


class LoginThrottle:
    """Counts failed sign-ins per account (independent of IP) and blocks further attempts for the window.

    Uses Redis when configured so the limit holds across API replicas."""

    def __init__(self, limiter: RateLimiter) -> None:
        self._limiter = limiter

    @staticmethod
    def _key(email: str) -> str:
        import hashlib

        return "login-fail:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]

    def _window(self) -> int:
        return max(60, get_settings().login_failure_window_seconds)

    def failures(self, email: str) -> int:
        key = self._key(email)
        if self._limiter._redis is not None:
            with contextlib.suppress(Exception):
                raw: Any = self._limiter._redis.get(key)
                return int(raw or 0)
        with self._limiter._memory._lock:
            count, reset = self._limiter._memory._counts.get(key, (0, 0.0))
            return count if time.time() < reset else 0

    def check(self, email: str) -> None:
        settings = get_settings()
        if settings.rate_limit_enabled and self.failures(email) >= settings.login_max_failures:
            raise RateLimited("Too many failed sign-in attempts. Try again later.", retry_after=self._window())

    def record_failure(self, email: str) -> None:
        key, window = self._key(email), self._window()
        if self._limiter._redis is not None:
            with contextlib.suppress(Exception):
                pipe = self._limiter._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, window)
                pipe.execute()
                return
        self._limiter._memory.hit(key, window)

    def reset(self, email: str) -> None:
        key = self._key(email)
        if self._limiter._redis is not None:
            with contextlib.suppress(Exception):
                self._limiter._redis.delete(key)
        with self._limiter._memory._lock:
            self._limiter._memory._counts.pop(key, None)


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def reset_limiter() -> None:
    global _limiter
    _limiter = None


def login_throttle() -> LoginThrottle:
    return LoginThrottle(get_limiter())


def client_ip(request: Request) -> str:
    """The caller's address. ``X-Forwarded-For`` is only honoured for the configured number of trusted
    proxy hops (the right-most entries are appended by our own proxies; anything to their left is
    client-controlled and must never be used for rate limiting or audit attribution)."""
    peer = request.client.host if request.client else "unknown"
    hops = get_settings().trusted_proxy_hops
    if hops <= 0:
        return peer
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(forwarded) >= hops:
        return forwarded[-hops]
    return forwarded[0] if forwarded else peer


def public_rate_limit(request: Request) -> None:
    get_limiter().check("public", client_ip(request))


def guest_rate_limit(request: Request) -> None:
    """Guest sandboxes create a workspace and seed data: allow a handful per address per window."""
    limiter = get_limiter()
    if not get_settings().rate_limit_enabled:
        return
    key = f"rl:guest:{client_ip(request)}:{int(time.time() // 3600)}"
    count, retry = limiter._memory.hit(key, 3600)
    if limiter._redis is not None:
        with contextlib.suppress(Exception):
            pipe = limiter._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 3605)
            count = int(pipe.execute()[0])
            retry = 3600 - int(time.time() % 3600)
    if count > GUEST_SANDBOXES_PER_HOUR:
        raise RateLimited("Too many demo sandboxes requested from this address", retry_after=max(retry, 1))


GUEST_SANDBOXES_PER_HOUR = 10


def expensive_rate_limit_for(identity_fn: Callable[[Request], str]) -> Callable[[Request], None]:
    def _dep(request: Request) -> None:
        get_limiter().check("expensive", identity_fn(request))

    return _dep
