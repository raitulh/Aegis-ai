"""API contract for workflow runs (``/api/v1/workflow-runs``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aegis_api.lab.models import WorkflowRun, WorkflowStep
from aegis_api.lab.workflows import runs

# Signals a client may send through the API. Approval decisions are normally delivered by the approvals
# service (``governance.approvals.decide_approval``); the API path exists for operators and requires a human.
API_SIGNALS: tuple[str, ...] = ("resume", "approval")
SignalName = Literal["resume", "approval"]


class WorkflowRunOut(BaseModel):
    id: str
    kind: str
    status: str
    engine: str
    subject_type: str
    subject_id: str
    project_id: str | None = None
    mission_id: str | None = None
    parent_workflow_run_id: str | None = None
    external_id: str
    external_run_id: str | None = Field(default=None, description="Temporal run id (Temporal engine only)")
    task_queue: str | None = None
    attempt: int
    input: dict[str, Any] = Field(default_factory=dict, description="The flow input")
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_requested: bool = False
    pending_signals: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    created_by_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_run(cls, run: WorkflowRun) -> WorkflowRunOut:
        signals = [s for s in (run.signals or []) if isinstance(s, dict)]
        if run.engine == runs.ENGINE_TEMPORAL:
            pending = sum(1 for s in signals if not s.get("delivered"))
        else:
            pending = len(signals)
        return cls(
            id=str(run.id),
            kind=run.kind,
            status=run.status,
            engine=run.engine,
            subject_type=run.subject_type,
            subject_id=run.subject_id,
            project_id=str(run.project_id) if run.project_id else None,
            mission_id=str(run.mission_id) if run.mission_id else None,
            parent_workflow_run_id=str(run.parent_workflow_run_id) if run.parent_workflow_run_id else None,
            external_id=run.external_id,
            # Local runs keep an internal executor lease token here; it is not part of the API.
            external_run_id=run.external_run_id if run.engine == runs.ENGINE_TEMPORAL else None,
            task_queue=run.task_queue,
            attempt=run.attempt,
            input=runs.flow_input(run),
            result=run.result,
            error=run.error,
            cancel_requested=run.cancel_requested,
            pending_signals=pending,
            started_at=run.started_at,
            completed_at=run.completed_at,
            heartbeat_at=run.heartbeat_at,
            created_by_id=str(run.created_by_id) if run.created_by_id else None,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )


class WorkflowStepError(BaseModel):
    type: str | None = None
    message: str | None = None
    code: str | None = None
    non_retryable: bool | None = None


class WorkflowStepOut(BaseModel):
    step_key: str
    activity: str
    status: str
    attempt: int
    error: WorkflowStepError | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_step(cls, step: WorkflowStep, error: dict[str, Any] | None) -> WorkflowStepOut:
        return cls(
            step_key=step.step_key,
            activity=step.activity,
            status=step.status,
            attempt=step.attempt,
            error=WorkflowStepError(
                type=error.get("type"),
                message=str(error.get("message") or "")[:1000] or None,
                code=error.get("code"),
                non_retryable=error.get("non_retryable"),
            )
            if error
            else None,
            started_at=step.started_at,
            completed_at=step.completed_at,
        )


class WorkflowRunDetail(WorkflowRunOut):
    steps: list[WorkflowStepOut] = Field(default_factory=list, description="Recorded steps (local engine)")
    step_counts: dict[str, int] = Field(default_factory=dict)
    steps_truncated: bool = False
    children: list[WorkflowRunOut] = Field(default_factory=list)
    temporal: dict[str, Any] | None = Field(default=None, description="Temporal describe (Temporal engine only)")


class WorkflowSignalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SignalName
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowSignalOut(BaseModel):
    workflow_run_id: str
    name: str
    status: str
    accepted: bool = True


class WorkflowRetryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_scratch: bool = Field(
        default=False,
        description="Discard every recorded step instead of resuming from the point of failure (local engine)",
    )
