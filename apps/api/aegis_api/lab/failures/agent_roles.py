"""FailureAnalyzerAgent — explains a failure the platform has already classified and suggests a bounded recovery.

The deterministic rule classification is always the basis (AGENTS.md rule 1): the model's diagnosis is stored
*alongside* it in ``failure.classification["model"]`` with full provenance and never replaces the rule-based
``failure_type``. Only when the rule classifier itself is unsure (confidence < 0.5) does the model's root cause
become the displayed one (``root_cause_source="model"``) — the rule root cause stays recorded. A suggested
recovery patch passes the same guardrails as rule proposals (resources/timeout/seeds/dependencies/parameters/
environment only — never network, secrets or permissions), is stored as ``recovery_action["model_proposal"]`` and
always needs a human approval before an automated actor may apply it.

Prompt ``failure.diagnose`` variables: ``failure_context`` (untrusted: failure summary, experiment spec excerpt,
similar failures, lessons), ``classification`` (deterministic), ``logs`` (untrusted, already redacted).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import ValidationFailed
from aegis_api.lab.agents.roles import RoleSpec, register_role
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.failures import service
from aegis_api.lab.models import AgentRun, Experiment, ExperimentVersion, Failure, Lesson
from engines.lab.experiment_spec import ExperimentSpec
from engines.lab.failures import RecoveryAction, merge_patch
from engines.lab.states import AgentRole, AutonomyLevel, FailureStatus, FailureType, assert_transition

RULE_CONFIDENCE_FLOOR = 0.5
MAX_CONTEXT_PARAMETERS_CHARS = 4000
MAX_LOG_CHARS_FOR_MODEL = 12_000

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]

GUARDRAIL_NOTE = (
    "A recovery spec_patch may only change resources (cpu, memory_mb, disk_mb within the execution limits), "
    "timeout_seconds, seeds (adding, never dropping), parameters (no deletions), environment.dependencies (new ones "
    "pinned as name==version), environment.image_digest / python_version and reproducibility.deterministic_flags. "
    "It may never touch the network policy, secrets, command, code, datasets, metrics, success criteria, statistical "
    "plan or baseline."
)


class RecoverySuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RecoveryAction
    spec_patch: dict[str, Any] = Field(
        default_factory=dict, description="JSON merge-patch for the experiment spec (empty for advice only)."
    )
    rationale: str = Field(default="", max_length=2000)


class FailureDiagnosis(BaseModel):
    """Structured output of the FailureAnalyzerAgent."""

    model_config = ConfigDict(extra="forbid")

    root_cause: str = Field(min_length=3, max_length=4000)
    confidence: float = Field(ge=0.0, le=1.0)
    contributing_factors: list[ShortText] = Field(default_factory=list, max_length=10)
    evidence: list[ShortText] = Field(
        default_factory=list, max_length=10, description="Short quotes of the log lines supporting the diagnosis."
    )
    suggested_failure_type: FailureType | None = Field(
        default=None, description="Only when the logs contradict the deterministic classification."
    )
    is_scientific_negative_result: bool = False
    recovery: RecoverySuggestion | None = None
    lesson: str | None = Field(default=None, max_length=2000)


def _failure_id(run: AgentRun) -> uuid.UUID:
    raw = (run.input or {}).get("failure_id")
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValidationFailed("FailureAnalyzerAgent input requires 'failure_id'") from exc


def _current_spec(db: Session, failure: Failure) -> dict[str, Any]:
    if failure.experiment_id is None:
        return {}
    experiment = db.get(Experiment, failure.experiment_id)
    if experiment is None or experiment.current_version_id is None:
        return {}
    version = db.get(ExperimentVersion, experiment.current_version_id)
    return service.normalized_spec(version.spec if version is not None else None)


def _spec_excerpt(spec: dict[str, Any]) -> dict[str, Any]:
    if not spec:
        return {}
    environment = dict(spec.get("environment") or {})
    parameters = service.redact_text(str(spec.get("parameters") or {}), MAX_CONTEXT_PARAMETERS_CHARS)
    return {
        "objective": service.redact_text(str(spec.get("objective") or ""), 1000),
        "kind": spec.get("kind"),
        "metrics": [m.get("name") for m in spec.get("metrics") or [] if isinstance(m, dict)],
        "resources": spec.get("resources"),
        "timeout_seconds": spec.get("timeout_seconds"),
        "seeds": spec.get("seeds"),
        "n_seeds_planned": (spec.get("statistical_plan") or {}).get("n_seeds"),
        "environment": {"image": environment.get("image"), "dependencies": environment.get("dependencies")},
        "parameters": parameters,
    }


def build_context(db: Session, actor: Actor, run: AgentRun) -> dict[str, Any]:
    failure = service.get_failure(db, actor, _failure_id(run), permission="failure:read")
    rule = dict(failure.classification or {})
    action = dict(failure.recovery_action or {})
    classification = {
        "failure_type": failure.failure_type,
        "subtype": rule.get("subtype"),
        "confidence": rule.get("confidence", failure.confidence),
        "matched_rules": rule.get("matched_rules"),
        "rule_root_cause": rule.get("root_cause"),
        "evidence": rule.get("evidence"),
        "signature": failure.signature,
        "engine_version": rule.get("engine_version"),
        "recovery_proposal": {
            "action": action.get("action"),
            "spec_patch": action.get("spec_patch"),
            "requires_approval": action.get("requires_approval"),
            "rationale": action.get("rationale"),
            "status": action.get("status"),
        },
        "recovery_guardrails": GUARDRAIL_NOTE,
    }
    similar = [
        {
            "failure_type": f.failure_type,
            "title": f.title,
            "root_cause": f.root_cause,
            "status": f.status,
            "recovery_action": (f.recovery_action or {}).get("action"),
            "recovery_status": f.recovery_status,
        }
        for f in service.similar_failures(db, actor, failure)
    ]
    lessons_stmt = (
        select(Lesson)
        .where(
            Lesson.organization_id == actor.organization_id,
            Lesson.project_id == failure.project_id,
            Lesson.failure_type == failure.failure_type,
            Lesson.status.in_(("ACTIVE", "PROPOSED")),
        )
        .order_by(Lesson.confidence.desc(), Lesson.created_at.desc())
        .limit(5)
    )
    lessons = [
        {
            "statement": lesson.statement,
            "status": lesson.status,
            "confidence": lesson.confidence,
            "times_applied": lesson.times_applied,
            "times_succeeded": lesson.times_succeeded,
            "recommended_action": (lesson.recommendation or {}).get("action"),
            "same_signature": lesson.signature == failure.signature,
        }
        for lesson in db.scalars(lessons_stmt)
    ]
    failure_context = {
        "failure": {
            "title": failure.title,
            "stage": (rule.get("signals") or {}).get("stage"),
            "status": failure.status,
            "detected_by": failure.detected_by,
            "recurrence_count": failure.recurrence_count,
        },
        "experiment_spec": _spec_excerpt(_current_spec(db, failure)),
        "similar_failures": similar,
        "lessons": lessons,
    }
    logs = "\n".join(part for part in (failure.traceback, failure.logs_excerpt) if part)
    return {
        "failure_context": failure_context,
        "classification": classification,
        "logs": service.redact_text(logs, MAX_LOG_CHARS_FOR_MODEL) or "(no logs were captured)",
    }


def _provenance(run: AgentRun) -> dict[str, Any]:
    return {
        "agent_run_id": str(run.id),
        "provider": run.provider,
        "model": run.model,
        "model_version": run.model_version,
        "prompt_key": run.prompt_key,
        "prompt_version": run.prompt_version,
        "prompt_hash": run.prompt_hash,
        "strategy_version_id": str(run.strategy_version_id) if run.strategy_version_id else None,
    }


def _check_suggestion(
    failure: Failure, suggestion: RecoverySuggestion, spec: dict[str, Any], limits: service.RecoveryLimits
) -> list[str]:
    patch = dict(suggestion.spec_patch)
    if not patch:
        return []
    if failure.failure_type in service.NO_AUTO_RECOVERY_TYPES:
        return ["policy failures and negative results are never recovered automatically"]
    if not spec:
        return ["no experiment specification is linked to this failure"]
    violations = service.validate_recovery_patch(patch, spec, limits)
    if not violations:
        try:
            ExperimentSpec.model_validate(merge_patch(spec, patch))
        except PydanticValidationError as exc:
            violations = [f"patched spec is invalid: {e['msg']} at {'.'.join(map(str, e['loc']))}" for e in exc.errors()]
    return violations[:20]


def apply_diagnosis(db: Session, actor: Actor, run: AgentRun, output: BaseModel) -> dict[str, Any]:
    diagnosis = output if isinstance(output, FailureDiagnosis) else FailureDiagnosis.model_validate(output.model_dump())
    failure = service.get_failure(db, actor, _failure_id(run), permission="failure:write")
    rule = dict(failure.classification or {})
    rule_confidence = float(rule.get("confidence", failure.confidence) or 0.0)
    provenance = _provenance(run)
    recorded_at = utcnow().isoformat()
    recovery_doc: dict[str, Any] | None = None
    violations: list[str] = []
    accepted = False
    if diagnosis.recovery is not None:
        spec = _current_spec(db, failure)
        limits = service.recovery_limits(db, failure.organization_id)
        violations = _check_suggestion(failure, diagnosis.recovery, spec, limits)
        accepted = bool(diagnosis.recovery.spec_patch) and not violations
        recovery_doc = {
            "action": diagnosis.recovery.action.value,
            "spec_patch": diagnosis.recovery.spec_patch,
            "rationale": service.redact_text(diagnosis.recovery.rationale, 2000),
            "requires_approval": True,
            "confidence": round(min(diagnosis.confidence, 1.0), 4),
            "failure_type": failure.failure_type,
            "subtype": rule.get("subtype"),
            "source": "model",
            "accepted": accepted,
            "guardrail_violations": violations,
            "provenance": provenance,
            "recorded_at": recorded_at,
        }
    agrees = diagnosis.suggested_failure_type in (None, failure.failure_type)
    model_doc = {
        "root_cause": service.redact_text(diagnosis.root_cause, 4000),
        "confidence": diagnosis.confidence,
        "contributing_factors": list(diagnosis.contributing_factors),
        "evidence": list(diagnosis.evidence),
        "suggested_failure_type": diagnosis.suggested_failure_type.value if diagnosis.suggested_failure_type else None,
        "agrees_with_rule": agrees,
        "is_scientific_negative_result": diagnosis.is_scientific_negative_result,
        "lesson": service.redact_text(diagnosis.lesson, 2000) if diagnosis.lesson else None,
        "recovery": (
            {"action": recovery_doc["action"], "accepted": accepted, "violations": violations} if recovery_doc else None
        ),
        "provenance": provenance,
        "recorded_at": recorded_at,
    }
    failure.classification = {**rule, "model": model_doc}
    used_model_root_cause = False
    if rule_confidence < RULE_CONFIDENCE_FLOOR and failure.root_cause_source in (None, "rule", "model"):
        failure.root_cause = model_doc["root_cause"]
        failure.root_cause_source = "model"
        used_model_root_cause = True
    if recovery_doc is not None:
        failure.recovery_action = {**(failure.recovery_action or {}), "model_proposal": recovery_doc}
    if failure.status == FailureStatus.OPEN:
        assert_transition("failure", failure.status, FailureStatus.DIAGNOSED)
        failure.status = FailureStatus.DIAGNOSED
    service.failure_evidence(
        db,
        failure,
        "Model-assisted failure diagnosis",
        {
            "rule_failure_type": failure.failure_type,
            "rule_confidence": rule_confidence,
            "model_confidence": diagnosis.confidence,
            "agrees_with_rule": agrees,
            "model_root_cause_used": used_model_root_cause,
            "recovery_accepted": accepted,
            "guardrail_violations": violations,
            "provenance": provenance,
        },
        kind="failure_diagnosis",
    )
    service.failure_event(
        db,
        actor,
        failure,
        "model_diagnosis",
        agent_run_id=str(run.id),
        agrees_with_rule=agrees,
        model_root_cause_used=used_model_root_cause,
        recovery_accepted=accepted,
    )
    db.flush()
    return {
        "failure_id": str(failure.id),
        "failure_type": failure.failure_type,
        "root_cause_source": failure.root_cause_source,
        "model_root_cause_used": used_model_root_cause,
        "agrees_with_rule": agrees,
        "recovery_proposed": recovery_doc is not None,
        "recovery_accepted": accepted,
        "guardrail_violations": violations,
    }


FAILURE_ANALYZER_ROLE = register_role(
    RoleSpec(
        role=AgentRole.FAILURE_ANALYZER,
        task_type="failure_analysis",
        prompt_key="failure.diagnose",
        output_model=FailureDiagnosis,
        build_context=build_context,
        apply=apply_diagnosis,
        description="Explains a deterministically classified failure and proposes a guardrail-checked recovery.",
        default_tools=(),
        default_permissions=frozenset({"failure:read", "failure:write", "experiment:read", "memory:read"}),
        min_autonomy_level=AutonomyLevel.L1_RESEARCH_AUTOMATION,
        untrusted_context_keys=("failure_context", "logs"),
        tier="default",
        temperature=0.1,
        max_steps=3,
    )
)
