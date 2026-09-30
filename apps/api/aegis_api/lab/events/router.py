"""HTTP API: event log, live SSE streams and outbound webhook management (tag "Events")."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from aegis_api.db.session import session_scope
from aegis_api.deps import get_db
from aegis_api.lab.core.access import load_project
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import get_actor, require_actor
from aegis_api.lab.core.events import mission_channel, org_channel
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params, paginate
from aegis_api.lab.events import service, webhooks
from aegis_api.lab.events.schemas import (
    EventOut,
    WebhookCreate,
    WebhookDeliveryOut,
    WebhookEventTypesOut,
    WebhookOut,
    WebhookUpdate,
    WebhookWithSecretOut,
)
from aegis_api.lab.events.service import EventFilter, normalize_types
from aegis_api.lab.events.sse import authenticate_stream, parse_last_event_id, sse_response
from aegis_api.models import Secret, Webhook
from aegis_api.schemas.common import Page, PageParams

router = APIRouter(prefix="/api/v1", tags=["Events"])

_E = {"description": "Error envelope `{error: {code, message, request_id, details}}`"}
_READ_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 422: _E}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {401: _E, 403: _E, 404: _E, 409: _E, 422: _E}
_SSE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events stream"},
    **_READ_ERRORS,
}
_SSE_DESCRIPTION = (
    "Server-Sent Events backed by the persisted event log. The first frame is `retry: 3000`; each event is "
    "`id: <id>`, `event: <TYPE>`, `data: {id, type, created_at, mission_id, project_id, subject_type, subject_id, "
    "payload}`; `: ping` comments are sent on the heartbeat. Resume with the `Last-Event-ID` header (sent "
    "automatically by EventSource) or `?last_event_id=`. Streams close after SSE_MAX_STREAM_SECONDS; clients "
    "reconnect and resume."
)


def _event_out(event: Any) -> EventOut:
    return EventOut.model_validate(event)


def _types(event_type: list[str] | None, types: list[str] | None) -> tuple[str, ...] | None:
    return normalize_types(event_type, types)


# --------------------------------------------------------------------------------------------------
# Event log
# --------------------------------------------------------------------------------------------------
@router.get(
    "/events",
    response_model=CursorPage[EventOut],
    status_code=200,
    summary="List events",
    description="Events of your organization in ascending id order (cursor pagination). Only events of "
    "projects you can see are returned; organization-level events are always included.",
    responses=_READ_ERRORS,
)
def list_events(
    mission_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    event_type: list[str] | None = Query(None, alias="type", description="Repeatable or comma-separated"),
    types: list[str] | None = Query(None, description="Alias of `type`"),
    subject_type: str | None = Query(None, max_length=32),
    subject_id: str | None = Query(None, max_length=64),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(require_actor("event:read")),
    db: Session = Depends(get_db),
) -> CursorPage[EventOut]:
    filters = EventFilter(
        mission_id=mission_id,
        project_id=project_id,
        types=_types(event_type, types),
        subject_type=subject_type,
        subject_id=subject_id,
    )
    return service.list_events(db, actor, filters, params, _event_out)


@router.get(
    "/events/stream",
    response_class=StreamingResponse,
    status_code=200,
    summary="Stream organization events (SSE)",
    description=_SSE_DESCRIPTION
    + " Without a resume id the organization stream starts at the current tail (pass `last_event_id=0` to "
    "replay history). Requires `event:read`.",
    responses=_SSE_RESPONSES,
)
async def stream_events(
    request: Request,
    event_type: list[str] | None = Query(None, alias="type", description="Repeatable or comma-separated"),
    types: list[str] | None = Query(None, description="Alias of `type`"),
    project_id: uuid.UUID | None = Query(None),
    last_event_id: str | None = Query(None, max_length=32, description="Resume after this event id"),
) -> StreamingResponse:
    actor = await authenticate_stream(request)
    actor.require("event:read")
    resume = parse_last_event_id(request, last_event_id)
    filters = EventFilter(project_id=project_id, types=_types(event_type, types))

    def _prepare() -> int:
        with session_scope(actor.organization_id, actor.user_id) as db:
            if project_id is not None:
                load_project(db, actor, project_id)
            return resume if resume is not None else service.latest_event_id(db, actor)

    start = await run_in_threadpool(_prepare)
    return sse_response(
        request, actor=actor, filters=filters, channel=org_channel(actor.organization_id), last_event_id=start
    )


@router.get(
    "/events/{event_id}",
    response_model=EventOut,
    status_code=200,
    summary="Get an event",
    responses=_READ_ERRORS,
)
def get_event(
    event_id: int,
    actor: Actor = Depends(require_actor("event:read")),
    db: Session = Depends(get_db),
) -> EventOut:
    return _event_out(service.get_event(db, actor, event_id))


@router.get(
    "/missions/{mission_id}/events",
    response_model=CursorPage[EventOut],
    status_code=200,
    summary="List mission events",
    description="The mission's event timeline in ascending id order (cursor pagination). Requires `mission:read` "
    "on the mission's project.",
    responses=_READ_ERRORS,
)
def list_mission_events(
    mission_id: uuid.UUID,
    event_type: list[str] | None = Query(None, alias="type", description="Repeatable or comma-separated"),
    types: list[str] | None = Query(None, description="Alias of `type`"),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
) -> CursorPage[EventOut]:
    service.load_mission_for_events(db, actor, mission_id)
    filters = EventFilter(mission_id=mission_id, types=_types(event_type, types))
    return service.list_events(db, actor, filters, params, _event_out)


@router.get(
    "/missions/{mission_id}/events/stream",
    response_class=StreamingResponse,
    status_code=200,
    summary="Stream mission events (SSE)",
    description=_SSE_DESCRIPTION
    + " Without a resume id the full mission history is replayed first. Requires `mission:read` on the "
    "mission's project.",
    responses=_SSE_RESPONSES,
)
async def stream_mission_events(
    request: Request,
    mission_id: uuid.UUID,
    event_type: list[str] | None = Query(None, alias="type", description="Repeatable or comma-separated"),
    types: list[str] | None = Query(None, description="Alias of `type`"),
    last_event_id: str | None = Query(None, max_length=32, description="Resume after this event id"),
) -> StreamingResponse:
    actor = await authenticate_stream(request)
    resume = parse_last_event_id(request, last_event_id)
    filters = EventFilter(mission_id=mission_id, types=_types(event_type, types))

    def _authorize() -> None:
        with session_scope(actor.organization_id, actor.user_id) as db:
            service.load_mission_for_events(db, actor, mission_id)

    await run_in_threadpool(_authorize)
    return sse_response(
        request,
        actor=actor,
        filters=filters,
        channel=mission_channel(actor.organization_id, mission_id),
        last_event_id=resume or 0,
    )


# --------------------------------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------------------------------
def _webhook_out(hook: Webhook, last4: str | None) -> WebhookOut:
    out = WebhookOut.model_validate(hook)
    out.secret_last4 = last4
    return out


def _load_webhook_out(db: Session, hook: Webhook) -> WebhookOut:
    return _webhook_out(hook, webhooks.secret_last4(db, hook))


def _with_secret(db: Session, hook: Webhook, secret: str) -> WebhookWithSecretOut:
    return WebhookWithSecretOut(**_load_webhook_out(db, hook).model_dump(), secret=secret)


def _delivery_out(delivery: Any) -> WebhookDeliveryOut:
    return WebhookDeliveryOut.model_validate(delivery)


@router.get(
    "/webhooks/event-types",
    response_model=WebhookEventTypesOut,
    status_code=200,
    summary="List webhook event names",
    responses={401: _E, 403: _E},
)
def webhook_event_types(actor: Actor = Depends(require_actor("webhook:manage"))) -> WebhookEventTypesOut:
    return WebhookEventTypesOut(events=sorted(webhooks.allowed_webhook_events()))


@router.get(
    "/webhooks",
    response_model=Page[WebhookOut],
    status_code=200,
    summary="List webhooks",
    responses={401: _E, 403: _E},
)
def list_webhooks(
    params: PageParams = Depends(),
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> Page[WebhookOut]:
    stmt = (
        select(Webhook)
        .where(Webhook.organization_id == actor.organization_id)
        .order_by(Webhook.created_at.desc(), Webhook.id.desc())
    )
    page: Page[Any] = paginate(db, stmt, params, lambda hook: hook)
    hooks: list[Webhook] = list(page.items)
    last4 = dict(
        db.execute(select(Secret.id, Secret.last4).where(Secret.id.in_([h.secret_id for h in hooks]))).tuples().all()
    )
    items = [_webhook_out(h, last4.get(h.secret_id)) for h in hooks]
    return Page[WebhookOut](items=items, meta=page.meta)


@router.post(
    "/webhooks",
    response_model=WebhookWithSecretOut,
    status_code=201,
    summary="Create a webhook",
    description="Registers an HTTPS endpoint (validated against SSRF rules). The signing secret is returned "
    "only in this response. Human users only.",
    responses=_WRITE_ERRORS,
)
def create_webhook(
    body: WebhookCreate,
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> WebhookWithSecretOut:
    hook, secret = webhooks.create_webhook(db, actor, body)
    return _with_secret(db, hook, secret)


@router.get(
    "/webhooks/{webhook_id}",
    response_model=WebhookOut,
    status_code=200,
    summary="Get a webhook",
    responses=_READ_ERRORS,
)
def get_webhook(
    webhook_id: uuid.UUID,
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> WebhookOut:
    return _load_webhook_out(db, webhooks.get_webhook(db, actor, webhook_id))


@router.patch(
    "/webhooks/{webhook_id}",
    response_model=WebhookOut,
    status_code=200,
    summary="Update a webhook",
    description="Change the URL (re-validated), subscribed events, description or active flag.",
    responses=_WRITE_ERRORS,
)
def update_webhook(
    webhook_id: uuid.UUID,
    body: WebhookUpdate,
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> WebhookOut:
    return _load_webhook_out(db, webhooks.update_webhook(db, actor, webhook_id, body))


@router.delete(
    "/webhooks/{webhook_id}",
    status_code=204,
    response_class=Response,
    summary="Delete a webhook",
    description="Deletes the webhook, its signing secret and its delivery history. Human users only.",
    responses=_WRITE_ERRORS,
)
def delete_webhook(
    webhook_id: uuid.UUID,
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> Response:
    webhooks.delete_webhook(db, actor, webhook_id)
    return Response(status_code=204)


@router.post(
    "/webhooks/{webhook_id}/rotate-secret",
    response_model=WebhookWithSecretOut,
    status_code=200,
    summary="Rotate the signing secret",
    description="Generates a new signing secret (returned once); the previous secret stops working "
    "immediately. Human users only.",
    responses=_WRITE_ERRORS,
)
def rotate_webhook_secret(
    webhook_id: uuid.UUID,
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> WebhookWithSecretOut:
    hook, secret = webhooks.rotate_secret(db, actor, webhook_id)
    return _with_secret(db, hook, secret)


@router.post(
    "/webhooks/{webhook_id}/test",
    response_model=WebhookDeliveryOut,
    status_code=202,
    summary="Send a test delivery",
    description="Queues a signed `webhook.test` delivery; the event consumer sends it within seconds. Inspect "
    "the result via the deliveries list.",
    responses=_WRITE_ERRORS,
)
def test_webhook(
    webhook_id: uuid.UUID,
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> WebhookDeliveryOut:
    return _delivery_out(webhooks.queue_test_delivery(db, actor, webhook_id))


@router.get(
    "/webhooks/{webhook_id}/deliveries",
    response_model=CursorPage[WebhookDeliveryOut],
    status_code=200,
    summary="List deliveries of a webhook",
    description="Newest first (cursor pagination).",
    responses=_READ_ERRORS,
)
def list_webhook_deliveries(
    webhook_id: uuid.UUID,
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> CursorPage[WebhookDeliveryOut]:
    return webhooks.list_deliveries(db, actor, webhook_id, params, _delivery_out)


@router.post(
    "/webhook-deliveries/{delivery_id}/redeliver",
    response_model=WebhookDeliveryOut,
    status_code=202,
    summary="Redeliver",
    description="Queues a new delivery with the same event id and payload (the original is kept).",
    responses=_WRITE_ERRORS,
)
def redeliver_webhook(
    delivery_id: uuid.UUID,
    actor: Actor = Depends(require_actor("webhook:manage")),
    db: Session = Depends(get_db),
) -> WebhookDeliveryOut:
    return _delivery_out(webhooks.redeliver(db, actor, delivery_id))
