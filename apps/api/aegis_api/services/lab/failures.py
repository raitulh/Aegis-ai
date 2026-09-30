"""Failure intelligence: deterministic classification, recurrence tracking, recovery and lessons.

The classification (type, rule, root cause, signature) is always deterministic (``FailureClassifier``). A
FailureAnalyzer agent may *assist* with a diagnosis; its output is stored separately as a model-assisted
diagnosis and never replaces the rule-based classification. Lessons learned are proposed to project memory
and, being agent/workflow-originated durable memory, require human review before they become active.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models.lab import Failure, Lesson
from aegis_api.security.context import Principal
from aegis_api.services.lab import events, evidence, graph
from aegis_api.services.lab import memory as memory_service
from aegis_api.services.lab.access import accessible_project_ids, get_scoped
from aegis_api.services.lab.common import Actor
from engines.lab.enums import FailureType, LabEventType
from engines.lab.failures.classifier import FailureClassifier, FailureSignal
from engines.lab.failures.recovery import RecoveryLimits, extract_lesson, propose_recovery


def analyze(
    organization_id: uuid.UUID,
    *,
    signal: dict[str, Any],
    actor: Actor,
    project_id: uuid.UUID | None,
    mission_id: uuid.UUID | None = None,
    experiment_id: uuid.UUID | None = None,
    experiment_run_id: uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
    strategy_version_id: uuid.UUID | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fs = FailureSignal.model_validate({k: v for k, v in signal.items() if k in FailureSignal.model_fields})
    classification = FailureClassifier().classify(fs)
    settings = get_settings()
    limits = RecoveryLimits(
        max_timeout_seconds=settings.execution_max_timeout_seconds, max_memory_mb=settings.execution_max_memory_mb
    )
    actions = propose_recovery(classification, spec or {}, limits)
    with session_scope(organization_id) as db:
        similar = db.execute(
            select(Failure.id, Failure.recovery_status)
            .where(Failure.organization_id == organization_id, Failure.signature == classification.signature)
            .order_by(Failure.detected_at.desc())
            .limit(10)
        ).all()
        recurrence = 1 + int(
            db.scalar(
                select(func.count(Failure.id)).where(
                    Failure.organization_id == organization_id, Failure.signature == classification.signature
                )
            )
            or 0
        )
        failure = Failure(
            organization_id=organization_id,
            project_id=project_id,
            mission_id=mission_id,
            experiment_id=experiment_id,
            experiment_run_id=experiment_run_id,
            agent_run_id=agent_run_id,
            strategy_version_id=strategy_version_id,
            stage=fs.stage,
            failure_type=str(classification.failure_type),
            rule_id=classification.rule_id,
            confidence=classification.confidence,
            root_cause=classification.root_cause,
            evidence_lines=classification.evidence_lines,
            traceback=(fs.stderr_tail or "")[-8000:] or None,
            signature=classification.signature,
            recurrence_count=recurrence,
            detected_at=utcnow(),
            signal={k: v for k, v in fs.model_dump().items() if k not in ("stdout_tail",)},
            diagnosis={},
            similar_failure_ids=[str(s.id) for s in similar],
            recovery_actions=[a.to_dict() for a in actions],
            recovery_status="proposed" if actions else "none",
        )
        db.add(failure)
        db.flush()
        record = evidence.seal(
            db,
            organization_id=organization_id,
            mission_id=mission_id,
            project_id=project_id,
            kind="failure_analysis",
            title=f"{classification.failure_type}: {classification.root_cause}"[:300],
            content={
                "failure_id": str(failure.id),
                "classification": classification.to_dict(),
                "recurrence": recurrence,
                "recovery_actions": [a.to_dict() for a in actions],
            },
            confidence=classification.confidence,
        )
        failure.evidence_id = record.id
        if experiment_id and project_id:
            graph.link_refs(
                db,
                organization_id=organization_id,
                project_id=project_id,
                source=("experiment", str(experiment_id), "experiment"),
                target=(
                    "failure",
                    str(failure.id),
                    f"{classification.failure_type}: {classification.root_cause[:120]}",
                ),
                relation="failed_because_of",
            )
        if mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=mission_id,
                project_id=project_id,
                event_type=LabEventType.FAILURE_ANALYZED,
                message=f"Failure classified as {classification.failure_type} ({classification.rule_id}); "
                f"recurrence {recurrence}; {len(actions)} recovery action(s) proposed",
                data={
                    "failure_id": str(failure.id),
                    "failure_type": str(classification.failure_type),
                    "signature": classification.signature,
                    "recovery": [a.kind for a in actions],
                },
                actor=actor,
            )
        return {
            "failure_id": str(failure.id),
            "classification": classification.to_dict(),
            "recurrence": recurrence,
            "recovery_actions": [a.to_dict() for a in actions],
            "similar_failure_ids": [str(s.id) for s in similar],
        }


def attach_diagnosis(
    organization_id: uuid.UUID, failure_id: uuid.UUID, diagnosis: dict[str, Any], agent_run_id: str
) -> None:
    with session_scope(organization_id) as db:
        failure = db.get(Failure, failure_id)
        if failure is None:
            return
        failure.diagnosis = {"source": "model_assisted", "agent_run_id": agent_run_id, **diagnosis}


def record_lesson(
    organization_id: uuid.UUID,
    failure_id: uuid.UUID,
    *,
    actor: Actor,
    recovery_kind: str | None,
    resolved: bool | None,
    context: str,
) -> dict[str, Any]:
    from engines.lab.failures.classifier import FailureClassification
    from engines.lab.failures.recovery import RecoveryAction

    with session_scope(organization_id) as db:
        failure = db.get(Failure, failure_id)
        if failure is None:
            raise NotFound("Failure not found")
        classification = FailureClassification(
            failure_type=FailureType(failure.failure_type),
            confidence=failure.confidence,
            rule_id=failure.rule_id,
            root_cause=failure.root_cause,
            evidence_lines=list(failure.evidence_lines or []),
            signature=failure.signature,
        )
        action = next(
            (
                RecoveryAction(kind=a["kind"], description=a.get("description", ""))
                for a in failure.recovery_actions or []
                if a.get("kind") == recovery_kind
            ),
            None,
        )
        text = extract_lesson(classification, context=context, recovery=action, resolved=resolved)
        kind = recovery_kind or "none"
        lesson = db.scalar(
            select(Lesson).where(
                Lesson.organization_id == organization_id,
                Lesson.signature == failure.signature,
                Lesson.recovery_kind == kind,
            )
        )
        if lesson is None:
            lesson = Lesson(
                organization_id=organization_id,
                project_id=failure.project_id,
                signature=failure.signature,
                failure_type=failure.failure_type,
                lesson=text,
                context=context[:4000],
                recovery_kind=kind,
                resolved=resolved,
                confidence=failure.confidence,
                occurrences=1,
            )
            db.add(lesson)
            db.flush()
        else:
            lesson.occurrences += 1
            lesson.resolved = resolved if resolved is not None else lesson.resolved
            lesson.lesson = text
        failure.lesson_id = lesson.id
        if resolved:
            failure.recovery_status = "resolved"
            failure.resolved_at = utcnow()
        memory_id = None
        if failure.project_id is not None:
            mem = memory_service.propose(
                db,
                organization_id=organization_id,
                actor=actor,
                scope="project",
                category="failure",
                content=text,
                source="failure_analysis",
                source_ref={"failure_id": str(failure.id), "lesson_id": str(lesson.id)},
                title=f"Lesson: {failure.failure_type}",
                project_id=failure.project_id,
                confidence=failure.confidence,
                provenance={"signature": failure.signature, "rule_id": failure.rule_id},
            )
            lesson.memory_id = mem.id
            memory_id = str(mem.id)
            memory_service.link(db, mem, target_type="failure", target_id=failure.id, relation="derived_from")
        return {"lesson_id": str(lesson.id), "lesson": text, "memory_id": memory_id}


def list_failures(
    db: Session,
    principal: Principal,
    *,
    mission_id: uuid.UUID | None = None,
    failure_type: str | None = None,
    project_id: uuid.UUID | None = None,
) -> Select[Failure]:
    stmt = select(Failure).where(Failure.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Failure.project_id.in_(visible))
    if mission_id:
        stmt = stmt.where(Failure.mission_id == mission_id)
    if project_id:
        stmt = stmt.where(Failure.project_id == project_id)
    if failure_type:
        stmt = stmt.where(Failure.failure_type == failure_type)
    return stmt.order_by(Failure.detected_at.desc())


def list_lessons(db: Session, principal: Principal, *, project_id: uuid.UUID | None = None) -> Select[Lesson]:
    stmt = select(Lesson).where(Lesson.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where((Lesson.project_id.is_(None)) | (Lesson.project_id.in_(visible)))
    if project_id:
        stmt = stmt.where(Lesson.project_id == project_id)
    return stmt.order_by(Lesson.occurrences.desc(), Lesson.created_at.desc())


def set_recovery_status(db: Session, principal: Principal, failure_id: uuid.UUID | str, status: str) -> Failure:
    if status not in ("proposed", "applied", "resolved", "wont_fix"):
        raise ValidationFailed("invalid recovery status")
    failure = get_scoped(db, principal, Failure, failure_id, label="Failure")
    failure.recovery_status = status
    if status == "resolved":
        failure.resolved_at = utcnow()
    return failure


def apply_recovery_patch(spec: dict[str, Any], action: dict[str, Any]) -> dict[str, Any] | None:
    """Apply an auto-applicable recovery's spec patch (bounded by recovery limits) → new spec, or None."""
    patch = action.get("spec_patch") or {}
    if not action.get("auto_applicable") or not patch:
        return None
    out = {**spec}
    for path, value in patch.items():
        parts = path.split(".")
        node = out
        for p in parts[:-1]:
            node[p] = dict(node.get(p) or {})
            node = node[p]
        node[parts[-1]] = value
    return out
