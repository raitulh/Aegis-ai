"""Entitlement engine — the single place that answers "can this workspace do X, and how much is left?".

Plan checks are never scattered through routers or the frontend: endpoints call :func:`require_feature` or
:func:`check_quota`, and the UI renders :func:`snapshot`. Usage comes from the immutable usage ledger and live
counts — never from client-side counters.

Over-quota behaviour:
* creation endpoints (systems, audits, red-team runs, exports, invitations) refuse with
  ``plan_limit_exceeded`` and details (metric, used, limit, plan, suggested plan);
* runtime *decisions* (``/runtime/check``) are never refused for quota reasons — a security control must
  keep working — but bulk telemetry ingestion beyond the monthly quota is refused.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.billing import plans
from aegis_api.config import get_settings
from aegis_api.errors import PlanLimitExceeded
from aegis_api.models import AISystem, Audit, Invitation, Membership, Organization, RedTeamRun, Subscription
from aegis_api.services import usage_service

THRESHOLDS = (50, 75, 90, 100)


def _org(session: Session, organization_id: uuid.UUID) -> Organization:
    org = session.get(Organization, organization_id)
    if org is None:
        raise PlanLimitExceeded("Workspace not found")
    return org


def effective_plan(session: Session, organization_id: uuid.UUID) -> tuple[plans.PlanDefinition, Subscription | None]:
    org = _org(session, organization_id)
    sub = session.scalar(select(Subscription).where(Subscription.organization_id == organization_id))
    key = sub.plan if sub is not None and sub.status in ("active", "trialing", "past_due") else org.plan
    if org.is_sandbox or org.is_demo:
        key = "pro"  # demo sandboxes showcase paid capabilities without billing
    plan = plans.get_plan(key)
    if sub is not None and sub.overrides:
        plan = plans.PlanDefinition(
            key=plan.key,
            name=plan.name,
            audience=plan.audience,
            limits={**plan.limits, **(sub.overrides.get("limits") or {})},
            features={**plan.features, **(sub.overrides.get("features") or {})},
            retention_days=sub.overrides.get("retention_days", plan.retention_days),
            support=plan.support,
            price_display=plan.price_display,
            self_serve=plan.self_serve,
        )
    return plan, sub


def period_bounds(sub: Subscription | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    if sub is not None and sub.current_period_start and sub.current_period_end and sub.current_period_end > now:
        return sub.current_period_start, sub.current_period_end
    start = usage_service.period_start(now)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def usage_for(session: Session, organization_id: uuid.UUID, metric: str, since: datetime) -> int:
    if metric == "systems":
        return int(
            session.scalar(
                select(func.count(AISystem.id)).where(
                    AISystem.organization_id == organization_id, AISystem.deleted_at.is_(None)
                )
            )
            or 0
        )
    if metric == "seats":
        members = session.scalar(
            select(func.count(Membership.id)).where(
                Membership.organization_id == organization_id, Membership.status == "active"
            )
        )
        pending = session.scalar(
            select(func.count(Invitation.id)).where(
                Invitation.organization_id == organization_id,
                Invitation.status == "pending",
                Invitation.expires_at > datetime.now(UTC),
            )
        )
        return int(members or 0) + int(pending or 0)
    if metric == "audit_run":
        return int(
            session.scalar(
                select(func.count(Audit.id)).where(
                    Audit.organization_id == organization_id,
                    Audit.created_at >= since,
                    Audit.status.not_in(["cancelled", "draft"]),
                )
            )
            or 0
        )
    if metric == "redteam_run":
        return int(
            session.scalar(
                select(func.count(RedTeamRun.id)).where(
                    RedTeamRun.organization_id == organization_id, RedTeamRun.created_at >= since
                )
            )
            or 0
        )
    return usage_service.total(session, organization_id, metric, since)


def require_feature(session: Session, organization_id: uuid.UUID, feature: str) -> None:
    plan, _ = effective_plan(session, organization_id)
    if feature in plans.ROADMAP_FEATURES:
        raise PlanLimitExceeded(
            f"{plans.FEATURES.get(feature, feature)} is on the roadmap and not available yet",
            code="feature_unavailable",
            details={"feature": feature, "roadmap": True},
        )
    if not plan.features.get(feature, False):
        upgrade = next(
            (p for p in plans.PLAN_ORDER if plans.get_plan(p).features.get(feature)),
            None,
        )
        raise PlanLimitExceeded(
            f"{plans.FEATURES.get(feature, feature)} is not included in the {plan.name} plan",
            code="feature_not_in_plan",
            details={"feature": feature, "plan": plan.key, "available_on": upgrade},
        )


def check_quota(session: Session, organization_id: uuid.UUID, metric: str, increment: int = 1) -> None:
    plan, sub = effective_plan(session, organization_id)
    limit = plan.limits.get(metric)
    if limit is None:
        return
    start, end = period_bounds(sub)
    used = usage_for(session, organization_id, metric, start)
    if used + increment > limit:
        raise PlanLimitExceeded(
            f"{plans.QUOTAS.get(metric, metric)} limit reached ({used:,} of {limit:,} on the {plan.name} plan)",
            details={
                "metric": metric,
                "used": used,
                "limit": limit,
                "requested": increment,
                "plan": plan.key,
                "period_end": end.isoformat() if metric not in ("systems", "seats") else None,
                "next_plan": plans.next_plan(plan.key),
            },
        )


def snapshot(session: Session, organization_id: uuid.UUID) -> dict[str, Any]:
    """Everything the Billing & Usage page needs, computed from the ledger and live counts."""
    plan, sub = effective_plan(session, organization_id)
    org = _org(session, organization_id)
    start, end = period_bounds(sub)
    now = datetime.now(UTC)
    elapsed = max((now - start).total_seconds(), 1.0)
    span = max((end - start).total_seconds(), 1.0)
    quotas = []
    for metric, label in plans.QUOTAS.items():
        used = usage_for(session, organization_id, metric, start)
        limit = plan.limits.get(metric)
        periodic = metric not in ("systems", "seats")
        # A linear projection from a few hours of data is noise: only project once a meaningful share of the
        # period has elapsed (and never below what is already used).
        if periodic and used and elapsed >= min(span * 0.1, 3 * 86400):
            projected: int | None = max(used, round(used * span / elapsed))
        else:
            projected = None
        quotas.append(
            {
                "metric": metric,
                "label": label,
                "used": used,
                "limit": limit,
                "remaining": None if limit is None else max(limit - used, 0),
                "percent": None if not limit else round(100 * used / limit, 1),
                "periodic": periodic,
                # Linear projection over the billing period from usage so far (labelled as a projection).
                "projected": projected,
                "projected_over_limit": bool(limit is not None and projected is not None and projected > limit),
                "threshold_reached": next((t for t in reversed(THRESHOLDS) if limit and used * 100 >= t * limit), None),
            }
        )
    return {
        "organization_id": str(organization_id),
        "plan": plan.public(),
        "plan_key": plan.key,
        "sandbox": bool(org.is_sandbox or org.is_demo),
        "subscription": {
            "status": sub.status if sub else "none",
            "provider": sub.provider if sub else get_settings().billing_provider,
            "cancel_at_period_end": bool(sub and sub.cancel_at_period_end),
        },
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "quotas": quotas,
        "billing_provider": get_settings().billing_provider,
        "self_serve_checkout": get_settings().billing_provider != "none",
    }
