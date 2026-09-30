"""Versioned organization/project policies and the policy decision point.

Rules live in immutable ``governance_policy_versions`` rows (UPDATE/DELETE rejected by trigger); a policy
points at its current version. :func:`evaluate_policy` combines the platform BASELINE (always first,
never overridable), the organization's active policies, project-scoped policies and the mission's own
approval policy, after enriching the caller's context with authoritative facts (actor kind, humanness,
effective autonomy under the org/project ceilings, egress allowlist, data-processing consent). Callers
cannot spoof those facts: authoritative keys always override caller-supplied values (except in the
explicit dry-run mode used by ``POST /governance/evaluate``).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.org_settings import get_org_settings
from aegis_api.lab.governance.durable import jsonable, on_rollback
from aegis_api.lab.governance.schemas import (
    PolicyCreate,
    PolicyDetailOut,
    PolicyOut,
    PolicyVersionCreate,
    PolicyVersionOut,
)
from aegis_api.lab.models import GovernancePolicy, GovernancePolicyVersion, Mission, Project
from aegis_api.lab.observability import metrics
from aegis_api.schemas.common import Page, PageParams
from engines.lab import autonomy as autonomy_engine
from engines.lab.policy import (
    ACTIONS,
    BASELINE,
    CONTEXT_KEYS,
    DEFAULT_EFFECT,
    Decision,
    PolicySet,
    PolicyValidationError,
    Rule,
    baseline_documents,
    is_valid_action_name,
    parse_rules,
)
from engines.lab.policy import evaluate as engine_evaluate
from engines.lab.states import AUTONOMY_RANK, AutonomyLevel

log = structlog.get_logger("aegis.lab.governance")

PolicyDecision = Decision
"""The decision returned by :func:`evaluate_policy` (effect, reasons, matched_rules, policy_versions,
obligations, approver_permission, default_applied)."""

POLICY_STATUSES = ("active", "disabled")
# Keys copied into POLICY_DENIED events/audit (never free-form content).
_EVENT_CONTEXT_KEYS = frozenset(CONTEXT_KEYS - {"org_egress_allowlist"})
_AUTHORITATIVE_KEYS = (
    "actor_kind",
    "is_human",
    "autonomy_level",
    "org_egress_allowlist",
    "data_processing_consent",
    "ceiling_rank",
    "requested_rank",
    "current_rank",
)


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def _uuid(value: uuid.UUID | str | None, label: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound(f"{label} not found") from exc


def rules_hash(documents: list[dict[str, Any]]) -> str:
    canonical = json.dumps(documents, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_or_422(rules: list[dict[str, Any]]) -> list[Rule]:
    try:
        return parse_rules(rules)
    except PolicyValidationError as exc:
        raise ValidationFailed("Policy rules are invalid", details={"errors": exc.errors}) from exc


def _action_label(action: str) -> str:
    return action if action in ACTIONS else "other"


def _require_policy_admin(actor: Actor) -> None:
    actor.require("admin:policy")
    actor.require_human("changing governance policies")


def _visible_policy(db: Session, actor: Actor, policy_id: uuid.UUID | str) -> GovernancePolicy:
    policy = get_owned(db, GovernancePolicy, policy_id, actor, label="Policy")
    if policy.project_id is not None:
        load_project(db, actor, policy.project_id)  # NotFound for projects the actor cannot see
    return policy


def _current_version(db: Session, policy: GovernancePolicy) -> GovernancePolicyVersion | None:
    if policy.current_version_id is None:
        return None
    return db.get(GovernancePolicyVersion, policy.current_version_id)


def _append_version(
    db: Session, actor: Actor, policy: GovernancePolicy, rules: list[Rule], change_note: str | None, number: int
) -> GovernancePolicyVersion:
    documents = [rule.to_document() for rule in rules]
    version = GovernancePolicyVersion(
        organization_id=policy.organization_id,
        policy_id=policy.id,
        version=number,
        rules=documents,
        content_hash=rules_hash(documents),
        change_note=change_note,
        created_by_id=actor.user_id,
    )
    db.add(version)
    db.flush()
    policy.current_version_id = version.id
    db.flush()
    return version


# ---------------------------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------------------------
def policy_out(policy: GovernancePolicy, version: GovernancePolicyVersion | None = None) -> PolicyOut:
    return PolicyOut(
        id=str(policy.id),
        key=policy.key,
        name=policy.name,
        description=policy.description,
        project_id=str(policy.project_id) if policy.project_id else None,
        status=policy.status,
        current_version_id=str(policy.current_version_id) if policy.current_version_id else None,
        current_version=version.version if version else None,
        created_by_id=str(policy.created_by_id) if policy.created_by_id else None,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def policy_detail_out(policy: GovernancePolicy, version: GovernancePolicyVersion | None) -> PolicyDetailOut:
    base = policy_out(policy, version).model_dump()
    return PolicyDetailOut(
        **base,
        rules=list(version.rules) if version else [],
        content_hash=version.content_hash if version else None,
    )


def version_out(version: GovernancePolicyVersion) -> PolicyVersionOut:
    return PolicyVersionOut.model_validate(version)


# ---------------------------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------------------------
def create_policy(db: Session, actor: Actor, data: PolicyCreate) -> GovernancePolicy:
    """Create an organization (or project) policy and its immutable version 1."""
    _require_policy_admin(actor)
    project_id: uuid.UUID | None = None
    if data.project_id:
        project_id = load_project(db, actor, data.project_id).id
    rules = _parse_or_422(data.rules)
    exists = db.scalar(
        select(GovernancePolicy.id).where(
            GovernancePolicy.organization_id == actor.organization_id, GovernancePolicy.key == data.key
        )
    )
    if exists is not None:
        raise Conflict(f"A policy with key '{data.key}' already exists", code="policy_key_exists")
    policy = GovernancePolicy(
        organization_id=actor.organization_id,
        project_id=project_id,
        key=data.key,
        name=data.name,
        description=data.description,
        status="active",
        created_by_id=actor.user_id,
    )
    try:
        with db.begin_nested():
            db.add(policy)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"A policy with key '{data.key}' already exists", code="policy_key_exists") from exc
    version = _append_version(db, actor, policy, rules, data.change_note, 1)
    audit(
        db,
        actor,
        AuditAction.POLICY_CHANGED,
        "governance_policy",
        policy.id,
        after={
            "operation": "created",
            "key": policy.key,
            "project_id": project_id,
            "version": 1,
            "content_hash": version.content_hash,
            "rules": len(rules),
        },
    )
    return policy


def create_policy_version(
    db: Session, actor: Actor, policy_id: uuid.UUID | str, data: PolicyVersionCreate
) -> GovernancePolicyVersion:
    """Append a new immutable version and make it current."""
    _require_policy_admin(actor)
    policy = _visible_policy(db, actor, policy_id)
    rules = _parse_or_422(data.rules)
    advisory_xact_lock(db, f"governance_policy:{policy.id}")
    latest = db.scalar(
        select(func.max(GovernancePolicyVersion.version)).where(GovernancePolicyVersion.policy_id == policy.id)
    )
    previous = _current_version(db, policy)
    version = _append_version(db, actor, policy, rules, data.change_note, int(latest or 0) + 1)
    audit(
        db,
        actor,
        AuditAction.POLICY_CHANGED,
        "governance_policy",
        policy.id,
        before={"version": previous.version, "content_hash": previous.content_hash} if previous else None,
        after={
            "operation": "version_created",
            "version": version.version,
            "content_hash": version.content_hash,
            "rules": len(rules),
        },
    )
    return version


def set_policy_status(db: Session, actor: Actor, policy_id: uuid.UUID | str, status: str) -> GovernancePolicy:
    _require_policy_admin(actor)
    if status not in POLICY_STATUSES:
        raise ValidationFailed(f"status must be one of {', '.join(POLICY_STATUSES)}")
    policy = _visible_policy(db, actor, policy_id)
    if policy.status != status:
        before = policy.status
        policy.status = status
        db.flush()
        audit(
            db,
            actor,
            AuditAction.POLICY_CHANGED,
            "governance_policy",
            policy.id,
            before={"status": before},
            after={"operation": "status_changed", "status": status},
        )
    return policy


def get_policy(db: Session, actor: Actor, policy_id: uuid.UUID | str) -> GovernancePolicy:
    return _visible_policy(db, actor, policy_id)


def get_policy_detail(db: Session, actor: Actor, policy_id: uuid.UUID | str) -> PolicyDetailOut:
    policy = _visible_policy(db, actor, policy_id)
    return policy_detail_out(policy, _current_version(db, policy))


def list_policies(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    status: str | None = None,
    project_id: uuid.UUID | str | None = None,
) -> Page[PolicyOut]:
    stmt = (
        select(GovernancePolicy, GovernancePolicyVersion.version)
        .outerjoin(GovernancePolicyVersion, GovernancePolicyVersion.id == GovernancePolicy.current_version_id)
        .where(GovernancePolicy.organization_id == actor.organization_id)
    )
    if status:
        stmt = stmt.where(GovernancePolicy.status == status)
    if project_id:
        pid = load_project(db, actor, project_id).id
        stmt = stmt.where(GovernancePolicy.project_id == pid)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(or_(GovernancePolicy.project_id.is_(None), GovernancePolicy.project_id.in_(visible)))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(
        stmt.order_by(GovernancePolicy.created_at.desc(), GovernancePolicy.id.desc())
        .limit(params.page_size)
        .offset(params.offset)
    ).all()
    items = [policy_out(policy).model_copy(update={"current_version": number}) for policy, number in rows]
    return Page.build(items, int(total), params)


def list_policy_versions(
    db: Session, actor: Actor, policy_id: uuid.UUID | str, params: PageParams
) -> Page[PolicyVersionOut]:
    policy = _visible_policy(db, actor, policy_id)
    base = select(GovernancePolicyVersion).where(GovernancePolicyVersion.policy_id == policy.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(GovernancePolicyVersion.version.desc()).limit(params.page_size).offset(params.offset)
    ).all()
    return Page.build([version_out(v) for v in rows], int(total), params)


def baseline_view() -> dict[str, Any]:
    return {
        "version": BASELINE.version,
        "rules": baseline_documents(),
        "default_effects": {action: DEFAULT_EFFECT.get(action, "allow") for action in ACTIONS},
        "actions": list(ACTIONS),
        "context_keys": sorted(CONTEXT_KEYS),
    }


# ---------------------------------------------------------------------------------------------
# Policy decision point
# ---------------------------------------------------------------------------------------------
def _invalid_policy_rule(policy: GovernancePolicy, version: GovernancePolicyVersion) -> Rule:
    return Rule.model_validate(
        {
            "id": f"invalid-policy.{policy.key}"[:80],
            "action": "*",
            "effect": "deny",
            "reason": f"Policy '{policy.key}' v{version.version} could not be loaded; denying until it is fixed",
            "priority": 10_000,
        }
    )


def active_policy_sets(db: Session, organization_id: uuid.UUID, project_id: uuid.UUID | None) -> list[PolicySet]:
    """Active organization policies (+ policies of ``project_id``) as engine policy sets."""
    scope: ColumnElement[bool] = GovernancePolicy.project_id.is_(None)
    if project_id is not None:
        scope = or_(scope, GovernancePolicy.project_id == project_id)
    rows = db.execute(
        select(GovernancePolicy, GovernancePolicyVersion)
        .join(GovernancePolicyVersion, GovernancePolicyVersion.id == GovernancePolicy.current_version_id)
        .where(GovernancePolicy.organization_id == organization_id, GovernancePolicy.status == "active", scope)
        .order_by(GovernancePolicy.key)
    ).all()
    sets: list[PolicySet] = []
    for policy, version in rows:
        try:
            rules = parse_rules(version.rules)
        except PolicyValidationError as exc:
            # Fail closed: a stored policy that no longer validates must not silently stop restricting.
            log.error("governance_policy_invalid", policy_key=policy.key, version=version.version, errors=exc.errors)
            rules = [_invalid_policy_rule(policy, version)]
        sets.append(
            PolicySet(
                key=policy.key,
                version=str(version.version),
                rules=tuple(rules),
                scope="project" if policy.project_id else "organization",
            )
        )
    return sets


def _mission_policy_set(mission: Mission) -> PolicySet | None:
    """``mission.approval_policy.require_approval_for`` as require_approval rules."""
    policy = mission.approval_policy or {}
    actions = policy.get("require_approval_for") or []
    permission = policy.get("approver_permission")
    rules: list[Rule] = []
    seen: set[str] = set()
    for action in actions if isinstance(actions, list) else []:
        if not isinstance(action, str) or action in seen:
            continue
        seen.add(action)
        doc: dict[str, Any] = {
            "id": f"mission.require_approval.{action.replace('*', 'all')}"[:80],
            "action": action,
            "effect": "require_approval",
            "reason": "The mission's approval policy requires human approval for this action",
            "priority": 500,
        }
        if isinstance(permission, str) and permission:
            doc["approver_permission"] = permission
        for candidate in (doc, {k: v for k, v in doc.items() if k != "approver_permission"}):
            try:
                rules.append(Rule.model_validate(candidate))
                break
            except ValueError:  # unknown action or malformed permission: fall back / skip
                continue
    if not rules:
        return None
    return PolicySet(key=f"mission:{mission.id}", version=str(mission.version), rules=tuple(rules), scope="mission")


def _safe_level(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return autonomy_engine.normalize_level(value)
    except ValueError:
        return None


def _ceiling(org_level: str | None, project_level: str | None) -> str:
    org = _safe_level(org_level) or AutonomyLevel.L0_ASSISTED.value  # unknown ceiling → least autonomy
    project = _safe_level(project_level) if project_level else None
    return autonomy_engine.effective_ceiling(org, project)


def egress_allowlist(org_entries: list[str] | None) -> list[str]:
    hosts = {h.strip().lower() for h in (org_entries or []) if isinstance(h, str) and h.strip()}
    hosts |= set(get_settings().tool_egress_allowlist_hosts)
    return sorted(hosts)


@dataclass(frozen=True)
class _Evaluation:
    decision: Decision
    context: dict[str, Any]
    mission_id: uuid.UUID | None
    project_id: uuid.UUID | None
    workspace_id: uuid.UUID | None


def _build_context(
    db: Session,
    actor: Actor,
    action: str,
    context: Mapping[str, Any],
    mission: Mission | None,
    project: Project | None,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    org = get_org_settings(db, actor.organization_id)
    supplied = dict(context)
    derived: dict[str, Any] = {}
    if mission is not None:
        derived["risk_level"] = mission.risk_level
        derived["mission_status"] = mission.status
    if project is not None:
        derived["project_visibility"] = project.visibility

    ceiling = _ceiling(org.max_autonomy_level, project.max_autonomy_level if project else None)
    candidates = [
        _safe_level(mission.autonomy_level) if mission is not None else None,
        _safe_level(actor.autonomy_level),
        _safe_level(supplied.get("autonomy_level")),
    ]
    levels = [level for level in candidates if level is not None]
    effective: str | None = None
    if levels:
        lowest = min(levels, key=lambda level: AUTONOMY_RANK[level])
        effective = autonomy_engine.clamp(lowest, ceiling)

    authoritative: dict[str, Any] = {
        "actor_kind": actor.kind,
        "is_human": actor.is_human,
        "autonomy_level": effective,
        "org_egress_allowlist": egress_allowlist(org.egress_allowlist),
        "data_processing_consent": bool((org.data_processing or {}).get("allow_external_llm", False)),
    }
    if action == "mission.autonomy_change" or "requested_autonomy_level" in supplied:
        authoritative["ceiling_rank"] = AUTONOMY_RANK[ceiling]
        requested = supplied.get("requested_autonomy_level")
        if requested is not None:
            level = _safe_level(requested)
            # An unknown requested level can never be within the ceiling (fail closed).
            authoritative["requested_rank"] = AUTONOMY_RANK[level] if level else len(AUTONOMY_RANK) + 1
        if mission is not None and _safe_level(mission.autonomy_level):
            authoritative["current_rank"] = AUTONOMY_RANK[autonomy_engine.normalize_level(mission.autonomy_level)]

    if (
        mission is not None
        and supplied.get("estimated_cost_usd") is not None
        and supplied.get("budget_remaining_usd") is None
    ):
        from aegis_api.lab.governance.budgets import check_budget

        remaining = check_budget(db, mission, "total").remaining_usd
        if remaining is not None:
            derived["budget_remaining_usd"] = remaining

    if dry_run:
        # Simulation: the caller may override anything to explore "what if" scenarios.
        merged = {**derived, **{k: v for k, v in authoritative.items() if v is not None}, **supplied}
    else:
        merged = {**derived, **supplied, **authoritative}
    return {k: v for k, v in merged.items() if v is not None}


def _evaluate(
    db: Session,
    actor: Actor,
    action: str,
    context: Mapping[str, Any] | None,
    *,
    project_id: uuid.UUID | str | None,
    mission: Mission | None,
    dry_run: bool,
) -> _Evaluation:
    if not is_valid_action_name(action):
        raise ValidationFailed(f"Invalid policy action '{action}'")
    if mission is not None and mission.organization_id != actor.organization_id:
        raise NotFound("Mission not found")
    pid = _uuid(project_id, "Project")
    if mission is not None:
        if pid is not None and pid != mission.project_id:
            raise ValidationFailed("project_id does not match the mission's project")
        pid = mission.project_id
    project: Project | None = None
    if pid is not None:
        project = db.get(Project, pid)
        if project is None or project.organization_id != actor.organization_id:
            raise NotFound("Project not found")
    ctx = _build_context(db, actor, action, context or {}, mission, project, dry_run=dry_run)
    sets = active_policy_sets(db, actor.organization_id, pid)
    if mission is not None and (mission_set := _mission_policy_set(mission)) is not None:
        sets.append(mission_set)
    decision = engine_evaluate(action, ctx, BASELINE, sets)
    return _Evaluation(
        decision=decision,
        context=ctx,
        mission_id=mission.id if mission is not None else None,
        project_id=pid,
        workspace_id=project.workspace_id if project is not None else None,
    )


def _record_denial(db: Session, actor: Actor, evaluation: _Evaluation) -> None:
    decision = evaluation.decision
    summary = {
        k: (v[:200] if isinstance(v, str) else v) for k, v in evaluation.context.items() if k in _EVENT_CONTEXT_KEYS
    }
    payload = jsonable(
        {
            "action": decision.action,
            "effect": decision.effect,
            "reasons": list(decision.reasons),
            "matched_rules": list(decision.matched_rules),
            "policy_versions": list(decision.policy_versions),
            "context": summary,
        }
    )
    org_id = actor.organization_id
    mission_id, project_id, workspace_id = evaluation.mission_id, evaluation.project_id, evaluation.workspace_id

    def write(session: Session) -> None:
        emit(
            session,
            organization_id=org_id,
            type=EventType.POLICY_DENIED,
            payload=payload,
            mission_id=mission_id,
            project_id=project_id,
            workspace_id=workspace_id,
            subject_type="policy_action",
            subject_id=decision.action,
            actor=actor,
        )
        if actor.kind in ("agent", "workflow"):
            audit(session, actor, "POLICY_DENIED", "policy_action", decision.action, after=payload)

    write(db)
    # The caller normally raises PolicyDenied next, rolling back this transaction: keep the record.
    on_rollback(db, org_id, write, user_id=actor.user_id, name="policy_denied")


def evaluate_policy(
    db: Session,
    actor: Actor,
    action: str,
    context: Mapping[str, Any] | None = None,
    *,
    project_id: uuid.UUID | str | None = None,
    mission: Mission | None = None,
    dry_run: bool = False,
) -> PolicyDecision:
    """Evaluate ``action`` for ``actor``: baseline → organization/project policies → mission approval policy.

    Returns the decision; it never raises for ``deny``/``require_approval`` (callers raise ``PolicyDenied`` or
    request an approval). Denials emit ``POLICY_DENIED`` (and an audit row for agent/workflow actors), kept
    even when the caller's transaction rolls back. ``dry_run`` evaluates without side effects and lets the
    context override derived facts (simulation).
    """
    evaluation = _evaluate(db, actor, action, context, project_id=project_id, mission=mission, dry_run=dry_run)
    if not dry_run:
        metrics.POLICY_DECISIONS.labels(_action_label(action), evaluation.decision.effect).inc()
        if evaluation.decision.denied:
            _record_denial(db, actor, evaluation)
    return evaluation.decision


def dry_run_policy(
    db: Session,
    actor: Actor,
    action: str,
    context: Mapping[str, Any] | None,
    *,
    project_id: str | None = None,
    mission_id: str | None = None,
) -> tuple[Decision, dict[str, Any]]:
    """Evaluate without side effects; returns the decision and the context that was evaluated."""
    mission: Mission | None = None
    if mission_id:
        mission = get_owned(db, Mission, mission_id, actor, label="Mission")
        load_project(db, actor, mission.project_id, "mission:read")
    if project_id:
        load_project(db, actor, project_id)
    evaluation = _evaluate(db, actor, action, context, project_id=project_id, mission=mission, dry_run=True)
    return evaluation.decision, jsonable(evaluation.context)
