"""Internal event bus for low-latency fan-out (SSE wake-ups, cross-process notifications).

Events are *persisted* in PostgreSQL (``lab.events``) first; the bus only carries "something new happened on
channel X" notifications, so a lost message never loses data (subscribers re-read from the database with the
last seen event id). The interface is transport-agnostic so Redis can later be replaced by Kafka / Pub/Sub.
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

import structlog

from aegis_api.config import get_settings

log = structlog.get_logger("aegis.eventbus")


class Subscription(ABC):
    @abstractmethod
    def get(self, timeout: float) -> dict[str, Any] | None: ...

    @abstractmethod
    def close(self) -> None: ...


class EventBus(ABC):
    name = "base"

    @abstractmethod
    def publish(self, channel: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def subscribe(self, channel: str) -> Subscription: ...

    @abstractmethod
    def health(self) -> tuple[bool, str]: ...


class _MemorySubscription(Subscription):
    def __init__(self, bus: MemoryEventBus, channel: str) -> None:
        self.bus = bus
        self.channel = channel
        self.queue: list[dict[str, Any]] = []
        self.cond = threading.Condition()

    def deliver(self, payload: dict[str, Any]) -> None:
        with self.cond:
            self.queue.append(payload)
            self.cond.notify_all()

    def get(self, timeout: float) -> dict[str, Any] | None:
        with self.cond:
            if not self.queue:
                self.cond.wait(timeout)
            return self.queue.pop(0) if self.queue else None

    def close(self) -> None:
        self.bus._remove(self)


class MemoryEventBus(EventBus):
    """Single-process bus (development / tests / inline mode)."""

    name = "memory"

    def __init__(self) -> None:
        self._subs: dict[str, list[_MemorySubscription]] = {}
        self._lock = threading.Lock()

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs.get(channel, []))
        for sub in subs:
            sub.deliver(payload)

    def subscribe(self, channel: str) -> Subscription:
        sub = _MemorySubscription(self, channel)
        with self._lock:
            self._subs.setdefault(channel, []).append(sub)
        return sub

    def _remove(self, sub: _MemorySubscription) -> None:
        with self._lock:
            subs = self._subs.get(sub.channel, [])
            if sub in subs:
                subs.remove(sub)

    def health(self) -> tuple[bool, str]:
        return True, "in-process bus"


class _RedisSubscription(Subscription):
    def __init__(self, client: Any, channel: str) -> None:
        self.pubsub = client.pubsub(ignore_subscribe_messages=True)
        self.pubsub.subscribe(channel)

    def get(self, timeout: float) -> dict[str, Any] | None:
        try:
            message = self.pubsub.get_message(timeout=timeout)
        except Exception:
            return None
        if not message or message.get("type") != "message":
            return None
        try:
            data = json.loads(message["data"])
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def close(self) -> None:
        try:
            self.pubsub.close()
        except Exception:
            log.debug("pubsub_close_failed")


class RedisEventBus(EventBus):
    name = "redis"

    def __init__(self, url: str) -> None:
        import redis

        self.client = redis.Redis.from_url(url, socket_timeout=5, socket_connect_timeout=2, health_check_interval=30)

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        try:
            self.client.publish(channel, json.dumps(payload, default=str))
        except Exception as exc:  # the bus is best-effort; persisted events remain authoritative
            log.warning("event_publish_failed", channel=channel, error=type(exc).__name__)

    def subscribe(self, channel: str) -> Subscription:
        return _RedisSubscription(self.client, channel)

    def health(self) -> tuple[bool, str]:
        try:
            self.client.ping()
            return True, "redis reachable"
        except Exception as exc:
            return False, f"redis unavailable: {type(exc).__name__}"


@lru_cache(maxsize=1)
def get_event_bus() -> EventBus:
    url = get_settings().redis_url
    if url:
        try:
            return RedisEventBus(url)
        except Exception:
            log.warning("redis_event_bus_unavailable_fallback_memory")
    return MemoryEventBus()


def mission_channel(organization_id: str, mission_id: str) -> str:
    return f"aegis:lab:{organization_id}:mission:{mission_id}"


def workflow_channel(run_id: str) -> str:
    return f"aegis:lab:workflow:{run_id}"
