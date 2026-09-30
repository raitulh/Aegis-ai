"""API contract of the evaluation context: evaluator registry entries and evaluation runs."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_api.schemas.common import ORMModel

EvaluatorKind = Literal[
    "metric",
    "regression",
    "classification",
    "benchmark",
    "statistical",
    "reproduction",
    "code_quality",
    "resource",
    "custom",
]
EVALUATOR_KEY_PATTERN = r"^[a-z][a-z0-9_.-]{1,79}$"
EVALUATOR_VERSION_PATTERN = r"^\d{1,6}\.\d{1,6}\.\d{1,6}$"
INPUT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$"
SPLIT_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
MAX_EXPLICIT_INPUTS = 8


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------------------------
class EvaluatorCreate(_Strict):
    """An organization evaluator: a versioned, immutable configuration of one built-in evaluator kind.

    ``kind="custom"`` evaluates a restricted boolean expression (``config.expression``) over aggregated run
    metrics; the expression is compiled by a safe AST walker (never ``eval``) and rejected when unsafe. A key
    equal to a built-in key overrides that built-in for the organization (its kind must match).
    """

    key: str = Field(pattern=EVALUATOR_KEY_PATTERN, description="Evaluator key (lowercase identifier).")
    version: str = Field(pattern=EVALUATOR_VERSION_PATTERN, description="Semantic version x.y.z (immutable).")
    kind: EvaluatorKind
    description: str | None = Field(default=None, max_length=2000)
    config: dict[str, Any] = Field(default_factory=dict, description="Validated against the kind's schema.")


class EvaluatorOut(ORMModel):
    id: str
    organization_id: str | None = None
    key: str
    version: str
    kind: str
    name: str | None = Field(default=None, description="Human-readable name of the evaluator kind.")
    description: str | None = None
    config: dict[str, Any]
    config_hash: str
    status: str
    builtin: bool = False
    independent: bool = Field(
        default=False,
        description="True when the evaluator recomputes results from raw data or platform measurements.",
    )
    created_at: datetime

    @model_validator(mode="after")
    def _derived(self) -> EvaluatorOut:
        self.builtin = self.organization_id is None
        return self


# ---------------------------------------------------------------------------------------------
# Evaluations
# ---------------------------------------------------------------------------------------------
class EvaluationInputRef(_Strict):
    """Explicit input for one evaluator file: an artifact version, or a dataset version (optionally one split).

    Held-out labels are read platform-side from ``evaluator_only`` splits; the experiment never sees them.
    """

    artifact_version_id: uuid.UUID | None = None
    dataset_version_id: uuid.UUID | None = None
    split: str | None = Field(default=None, pattern=SPLIT_PATTERN)

    @model_validator(mode="after")
    def _one_source(self) -> EvaluationInputRef:
        if (self.artifact_version_id is None) == (self.dataset_version_id is None):
            raise ValueError("provide exactly one of artifact_version_id or dataset_version_id")
        if self.split is not None and self.dataset_version_id is None:
            raise ValueError("split is only valid with dataset_version_id")
        return self


class EvaluationCreate(_Strict):
    """Run one evaluator against exactly one subject: an experiment run, an experiment, or a comparison."""

    evaluator_key: str = Field(pattern=EVALUATOR_KEY_PATTERN)
    evaluator_version: str | None = Field(default=None, pattern=EVALUATOR_VERSION_PATTERN)
    experiment_run_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    comparison_id: uuid.UUID | None = None
    config: dict[str, Any] | None = Field(
        default=None, description="Overrides merged onto the evaluator's stored configuration (validated)."
    )
    inputs: dict[str, EvaluationInputRef] | None = Field(
        default=None,
        description="Explicit evaluator files by name (e.g. `labels.csv`); defaults are resolved from the run's "
        "outputs and the spec's evaluator-only dataset splits.",
    )

    @field_validator("inputs")
    @classmethod
    def _input_names(cls, value: dict[str, EvaluationInputRef] | None) -> dict[str, EvaluationInputRef] | None:
        if value is None:
            return value
        if len(value) > MAX_EXPLICIT_INPUTS:
            raise ValueError(f"at most {MAX_EXPLICIT_INPUTS} explicit inputs are allowed")
        for name in value:
            if not re.match(INPUT_NAME_PATTERN, name) or ".." in name:
                raise ValueError(f"invalid input name {name!r}")
        return value

    @model_validator(mode="after")
    def _one_subject(self) -> EvaluationCreate:
        subjects = [s for s in (self.experiment_run_id, self.experiment_id, self.comparison_id) if s is not None]
        if len(subjects) != 1:
            raise ValueError("provide exactly one of experiment_run_id, experiment_id or comparison_id")
        return self


class EvaluationRunOut(ORMModel):
    id: str
    organization_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    mission_id: str | None = None
    experiment_id: str | None = None
    experiment_run_id: str | None = None
    comparison_id: str | None = None
    evaluator_id: str
    evaluator_key: str
    evaluator_version: str
    status: str
    passed: bool | None = None
    metrics: dict[str, Any]
    confidence: float | None = None
    warnings: list[Any]
    evidence: list[Any]
    inputs: dict[str, Any]
    independent: bool
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class EvaluationSubmitOut(BaseModel):
    """Result of ``POST /evaluations``: completed inline, or queued on the EvaluationWorkflow."""

    mode: Literal["inline", "workflow"]
    evaluation: EvaluationRunOut
    workflow_run_id: str | None = None
