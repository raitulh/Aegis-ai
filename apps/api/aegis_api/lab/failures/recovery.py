"""Recovery of diagnosed failures: apply a bounded spec patch as a NEW experiment version, test it, record the
outcome and let the lesson learn from it.

* :func:`apply_recovery` — a human with ``failure:write`` or an automated actor whose (mission) autonomy is at least
  ``L3_AUTOMATED_EXECUTION``. Policy failures and hypothesis failures (legitimate negative results) are never
  recovered. The patch (rule proposal, or the model-suggested one) is re-checked by the recovery guardrails and the
  merged spec must still be a valid :class:`~engines.lab.experiment_spec.ExperimentSpec`. Proposals that require
  approval (and model patches applied by automated actors) request a human approval (action
  ``experiment.recovery``) instead; the approval hook applies the recovery once a human approves. Otherwise the
  experiments service creates the new immutable, re-validated version (``create_version``) and the failure moves
  to ``RECOVERING`` (recovery status ``testing``).
* :func:`record_recovery_outcome` / :func:`assess_recovery` — the outcome (explicit, or assessed deterministically
  from the runs of the recovery version) moves the failure to ``RESOLVED`` or back to ``DIAGNOSED``; the lesson's
  success count and confidence are updated with :func:`engines.lab.failures.extract_lesson`, and a PROPOSED lesson
  becomes ``ACTIVE`` after two successful recoveries.
"""

from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.access import get_owned
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import audit
from aegis_api.lab.core.errors import PolicyDenied
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.failures import service
from aegis_api.lab.governance.approvals import is_approved, register_approval_hook, request_approval
from aegis_api.lab.models import Approval, Experiment, ExperimentRun, ExperimentVersion, Failure, Lesson, Mission
from engines.lab.experiment_spec import ExperimentSpec
from engines.lab.failures import RecoveryProposal, extract_lesson, merge_patch
from engines.lab.states import (
    ApprovalStatus,
    AutonomyLevel,
    ExecutionStatus,
    FailureStatus,
    assert_transition,
    autonomy_rank,
)

log = structlog.get_logger("aegis.lab.failures.recovery")

RECOVERY_APPROVAL_ACTION = "experiment.recovery"
RECOVERY_APPLIED = "FAILURE_RECOVERY_APPLIED"
RECOVERY_OUTCOME = "FAILURE_RECOVERY_OUTCOME"
MIN_AUTOMATED_AUTONOMY = AutonomyLevel.L3_AUTOMATED_EXECUTION
_RUN_TERMINAL = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.VERIFICATION_PENDING,
        ExecutionStatus.VERIFIED,
    }
)
_RUN_SUCCESS = frozenset({ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFICATION_PENDING, ExecutionStatus.VERIFIED})

RecoverySource = Literal["rule", "model"]


@dataclass
class RecoveryResult:
    status: Literal["applied", "approval_required", "already_applied"]
    failure: Failure
    experiment_id: uuid.UUID | None = None
    new_version_id: uuid.UUID | None = None
    approval_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failure_id": str(self.failure.id),
            "failure_status": self.failure.status,
            "recovery_status": self.failure.recovery_status,
            "experiment_id": str(self.experiment_id) if self.experiment_id else None,
            "new_version_id": str(self.new_version_id) if self.new_version_id else None,
            "approval_id": str(self.approval_id) if self.approval_id else None,
        }


def create_experiment_version(db: Session, actor: Actor, experiment_id: uuid.UUID, spec: ExperimentSpec) -> Any:
    """Create a new immutable, re-validated experiment version through the experiments service."""
    try:
        module = importlib.import_module("aegis_api.lab.experiments.service")
    except ModuleNotFoundError as exc:
        if exc.name not in ("aegis_api.lab.experiments.service", "aegis_api.lab.experiments"):
            raise
        raise ServiceUnavailable(
            "The experiments service is not available; the recovery cannot create a new version",
            code="experiments_unavailable",
        ) from exc
    create_version = getattr(module, "create_version", None)
    if create_version is None:
        raise ServiceUnavailable(
            "The experiments service does not expose create_version", code="experiments_unavailable"
        )
    return create_version(db, actor, experiment_id, spec)


def _uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except ValueError:
        return None


def _authorize(db: Session, actor: Actor, failure: Failure) -> str | None:
    """Humans (with ``failure:write``, already checked) may recover; automated actors need autonomy ≥ L3.

    Returns the effective autonomy level used for automated actors (``None`` for humans).
    """
    if actor.is_human:
        return None
    levels = [actor.autonomy_level] if actor.autonomy_level else []
    if failure.mission_id is not None:
        mission = db.get(Mission, failure.mission_id)
        if mission is not None and mission.organization_id == actor.organization_id:
            levels.append(mission.autonomy_level)
    valid = [lvl for lvl in levels if lvl in AutonomyLevel.__members__.values()]
    if not valid:
        raise PolicyDenied(
            "Automated recovery requires a mission with autonomy ≥ L3_AUTOMATED_EXECUTION (or a human with "
            "failure:write)"
        )
    effective = min(valid, key=autonomy_rank)
    if autonomy_rank(effective) < autonomy_rank(MIN_AUTOMATED_AUTONOMY):
        raise PolicyDenied(
            f"Automated recovery requires autonomy ≥ {MIN_AUTOMATED_AUTONOMY.value}; the effective level is {effective}"
        )
    return effective


def _proposal_for(failure: Failure, source: RecoverySource) -> dict[str, Any]:
    action = dict(failure.recovery_action or {})
    if source == "model":
        model = action.get("model_proposal")
        if not isinstance(model, dict) or not model.get("accepted"):
            raise Conflict(
                "There is no guardrail-accepted model recovery proposal for this failure", code="no_recovery_patch"
            )
        return model
    return action


def apply_recovery(
    db: Session, actor: Actor, failure_id: uuid.UUID | str, *, source: RecoverySource = "rule"
) -> RecoveryResult:
    """Apply the failure's recovery patch as a new experiment version (or request the required approval)."""
    failure = service.get_failure(db, actor, failure_id, permission="failure:write")
    advisory_xact_lock(db, f"failure-recovery:{failure.id}")
    db.refresh(failure)
    action = dict(failure.recovery_action or {})
    if failure.status == FailureStatus.RECOVERING and action.get("new_version_id"):
        return RecoveryResult(
            "already_applied",
            failure,
            experiment_id=failure.recovery_experiment_id,
            new_version_id=_uuid(action.get("new_version_id")),
        )
    if failure.failure_type in service.NO_AUTO_RECOVERY_TYPES:
        raise Conflict(
            "Policy failures and legitimate negative results are never recovered automatically",
            code="recovery_not_applicable",
        )
    proposal = _proposal_for(failure, source)
    patch = proposal.get("spec_patch")
    if not isinstance(patch, dict) or not patch:
        raise Conflict(
            f"The recovery proposal '{proposal.get('action')}' has no automatic patch; it needs manual work",
            code="no_recovery_patch",
        )
    effective_autonomy = _authorize(db, actor, failure)
    assert_transition("failure", failure.status, FailureStatus.RECOVERING)
    run = db.get(ExperimentRun, failure.experiment_run_id) if failure.experiment_run_id else None
    experiment_id = failure.experiment_id or (run.experiment_id if run is not None else None)
    if experiment_id is None:
        raise Conflict("The failure is not linked to an experiment to recover", code="recovery_needs_experiment")
    experiment = get_owned(db, Experiment, experiment_id, actor, label="Experiment")
    if experiment.current_version_id is None:
        raise Conflict("The experiment has no version to patch", code="experiment_has_no_version")
    version = db.get(ExperimentVersion, experiment.current_version_id)
    if version is None:
        raise NotFound("Experiment version not found")
    current = service.normalized_spec(version.spec)
    limits = service.recovery_limits(db, failure.organization_id)
    violations = service.validate_recovery_patch(patch, current, limits)
    if violations:
        raise ValidationFailed(
            "The recovery patch violates the recovery guardrails",
            code="recovery_patch_rejected",
            details=[{"message": v} for v in violations],
        )
    try:
        new_spec = ExperimentSpec.model_validate(merge_patch(current, patch))
    except PydanticValidationError as exc:
        raise ValidationFailed(
            "The patched experiment specification is invalid",
            code="recovery_patch_rejected",
            details=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()],
        ) from exc
    needs_approval = bool(proposal.get("requires_approval")) or (source == "model" and not actor.is_human)
    if needs_approval and not is_approved(db, actor.organization_id, RECOVERY_APPROVAL_ACTION, "failure", failure.id):
        approval = request_approval(
            db,
            actor,
            action=RECOVERY_APPROVAL_ACTION,
            subject_type="failure",
            subject_id=failure.id,
            title=f"Apply recovery '{proposal.get('action')}' to experiment '{experiment.title}'",
            payload={
                "failure_id": str(failure.id),
                "experiment_id": str(experiment.id),
                "source": source,
                "action": proposal.get("action"),
                "spec_patch": patch,
                "rationale": proposal.get("rationale"),
                "failure_type": failure.failure_type,
            },
            risk_level="MEDIUM",
            project_id=failure.project_id,
            mission_id=failure.mission_id,
            workflow_run_id=actor.workflow_run_id,
        )
        failure.recovery_action = {
            **action,
            "approval": {"id": str(approval.id), "status": approval.status, "source": source},
        }
        db.flush()
        return RecoveryResult("approval_required", failure, experiment_id=experiment.id, approval_id=approval.id)
    new_version = create_experiment_version(db, actor, experiment.id, new_spec)
    new_version_id = _uuid(getattr(new_version, "id", None))
    if new_version_id is None:
        raise ServiceUnavailable("The experiments service returned no version id", code="experiments_unavailable")
    applied = {
        "source": source,
        "action": proposal.get("action"),
        "spec_patch": patch,
        "base_version_id": str(version.id),
        "new_version_id": str(new_version_id),
        "validation_passed": getattr(new_version, "validation_passed", None),
        "applied_at": utcnow().isoformat(),
        "by": actor.as_dict(),
        "autonomy_level": effective_autonomy,
    }
    failure.status = FailureStatus.RECOVERING
    failure.recovery_status = "testing"
    failure.recovery_experiment_id = experiment.id
    failure.recovery_action = {**action, "applied": applied, "new_version_id": str(new_version_id)}
    service.failure_evidence(
        db,
        failure,
        f"Recovery applied: {proposal.get('action')}",
        {
            "experiment_id": str(experiment.id),
            "base_version_id": str(version.id),
            "new_version_id": str(new_version_id),
            "spec_patch": patch,
            "source": source,
        },
        kind="failure_recovery",
    )
    audit(
        db,
        actor,
        RECOVERY_APPLIED,
        "failure",
        failure.id,
        before={"status": FailureStatus.DIAGNOSED},
        after={"status": FailureStatus.RECOVERING, "new_version_id": str(new_version_id), "source": source},
    )
    service.failure_event(
        db,
        actor,
        failure,
        "recovery_applied",
        experiment_id=str(experiment.id),
        new_version_id=str(new_version_id),
        action=proposal.get("action"),
        source=source,
    )
    db.flush()
    return RecoveryResult("applied", failure, experiment_id=experiment.id, new_version_id=new_version_id)


def _applied_proposal(failure: Failure, confidence: float) -> RecoveryProposal | None:
    action = dict(failure.recovery_action or {})
    raw_applied = action.get("applied")
    applied: dict[str, Any] = raw_applied if isinstance(raw_applied, dict) else {}
    source = applied.get("source", "rule")
    base = action.get("model_proposal") if source == "model" else action
    if not isinstance(base, dict):
        return None
    try:
        return RecoveryProposal.model_validate(
            {
                "action": base.get("action"),
                "spec_patch": base.get("spec_patch") or {},
                "rationale": base.get("rationale") or "",
                "requires_approval": bool(base.get("requires_approval")),
                "confidence": min(1.0, max(0.0, float(confidence))),
                "failure_type": failure.failure_type,
                "subtype": base.get("subtype") or service.classification_of(failure).subtype,
            }
        )
    except PydanticValidationError:
        log.warning("recovery_proposal_unparseable", failure_id=str(failure.id))
        return None


def _learn(db: Session, actor: Actor, failure: Failure, succeeded: bool) -> tuple[Lesson | None, bool]:
    """Update the lesson from a recovery outcome → ``(lesson, activated)``."""
    lesson = service.lesson_for(db, actor, failure)
    if lesson is None or lesson.status == "RETIRED":
        return lesson, False
    if succeeded:
        lesson.times_succeeded = int(lesson.times_succeeded or 0) + 1
    proposal = _applied_proposal(failure, lesson.confidence)
    if proposal is not None:
        draft = extract_lesson(service.classification_of(failure), proposal, "succeeded" if succeeded else "failed")
        lesson.confidence = draft.confidence
        lesson.statement = draft.statement
        lesson.recommendation = {
            **draft.recommendation,
            "times_applied": lesson.times_applied,
            "times_succeeded": lesson.times_succeeded,
        }
    lesson.evidence = service.append_bounded(
        lesson.evidence,
        {
            "event": "recovery_succeeded" if succeeded else "recovery_failed",
            "failure_id": str(failure.id),
            "at": utcnow().isoformat(),
        },
        service.MAX_LESSON_EVIDENCE,
    )
    activated = (
        succeeded and lesson.status == "PROPOSED" and lesson.times_succeeded >= service.LESSON_ACTIVATION_SUCCESSES
    )
    if activated:
        lesson.status = "ACTIVE"
    return lesson, activated


def record_recovery_outcome(
    db: Session,
    actor: Actor,
    failure_id: uuid.UUID | str,
    succeeded: bool,
    *,
    reason: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> Failure:
    """Close a recovery test: ``RESOLVED`` (succeeded) or back to ``DIAGNOSED`` (failed); the lesson learns."""
    failure = service.get_failure(db, actor, failure_id, permission="failure:write")
    advisory_xact_lock(db, f"failure-recovery:{failure.id}")
    db.refresh(failure)
    if failure.status != FailureStatus.RECOVERING:
        raise Conflict(
            f"No recovery is being tested for this failure (status {failure.status})", code="recovery_not_in_progress"
        )
    target = FailureStatus.RESOLVED if succeeded else FailureStatus.DIAGNOSED
    assert_transition("failure", failure.status, target)
    failure.status = target
    failure.recovery_status = "succeeded" if succeeded else "failed"
    outcome = {
        "succeeded": succeeded,
        "reason": " ".join((reason or "").split())[:2000] or None,
        "evidence": evidence or {},
        "by": actor.as_dict(),
        "at": utcnow().isoformat(),
    }
    failure.recovery_action = {**(failure.recovery_action or {}), "outcome": outcome}
    lesson, activated = _learn(db, actor, failure, succeeded)
    service.failure_evidence(
        db,
        failure,
        f"Recovery {'succeeded' if succeeded else 'failed'}",
        {
            "succeeded": succeeded,
            "reason": outcome["reason"],
            "evidence": outcome["evidence"],
            "lesson_id": str(lesson.id) if lesson else None,
            "lesson_confidence": lesson.confidence if lesson else None,
            "lesson_status": lesson.status if lesson else None,
        },
        kind="failure_recovery",
    )
    audit(
        db,
        actor,
        RECOVERY_OUTCOME,
        "failure",
        failure.id,
        before={"status": FailureStatus.RECOVERING},
        after={"status": target, "succeeded": succeeded},
    )
    service.failure_event(db, actor, failure, "recovery_outcome", succeeded=succeeded)
    if lesson is not None and activated:
        service.lesson_event(db, actor, lesson, "activated", failure_id=str(failure.id))
    db.flush()
    return failure


def assess_recovery(db: Session, actor: Actor, failure_id: uuid.UUID | str, *, reason: str | None = None) -> Failure:
    """Decide the recovery outcome deterministically from the runs of the recovery version.

    Succeeded when every run of the new version finished and at least one succeeded; failed when every run
    finished without success; 409 while runs are missing or still running.
    """
    failure = service.get_failure(db, actor, failure_id, permission="failure:write")
    if failure.status != FailureStatus.RECOVERING:
        raise Conflict(
            f"No recovery is being tested for this failure (status {failure.status})", code="recovery_not_in_progress"
        )
    version_id = _uuid((failure.recovery_action or {}).get("new_version_id"))
    runs = (
        list(db.scalars(select(ExperimentRun).where(ExperimentRun.experiment_version_id == version_id)))
        if version_id is not None
        else []
    )
    if not runs:
        raise Conflict("The recovery version has not been run yet", code="recovery_not_tested")
    if any(r.status not in _RUN_TERMINAL for r in runs):
        raise Conflict("Runs of the recovery version are still in progress", code="recovery_testing_in_progress")
    succeeded = any(r.status in _RUN_SUCCESS for r in runs)
    return record_recovery_outcome(
        db,
        actor,
        failure.id,
        succeeded,
        reason=reason or "assessed from the runs of the recovery version",
        evidence={"version_id": str(version_id), "runs": {str(r.id): r.status for r in runs}},
    )


# =============================================================================================
# Approval hook (loaded by governance.approvals; runs in a savepoint after a HUMAN decision)
# =============================================================================================
def on_approval_decided(db: Session, actor: Actor, approval: Approval) -> None:
    if approval.action != RECOVERY_APPROVAL_ACTION or approval.subject_type != "failure":
        return
    failure_id = _uuid(approval.subject_id)
    failure = db.get(Failure, failure_id) if failure_id else None
    if failure is None or failure.organization_id != approval.organization_id:
        return
    action = dict(failure.recovery_action or {})
    raw_pending = action.get("approval")
    pending: dict[str, Any] = raw_pending if isinstance(raw_pending, dict) else {}
    source: RecoverySource = "model" if (approval.request_payload or {}).get("source") == "model" else "rule"
    if approval.status == ApprovalStatus.APPROVED:
        failure.recovery_action = {**action, "approval": {**pending, "id": str(approval.id), "status": approval.status}}
        db.flush()
        apply_recovery(db, actor, failure.id, source=source)
    elif approval.status == ApprovalStatus.REJECTED:
        failure.recovery_action = {**action, "approval": {**pending, "id": str(approval.id), "status": approval.status}}
        failure.recovery_status = "rejected"
        service.failure_event(db, actor, failure, "recovery_rejected", approval_id=str(approval.id))
        db.flush()


register_approval_hook(RECOVERY_APPROVAL_ACTION, on_approval_decided)

__all__ = [
    "RECOVERY_APPROVAL_ACTION",
    "RecoveryResult",
    "apply_recovery",
    "assess_recovery",
    "create_experiment_version",
    "on_approval_decided",
    "record_recovery_outcome",
]
