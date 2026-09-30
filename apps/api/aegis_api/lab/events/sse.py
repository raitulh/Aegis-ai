"""Server-Sent Events streams backed by the persisted event outbox.

Every frame comes from the ``events`` table (never from the bus): the bus only wakes the stream up.

Protocol::

    retry: 3000                      (first)
    id: <event id>
    event: <EVENT_TYPE>
    data: {"id", "type", "created_at", "mission_id", "project_id", "subject_type", "subject_id", "payload"}

    : ping                           (comment, every heartbeat without traffic)

Resume: ``Last-Event-ID`` header (sent by ``EventSource`` on reconnect) or the ``last_event_id`` query
parameter. Event ids come from one identity sequence, so a transaction may commit an event with a lower id
*after* a higher one was streamed. The stream therefore re-checks a short "settle" window below the
highest id it has sent and delivers such late events too (possibly out of id order, never twice).

Resources: no DB session is held across ``await``s — every poll runs in a worker thread with a fresh
RLS-scoped session; authentication uses a short owner session that is closed before streaming starts.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import structlog
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from aegis_api.config import get_settings
from aegis_api.db.session import session_factory, session_scope
from aegis_api.deps import get_current_principal
from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import actor_from_principal
from aegis_api.lab.events.bus import get_event_bus
from aegis_api.lab.events.service import EventFilter, event_to_dict, filtered_statement
from aegis_api.lab.models import LabEvent
from aegis_api.lab.observability.metrics import SSE_CONNECTIONS, SSE_EVENTS_SENT

log = structlog.get_logger("aegis.lab.sse")

RETRY_MILLISECONDS = 3000
BATCH_SIZE = 200
SETTLE_SECONDS = 30.0
MIN_WAIT_SECONDS = 0.05
MAX_EVENT_ID = 2**63 - 1
_EVENT_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.:\-]")
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# --- request helpers --------------------------------------------------------------------------------
def parse_last_event_id(request: Request, query_value: str | None = None) -> int | None:
    """Resume point from ``Last-Event-ID`` (preferred) or ``?last_event_id=``; validated int ≥ 0."""
    raw = request.headers.get("last-event-id")
    if raw is None or not raw.strip():
        raw = query_value
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip()
    if not value.isdigit() or len(value) > 19 or int(value) > MAX_EVENT_ID:
        raise ValidationFailed(
            "Last-Event-ID must be a non-negative integer event id", details={"last_event_id": value[:32]}
        )
    return int(value)


async def authenticate_stream(request: Request) -> Actor:
    """Resolve the caller for a long-lived stream without pinning identity-layer DB connections.

    Request-scoped dependencies with ``yield`` are only torn down after the response finishes, which for
    a stream can be an hour; the identity lookup therefore runs here with a session closed immediately.
    """

    def _resolve() -> Actor:
        session = session_factory(admin=True)()
        try:
            principal = get_current_principal(request, session)
        finally:
            session.close()
        return actor_from_principal(request, principal)

    actor = await run_in_threadpool(_resolve)
    structlog.contextvars.bind_contextvars(
        tenant_id=str(actor.organization_id), user_id=str(actor.user_id) if actor.user_id else None
    )
    return actor


# --- framing -----------------------------------------------------------------------------------------
def format_event(event: dict[str, object]) -> str:
    name = _EVENT_NAME_UNSAFE.sub("", str(event.get("type") or "")) or "message"
    data = json.dumps(event, separators=(",", ":"), default=str)
    return f"id: {event['id']}\nevent: {name}\ndata: {data}\n\n"


# --- polling state -----------------------------------------------------------------------------------
@dataclass
class StreamCursor:
    """``cursor`` = highest id sent; ids ≤ ``settled`` are final; ``recent`` = sent ids above ``settled``."""

    cursor: int
    settled: int
    recent: dict[int, float] = field(default_factory=dict)

    def mark_sent(self, event_id: int, created_at: float) -> None:
        self.recent[event_id] = created_at
        self.cursor = max(self.cursor, event_id)

    def settle(self, older_than: float) -> None:
        old = [event_id for event_id, created in self.recent.items() if created < older_than]
        if not old:
            return
        self.settled = max(self.settled, max(old))
        self.recent = {k: v for k, v in self.recent.items() if k > self.settled}


@dataclass(frozen=True)
class PollResult:
    frames: list[tuple[str, int, float]]
    more: bool


def poll_events(
    actor: Actor, filters: EventFilter, cursor: int, settled: int, sent: frozenset[int], limit: int = BATCH_SIZE
) -> PollResult:
    """One short read transaction: late events in (settled, cursor] not yet sent, then new ones > cursor."""
    with session_scope(actor.organization_id, actor.user_id) as db:
        base = filtered_statement(db, actor, filters)
        rows: list[LabEvent] = []
        if cursor > settled:
            window_ids = db.scalars(
                base.with_only_columns(LabEvent.id).where(LabEvent.id > settled, LabEvent.id <= cursor)
            ).all()
            missing = sorted(set(window_ids) - sent)[:limit]
            if missing:
                rows.extend(db.scalars(base.where(LabEvent.id.in_(missing)).order_by(LabEvent.id)).all())
        fresh = db.scalars(base.where(LabEvent.id > cursor).order_by(LabEvent.id).limit(limit)).all()
        rows.extend(fresh)
        frames = [
            (format_event(event_to_dict(e)), e.id, e.created_at.timestamp() if e.created_at else time.time())
            for e in rows
        ]
        return PollResult(frames=frames, more=len(fresh) >= limit)


# --- the stream --------------------------------------------------------------------------------------
async def _event_stream(
    request: Request, actor: Actor, filters: EventFilter, channel: str, last_event_id: int
) -> AsyncIterator[str]:
    settings = get_settings()
    heartbeat = max(MIN_WAIT_SECONDS, float(settings.sse_heartbeat_seconds))
    max_seconds = max(0.0, float(settings.sse_max_stream_seconds))
    loop = asyncio.get_running_loop()
    started = loop.time()
    state = StreamCursor(cursor=last_event_id, settled=last_event_id)
    bus = get_event_bus()
    SSE_CONNECTIONS.inc()
    try:
        yield f"retry: {RETRY_MILLISECONDS}\n\n"
        last_write = loop.time()
        async with bus.listen(channel) as listener:
            while True:
                if await request.is_disconnected() or loop.time() - started >= max_seconds:
                    break
                result = await asyncio.to_thread(
                    poll_events, actor, filters, state.cursor, state.settled, frozenset(state.recent)
                )
                for frame, event_id, created_at in result.frames:
                    yield frame
                    state.mark_sent(event_id, created_at)
                if result.frames:
                    SSE_EVENTS_SENT.inc(len(result.frames))
                    last_write = loop.time()
                state.settle(time.time() - SETTLE_SECONDS)
                if result.more:
                    continue
                remaining_life = max_seconds - (loop.time() - started)
                if remaining_life <= 0:
                    break
                until_ping = heartbeat - (loop.time() - last_write)
                await listener.wait(max(MIN_WAIT_SECONDS, min(until_ping, remaining_life)))
                if loop.time() - last_write >= heartbeat:
                    yield ": ping\n\n"
                    last_write = loop.time()
    finally:
        SSE_CONNECTIONS.dec()


def sse_response(
    request: Request, *, actor: Actor, filters: EventFilter, channel: str, last_event_id: int
) -> StreamingResponse:
    """A ``text/event-stream`` response replaying events with id > ``last_event_id`` and then live ones."""
    return StreamingResponse(
        _event_stream(request, actor, filters, channel, max(0, last_event_id)),
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )
