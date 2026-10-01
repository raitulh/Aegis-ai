"""Billing state transitions driven by verified provider webhooks (idempotent per provider event id)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.billing import plans
from aegis_api.billing.provider import SubscriptionUpdate
from aegis_api.db.base import utcnow
from aegis_api.models import BillingEvent, Organization, Subscription
from aegis_api.services import audit_log

log = structlog.get_logger("aegis.billing")


def _ts(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=UTC) if value else None


def apply_update(session: Session, provider: str, update: SubscriptionUpdate, payload: dict) -> bool:
    """Record the provider event once; apply it once. Returns False for an already-processed event."""
    inserted = session.execute(
        insert(BillingEvent)
        .values(
            id=uuid.uuid4(),
            organization_id=uuid.UUID(update.organization_id) if update.organization_id else None,
            provider=provider,
            provider_event_id=update.provider_event_id,
            type=update.type,
            payload=payload,
        )
        .on_conflict_do_nothing(constraint="uq_billing_events_provider_event")
        .returning(BillingEvent.id)
    ).scalar()
    if inserted is None:
        log.info("billing_event_duplicate", provider_event_id=update.provider_event_id)
        return False
    event = session.get(BillingEvent, inserted)
    if not update.organization_id or update.status is None:
        if event is not None:
            event.processed_at = utcnow()
        return True
    org = session.get(Organization, uuid.UUID(update.organization_id))
    if org is None:
        if event is not None:
            event.error = "organization not found"
        return True
    sub = session.scalar(select(Subscription).where(Subscription.organization_id == org.id))
    if sub is None:
        sub = Subscription(organization_id=org.id, plan=plans.normalise(update.plan or org.plan), provider=provider)
        session.add(sub)
    before = {"plan": sub.plan, "status": sub.status}
    if update.plan:
        sub.plan = plans.normalise(update.plan)
    sub.status = update.status
    sub.provider = provider
    sub.provider_customer_id = update.customer_id or sub.provider_customer_id
    sub.provider_subscription_id = update.subscription_id or sub.provider_subscription_id
    sub.current_period_start = _ts(update.period_start) or sub.current_period_start
    sub.current_period_end = _ts(update.period_end) or sub.current_period_end
    if update.cancel_at_period_end is not None:
        sub.cancel_at_period_end = update.cancel_at_period_end
    org.plan = sub.plan if sub.status in ("active", "trialing", "past_due") else "free"
    if event is not None:
        event.processed_at = utcnow()
    audit_log.record(
        session,
        organization_id=org.id,
        action="billing.subscription_updated",
        resource_type="subscription",
        resource_id=sub.id,
        actor_type="system",
        actor_label=f"billing:{provider}",
        before=before,
        after={"plan": sub.plan, "status": sub.status, "event": update.type},
    )
    return True
