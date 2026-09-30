"""Event bus (in-memory + Redis) and pure SSE helpers."""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid

import pytest

REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/0")


def _redis_available() -> bool:
    try:
        import redis

        return bool(redis.Redis.from_url(REDIS_TEST_URL, socket_connect_timeout=0.5).ping())
    except Exception:
        return False


requires_redis = pytest.mark.skipif(not _redis_available(), reason="Redis is not available")


# --- in-memory bus -------------------------------------------------------------------------------------
def test_memory_notify_from_another_thread_wakes_waiter():
    from aegis_api.lab.events.bus import InMemoryEventBus

    bus = InMemoryEventBus()

    async def scenario() -> tuple[bool, float]:
        started = time.perf_counter()
        timer = threading.Timer(0.1, bus.notify, args=("chan-a", 42))
        timer.start()
        try:
            woke = await bus.wait("chan-a", timeout=5)
        finally:
            timer.cancel()
        return woke, time.perf_counter() - started

    woke, elapsed = asyncio.run(scenario())
    assert woke is True
    assert elapsed < 2  # woken by the notification, not by the timeout
    assert bus.listener_count() == 0


def test_memory_wait_timeout_returns_false():
    from aegis_api.lab.events.bus import InMemoryEventBus

    bus = InMemoryEventBus()
    assert asyncio.run(bus.wait("quiet", timeout=0.05)) is False
    assert bus.listener_count("quiet") == 0


def test_memory_notification_on_other_channel_does_not_wake():
    from aegis_api.lab.events.bus import InMemoryEventBus

    bus = InMemoryEventBus()

    async def scenario() -> bool:
        async with bus.listen("mine") as listener:
            bus.notify("other", 1)
            return await listener.wait(0.1)

    assert asyncio.run(scenario()) is False


def test_memory_listener_buffers_notifications_between_waits():
    """A notification arriving while the subscriber polls the DB is not lost (listen before poll)."""
    from aegis_api.lab.events.bus import InMemoryEventBus

    bus = InMemoryEventBus()

    async def scenario() -> tuple[bool, bool]:
        async with bus.listen("c") as listener:
            bus.notify("c", 1)
            bus.notify("c", 2)  # coalesced
            first = await listener.wait(0.01)
            second = await listener.wait(0.05)
            return first, second

    assert asyncio.run(scenario()) == (True, False)


def test_memory_close_wakes_listeners():
    from aegis_api.lab.events.bus import InMemoryEventBus

    bus = InMemoryEventBus()

    async def scenario() -> bool:
        async with bus.listen("c") as listener:
            threading.Timer(0.05, bus.close).start()
            return await listener.wait(5)

    assert asyncio.run(scenario()) is True


def test_get_event_bus_selects_backend(monkeypatch):
    from aegis_api.config import get_settings
    from aegis_api.lab.events import bus as bus_module

    settings = get_settings()
    bus_module.reset_event_bus()
    try:
        monkeypatch.setattr(settings, "event_bus_backend", "memory")
        assert isinstance(bus_module.get_event_bus(), bus_module.InMemoryEventBus)
        assert bus_module.get_event_bus() is bus_module.get_event_bus()
        bus_module.reset_event_bus()
        monkeypatch.setattr(settings, "event_bus_backend", "redis")
        monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
        assert isinstance(bus_module.get_event_bus(), bus_module.RedisEventBus)
        bus_module.reset_event_bus()
        monkeypatch.setattr(settings, "redis_url", "")  # redis requested but not configured → memory
        assert isinstance(bus_module.get_event_bus(), bus_module.InMemoryEventBus)
    finally:
        bus_module.reset_event_bus()


# --- Redis bus -----------------------------------------------------------------------------------------
def test_redis_notify_never_raises_when_redis_is_down():
    from aegis_api.lab.events.bus import RedisEventBus

    bus = RedisEventBus("redis://127.0.0.1:1/0")
    try:
        started = time.perf_counter()
        bus.notify("x", 1)  # connection refused → logged, swallowed
        bus.notify("x", 2)  # circuit open → skipped immediately
        assert time.perf_counter() - started < 3
    finally:
        bus.close()


def test_redis_listener_degrades_to_polling_when_redis_is_down():
    from aegis_api.lab.events.bus import RedisEventBus

    bus = RedisEventBus("redis://127.0.0.1:1/0")

    async def scenario() -> bool:
        async with bus.listen("x") as listener:
            return await listener.wait(0.1)

    try:
        assert asyncio.run(scenario()) is False
    finally:
        bus.close()


@pytest.mark.redis
@requires_redis
def test_redis_publish_wakes_waiter_and_timeout():
    from aegis_api.lab.events.bus import RedisEventBus

    bus = RedisEventBus(REDIS_TEST_URL)
    channel = f"lab:test:{uuid.uuid4().hex}"

    async def scenario() -> tuple[bool, bool, bool]:
        async with bus.listen(channel) as listener:
            timer = threading.Timer(0.2, bus.notify, args=(channel, 7))
            timer.start()
            woke = await listener.wait(5)
            timer.cancel()
            idle = await listener.wait(0.2)
        # one-shot helper on a fresh subscription
        one_shot = await bus.wait(channel, timeout=0.2)
        return woke, idle, one_shot

    try:
        assert asyncio.run(scenario()) == (True, False, False)
    finally:
        bus.close()


@pytest.mark.redis
@requires_redis
def test_redis_notifications_are_coalesced():
    from aegis_api.lab.events.bus import RedisEventBus

    bus = RedisEventBus(REDIS_TEST_URL)
    channel = f"lab:test:{uuid.uuid4().hex}"

    async def scenario() -> tuple[bool, bool]:
        async with bus.listen(channel) as listener:
            await asyncio.to_thread(lambda: [bus.notify(channel, i) for i in range(5)])
            await asyncio.sleep(0.2)
            first = await listener.wait(2)
            second = await listener.wait(0.2)
            return first, second

    try:
        assert asyncio.run(scenario()) == (True, False)
    finally:
        bus.close()


# --- SSE helpers ---------------------------------------------------------------------------------------
def _request(headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


def test_parse_last_event_id_prefers_header_and_validates():
    from aegis_api.errors import ValidationFailed
    from aegis_api.lab.events.sse import parse_last_event_id

    assert parse_last_event_id(_request(), None) is None
    assert parse_last_event_id(_request(), "12") == 12
    assert parse_last_event_id(_request({"Last-Event-ID": "40"}), "12") == 40
    assert parse_last_event_id(_request({"Last-Event-ID": "0"})) == 0
    for bad in ("-1", "abc", "1.5", "99999999999999999999"):
        with pytest.raises(ValidationFailed):
            parse_last_event_id(_request(), bad)
    with pytest.raises(ValidationFailed):
        parse_last_event_id(_request({"Last-Event-ID": "x1"}))


def test_format_event_frame():
    import json

    from aegis_api.lab.events.sse import format_event

    frame = format_event({"id": 5, "type": "MISSION_STARTED", "payload": {"note": "line1\nline2"}})
    lines = frame.split("\n")
    assert lines[0] == "id: 5"
    assert lines[1] == "event: MISSION_STARTED"
    assert lines[2].startswith("data: ")
    assert json.loads(lines[2][6:])["payload"]["note"] == "line1\nline2"  # newline escaped inside JSON
    assert frame.endswith("\n\n")
    assert format_event({"id": 1, "type": "BAD\r\nevent: x"}).split("\n")[1] == "event: BADevent:x"


def test_stream_cursor_settles_old_ids():
    from aegis_api.lab.events.sse import StreamCursor

    cursor = StreamCursor(cursor=10, settled=10)
    now = time.time()
    cursor.mark_sent(12, now - 100)
    cursor.mark_sent(15, now - 50)
    cursor.mark_sent(17, now)
    assert cursor.cursor == 17
    cursor.settle(now - 30)
    assert cursor.settled == 15
    assert set(cursor.recent) == {17}
    cursor.settle(now - 30)
    assert cursor.settled == 15
