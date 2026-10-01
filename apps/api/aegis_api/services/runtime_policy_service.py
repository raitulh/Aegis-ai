"""Policy Studio: runtime policies with versions, validation, testing, simulation, publish/rollback and
assignments. Versions are immutable (database trigger); publishing moves a pointer, rollback re-publishes."""

from __future__ import annotations

import difflib
import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.models import (
    AISystem,
    RuntimeEvent,
    RuntimePolicy,
    RuntimePolicyAssignment,
    RuntimePolicyVersion,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from engines.runtime.policy import PolicyError, compile_policy, diff_rules, evaluate
from engines.runtime.templates import TEMPLATES_BY_KEY

SIMULATION_MAX_EVENTS = 20_000
_KEY = re.compile(r"[^a-z0-9]+")


def _key(value: str) -> str:
    return _KEY.sub("-", value.lower()).strip("-")[:80] or "policy"


def validate_source(source: str) -> dict[str, Any]:
    try:
        return compile_policy(source)
    except PolicyError as exc:
        raise ValidationFailed("Policy is invalid", code="policy_invalid", details={"errors": exc.errors}) from exc


def get_policy(session: Session, policy_id: uuid.UUID, organization_id: uuid.UUID) -> RuntimePolicy:
    policy = session.get(RuntimePolicy, policy_id)
    if policy is None or policy.organization_id != organization_id:
        raise NotFound("Runtime policy not found")
    return policy


def get_version(session: Session, policy: RuntimePolicy, version: int) -> RuntimePolicyVersion:
    record = session.scalar(
        select(RuntimePolicyVersion).where(
            RuntimePolicyVersion.policy_id == policy.id, RuntimePolicyVersion.version == version
        )
    )
    if record is None:
        raise NotFound(f"Version {version} not found")
    return record


def create_policy(
    session: Session,
    principal: Principal,
    *,
    name: str | None,
    source_yaml: str | None,
    template_key: str | None = None,
    key: str | None = None,
    description: str | None = None,
) -> tuple[RuntimePolicy, RuntimePolicyVersion]:
    if template_key:
        template = TEMPLATES_BY_KEY.get(template_key)
        if template is None:
            raise NotFound("Policy template not found")
        source_yaml = source_yaml or template.source
        name = name or template.name
        category = template.category
    else:
        category = None
    if not source_yaml:
        raise ValidationFailed("Provide policy source or a template")
    compiled = validate_source(source_yaml)
    name = name or compiled["name"]
    base_key = _key(key or name)
    candidate, i = base_key, 1
    while session.scalar(
        select(RuntimePolicy.id).where(
            RuntimePolicy.organization_id == principal.organization_id, RuntimePolicy.key == candidate
        )
    ):
        i += 1
        candidate = f"{base_key}-{i}"
    policy = RuntimePolicy(
        organization_id=principal.organization_id,
        key=candidate,
        name=name,
        description=description or compiled.get("description"),
        category=category,
        status="draft",
        latest_version=1,
        template_key=template_key,
        created_by_id=_user(principal),
    )
    session.add(policy)
    session.flush()
    version = RuntimePolicyVersion(
        organization_id=principal.organization_id,
        policy_id=policy.id,
        version=1,
        source_yaml=source_yaml,
        compiled=compiled,
        checksum=compiled["checksum"],
        status="draft",
        change_note="Created from template" if template_key else "Created",
        created_by_id=_user(principal),
    )
    session.add(version)
    session.flush()
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="policy.created",
        resource_type="runtime_policy",
        resource_id=policy.id,
        principal=principal,
        after={"key": policy.key, "template": template_key},
    )
    return policy, version


def _user(principal: Principal) -> uuid.UUID | None:
    return principal.user_id if principal.auth_method != "api_key" else None


def new_version(
    session: Session, principal: Principal, policy: RuntimePolicy, source_yaml: str, change_note: str | None
) -> RuntimePolicyVersion:
    compiled = validate_source(source_yaml)
    latest = session.scalar(
        select(RuntimePolicyVersion)
        .where(RuntimePolicyVersion.policy_id == policy.id)
        .order_by(RuntimePolicyVersion.version.desc())
        .limit(1)
    )
    if latest is not None and latest.checksum == compiled["checksum"]:
        raise Conflict("This source is identical to the latest version")
    policy.latest_version = (policy.latest_version or 0) + 1
    version = RuntimePolicyVersion(
        organization_id=policy.organization_id,
        policy_id=policy.id,
        version=policy.latest_version,
        source_yaml=source_yaml,
        compiled=compiled,
        checksum=compiled["checksum"],
        status="draft",
        change_note=change_note,
        created_by_id=_user(principal),
    )
    session.add(version)
    policy.updated_at = utcnow()
    session.flush()
    audit_log.record(
        session,
        organization_id=policy.organization_id,
        action="policy.changed",
        resource_type="runtime_policy",
        resource_id=policy.id,
        principal=principal,
        after={"version": version.version, "note": change_note},
    )
    return version


def publish(
    session: Session,
    principal: Principal,
    policy: RuntimePolicy,
    version: RuntimePolicyVersion,
    *,
    rollback: bool = False,
) -> RuntimePolicy:
    if version.policy_id != policy.id:
        raise NotFound("Version not found")
    previous = policy.published_version_id
    for other in session.scalars(
        select(RuntimePolicyVersion).where(
            RuntimePolicyVersion.policy_id == policy.id, RuntimePolicyVersion.status == "published"
        )
    ).all():
        if other.id != version.id:
            other.status = "superseded"
    version.status = "published"
    version.published_at = utcnow()
    version.published_by_id = _user(principal)
    policy.published_version_id = version.id
    policy.status = "published"
    policy.updated_at = utcnow()
    audit_log.record(
        session,
        organization_id=policy.organization_id,
        action="policy.rollback" if rollback else "policy.published",
        resource_type="runtime_policy",
        resource_id=policy.id,
        principal=principal,
        before={"published_version_id": str(previous) if previous else None},
        after={"version": version.version, "checksum": version.checksum},
    )
    return policy


def set_enabled(session: Session, principal: Principal, policy: RuntimePolicy, enabled: bool) -> RuntimePolicy:
    if enabled and policy.published_version_id is None:
        raise Conflict("Publish a version before enabling the policy")
    policy.status = "published" if enabled else "disabled"
    policy.updated_at = utcnow()
    audit_log.record(
        session,
        organization_id=policy.organization_id,
        action="policy.enabled" if enabled else "policy.disabled",
        resource_type="runtime_policy",
        resource_id=policy.id,
        principal=principal,
    )
    return policy


def clone(session: Session, principal: Principal, policy: RuntimePolicy) -> tuple[RuntimePolicy, RuntimePolicyVersion]:
    source = session.scalar(
        select(RuntimePolicyVersion)
        .where(RuntimePolicyVersion.policy_id == policy.id)
        .order_by(RuntimePolicyVersion.version.desc())
        .limit(1)
    )
    if source is None:
        raise NotFound("Policy has no versions")
    return create_policy(
        session, principal, name=f"{policy.name} (copy)", source_yaml=source.source_yaml, key=f"{policy.key}-copy"
    )


def diff(session: Session, policy: RuntimePolicy, a: int, b: int) -> dict[str, Any]:
    va, vb = get_version(session, policy, a), get_version(session, policy, b)
    unified = "\n".join(
        difflib.unified_diff(
            va.source_yaml.splitlines(),
            vb.source_yaml.splitlines(),
            fromfile=f"v{a}",
            tofile=f"v{b}",
            lineterm="",
        )
    )
    return {"from": a, "to": b, "unified": unified, "rules": diff_rules(va.compiled, vb.compiled)}


# --- assignments ---------------------------------------------------------------------------------
def assign(
    session: Session,
    principal: Principal,
    policy: RuntimePolicy,
    *,
    scope_type: str,
    system_id: str | None = None,
    environment: str | None = None,
) -> RuntimePolicyAssignment:
    if scope_type == "organization":
        scope_key, system_uuid = "*", None
    elif scope_type == "environment":
        if environment not in ("production", "staging", "development"):
            raise ValidationFailed("environment must be production, staging or development")
        scope_key, system_uuid = environment, None
    elif scope_type == "system":
        if not system_id:
            raise ValidationFailed("system_id is required for system scope")
        system = session.get(AISystem, uuid.UUID(system_id))
        if system is None or system.organization_id != policy.organization_id or system.deleted_at is not None:
            raise NotFound("AI system not found")
        scope_key, system_uuid = str(system.id), system.id
    else:
        raise ValidationFailed("scope_type must be organization, environment or system")
    existing = session.scalar(
        select(RuntimePolicyAssignment).where(
            RuntimePolicyAssignment.policy_id == policy.id,
            RuntimePolicyAssignment.scope_type == scope_type,
            RuntimePolicyAssignment.scope_key == scope_key,
        )
    )
    if existing is not None:
        existing.enabled = True
        return existing
    assignment = RuntimePolicyAssignment(
        organization_id=policy.organization_id,
        policy_id=policy.id,
        scope_type=scope_type,
        scope_key=scope_key,
        system_id=system_uuid,
        enabled=True,
        created_by_id=_user(principal),
    )
    session.add(assignment)
    session.flush()
    audit_log.record(
        session,
        organization_id=policy.organization_id,
        action="policy.assigned",
        resource_type="runtime_policy",
        resource_id=policy.id,
        principal=principal,
        after={"scope_type": scope_type, "scope_key": scope_key},
    )
    return assignment


def applicable_policies(session: Session, system: AISystem) -> list[dict[str, Any]]:
    """Published, enabled policies assigned to the workspace, the system's environment, or the system."""
    rows = session.execute(
        select(RuntimePolicy, RuntimePolicyVersion)
        .join(RuntimePolicyAssignment, RuntimePolicyAssignment.policy_id == RuntimePolicy.id)
        .join(RuntimePolicyVersion, RuntimePolicyVersion.id == RuntimePolicy.published_version_id)
        .where(
            RuntimePolicy.organization_id == system.organization_id,
            RuntimePolicy.status == "published",
            RuntimePolicyAssignment.enabled.is_(True),
            or_(
                RuntimePolicyAssignment.scope_type == "organization",
                (RuntimePolicyAssignment.scope_type == "environment")
                & (RuntimePolicyAssignment.scope_key == system.environment),
                (RuntimePolicyAssignment.scope_type == "system") & (RuntimePolicyAssignment.system_id == system.id),
            ),
        )
    ).all()
    seen: set[uuid.UUID] = set()
    out = []
    for policy, version in rows:
        if policy.id in seen:
            continue
        seen.add(policy.id)
        out.append(
            {
                "policy_id": str(policy.id),
                "policy_key": policy.key,
                "version": version.version,
                "compiled": version.compiled,
            }
        )
    return out


# --- testing and simulation ----------------------------------------------------------------------
def event_row_to_dict(row: RuntimeEvent) -> dict[str, Any]:
    return {
        "event_type": row.event_type,
        "tool_name": row.tool_name,
        "agent_name": row.agent_name,
        "environment": row.environment,
        "source": row.source,
        "actor": row.actor,
        "payload": row.payload,
        "signals": row.signals,
    }


def simulate(
    session: Session,
    organization_id: uuid.UUID,
    compiled: dict[str, Any],
    *,
    policy_key: str,
    system_ids: list[uuid.UUID] | None = None,
    days: int = 7,
) -> dict[str, Any]:
    """Replay recorded runtime events through a (draft or published) policy. All numbers are counts of
    real recorded events; nothing is extrapolated."""
    since = utcnow() - timedelta(days=days)
    base = select(RuntimeEvent).where(
        RuntimeEvent.organization_id == organization_id, RuntimeEvent.occurred_at >= since
    )
    if system_ids:
        base = base.where(RuntimeEvent.system_id.in_(system_ids))
    total_available = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(base.order_by(RuntimeEvent.occurred_at.desc()).limit(SIMULATION_MAX_EVENTS)).all()
    policy = [{"policy_id": None, "policy_key": policy_key, "version": "draft", "compiled": compiled}]
    counts = {"allow": 0, "flag": 0, "require_approval": 0, "block": 0}
    by_rule: dict[str, int] = {}
    systems: dict[str, dict[str, int]] = {}
    sessions_requiring_approval: set[str] = set()
    sessions_blocked: set[str] = set()
    changed_vs_recorded = 0
    samples: list[dict[str, Any]] = []
    for row in rows:
        result = evaluate(event_row_to_dict(row), policy)
        decision = result["decision"]
        counts[decision] += 1
        for match in result["matches"]:
            by_rule[match["rule_id"]] = by_rule.get(match["rule_id"], 0) + 1
        stats = systems.setdefault(str(row.system_id), {"events": 0, "flag": 0, "require_approval": 0, "block": 0})
        stats["events"] += 1
        if decision != "allow":
            stats[decision] += 1
            if len(samples) < 25:
                samples.append(
                    {
                        "event_id": row.event_id,
                        "event_type": row.event_type,
                        "system_id": str(row.system_id),
                        "agent": row.agent_name,
                        "tool": row.tool_name,
                        "occurred_at": row.occurred_at.isoformat(),
                        "decision": decision,
                        "rules": [m["rule_id"] for m in result["matches"]],
                    }
                )
        key = row.session_id or row.trace_id
        if key and decision == "require_approval":
            sessions_requiring_approval.add(key)
        if key and decision == "block":
            sessions_blocked.add(key)
        if decision != row.decision:
            changed_vs_recorded += 1
    return {
        "window_days": days,
        "events_available": int(total_available),
        "events_evaluated": len(rows),
        "truncated": int(total_available) > len(rows),
        "allowed": counts["allow"],
        "flagged": counts["flag"],
        "require_approval": counts["require_approval"],
        "blocked": counts["block"],
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "by_system": systems,
        "workflows_requiring_approval": len(sessions_requiring_approval),
        "workflows_blocked": len(sessions_blocked),
        "decisions_changed_vs_recorded": changed_vs_recorded,
        "samples": samples,
    }
