"""Lab policy service: versioned organization policy sets evaluated on top of the immutable baseline.

Every automated decision point (tool calls, execution, model calls, promotions, approvals) calls
``evaluate``/``enforce`` with a *facts* document. Denials are audit-logged; decisions are counted in metrics
and returned as structured results so callers can record them next to the action they gated.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.errors import Conflict, PolicyDenied, ValidationFailed
from aegis_api.infrastructure.observability import metrics
from aegis_api.models.lab import LabPolicy, LabPolicyVersion, Mission
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, quota_service
from aegis_api.services.lab.common import Actor
from engines.lab.enums import AutonomyLevel
from engines.lab.policy.engine import (
    BASELINE_POLICY,
    PolicyEngine,
    PolicyError,
    PolicyResult,
    PolicySet,
    parse_policy_document,
)

log = structlog.get_logger("aegis.lab.policy")


def org_policy_sets(db: Session, organization_id: uuid.UUID) -> list[PolicySet]:
    rows = db.execute(
        select(LabPolicy, LabPolicyVersion)
        .join(LabPolicyVersion, LabPolicyVersion.id == LabPolicy.current_version_id)
        .where(LabPolicy.organization_id == organization_id, LabPolicy.status == "active")
        .order_by(LabPolicy.key)
    ).all()
    sets: list[PolicySet] = []
    for policy, version in rows:
        try:
            sets.append(parse_policy_document(policy.key, str(version.version), version.document))
        except PolicyError:
            # A stored version was validated on write; if it no longer parses, fail closed for that org.
            log.error("stored_policy_invalid", policy=policy.key, version=version.version)
            raise
    return sets


def engine_for(db: Session, organization_id: uuid.UUID) -> PolicyEngine:
    return PolicyEngine(org_policy_sets(db, organization_id))


def base_facts(
    db: Session,
    organization_id: uuid.UUID,
    actor: Actor,
    *,
    mission: Mission | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quota = quota_service.get_quota(db, organization_id)
    facts: dict[str, Any] = {
        "actor": actor.fact(),
        "org": {
            "allow_auto_strategy_promotion": bool(quota.allow_auto_strategy_promotion),
            "allow_external_models": bool(quota.allow_external_models),
            "expensive_compute_threshold_usd": float(quota.expensive_compute_threshold_usd or 0.0),
            "max_autonomy_level": quota.max_autonomy_level,
        },
    }
    if mission is not None:
        facts["mission"] = {
            "id": str(mission.id),
            "autonomy_level": mission.autonomy_level,
            "autonomy_rank": AutonomyLevel(mission.autonomy_level).rank,
            "risk_level": mission.risk_level,
            "domain": mission.domain,
            "status": mission.status,
        }
    if extra:
        facts.update(extra)
    return facts


def evaluate(
    db: Session,
    *,
    organization_id: uuid.UUID,
    action: str,
    facts: dict[str, Any],
    actor: Actor,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> PolicyResult:
    result = engine_for(db, organization_id).evaluate(action, facts)
    metrics.POLICY_DECISIONS.labels(action=action, decision=str(result.decision)).inc()
    if result.denied:
        audit_log.record(
            db,
            organization_id=organization_id,
            action="lab.policy.denied",
            resource_type=resource_type or "lab_action",
            resource_id=resource_id,
            actor_label=actor.label,
            actor_type=actor.type,
            after={"action": action, "reasons": result.reasons},
        )
    return result


def enforce(
    db: Session,
    *,
    organization_id: uuid.UUID,
    action: str,
    facts: dict[str, Any],
    actor: Actor,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> PolicyResult:
    """Evaluate and raise ``PolicyDenied`` on deny. ``require_approval`` is returned to the caller, which
    decides whether to create an approval request (automation) or reject the request (interactive APIs)."""
    result = evaluate(
        db,
        organization_id=organization_id,
        action=action,
        facts=facts,
        actor=actor,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    if result.denied:
        raise PolicyDenied("; ".join(result.reasons) or f"'{action}' denied by policy", details=result.to_dict())
    return result


# --- management ---------------------------------------------------------------------------------------------


def list_policies(db: Session, organization_id: uuid.UUID) -> list[LabPolicy]:
    return list(
        db.scalars(select(LabPolicy).where(LabPolicy.organization_id == organization_id).order_by(LabPolicy.key)).all()
    )


def baseline() -> dict[str, Any]:
    return {**BASELINE_POLICY.model_dump(mode="json"), "fingerprint": BASELINE_POLICY.fingerprint, "immutable": True}


def create_policy(
    db: Session,
    principal: Principal,
    *,
    key: str,
    name: str,
    description: str | None,
    document: dict[str, Any],
    change_note: str | None = None,
) -> tuple[LabPolicy, LabPolicyVersion]:
    if key.startswith("system."):
        raise ValidationFailed("Policy keys starting with 'system.' are reserved for platform policies")
    exists = db.scalar(
        select(LabPolicy.id).where(LabPolicy.organization_id == principal.organization_id, LabPolicy.key == key)
    )
    if exists:
        raise Conflict(f"Policy '{key}' already exists; add a new version instead")
    policy = LabPolicy(
        organization_id=principal.organization_id,
        key=key,
        name=name,
        description=description,
        status="active",
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(policy)
    db.flush()
    version = add_version(db, principal, policy, document=document, change_note=change_note)
    return policy, version


def add_version(
    db: Session,
    principal: Principal,
    policy: LabPolicy,
    *,
    document: dict[str, Any],
    change_note: str | None = None,
) -> LabPolicyVersion:
    number = policy.version_count + 1
    try:
        parsed = parse_policy_document(policy.key, str(number), document)
    except PolicyError as exc:
        raise ValidationFailed(str(exc)) from exc
    stored = parsed.model_dump(mode="json", exclude={"key", "version"})
    version = LabPolicyVersion(
        organization_id=policy.organization_id,
        policy_id=policy.id,
        version=number,
        document=stored,
        fingerprint=parsed.fingerprint,
        change_note=change_note,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(version)
    db.flush()
    before = {"current_version_id": str(policy.current_version_id) if policy.current_version_id else None}
    policy.current_version_id = version.id
    policy.version_count = number
    audit_log.record(
        db,
        organization_id=policy.organization_id,
        action="lab.policy.version_created",
        resource_type="lab_policy",
        resource_id=policy.id,
        principal=principal,
        before=before,
        after={"version": number, "fingerprint": parsed.fingerprint},
    )
    return version


def set_status(db: Session, principal: Principal, policy: LabPolicy, status: str) -> LabPolicy:
    if status not in ("active", "disabled"):
        raise ValidationFailed("status must be 'active' or 'disabled'")
    before = policy.status
    policy.status = status
    audit_log.record(
        db,
        organization_id=policy.organization_id,
        action="lab.policy.status_changed",
        resource_type="lab_policy",
        resource_id=policy.id,
        principal=principal,
        before={"status": before},
        after={"status": status},
    )
    return policy


def simulate(db: Session, organization_id: uuid.UUID, action: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Dry-run an action against the current policy stack (no side effects, no audit entry)."""
    return engine_for(db, organization_id).evaluate(action, facts).to_dict()
