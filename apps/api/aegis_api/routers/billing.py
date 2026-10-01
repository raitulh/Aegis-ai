"""Organization settings, plans, usage and billing endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from aegis_api.billing import plans
from aegis_api.billing.provider import get_provider
from aegis_api.db.session import admin_session_scope
from aegis_api.deps import get_db, require
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models import Organization, Subscription
from aegis_api.ratelimit import public_rate_limit
from aegis_api.schemas.platform import CheckoutRequest, OrganizationSettingsUpdate
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, billing_service, entitlements

router = APIRouter(prefix="/api/v1", tags=["Billing & Usage"])


@router.get("/plans", dependencies=[Depends(public_rate_limit)])
def list_plans() -> dict[str, Any]:
    """Public plan catalogue (features, quotas, retention). Prices appear only when configured."""
    catalogue = plans.catalogue()
    return {
        "plans": [catalogue[k].public() for k in plans.PLAN_ORDER],
        "quotas": plans.QUOTAS,
        "features": plans.FEATURES,
        "roadmap_features": sorted(plans.ROADMAP_FEATURES),
    }


@router.get("/usage")
def usage(principal: Principal = Depends(require("usage:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Current plan, period, quotas, usage from the immutable ledger, and linear projections."""
    return entitlements.snapshot(db, principal.organization_id)


@router.get("/organization")
def get_organization(
    principal: Principal = Depends(require("org:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    org = db.get(Organization, principal.organization_id)
    if org is None:
        raise NotFound("Workspace not found")
    plan, _ = entitlements.effective_plan(db, org.id)
    from aegis_api.services import finding_service, retention_service

    settings = org.settings or {}
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "plan": plan.key,
        "is_demo": org.is_demo,
        "is_sandbox": org.is_sandbox,
        "expires_at": org.expires_at.isoformat() if org.expires_at else None,
        "retention": {
            "runtime_events_days": retention_service.runtime_retention_days(org),
            "custom_allowed": bool(plan.features.get("custom_retention")),
            "evidence": "retained (never deleted by retention)",
        },
        "finding_sla_days": settings.get("finding_sla_days") or finding_service.SLA_DAYS,
        "features": dict(plan.features),
    }


@router.patch("/organization")
def update_organization(
    body: OrganizationSettingsUpdate,
    principal: Principal = Depends(require("org:manage")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    org = db.get(Organization, principal.organization_id)
    if org is None:
        raise NotFound("Workspace not found")
    before = {"name": org.name, "settings": dict(org.settings or {})}
    settings = dict(org.settings or {})
    if body.name is not None:
        org.name = body.name
    if body.runtime_events_retention_days is not None:
        entitlements.require_feature(db, org.id, "custom_retention")
        settings["retention"] = {
            **(settings.get("retention") or {}),
            "runtime_events_days": body.runtime_events_retention_days,
        }
    if body.finding_sla_days is not None:
        allowed = {"critical", "high", "medium", "low", "info"}
        if not set(body.finding_sla_days) <= allowed or any(v < 1 or v > 3650 for v in body.finding_sla_days.values()):
            raise ValidationFailed("finding_sla_days keys must be severities with 1–3650 days")
        settings["finding_sla_days"] = body.finding_sla_days
    org.settings = settings
    audit_log.record(
        db,
        organization_id=org.id,
        action="settings.changed",
        resource_type="organization",
        resource_id=org.id,
        principal=principal,
        before=before,
        after={"name": org.name, "settings": settings},
    )
    return get_organization(principal, db)


@router.post("/billing/checkout")
def checkout(
    body: CheckoutRequest, principal: Principal = Depends(require("billing:manage")), db: Session = Depends(get_db)
) -> dict[str, str]:
    url = get_provider().checkout_url(
        organization_id=str(principal.organization_id), plan=body.plan, customer_email=principal.email
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="billing.checkout_started",
        resource_type="organization",
        resource_id=principal.organization_id,
        principal=principal,
        after={"plan": body.plan},
    )
    return {"url": url}


@router.post("/billing/portal")
def portal(principal: Principal = Depends(require("billing:manage")), db: Session = Depends(get_db)) -> dict[str, str]:
    from sqlalchemy import select

    sub = db.scalar(select(Subscription).where(Subscription.organization_id == principal.organization_id))
    if sub is None or not sub.provider_customer_id:
        raise NotFound("No billing account exists for this workspace yet")
    return {"url": get_provider().portal_url(customer_id=sub.provider_customer_id)}


@router.post("/billing/webhooks/{provider}", include_in_schema=False)
async def provider_webhook(provider: str, request: Request) -> dict[str, Any]:
    """Inbound billing-provider events. Authenticated by the provider's signature (not a session), recorded
    once per provider event id, and applied idempotently on the owner connection (billing layer)."""
    import json

    impl = get_provider()
    if impl.name != provider:
        raise NotFound("Unknown billing provider")
    body = await request.body()
    update = impl.parse_webhook(body, {k.lower(): v for k, v in request.headers.items()})
    if update is None:
        return {"received": True}
    with admin_session_scope() as session:
        applied = billing_service.apply_update(session, provider, update, json.loads(body or b"{}"))
    return {"received": True, "applied": applied}
