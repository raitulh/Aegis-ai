"""Webhooks: signed event delivery with retry/backoff, plus webhook management.

Deliveries are enqueued in the same transaction as the event that caused them. Delivery never holds a
database transaction across the outbound HTTP call: each attempt is (short tx: claim + load) → HTTP POST
(no tx) → (short tx: record result). Payloads are signed with the webhook's secret
(``Aegis-Signature: t=<ts>,v1=<hmac>``) and every hop is SSRF-validated; redirects are not followed.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.models import Webhook, WebhookDelivery
from aegis_api.models.enums import DeliveryStatus
from aegis_api.security.ssrf import validate_outbound_url
from aegis_api.security.webhook_signing import sign_payload
from aegis_api.services import secrets_service

log = structlog.get_logger("aegis.webhooks")
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [0, 30, 120, 600, 3600]
CLAIM_SECONDS = 60


def enqueue_event(session: Session, organization_id: uuid.UUID, event_type: str, data: dict[str, Any]) -> None:
    hooks = session.scalars(
        select(Webhook).where(Webhook.organization_id == organization_id, Webhook.active.is_(True))
    ).all()
    event_id = uuid.uuid4().hex
    for hook in hooks:
        if hook.events and event_type not in hook.events:
            continue
        session.add(
            WebhookDelivery(
                organization_id=organization_id,
                webhook_id=hook.id,
                event_type=event_type,
                event_id=event_id,
                payload={"id": event_id, "type": event_type, "created_at": utcnow().isoformat(), "data": data},
                status=DeliveryStatus.PENDING,
                next_attempt_at=utcnow(),
            )
        )


def deliver_pending(session: Session, *, limit: int = 50) -> int:
    """Deliver due webhooks for the session's tenant. Commits between steps (never holds a transaction
    across the HTTP call)."""
    now = utcnow()
    due = session.scalars(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.RETRYING]),
            WebhookDelivery.next_attempt_at <= now,
        )
        .order_by(WebhookDelivery.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    jobs: list[tuple[uuid.UUID, str, str, bytes, str]] = []
    for delivery in due:
        hook = session.get(Webhook, delivery.webhook_id)
        if hook is None or not hook.active:
            delivery.status = DeliveryStatus.FAILED
            delivery.last_error = "webhook inactive or deleted"
            continue
        try:
            secret = secrets_service.reveal_secret(session, hook.secret_id, hook.organization_id)
        except Exception:
            delivery.status = DeliveryStatus.FAILED
            delivery.last_error = "webhook secret unavailable"
            continue
        delivery.attempts += 1
        # Claim: push the next attempt out so concurrent consumers skip it while we post.
        delivery.next_attempt_at = now + timedelta(seconds=CLAIM_SECONDS)
        body = json.dumps(delivery.payload, separators=(",", ":")).encode()
        jobs.append((delivery.id, hook.url, delivery.event_type, body, secret))
    session.commit()
    delivered = 0
    for delivery_id, url, event_type, body, secret in jobs:
        status_code, excerpt, error = _post(url, event_type, body, secret)
        row = session.get(WebhookDelivery, delivery_id)
        if row is None:
            continue
        delivery = row
        hook = session.get(Webhook, delivery.webhook_id)
        delivery.response_status = status_code
        delivery.response_excerpt = excerpt
        if error is None and status_code is not None and 200 <= status_code < 300:
            delivery.status = DeliveryStatus.SUCCEEDED
            delivery.delivered_at = utcnow()
            if hook is not None:
                hook.failure_count = 0
                hook.last_delivery_at = utcnow()
            delivered += 1
        else:
            delivery.last_error = (error or f"HTTP {status_code}")[:500]
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = DeliveryStatus.FAILED
                if hook is not None:
                    hook.failure_count += 1
            else:
                delivery.status = DeliveryStatus.RETRYING
                delivery.next_attempt_at = utcnow() + timedelta(
                    seconds=BACKOFF_SECONDS[min(delivery.attempts, len(BACKOFF_SECONDS) - 1)]
                )
        session.commit()
    return delivered


def _post(url: str, event_type: str, body: bytes, secret: str) -> tuple[int | None, str | None, str | None]:
    try:
        target = validate_outbound_url(url)
        response = httpx.post(
            target,
            content=body,
            headers={
                "content-type": "application/json",
                "aegis-signature": sign_payload(secret, body),
                "aegis-event": event_type,
            },
            timeout=10,
            follow_redirects=False,
        )
        return response.status_code, response.text[:500], None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"[:500]


# --- management ----------------------------------------------------------------------------------------------


def create_webhook(
    session: Session,
    *,
    organization_id: uuid.UUID,
    url: str,
    events: list[str],
    description: str | None,
    created_by_id: uuid.UUID | None,
) -> tuple[Webhook, str]:
    """Returns (webhook, signing_secret). The secret is shown once and stored encrypted."""
    import secrets as pysecrets

    validate_outbound_url(url)
    signing_secret = "whsec_" + pysecrets.token_urlsafe(32)
    secret = secrets_service.create_secret(
        session,
        organization_id=organization_id,
        name=f"webhook:{uuid.uuid4().hex}",
        value=signing_secret,
        kind="webhook_signing",
        created_by_id=created_by_id,
    )
    hook = Webhook(
        organization_id=organization_id,
        url=url[:1000],
        description=description,
        events=sorted(set(events)),
        secret_id=secret.id,
        active=True,
    )
    session.add(hook)
    session.flush()
    return hook, signing_secret


def rotate_secret(session: Session, hook: Webhook) -> str:
    import secrets as pysecrets

    signing_secret = "whsec_" + pysecrets.token_urlsafe(32)
    secret = secrets_service.create_secret(
        session,
        organization_id=hook.organization_id,
        name=f"webhook:{uuid.uuid4().hex}",
        value=signing_secret,
        kind="webhook_signing",
    )
    hook.secret_id = secret.id
    return signing_secret
