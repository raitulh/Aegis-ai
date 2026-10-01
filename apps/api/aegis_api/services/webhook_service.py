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
from aegis_api.security.ssrf import guarded_client, validate_outbound_url
from aegis_api.security.webhook_signing import sign_payload
from aegis_api.services import secrets_service

log = structlog.get_logger("aegis.webhooks")
MAX_ATTEMPTS = 5
AUTO_DISABLE_AFTER = 20
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
        with guarded_client(timeout=10) as client:
            response = client.post(
                url,
                content=body,
                headers={
                    "content-type": "application/json",
                    "user-agent": "Aegis-Webhooks/1.0",
                    "aegis-signature": signature,
                    "aegis-event": delivery.event_type,
                    "aegis-delivery": str(delivery.id),
                    "aegis-event-id": delivery.event_id,
                },
            )
        delivery.response_status = response.status_code
        delivery.response_excerpt = response.text[:500]
        if 200 <= response.status_code < 300:
            _count("succeeded")
            delivery.status = DeliveryStatus.SUCCEEDED
            delivery.delivered_at = utcnow()
            hook.failure_count = 0
            hook.last_delivery_at = utcnow()
            return True
        raise httpx.HTTPStatusError("non-2xx", request=response.request, response=response)
    except Exception as exc:
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:500]
        _count("failed")
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED
            hook.failure_count += 1
            if hook.failure_count >= AUTO_DISABLE_AFTER:
                hook.active = False  # stop hammering a dead endpoint; owners can re-enable it
        else:
            delivery.status = DeliveryStatus.RETRYING
            delivery.next_attempt_at = utcnow() + timedelta(
                seconds=BACKOFF_SECONDS[min(delivery.attempts, len(BACKOFF_SECONDS) - 1)]
            )
        return False


def _count(outcome: str) -> None:
    from aegis_api.observability import metrics

    try:
        metrics.WEBHOOK_DELIVERIES.labels(outcome).inc()
    except Exception:  # pragma: no cover - metrics are best-effort
        log.debug("webhook_metric_failed", exc_info=True)


def create_webhook(
    session: Session, principal: Any, *, url: str, description: str | None, events: list[str]
) -> tuple[Webhook, str]:
    """Register an endpoint. The URL is SSRF-validated now and again at every delivery; the signing secret
    is returned once and stored encrypted."""
    import secrets as pysecrets

    from aegis_api.errors import ValidationFailed
    from aegis_api.schemas.platform import WEBHOOK_EVENTS
    from aegis_api.services import audit_log

    unknown = [e for e in events if e not in WEBHOOK_EVENTS]
    if unknown:
        raise ValidationFailed(f"Unknown webhook events: {', '.join(unknown)}")
    # Payloads describe findings and decisions: production requires TLS; plain HTTP is for local development.
    from aegis_api.config import get_settings

    schemes = frozenset({"https"}) if get_settings().is_production else frozenset({"https", "http"})
    clean_url = validate_outbound_url(url, allowed_schemes=schemes)
    secret_value = "whsec_" + pysecrets.token_urlsafe(32)
    secret = secrets_service.create_secret(
        session,
        organization_id=principal.organization_id,
        name=f"webhook:{uuid.uuid4().hex[:12]}",
        value=secret_value,
        kind="webhook_signing",
        created_by_id=principal.user_id if principal.auth_method != "api_key" else None,
    )
    hook = Webhook(
        organization_id=principal.organization_id,
        url=clean_url,
        description=description,
        events=list(dict.fromkeys(events)),
        secret_id=secret.id,
        active=True,
    )
    session.add(hook)
    session.flush()
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="integration.webhook_created",
        resource_type="webhook",
        resource_id=hook.id,
        principal=principal,
        after={"url": clean_url, "events": hook.events},
    )
    return hook, secret_value


def send_test(session: Session, hook: Webhook) -> WebhookDelivery:
    """Deliver a signed ``webhook.test`` event immediately (subject to the same SSRF guard)."""
    event_id = uuid.uuid4().hex
    delivery = WebhookDelivery(
        organization_id=hook.organization_id,
        webhook_id=hook.id,
        event_type="webhook.test",
        event_id=event_id,
        payload={"id": event_id, "type": "webhook.test", "created_at": utcnow().isoformat(), "data": {"ok": True}},
        status=DeliveryStatus.PENDING,
        next_attempt_at=utcnow(),
    )
    session.add(delivery)
    session.flush()
    _attempt(session, hook, delivery)
    return delivery
