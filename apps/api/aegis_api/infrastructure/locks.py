"""Distributed locks.

* ``advisory_xact_lock(session, key)`` — PostgreSQL transaction-scoped advisory lock; released automatically
  at commit/rollback. Used for short critical sections that are also DB transactions (evidence chain appends,
  strategy promotion, sequence allocation).
* ``distributed_lock(name, ttl)`` — cross-process mutual exclusion for singletons (scheduler loops, webhook
  delivery). Redis ``SET NX PX`` with a random token and compare-and-delete release when Redis is configured;
  otherwise a session-level PostgreSQL advisory lock on a dedicated connection.
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from aegis_api.config import get_settings

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""


def lock_id(key: str) -> int:
    """Stable signed 64-bit id for PostgreSQL advisory locks."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)


def advisory_xact_lock(session: Session, key: str) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_id(key)})


@contextmanager
def distributed_lock(name: str, *, ttl_seconds: int = 60, wait_seconds: float = 0.0) -> Iterator[bool]:
    """Yield True when the lock was acquired (the body should skip its work when False)."""
    settings = get_settings()
    if settings.redis_url:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        token = secrets.token_hex(16)
        key = f"aegis:lock:{name}"
        deadline = time.monotonic() + wait_seconds
        acquired = False
        try:
            while True:
                acquired = bool(client.set(key, token, nx=True, px=ttl_seconds * 1000))
                if acquired or time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            yield acquired
        finally:
            if acquired:
                with contextlib.suppress(Exception):  # the key also expires via its TTL
                    client.eval(_RELEASE_LUA, 1, key, token)
        return
    from aegis_api.db.session import get_admin_engine

    with get_admin_engine().connect() as conn:
        lid = lock_id(name)
        deadline = time.monotonic() + wait_seconds
        acquired = False
        while True:
            acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": lid}).scalar())
            if acquired or time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lid})
            conn.commit()
