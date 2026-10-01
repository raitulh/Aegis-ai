"""Continuous assurance: scheduled and change-triggered audits, risk-based test selection, baselines and
regression detection.

Flow::

    change (CI/CD webhook, SDK, system configuration change) or schedule tick
      → risk-based selection of categories for that change type
      → audit (entitlement + quota checked) → results
      → comparison with the previous audit and the known-good baseline → regression signal + webhook
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope
from aegis_api.errors import AppError, NotFound, ValidationFailed
from aegis_api.models import AISystem, AssuranceSchedule, AssuranceTrigger, Audit
from aegis_api.security.context import Principal
from aegis_api.security.rbac import permissions_for_role
from aegis_api.services import audit_log, entitlements

log = structlog.get_logger("aegis.assurance")

EVENT_TYPES = (
    "deployment",
    "pull_request",
    "model_change",
    "prompt_change",
    "policy_change",
    "tool_change",
    "agent_version",
    "config_change",
    "schedule",
    "manual",
)
AGENT_TYPES = ("agent", "multi_agent")
BASE_CATEGORIES = ["safety", "privacy", "prompt_injection", "hallucination"]
# Risk-based selection: which categories a given kind of change can plausibly affect.
SELECTION: dict[str, tuple[list[str], str]] = {
    "model_change": (
        ["fairness", "hallucination", "safety", "privacy", "prompt_injection", "jailbreak", "agent_action"],
        "A model change can shift behaviour in every dimension: full category coverage.",
    ),
    "prompt_change": (
        ["safety", "prompt_injection", "jailbreak", "hallucination", "policy"],
        "Prompt changes most often affect refusal behaviour, injection resistance and grounding.",
    ),
    "tool_change": (
        ["agent_action", "tool_abuse", "prompt_injection", "privacy"],
        "Tool changes affect what an agent can do: action authorization, tool abuse and data exposure.",
    ),
    "policy_change": (["policy", "agent_action", "privacy"], "Policy changes are verified against their controls."),
    "config_change": (
        ["agent_action", "safety", "privacy", "prompt_injection"],
        "Guardrail or configuration changes are re-checked across the controls they govern.",
    ),
    "agent_version": (
        ["agent_action", "tool_abuse", "prompt_injection", "safety", "privacy"],
        "A new agent version re-runs agent-behaviour and safety coverage.",
    ),
    "deployment": ([*BASE_CATEGORIES, "agent_action"], "Deployments run the standard regression coverage."),
    "pull_request": (BASE_CATEGORIES, "Pull requests run a quick pre-merge regression pass."),
}


def select_categories(system: AISystem, event_type: str, configured: list[str] | None) -> tuple[list[str], str]:
    chosen, reason = SELECTION.get(event_type, (BASE_CATEGORIES, "Standard regression coverage."))
    categories = list(dict.fromkeys(configured or chosen))
    if system.system_type not in AGENT_TYPES:
        categories = [c for c in categories if c not in ("agent_action", "tool_abuse")]
    if not categories:
        categories = ["safety", "privacy"]
    return categories, reason if not configured else "Categories configured on the assurance schedule."


def _system(session: Session, organization_id: uuid.UUID, system_id: str | uuid.UUID) -> AISystem:
    system = session.get(AISystem, uuid.UUID(str(system_id)))
    if system is None or system.organization_id != organization_id or system.deleted_at is not None:
        raise NotFound("AI system not found")
    return system


def create_schedule(session: Session, principal: Principal, data: Any) -> AssuranceSchedule:
    entitlements.require_feature(session, principal.organization_id, "continuous_assurance")
    system = _system(session, principal.organization_id, data.system_id)
    if data.interval_hours is None and not data.trigger_on:
        raise ValidationFailed("Set an interval, change triggers, or both")
    bad = [t for t in data.trigger_on if t not in EVENT_TYPES]
    if bad:
        raise ValidationFailed(f"Unknown trigger types: {', '.join(bad)}")
    schedule = AssuranceSchedule(
        organization_id=principal.organization_id,
        system_id=system.id,
        name=data.name or f"Continuous assurance — {system.name}",
        enabled=True,
        interval_hours=data.interval_hours,
        trigger_on=list(dict.fromkeys(data.trigger_on)),
        categories=data.categories,
        intensity=data.intensity,
        policy_version_ids=data.policy_version_ids,
        next_run_at=(utcnow() + timedelta(hours=data.interval_hours)) if data.interval_hours else None,
        created_by_id=principal.user_id if principal.auth_method != "api_key" else None,
    )
    session.add(schedule)
    session.flush()
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="assurance.schedule_created",
        resource_type="assurance_schedule",
        resource_id=schedule.id,
        principal=principal,
        after={"system_id": str(system.id), "interval_hours": data.interval_hours, "trigger_on": schedule.trigger_on},
    )
    return schedule


def trigger(
    session: Session,
    principal: Principal,
    *,
    system_id: str,
    event_type: str,
    ref: str,
    metadata: dict[str, Any] | None = None,
    source: str = "api",
    schedule: AssuranceSchedule | None = None,
) -> AssuranceTrigger:
    """Record a change event and start the audit it calls for. Idempotent per (system, event_type, ref)."""
    from aegis_api.schemas.audits import AuditCreate
    from aegis_api.services import audit_service

    if event_type not in EVENT_TYPES:
        raise ValidationFailed(f"event_type must be one of {', '.join(EVENT_TYPES)}")
    system = _system(session, principal.organization_id, system_id)
    existing = session.scalar(
        select(AssuranceTrigger).where(
            AssuranceTrigger.organization_id == principal.organization_id,
            AssuranceTrigger.system_id == system.id,
            AssuranceTrigger.event_type == event_type,
            AssuranceTrigger.ref == ref[:200],
        )
    )
    if existing is not None:
        return existing
    entitlements.require_feature(session, principal.organization_id, "continuous_assurance")
    if schedule is None:
        schedule = session.scalar(
            select(AssuranceSchedule).where(
                AssuranceSchedule.system_id == system.id, AssuranceSchedule.enabled.is_(True)
            )
        )
    categories, reason = select_categories(system, event_type, schedule.categories if schedule else None)
    inserted = session.execute(
        insert(AssuranceTrigger)
        .values(
            id=uuid.uuid4(),
            organization_id=principal.organization_id,
            system_id=system.id,
            schedule_id=schedule.id if schedule else None,
            event_type=event_type,
            ref=ref[:200],
            source=source,
            meta=metadata or {},
            categories=categories,
            selection_reason=reason,
        )
        .on_conflict_do_nothing(constraint="uq_assurance_triggers_ref")
        .returning(AssuranceTrigger.id)
    ).scalar()
    record = session.get(AssuranceTrigger, inserted) if inserted else None
    if record is None:  # a concurrent identical trigger won the race
        return session.scalars(
            select(AssuranceTrigger).where(
                AssuranceTrigger.organization_id == principal.organization_id,
                AssuranceTrigger.system_id == system.id,
                AssuranceTrigger.event_type == event_type,
                AssuranceTrigger.ref == ref[:200],
            )
        ).one()
    audit = audit_service.create_audit(
        session,
        principal,
        AuditCreate.model_validate(
            {
                "system_id": str(system.id),
                "name": f"{system.name} — {event_type.replace('_', ' ')} {ref[:40]}",
                "categories": categories,
                "policy_version_ids": schedule.policy_version_ids if schedule else [],
                "intensity": schedule.intensity if schedule else "quick",
                "config": {"seed": 1337},
            }
        ),
        start=True,
    )
    audit.config = {
        **(audit.config or {}),
        "assurance": {"trigger_id": str(record.id), "event_type": event_type, "ref": ref[:200]},
    }
    record.audit_id = audit.id
    if schedule is not None:
        schedule.last_run_at = utcnow()
        schedule.last_audit_id = audit.id
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="assurance.triggered",
        resource_type="assurance_trigger",
        resource_id=record.id,
        principal=principal,
        after={"event_type": event_type, "ref": ref[:200], "audit_id": str(audit.id), "categories": categories},
    )
    return record


def maybe_trigger_on_change(session: Session, principal: Principal, system: AISystem, changed: list[str]) -> None:
    """Called after a system configuration change: start an audit if a schedule opted into that change."""
    mapping = {
        "model_name": "model_change",
        "model_version": "model_change",
        "system_instructions": "prompt_change",
        "prompt_version": "prompt_change",
        "config": "config_change",
    }
    kinds = list(dict.fromkeys(mapping[f] for f in changed if f in mapping))
    if not kinds:
        return
    schedule = session.scalar(
        select(AssuranceSchedule).where(AssuranceSchedule.system_id == system.id, AssuranceSchedule.enabled.is_(True))
    )
    if schedule is None:
        return
    kind = next((k for k in kinds if k in schedule.trigger_on), None)
    if kind is None:
        return
    try:
        trigger(
            session,
            principal,
            system_id=str(system.id),
            event_type=kind,
            ref=f"{system.version}",
            metadata={"changed_fields": changed},
            source="system_change",
            schedule=schedule,
        )
    except AppError as exc:  # quota or plan: the change itself must still succeed
        log.info("assurance_change_trigger_skipped", system_id=str(system.id), reason=exc.code)


def _service_principal(organization_id: uuid.UUID, user_id: uuid.UUID | None) -> Principal:
    return Principal(
        user_id=user_id or uuid.UUID(int=0),
        organization_id=organization_id,
        role="service_account",
        permissions=permissions_for_role("service_account"),
        auth_method="api_key",
        display_name="Continuous assurance scheduler",
    )


def run_due_schedules(limit: int = 20) -> int:
    """Maintenance task: start audits for schedules whose interval has elapsed."""
    started = 0
    with admin_session_scope() as session:
        due = session.scalars(
            select(AssuranceSchedule)
            .where(
                AssuranceSchedule.enabled.is_(True),
                AssuranceSchedule.interval_hours.is_not(None),
                AssuranceSchedule.next_run_at <= utcnow(),
            )
            .order_by(AssuranceSchedule.next_run_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
        for schedule in due:
            tick_ref = f"schedule:{schedule.id}:{(schedule.next_run_at or utcnow()).isoformat()}"
            schedule.next_run_at = utcnow() + timedelta(hours=schedule.interval_hours or 24)
            principal = _service_principal(schedule.organization_id, schedule.created_by_id)
            try:
                with session.begin_nested():
                    trigger(
                        session,
                        principal,
                        system_id=str(schedule.system_id),
                        event_type="schedule",
                        ref=tick_ref,
                        source="schedule",
                        schedule=schedule,
                    )
                started += 1
            except AppError as exc:
                log.info("assurance_schedule_skipped", schedule_id=str(schedule.id), reason=exc.code)
    return started


def regression_report(session: Session, audit: Audit) -> dict[str, Any]:
    """Compare an audit with the previous completed audit of the same system and with the known-good
    baseline. A regression is a new high/critical finding or a dimension score drop of more than 5 points."""
    from aegis_api.services import audit_service

    previous = session.scalar(
        select(Audit)
        .where(
            Audit.system_id == audit.system_id,
            Audit.id != audit.id,
            Audit.status.in_(["completed", "partially_completed"]),
            Audit.created_at < audit.created_at,
        )
        .order_by(Audit.created_at.desc())
        .limit(1)
    )
    system = session.get(AISystem, audit.system_id)
    baseline = session.get(Audit, system.baseline_audit_id) if system and system.baseline_audit_id else None
    out: dict[str, Any] = {"audit_id": str(audit.id), "comparisons": {}}
    regression = False
    for label, other in (("previous", previous), ("baseline", baseline)):
        if other is None or other.id == audit.id:
            continue
        diff = audit_service.compare_audits(session, other, audit)
        score_drops = {
            dim: round(float(other.summary.get("dimensions", {}).get(dim, 0)) - float(score), 1)
            for dim, score in (audit.summary or {}).get("dimensions", {}).items()
            if dim in (other.summary or {}).get("dimensions", {})
            and float(other.summary["dimensions"][dim]) - float(score) > 5
        }
        severe_new = [f for f in diff["new_findings"] if f["severity"] in ("high", "critical")]
        is_regression = bool(severe_new or diff["regressions"] or score_drops)
        regression = regression or is_regression
        out["comparisons"][label] = {
            "audit_id": str(other.id),
            "new_findings": len(diff["new_findings"]),
            "resolved_findings": len(diff["resolved_findings"]),
            "severity_regressions": len(diff["regressions"]),
            "severe_new_findings": severe_new,
            "score_drops": score_drops,
            "regression": is_regression,
        }
    out["regression"] = regression
    return out


def set_baseline(session: Session, principal: Principal, system: AISystem, audit: Audit) -> AISystem:
    if audit.system_id != system.id or audit.status not in ("completed", "partially_completed"):
        raise ValidationFailed("The baseline must be a completed audit of this system")
    before = str(system.baseline_audit_id) if system.baseline_audit_id else None
    system.baseline_audit_id = audit.id
    audit_log.record(
        session,
        organization_id=system.organization_id,
        action="assurance.baseline_set",
        resource_type="ai_system",
        resource_id=system.id,
        principal=principal,
        before={"baseline_audit_id": before},
        after={"baseline_audit_id": str(audit.id)},
    )
    return system
