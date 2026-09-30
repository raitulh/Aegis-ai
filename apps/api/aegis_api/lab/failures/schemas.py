"""API contract of the failure intelligence context: failures, diagnoses, recoveries and lessons."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_api.schemas.common import ORMModel
from engines.lab.failures import FailureStage
from engines.lab.states import FailureStatus, FailureType

MAX_SIGNALS_BYTES = 512 * 1024
SUBTYPE_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FailureInput(_Strict):
    """A failure observed by the platform, an agent or a human.

    ``signals`` are the observations the deterministic classifier uses (see ``engines.lab.failures
    .FailureSignals``: exit_code, oom_killed, timed_out, traceback, logs, error_message, error_class, metrics,
    metrics_file_present, policy_decision, provider_error_class, reproduction_verdict, p_value, alpha,
    adequately_powered, power, effect_direction, n_seeds, required_seeds, validation_issue_codes,
    repeated_failures_for_strategy, tool_name). Text signals are redacted and truncated before storage.
    """

    stage: FailureStage
    project_id: uuid.UUID
    mission_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    experiment_run_id: uuid.UUID | None = None
    compute_job_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    strategy_version_id: uuid.UUID | None = None
    signals: dict[str, Any] = Field(default_factory=dict)
    title: str | None = Field(default=None, max_length=300)

    @field_validator("signals")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError("signals must be JSON-serialisable") from exc
        if len(encoded.encode("utf-8")) > MAX_SIGNALS_BYTES:
            raise ValueError(f"signals exceed {MAX_SIGNALS_BYTES} bytes")
        return value


FailureCreate = FailureInput


class LessonOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    failure_type: str
    signature: str | None = None
    statement: str
    recommendation: dict[str, Any]
    evidence: list[Any]
    confidence: float
    status: str
    source: str
    times_applied: int
    times_succeeded: int
    memory_id: str | None = None
    created_at: datetime
    updated_at: datetime


class FailureOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    mission_id: str | None = None
    experiment_id: str | None = None
    experiment_run_id: str | None = None
    compute_job_id: str | None = None
    agent_run_id: str | None = None
    strategy_version_id: str | None = None
    failure_type: str
    signature: str
    title: str
    detected_at: datetime
    detected_by: str
    status: str
    root_cause: str | None = None
    root_cause_source: str | None = None
    confidence: float
    recurrence_count: int
    similar_failure_ids: list[str]
    recovery_status: str
    recovery_experiment_id: str | None = None
    lesson_id: str | None = None
    created_at: datetime
    updated_at: datetime


class FailureDetailOut(FailureOut):
    """A failure with its (redacted) traceback/log excerpt, the deterministic classification (plus any model or
    human diagnosis recorded alongside it), the recovery proposal, similar failures and the linked lesson."""

    evidence: list[Any]
    traceback: str | None = None
    logs_excerpt: str | None = None
    classification: dict[str, Any]
    recovery_action: dict[str, Any]
    similar: list[FailureOut] = Field(default_factory=list)
    lesson: LessonOut | None = None


class DiagnosisOverride(_Strict):
    """A human root-cause diagnosis. The rule classification is kept alongside it."""

    root_cause: str = Field(min_length=3, max_length=4000)
    failure_type: FailureType | None = Field(
        default=None, description="Re-label the failure (the rule classification stays recorded)."
    )
    subtype: str | None = Field(default=None, pattern=SUBTYPE_PATTERN)
    reason: str = Field(min_length=3, max_length=2000)


class RecoveryApplyIn(_Strict):
    source: Literal["rule", "model"] = Field(
        default="rule",
        description="Apply the deterministic rule proposal (default) or the model-suggested patch (always "
        "guardrail-checked; automated actors need a human approval for model patches).",
    )


class RecoveryApplyOut(BaseModel):
    status: Literal["applied", "approval_required", "already_applied"]
    failure: FailureOut
    experiment_id: str | None = None
    new_version_id: str | None = None
    approval_id: str | None = None


class RecoveryOutcomeIn(_Strict):
    succeeded: bool | None = Field(
        default=None,
        description="Omit to assess the outcome deterministically from the runs of the recovery version.",
    )
    reason: str | None = Field(default=None, max_length=2000)


class FailureTransitionIn(_Strict):
    status: FailureStatus
    reason: str = Field(min_length=3, max_length=2000)


class LessonDecisionIn(_Strict):
    reason: str = Field(min_length=3, max_length=2000)
