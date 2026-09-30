"""Outbound webhooks: management use cases, outbox → delivery enqueueing, and delivery.

Signing secrets are generated server-side, stored encrypted (``secrets_service``) and returned exactly
once (on create and on rotation). Receivers verify ``Aegis-Signature: t=<unix>,v1=<hex>`` with
``aegis_api.security.webhook_signing.verify_signature``.

Delivery never holds a database transaction open across HTTP calls: due deliveries are *claimed* in a
short transaction (``FOR UPDATE SKIP LOCKED`` plus a lease on ``next_attempt_at`` so concurrent consumers
never send the same delivery twice), sent without any transaction, and their outcome is recorded in a
second short transaction. Retry/backoff follows ``webhook_service`` (``MAX_ATTEMPTS``/``BACKOFF_SECONDS``).
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import Conflict, ValidationFailed
from aegis_api.lab.core.access import get_owned
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.events import WEBHOOK_EVENT_NAMES
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_keyset
from aegis_api.lab.events.schemas import WebhookCreate, WebhookUpdate
from aegis_api.lab.models import LabEvent
from aegis_api.lab.observability.metrics import WEBHOOK_DELIVERIES, WEBHOOK_DELIVERY_LATENCY
from aegis_api.models import Secret, Webhook, WebhookDelivery
from aegis_api.models.enums import DeliveryStatus
from aegis_api.security.ssrf import validate_outbound_url
from aegis_api.security.webhook_signing import sign_payload
from aegis_api.services import secrets_service
from aegis_api.services.webhook_service import BACKOFF_SECONDS, MAX_ATTEMPTS

log = structlog.get_logger("aegis.lab.webhooks")

# Event names emitted by the Aegis assurance services through ``webhook_service.enqueue_event``.
AEGIS_WEBHOOK_EVENTS: frozenset[str] = frozenset(
    {"audit.completed", "finding.created", "critical_risk.detected", "monitoring.alert"}
)
TEST_EVENT = "webhook.test"
SECRET_KIND = "webhook_signing"  # noqa: S105 - a secret *kind* label, not a secret
DELIVERY_TIMEOUT_SECONDS = 10.0
CLAIM_LEASE_SECONDS = 300
RESPONSE_EXCERPT_CHARS = 500
USER_AGENT = "Aegis-Webhooks/1.0"
_DUE = (DeliveryStatus.PENDING, DeliveryStatus.RETRYING)


def allowed_webhook_events() -> frozenset[str]:
    return frozenset(WEBHOOK_EVENT_NAMES.values()) | AEGIS_WEBHOOK_EVENTS


def _validate_events(events: Sequence[str]) -> list[str]:
    allowed = allowed_webhook_events()
    unknown = sorted(set(events) - allowed)
    if unknown:
        raise ValidationFailed(
            "Unknown webhook event name(s)", details={"unknown": unknown, "allowed": sorted(allowed)}
        )
    return list(events)


def _validate_url(url: str) -> str:
    return validate_outbound_url(url.strip())


def _secret_name(webhook_id: uuid.UUID) -> str:
    return f"webhook-signing:{webhook_id}"


def _new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def _snapshot(hook: Webhook) -> dict[str, Any]:
    return {"url": hook.url, "description": hook.description, "events": list(hook.events or []), "active": hook.active}


def secret_last4(db: Session, hook: Webhook) -> str | None:
    secret = db.get(Secret, hook.secret_id)
    return secret.last4 if secret is not None else None


# --------------------------------------------------------------------------------------------------
# Management use cases (request transaction; services never commit)
# --------------------------------------------------------------------------------------------------
def list_webhooks(db: Session, actor: Actor) -> list[Webhook]:
    return list(
        db.scalars(
            select(Webhook)
            .where(Webhook.organization_id == actor.organization_id)
            .order_by(Webhook.created_at.desc(), Webhook.id.desc())
        ).all()
    )


def get_webhook(db: Session, actor: Actor, webhook_id: uuid.UUID | str) -> Webhook:
    return get_owned(db, Webhook, webhook_id, actor, label="Webhook")


def create_webhook(db: Session, actor: Actor, data: WebhookCreate) -> tuple[Webhook, str]:
    """Create a webhook with a fresh signing secret; returns (webhook, plaintext secret — show once)."""
    actor.require_human("webhook.create")
    url = _validate_url(data.url)
    events = _validate_events(data.events)
    webhook_id = uuid.uuid4()
    plaintext = _new_secret()
    secret = secrets_service.create_secret(
        db,
        organization_id=actor.organization_id,
        name=_secret_name(webhook_id),
        value=plaintext,
        kind=SECRET_KIND,
        created_by_id=actor.user_id,
    )
    hook = Webhook(
        id=webhook_id,
        organization_id=actor.organization_id,
        url=url,
        description=data.description,
        events=events,
        secret_id=secret.id,
        active=data.active,
    )
    db.add(hook)
    db.flush()
    audit(db, actor, AuditAction.WEBHOOK_CHANGED, "webhook", hook.id, after={"op": "create", **_snapshot(hook)})
    return hook, plaintext


def update_webhook(db: Session, actor: Actor, webhook_id: uuid.UUID, data: WebhookUpdate) -> Webhook:
    hook = get_webhook(db, actor, webhook_id)
    before = _snapshot(hook)
    changes = data.model_dump(exclude_unset=True)
    if "url" in changes and changes["url"] is not None:
        hook.url = _validate_url(changes["url"])
    if "events" in changes and changes["events"] is not None:
        hook.events = _validate_events(changes["events"])
    if "description" in changes:
        hook.description = changes["description"]
    if "active" in changes and changes["active"] is not None:
        if changes["active"] and not hook.active:
            hook.failure_count = 0
        hook.active = bool(changes["active"])
    db.flush()
    audit(
        db,
        actor,
        AuditAction.WEBHOOK_CHANGED,
        "webhook",
        hook.id,
        before=before,
        after={"op": "update", **_snapshot(hook)},
    )
    return hook


def delete_webhook(db: Session, actor: Actor, webhook_id: uuid.UUID) -> None:
    actor.require_human("webhook.delete")
    hook = get_webhook(db, actor, webhook_id)
    before = _snapshot(hook)
    secret = db.get(Secret, hook.secret_id)
    db.delete(hook)  # deliveries cascade
    db.flush()
    if secret is not None and secret.organization_id == actor.organization_id:
        db.delete(secret)
    audit(db, actor, AuditAction.WEBHOOK_CHANGED, "webhook", webhook_id, before=before, after={"op": "delete"})


def rotate_secret(db: Session, actor: Actor, webhook_id: uuid.UUID) -> tuple[Webhook, str]:
    """Replace the signing secret; the old one stops verifying immediately."""
    actor.require_human("webhook.rotate_secret")
    hook = get_webhook(db, actor, webhook_id)
    plaintext = _new_secret()
    secret = secrets_service.create_secret(
        db,
        organization_id=actor.organization_id,
        name=_secret_name(hook.id),
        value=plaintext,
        kind=SECRET_KIND,
        created_by_id=actor.user_id,
    )
    secret.rotated_at = utcnow()
    if secret.id != hook.secret_id:
        hook.secret_id = secret.id
    db.flush()
    audit(db, actor, AuditAction.WEBHOOK_CHANGED, "webhook", hook.id, after={"op": "rotate_secret"})
    return hook, plaintext


def _envelope(event_id: str, event_type: str, created_at: datetime, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": event_id, "type": event_type, "created_at": created_at.isoformat(), "data": data}


def queue_test_delivery(db: Session, actor: Actor, webhook_id: uuid.UUID) -> WebhookDelivery:
    """Queue a signed ``webhook.test`` delivery (sent by the event consumer, never in the request)."""
    hook = get_webhook(db, actor, webhook_id)
    if not hook.active:
        raise Conflict("The webhook is inactive; activate it before sending a test delivery")
    event_id = uuid.uuid4().hex
    now = utcnow()
    delivery = WebhookDelivery(
        organization_id=actor.organization_id,
        webhook_id=hook.id,
        event_type=TEST_EVENT,
        event_id=event_id,
        payload=_envelope(event_id, TEST_EVENT, now, {"webhook_id": str(hook.id), "message": "Aegis webhook test"}),
        status=DeliveryStatus.PENDING,
        next_attempt_at=now,
    )
    db.add(delivery)
    db.flush()
    return delivery


def list_deliveries(
    db: Session, actor: Actor, webhook_id: uuid.UUID, params: CursorParams, mapper: Any
) -> CursorPage[Any]:
    hook = get_webhook(db, actor, webhook_id)
    stmt = select(WebhookDelivery).where(
        WebhookDelivery.organization_id == actor.organization_id, WebhookDelivery.webhook_id == hook.id
    )
    return paginate_keyset(
        db, stmt, params, time_col=WebhookDelivery.created_at, id_col=WebhookDelivery.id, mapper=mapper
    )


def redeliver(db: Session, actor: Actor, delivery_id: uuid.UUID) -> WebhookDelivery:
    """Queue a new attempt series for a past delivery (same event id and payload; history is kept)."""
    original = get_owned(db, WebhookDelivery, delivery_id, actor, label="Webhook delivery")
    hook = get_webhook(db, actor, original.webhook_id)
    if not hook.active:
        raise Conflict("The webhook is inactive; activate it before redelivering")
    copy = WebhookDelivery(
        organization_id=actor.organization_id,
        webhook_id=hook.id,
        event_type=original.event_type,
        event_id=original.event_id,
        payload=dict(original.payload or {}),
        status=DeliveryStatus.PENDING,
        next_attempt_at=utcnow(),
    )
    db.add(copy)
    db.flush()
    return copy


# --------------------------------------------------------------------------------------------------
# Outbox → deliveries (called by the event consumer inside its locked transaction)
# --------------------------------------------------------------------------------------------------
def active_webhooks(db: Session, organization_id: uuid.UUID) -> list[Webhook]:
    return list(
        db.scalars(select(Webhook).where(Webhook.organization_id == organization_id, Webhook.active.is_(True))).all()
    )


def lab_event_payload(event: LabEvent, name: str) -> dict[str, Any]:
    ids = {
        "event_id": event.id,
        "event_type": event.type,
        "mission_id": str(event.mission_id) if event.mission_id else None,
        "project_id": str(event.project_id) if event.project_id else None,
        "subject_type": event.subject_type,
        "subject_id": event.subject_id,
    }
    return _envelope(str(event.event_uuid), name, event.created_at, {**(event.payload or {}), **ids})


def already_enqueued(db: Session, organization_id: uuid.UUID, event_ids: Iterable[str]) -> set[str]:
    wanted = list(set(event_ids))
    if not wanted:
        return set()
    rows = db.scalars(
        select(WebhookDelivery.event_id).where(
            WebhookDelivery.organization_id == organization_id, WebhookDelivery.event_id.in_(wanted)
        )
    ).all()
    return set(rows)


def enqueue_for_event(db: Session, event: LabEvent, hooks: Sequence[Webhook]) -> int:
    """Create one delivery per subscribed hook (hooks created after the event are not back-filled)."""
    name = WEBHOOK_EVENT_NAMES.get(event.type)
    if name is None:
        return 0
    created = 0
    payload = lab_event_payload(event, name)
    for hook in hooks:
        if hook.events and name not in hook.events:
            continue
        if hook.created_at is not None and event.created_at is not None and hook.created_at > event.created_at:
            continue
        db.add(
            WebhookDelivery(
                organization_id=event.organization_id,
                webhook_id=hook.id,
                event_type=name,
                event_id=str(event.event_uuid),
                payload=payload,
                status=DeliveryStatus.PENDING,
                next_attempt_at=utcnow(),
            )
        )
        created += 1
    return created


# --------------------------------------------------------------------------------------------------
# Delivery (claim → send without a transaction → record)
# --------------------------------------------------------------------------------------------------
@dataclass
class _Claimed:
    delivery_id: uuid.UUID
    webhook_id: uuid.UUID
    url: str
    event_type: str
    body: bytes
    secret: str = field(repr=False)


@dataclass
class _Outcome:
    delivery_id: uuid.UUID
    webhook_id: uuid.UUID
    ok: bool
    status_code: int | None = None
    excerpt: str | None = None
    error: str | None = None


@dataclass
class DeliveryStats:
    claimed: int = 0
    succeeded: int = 0
    retrying: int = 0
    failed: int = 0


def _fail_permanently(delivery: WebhookDelivery, hook: Webhook | None, reason: str) -> None:
    delivery.status = DeliveryStatus.FAILED
    delivery.last_error = reason[:500]
    delivery.next_attempt_at = None
    if hook is not None:
        hook.failure_count = (hook.failure_count or 0) + 1


def _apply_failure(
    delivery: WebhookDelivery, hook: Webhook | None, error: str, now: datetime, stats: DeliveryStats
) -> None:
    """Failed attempt: retry with backoff until ``MAX_ATTEMPTS``, then fail and count it on the hook."""
    delivery.last_error = error[:500]
    if (delivery.attempts or 0) >= MAX_ATTEMPTS:
        delivery.status = DeliveryStatus.FAILED
        delivery.next_attempt_at = None
        if hook is not None:
            hook.failure_count = (hook.failure_count or 0) + 1
        stats.failed += 1
        WEBHOOK_DELIVERIES.labels("failed").inc()
        return
    delivery.status = DeliveryStatus.RETRYING
    backoff = BACKOFF_SECONDS[min(delivery.attempts or 0, len(BACKOFF_SECONDS) - 1)]
    delivery.next_attempt_at = now + timedelta(seconds=backoff)
    stats.retrying += 1
    WEBHOOK_DELIVERIES.labels("retrying").inc()


def _claim(organization_id: uuid.UUID, limit: int, stats: DeliveryStats) -> list[_Claimed]:
    claimed: list[_Claimed] = []
    with session_scope(organization_id) as db:
        now = utcnow()
        due = db.scalars(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.organization_id == organization_id,
                WebhookDelivery.status.in_(_DUE),
                WebhookDelivery.next_attempt_at <= now,
            )
            .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for delivery in due:
            hook = db.get(Webhook, delivery.webhook_id)
            if hook is None or not hook.active:
                _fail_permanently(delivery, None, "Webhook is inactive or deleted")
                stats.failed += 1
                WEBHOOK_DELIVERIES.labels("failed").inc()
                continue
            try:
                url = _validate_url(hook.url)
            except ValidationFailed as exc:  # may be transient (DNS): counts as a failed attempt
                delivery.attempts = (delivery.attempts or 0) + 1
                _apply_failure(delivery, hook, f"URL rejected: {exc.message}", now, stats)
                continue
            try:
                secret = secrets_service.reveal_secret(db, hook.secret_id, organization_id)
            except Exception as exc:
                _fail_permanently(delivery, hook, f"Signing secret unavailable: {type(exc).__name__}")
                stats.failed += 1
                WEBHOOK_DELIVERIES.labels("failed").inc()
                continue
            delivery.attempts = (delivery.attempts or 0) + 1
            delivery.next_attempt_at = now + timedelta(seconds=CLAIM_LEASE_SECONDS)  # lease
            claimed.append(
                _Claimed(
                    delivery_id=delivery.id,
                    webhook_id=hook.id,
                    url=url,
                    event_type=delivery.event_type,
                    body=json.dumps(delivery.payload, separators=(",", ":")).encode(),
                    secret=secret,
                )
            )
    stats.claimed += len(claimed)
    return claimed


def _send(job: _Claimed) -> _Outcome:
    started = time.perf_counter()
    try:
        response = httpx.post(
            job.url,
            content=job.body,
            headers={
                "content-type": "application/json",
                "user-agent": USER_AGENT,
                "aegis-signature": sign_payload(job.secret, job.body),
                "aegis-event": job.event_type,
                "aegis-delivery": str(job.delivery_id),
            },
            timeout=DELIVERY_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        ok = 200 <= response.status_code < 300
        outcome = _Outcome(
            job.delivery_id,
            job.webhook_id,
            ok=ok,
            status_code=response.status_code,
            excerpt=response.text[:RESPONSE_EXCERPT_CHARS],
            error=None if ok else f"HTTP {response.status_code}",
        )
    except Exception as exc:  # network errors are retried with backoff
        outcome = _Outcome(job.delivery_id, job.webhook_id, ok=False, error=f"{type(exc).__name__}")
    WEBHOOK_DELIVERY_LATENCY.labels("ok" if outcome.ok else "error").observe(time.perf_counter() - started)
    return outcome


def _record(organization_id: uuid.UUID, outcomes: Sequence[_Outcome], stats: DeliveryStats) -> None:
    if not outcomes:
        return
    with session_scope(organization_id) as db:
        for outcome in outcomes:
            delivery = db.get(WebhookDelivery, outcome.delivery_id, with_for_update=True)
            if delivery is None or delivery.status not in _DUE:
                continue
            hook = db.get(Webhook, outcome.webhook_id)
            delivery.response_status = outcome.status_code
            delivery.response_excerpt = outcome.excerpt
            now = utcnow()
            if outcome.ok:
                delivery.status = DeliveryStatus.SUCCEEDED
                delivery.delivered_at = now
                delivery.next_attempt_at = None
                delivery.last_error = None
                if hook is not None:
                    hook.failure_count = 0
                    hook.last_delivery_at = now
                stats.succeeded += 1
                WEBHOOK_DELIVERIES.labels("succeeded").inc()
                continue
            _apply_failure(delivery, hook, outcome.error or "delivery failed", now, stats)


def deliver_due(organization_id: uuid.UUID, *, limit: int = 20) -> DeliveryStats:
    """Deliver due webhook deliveries of one organization (bounded; safe to run concurrently)."""
    stats = DeliveryStats()
    claimed = _claim(organization_id, limit, stats)
    outcomes = [_send(job) for job in claimed]
    _record(organization_id, outcomes, stats)
    if claimed:
        log.info(
            "webhook_deliveries",
            organization_id=str(organization_id),
            claimed=stats.claimed,
            succeeded=stats.succeeded,
            retrying=stats.retrying,
            failed=stats.failed,
        )
    return stats
