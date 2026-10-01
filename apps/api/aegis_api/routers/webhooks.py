"""Outbound webhooks: signed event delivery with retries, replay protection and a delivery log."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.deps import get_db, require
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models import Webhook, WebhookDelivery
from aegis_api.routers._helpers import paginate
from aegis_api.schemas.common import Message, Page, PageParams
from aegis_api.schemas.platform import (
    WEBHOOK_EVENTS,
    WebhookCreate,
    WebhookCreated,
    WebhookDeliveryOut,
    WebhookOut,
    WebhookUpdate,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, entitlements, webhook_service

router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])


def _hook(db: Session, webhook_id: uuid.UUID, org_id: uuid.UUID) -> Webhook:
    hook = db.get(Webhook, webhook_id)
    if hook is None or hook.organization_id != org_id:
        raise NotFound("Webhook not found")
    return hook


@router.get("/events")
def event_catalogue(principal: Principal = Depends(require("integrations:read"))) -> dict:
    return {
        "events": list(WEBHOOK_EVENTS),
        "signature": {
            "header": "Aegis-Signature",
            "scheme": "t=<unix seconds>,v1=<hex HMAC-SHA256(secret, '<t>.<raw body>')>",
            "replay_window_seconds": 300,
            "idempotency": "Aegis-Event-Id is stable across retries; deduplicate on it.",
        },
        "retries": {"max_attempts": webhook_service.MAX_ATTEMPTS, "backoff_seconds": webhook_service.BACKOFF_SECONDS},
    }


@router.get("", response_model=list[WebhookOut])
def list_webhooks(
    principal: Principal = Depends(require("integrations:read")), db: Session = Depends(get_db)
) -> list[WebhookOut]:
    rows = db.scalars(select(Webhook).where(Webhook.organization_id == principal.organization_id)).all()
    return [WebhookOut.model_validate(w) for w in rows]


@router.post("", response_model=WebhookCreated, status_code=201)
def create_webhook(
    body: WebhookCreate, principal: Principal = Depends(require("webhooks:manage")), db: Session = Depends(get_db)
) -> WebhookCreated:
    entitlements.require_feature(db, principal.organization_id, "webhooks")
    hook, secret = webhook_service.create_webhook(
        db, principal, url=body.url, description=body.description, events=body.events
    )
    return WebhookCreated(webhook=WebhookOut.model_validate(hook), signing_secret=secret)


@router.patch("/{webhook_id}", response_model=WebhookOut)
def update_webhook(
    webhook_id: uuid.UUID,
    body: WebhookUpdate,
    principal: Principal = Depends(require("webhooks:manage")),
    db: Session = Depends(get_db),
) -> WebhookOut:
    hook = _hook(db, webhook_id, principal.organization_id)
    changes = body.model_dump(exclude_unset=True)
    if "events" in changes:
        unknown = [e for e in changes["events"] or [] if e not in WEBHOOK_EVENTS]
        if unknown:
            raise ValidationFailed(f"Unknown webhook events: {', '.join(unknown)}")
    for key, value in changes.items():
        setattr(hook, key, value)
    if changes.get("active"):
        hook.failure_count = 0
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="integration.webhook_changed",
        resource_type="webhook",
        resource_id=hook.id,
        principal=principal,
        after=changes,
    )
    return WebhookOut.model_validate(hook)


@router.delete("/{webhook_id}", response_model=Message)
def delete_webhook(
    webhook_id: uuid.UUID, principal: Principal = Depends(require("webhooks:manage")), db: Session = Depends(get_db)
) -> Message:
    hook = _hook(db, webhook_id, principal.organization_id)
    hook.active = False
    db.delete(hook)
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="integration.webhook_deleted",
        resource_type="webhook",
        resource_id=webhook_id,
        principal=principal,
    )
    return Message(message="Webhook deleted")


@router.post("/{webhook_id}/test", response_model=WebhookDeliveryOut)
def test_webhook(
    webhook_id: uuid.UUID, principal: Principal = Depends(require("webhooks:manage")), db: Session = Depends(get_db)
) -> WebhookDeliveryOut:
    hook = _hook(db, webhook_id, principal.organization_id)
    return WebhookDeliveryOut.model_validate(webhook_service.send_test(db, hook))


@router.get("/{webhook_id}/deliveries", response_model=Page[WebhookDeliveryOut])
def deliveries(
    webhook_id: uuid.UUID,
    params: PageParams = Depends(),
    principal: Principal = Depends(require("integrations:read")),
    db: Session = Depends(get_db),
) -> Page[WebhookDeliveryOut]:
    _hook(db, webhook_id, principal.organization_id)
    stmt = (
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
    )
    return paginate(db, stmt, params, WebhookDeliveryOut.model_validate)
