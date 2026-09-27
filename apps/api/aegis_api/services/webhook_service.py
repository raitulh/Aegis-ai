"""Webhook delivery: enqueue signed events and deliver with retry/backoff."""

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
    now = utcnow()
    deliveries = session.scalars(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.RETRYING]),
            WebhookDelivery.next_attempt_at <= now,
        )
        .limit(limit)
    ).all()
    delivered = 0
    for delivery in deliveries:
        hook = session.get(Webhook, delivery.webhook_id)
        if hook is None or not hook.active:
            delivery.status = DeliveryStatus.FAILED
            continue
        if _attempt(session, hook, delivery):
            delivered += 1
    return delivered


def _attempt(session: Session, hook: Webhook, delivery: WebhookDelivery) -> bool:
    delivery.attempts += 1
    body = json.dumps(delivery.payload, separators=(",", ":")).encode()
    try:
        secret = secrets_service.reveal_secret(session, hook.secret_id, hook.organization_id)
        url = validate_outbound_url(hook.url)
        signature = sign_payload(secret, body)
        response = httpx.post(
            url,
            content=body,
            headers={
                "content-type": "application/json",
                "aegis-signature": signature,
                "aegis-event": delivery.event_type,
            },
            timeout=10,
            follow_redirects=False,
        )
        delivery.response_status = response.status_code
        delivery.response_excerpt = response.text[:500]
        if 200 <= response.status_code < 300:
            delivery.status = DeliveryStatus.SUCCEEDED
            delivery.delivered_at = utcnow()
            hook.failure_count = 0
            hook.last_delivery_at = utcnow()
            return True
        raise httpx.HTTPStatusError("non-2xx", request=response.request, response=response)
    except Exception as exc:
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:500]
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED
            hook.failure_count += 1
        else:
            delivery.status = DeliveryStatus.RETRYING
            delivery.next_attempt_at = utcnow() + timedelta(
                seconds=BACKOFF_SECONDS[min(delivery.attempts, len(BACKOFF_SECONDS) - 1)]
            )
        return False
