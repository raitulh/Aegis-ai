"""Event bus: low-latency wake-ups for SSE streams and consumers.

The bus carries *notifications* ("event N was committed on channel C"), never event data: the
``events`` outbox table is the source of truth and every subscriber re-reads it. A lost notification
therefore only adds latency (subscribers also poll on their heartbeat), which is why publishing is
best-effort and never raises.

* :class:`InMemoryEventBus` — process-local; ``notify`` is thread-safe and wakes waiters on any event
  loop via ``loop.call_soon_threadsafe``.
* :class:`RedisEventBus` — cross-process via Redis pub/sub. ``notify`` is a synchronous ``PUBLISH``
  with short socket timeouts and a short circuit-breaker when Redis is down; listeners use
  ``redis.asyncio`` pub/sub and degrade to timed polling if Redis is unavailable.

``listen(channel)`` registers interest *before* the caller polls the database, so a notification that
arrives between the poll and the wait is never missed. ``wait(channel, timeout)`` is the one-shot form.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable

import structlog

from aegis_api.config import get_settings
from aegis_api.lab.observability.metrics import EVENT_BUS_NOTIFICATIONS, REDIS_LATENCY

log = structlog.get_logger("aegis.lab.events.bus")

DEGRADED_POLL_SECONDS = 5.0
REDIS_RETRY_AFTER_SECONDS = 5.0
RESUBSCRIBE_INTERVAL_SECONDS = 30.0


@runtime_checkable
class Listener(Protocol):
    async def wait(self, timeout: float) -> bool:
        """Wait until notified (True) or until ``timeout`` seconds pass (False)."""
        ...


@runtime_checkable
class EventBus(Protocol):
    backend: str

    def notify(self, channel: str, event_id: int) -> None:
        """Publish a wake-up for ``channel`` (sync, thread-safe, never raises)."""
        ...

    async def wait(self, channel: str, timeout: float) -> bool:
        """Wait for one notification on ``channel``; False on timeout."""
        ...

    def listen(self, channel: str) -> contextlib.AbstractAsyncContextManager[Listener]:
        """Subscribe for the duration of the context; notifications are buffered (coalesced)."""
        ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------------------------------
# In-memory
# --------------------------------------------------------------------------------------------------
class _MemoryListener:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.event = asyncio.Event()

    async def wait(self, timeout: float) -> bool:
        if not self.event.is_set():
            try:
                await asyncio.wait_for(self.event.wait(), timeout=max(0.0, timeout))
            except TimeoutError:
                return False
        self.event.clear()
        return True


class InMemoryEventBus:
    """Process-local bus: channel → registered listeners (each bound to its event loop)."""

    backend = "memory"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._listeners: dict[str, set[_MemoryListener]] = {}

    def notify(self, channel: str, event_id: int) -> None:
        with self._lock:
            listeners = list(self._listeners.get(channel, ()))
        for listener in listeners:
            try:
                listener.loop.call_soon_threadsafe(listener.event.set)
            except RuntimeError:  # the listener's loop is closed; it will be unregistered by its owner
                continue
        EVENT_BUS_NOTIFICATIONS.labels(self.backend, "published").inc()

    @asynccontextmanager
    async def listen(self, channel: str) -> AsyncIterator[_MemoryListener]:
        listener = _MemoryListener()
        with self._lock:
            self._listeners.setdefault(channel, set()).add(listener)
        try:
            yield listener
        finally:
            with self._lock:
                registered = self._listeners.get(channel)
                if registered is not None:
                    registered.discard(listener)
                    if not registered:
                        del self._listeners[channel]

    async def wait(self, channel: str, timeout: float) -> bool:
        async with self.listen(channel) as listener:
            return await listener.wait(timeout)

    def listener_count(self, channel: str | None = None) -> int:
        with self._lock:
            if channel is not None:
                return len(self._listeners.get(channel, ()))
            return sum(len(v) for v in self._listeners.values())

    def close(self) -> None:
        """Wake every listener (so streams re-poll) and forget them."""
        with self._lock:
            listeners = [item for group in self._listeners.values() for item in group]
            self._listeners.clear()
        for listener in listeners:
            with contextlib.suppress(RuntimeError):
                listener.loop.call_soon_threadsafe(listener.event.set)


# --------------------------------------------------------------------------------------------------
# Redis
# --------------------------------------------------------------------------------------------------
class _RedisListener:
    def __init__(self, url: str, channel: str) -> None:
        self.url = url
        self.channel = channel
        self._client: Any = None
        self._pubsub: Any = None
        self._next_subscribe_at = 0.0

    async def open(self) -> None:
        await self._subscribe()

    async def _subscribe(self) -> bool:
        import redis.asyncio as aioredis

        self._next_subscribe_at = time.monotonic() + RESUBSCRIBE_INTERVAL_SECONDS
        try:
            self._client = aioredis.Redis.from_url(
                self.url, socket_connect_timeout=1, socket_keepalive=True, health_check_interval=30
            )
            self._pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            await self._pubsub.subscribe(self.channel)
            return True
        except Exception as exc:  # degrade to polling; the outbox still delivers every event
            log.warning("event_bus_subscribe_failed", error=type(exc).__name__)
            await self._release()
            return False

    async def _release(self) -> None:
        pubsub, client = self._pubsub, self._client
        self._pubsub = self._client = None
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
            with contextlib.suppress(Exception):
                await pubsub.aclose()
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()

    async def close(self) -> None:
        await self._release()

    async def wait(self, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        if self._pubsub is None and time.monotonic() >= self._next_subscribe_at:
            await self._subscribe()
        if self._pubsub is None:  # degraded: timed polling
            await asyncio.sleep(max(0.0, min(timeout, DEGRADED_POLL_SECONDS)))
            return False
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            try:
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining)
            except Exception as exc:
                log.warning("event_bus_listen_failed", error=type(exc).__name__)
                await self._release()
                self._next_subscribe_at = time.monotonic() + REDIS_RETRY_AFTER_SECONDS
                await asyncio.sleep(max(0.0, min(deadline - loop.time(), DEGRADED_POLL_SECONDS)))
                return False
            if message is not None and message.get("type") == "message":
                await self._drain()
                return True

    async def _drain(self) -> None:
        """Coalesce notifications that are already buffered (one re-poll covers them all)."""
        for _ in range(1000):
            try:
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=0)
            except Exception:
                return
            if message is None:
                return


class RedisEventBus:
    """Cross-process bus over Redis pub/sub (notifications only)."""

    backend = "redis"

    def __init__(self, url: str) -> None:
        import redis

        self.url = url
        self._client = redis.Redis.from_url(
            url, socket_connect_timeout=0.5, socket_timeout=0.5, health_check_interval=30
        )
        self._down_until = 0.0
        self._lock = threading.Lock()

    def notify(self, channel: str, event_id: int) -> None:
        if time.monotonic() < self._down_until:
            EVENT_BUS_NOTIFICATIONS.labels(self.backend, "skipped").inc()
            return
        started = time.perf_counter()
        try:
            self._client.publish(channel, str(event_id))
        except Exception as exc:  # best-effort: subscribers also poll the outbox
            with self._lock:
                self._down_until = time.monotonic() + REDIS_RETRY_AFTER_SECONDS
            EVENT_BUS_NOTIFICATIONS.labels(self.backend, "failed").inc()
            log.warning("event_bus_publish_failed", error=type(exc).__name__)
            return
        finally:
            REDIS_LATENCY.labels("publish").observe(time.perf_counter() - started)
        EVENT_BUS_NOTIFICATIONS.labels(self.backend, "published").inc()

    @asynccontextmanager
    async def listen(self, channel: str) -> AsyncIterator[_RedisListener]:
        listener = _RedisListener(self.url, channel)
        await listener.open()
        try:
            yield listener
        finally:
            await listener.close()

    async def wait(self, channel: str, timeout: float) -> bool:
        async with self.listen(channel) as listener:
            return await listener.wait(timeout)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._client.close()


# --------------------------------------------------------------------------------------------------
# Singleton
# --------------------------------------------------------------------------------------------------
_bus: InMemoryEventBus | RedisEventBus | None = None
_bus_lock = threading.Lock()


def create_event_bus() -> InMemoryEventBus | RedisEventBus:
    settings = get_settings()
    if settings.effective_event_bus == "redis":
        if settings.redis_url:
            return RedisEventBus(settings.redis_url)
        log.warning("event_bus_redis_without_url", fallback="memory")
    return InMemoryEventBus()


def get_event_bus() -> InMemoryEventBus | RedisEventBus:
    """The process-wide bus selected by ``settings.effective_event_bus``."""
    global _bus
    bus = _bus
    if bus is not None:
        return bus
    with _bus_lock:
        if _bus is None:
            _bus = create_event_bus()
        return _bus


def close_event_bus() -> None:
    """Close and forget the process-wide bus (app shutdown); the next ``get_event_bus`` creates a new one."""
    global _bus
    with _bus_lock:
        bus, _bus = _bus, None
    if bus is not None:
        bus.close()


reset_event_bus = close_event_bus
