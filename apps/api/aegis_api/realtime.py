"""Cross-process real-time fan-out for Server-Sent Events.

The database (``audit_events``) is the source of truth: every event has a per-audit sequence number and
SSE clients resume from ``Last-Event-ID``, so nothing is lost across reconnects, API restarts or worker
restarts. Redis pub/sub only *wakes* waiting streams early; the payload is just the sequence number, never
event data, so the channel cannot leak tenant information. Without Redis, streams fall back to polling.

One Redis subscription is shared per API process (pattern subscribe), fanning out to per-audit asyncio
waiters, so the number of Redis connections does not grow with the number of open streams.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections import defaultdict
from typing import Any

import structlog

from aegis_api.config import get_settings

log = structlog.get_logger("aegis.realtime")
AUDIT_CHANNEL_PREFIX = "aegis:audit:"
_sync_client: Any = None


def _redis_sync() -> Any:
    global _sync_client
    url = get_settings().redis_url
    if not url:
        return None
    if _sync_client is None:
        import redis

        _sync_client = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
    return _sync_client


def publish_audit_event(organization_id: uuid.UUID, audit_id: uuid.UUID, seq: int) -> None:
    """Best-effort wake-up for SSE streams following ``audit_id`` (called after the event is committed)."""
    client = _redis_sync()
    if client is None:
        return
    try:
        client.publish(f"{AUDIT_CHANNEL_PREFIX}{audit_id}", str(seq))
    except Exception:
        log.debug("realtime_publish_failed", exc_info=True)


class EventHub:
    """Process-wide subscriber that turns Redis notifications into asyncio wake-ups."""

    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None
        self.connections = 0

    def _ensure_listener(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not get_settings().redis_url:
            return
        self._task = asyncio.get_running_loop().create_task(self._listen())

    async def _listen(self) -> None:
        import redis.asyncio as aioredis

        backoff = 0.5
        while True:
            try:
                client = aioredis.from_url(get_settings().redis_url or "", socket_connect_timeout=2)
                pubsub = client.pubsub()
                await pubsub.psubscribe(f"{AUDIT_CHANNEL_PREFIX}*")
                backoff = 0.5
                async for message in pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    channel = message.get("channel")
                    key = (channel.decode() if isinstance(channel, bytes) else str(channel)).removeprefix(
                        AUDIT_CHANNEL_PREFIX
                    )
                    for waiter in list(self._waiters.get(key, ())):
                        waiter.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("realtime_listener_reconnecting", exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    async def wait(self, audit_id: uuid.UUID, timeout: float) -> bool:
        """Wait until a new event for ``audit_id`` is announced (True) or ``timeout`` elapses (False)."""
        if not get_settings().redis_url:
            await asyncio.sleep(min(timeout, 1.0))
            return False
        self._ensure_listener()
        key = str(audit_id)
        event = asyncio.Event()
        self._waiters[key].add(event)
        try:
            await asyncio.wait_for(event.wait(), timeout)
            return True
        except TimeoutError:
            return False
        finally:
            self._waiters[key].discard(event)
            if not self._waiters[key]:
                self._waiters.pop(key, None)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


hub = EventHub()
