"""Execution tools: ``python_execution``, ``simulation`` (sandboxed short jobs) and ``experiment_run``.

Code never runs in the API or worker process: the tools submit a compute job through the execution service
(validation, allowlisted image, quota, mission budget, ``execution.submit`` policy — which may itself require a
human approval), dispatch it through the workflow launcher after commit and poll its status until the tool
deadline. Sandboxes have no network, 1 vCPU, 1 GiB memory and a 120 s wall-clock limit. The job's program
output (log tail) is untrusted and returned sanitized by the broker; compute cost is recorded by the execution
fabric itself (``compute_usage``), never twice.
"""

from __future__ import annotations

import importlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_api.config import get_settings
from aegis_api.errors import AppError
from aegis_api.lab.core.deps import tenant_uow
from aegis_api.lab.core.errors import ApprovalRequired
from aegis_api.lab.models import ComputeJob
from aegis_api.lab.tools.registry import (
    ToolDefinition,
    ToolError,
    ToolExecutionContext,
    ToolInputError,
    ToolOutput,
    ToolTimeout,
    ToolUnavailable,
    register_tool,
)
from engines.lab.states import ExecutionStatus, RiskLevel

SANDBOX_TIMEOUT_SECONDS = 120
SANDBOX_CPU = 1.0
SANDBOX_MEMORY_MB = 1024
SANDBOX_DISK_MB = 1024
LOG_TAIL_BYTES = 8192
MAX_FILE_CHARS = 500_000
TOOL_TIMEOUT_SECONDS = 180.0
_FINISHED = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.VERIFICATION_PENDING,
        ExecutionStatus.VERIFIED,
    }
)
MAIN_PATH = "code/main.py"


class InlineFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$",
        description="File name placed read-only under /workspace/input/",
    )
    content: str = Field(max_length=MAX_FILE_CHARS)


class PythonExecutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        min_length=1,
        max_length=100_000,
        description=(
            "Python 3 source run as /workspace/code/main.py. Inputs are read-only under /workspace/input/; write "
            "results to /workspace/output/ (metrics.json for metrics). No network access."
        ),
    )
    files: list[InlineFile] = Field(default_factory=list, max_length=10)
    timeout_seconds: int = Field(default=SANDBOX_TIMEOUT_SECONDS, ge=5, le=SANDBOX_TIMEOUT_SECONDS)

    @field_validator("files")
    @classmethod
    def _unique(cls, files: list[InlineFile]) -> list[InlineFile]:
        names = [f.name for f in files]
        if len(names) != len(set(names)):
            raise ValueError("file names must be unique")
        if "params.json" in names:
            raise ValueError("params.json is reserved")
        return files


class SimulationInput(PythonExecutionInput):
    seed: int = Field(ge=0, le=2**31 - 1, description="Random seed (required: simulations must be reproducible)")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Written to /workspace/input/params.json")

    @field_validator("parameters")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > 64 * 1024:
            raise ValueError("parameters must serialize to at most 64 KiB")
        return value


def simulation_image() -> str:
    """A scientific-Python image when one is allowlisted (numpy/scipy variant), else the default image."""
    settings = get_settings()
    for image in settings.execution_allowed_image_list:
        lowered = image.lower()
        if any(marker in lowered for marker in ("numpy", "scipy", "scientific")):
            return image
    return settings.execution_default_image


def _launcher_dispatch(db: Any, ctx: ToolExecutionContext, job: ComputeJob) -> None:
    try:
        launcher = importlib.import_module("aegis_api.lab.workflows.launcher")
    except ModuleNotFoundError as exc:
        raise ToolUnavailable("The workflow launcher is not installed; sandboxed jobs cannot be dispatched") from exc
    dispatch = getattr(launcher, "dispatch_compute_job", None)
    if not callable(dispatch):
        raise ToolUnavailable("The workflow launcher cannot dispatch compute jobs in this deployment")
    dispatch(db, ctx.actor, job)


def _submit(
    ctx: ToolExecutionContext,
    *,
    code: str,
    files: list[InlineFile],
    timeout_seconds: int,
    image: str,
    env: dict[str, str],
    parameters: dict[str, Any],
) -> uuid.UUID:
    from aegis_api.lab.execution.schemas import JobInput, JobSpec
    from aegis_api.lab.execution.service import submit_job
    from aegis_api.lab.governance.approvals import is_approved
    from engines.lab.sandbox import NetworkPolicy, ResourceRequest

    spec = JobSpec(
        project_id=ctx.project_id,
        image=image,
        command=["python", f"/workspace/{MAIN_PATH}"],
        env=env,
        inputs=[
            JobInput(kind="inline", path=MAIN_PATH, content=code),
            *[JobInput(kind="inline", path=f"input/{f.name}", content=f.content) for f in files],
        ],
        parameters=parameters,
        resources=ResourceRequest(cpu=SANDBOX_CPU, memory_mb=SANDBOX_MEMORY_MB, disk_mb=SANDBOX_DISK_MB),
        timeout_seconds=timeout_seconds,
        network=NetworkPolicy(mode="none"),
        mission_id=ctx.mission_id,
        purpose="tool",
        idempotency_key=f"tool:{ctx.invocation_id}",
    )
    pending_approval: str | None = None
    with tenant_uow(ctx.actor) as db:
        job = submit_job(db, ctx.actor, spec)
        job_id = job.id
        if job.status == ExecutionStatus.QUEUED and not job.cancel_requested:
            if job.approval_id is not None and not is_approved(
                db, ctx.organization_id, "execution.submit", "compute_job", job.id
            ):
                pending_approval = str(job.approval_id)
            else:
                _launcher_dispatch(db, ctx, job)
    if pending_approval is not None:
        # Raised after the commit above so the job and its approval request persist.
        raise ApprovalRequired(
            "The sandboxed job requires human approval under the execution policy",
            approval_id=pending_approval,
            details={"job_id": str(job_id)},
        )
    return job_id


def _log_tail(job: ComputeJob) -> tuple[str, bool]:
    logs = (job.output_manifest or {}).get("logs") or {}
    key = logs.get("storage_key")
    if not key:
        return "", False
    from aegis_api.lab.storage import get_storage

    size = int(logs.get("size") or 0)
    limit = max(int(get_settings().execution_max_log_bytes), size) + 1
    try:
        data = get_storage().get_bytes(str(key), limit)
    except Exception:
        return "", False
    tail = data[-LOG_TAIL_BYTES:]
    return tail.decode("utf-8", "replace"), bool(logs.get("truncated")) or len(data) > len(tail)


def _wait(ctx: ToolExecutionContext, job_id: uuid.UUID) -> dict[str, Any]:
    from aegis_api.lab.execution.service import job_result

    interval = 1.0
    while True:
        with tenant_uow(ctx.actor) as db:
            job = db.get(ComputeJob, job_id)
            if job is None or job.organization_id != ctx.organization_id:
                raise ToolError("The compute job disappeared", code="job_missing")
            if job.status in _FINISHED:
                result = job_result(job)
                stdout, truncated = _log_tail(job)
                return {
                    "job_id": str(job.id),
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "duration_seconds": result.duration_seconds,
                    "output_artifact_version_ids": dict(result.outputs),
                    "logs_artifact_version_id": result.logs_artifact_version_id,
                    "metrics": result.metrics,
                    "stdout_tail": stdout,
                    "stdout_truncated": truncated,
                    "error": result.error,
                }
            status = job.status
        if ctx.remaining() <= interval + 0.5:
            raise ToolTimeout(
                f"The sandboxed job is still {status}; it keeps running — check compute job {job_id}",
                details={"job_id": str(job_id), "status": status},
            )
        ctx.sleep(interval)
        interval = min(interval * 1.5, 5.0)


def _run_sandboxed(ctx: ToolExecutionContext, args: PythonExecutionInput, *, image: str, env: dict[str, str],
                   parameters: dict[str, Any]) -> ToolOutput:
    try:
        job_id = _submit(
            ctx,
            code=args.code,
            files=args.files,
            timeout_seconds=args.timeout_seconds,
            image=image,
            env=env,
            parameters=parameters,
        )
    except (ToolError, ApprovalRequired):
        raise
    except AppError as exc:
        if exc.status_code in (400, 413, 422):
            raise ToolInputError(exc.message, details={"code": exc.code}) from exc
        raise
    outcome = _wait(ctx, job_id)
    failed = outcome["status"] != ExecutionStatus.SUCCEEDED or (outcome.get("exit_code") not in (0, None))
    return ToolOutput(
        content=outcome,
        is_error=failed,
        metadata={"job_id": outcome["job_id"], "status": outcome["status"], "exit_code": outcome.get("exit_code")},
    )


def _python_execution(ctx: ToolExecutionContext, args: PythonExecutionInput) -> ToolOutput:
    return _run_sandboxed(ctx, args, image=get_settings().execution_default_image, env={}, parameters={})


def _simulation(ctx: ToolExecutionContext, args: SimulationInput) -> ToolOutput:
    env = {"SIMULATION_SEED": str(args.seed), "PYTHONHASHSEED": str(args.seed)}
    parameters = {**args.parameters, "seed": args.seed}
    return _run_sandboxed(ctx, args, image=simulation_image(), env=env, parameters=parameters)


PYTHON_EXECUTION = register_tool(
    ToolDefinition(
        name="python_execution",
        description=(
            "Run a short Python program in an isolated sandbox (no network, 1 vCPU, 1 GiB, at most 120 s). Returns "
            "the exit code, the tail of stdout/stderr and the ids of files written to /workspace/output/. High risk: "
            "may require human approval."
        ),
        input_model=PythonExecutionInput,
        handler=_python_execution,
        risk_level=RiskLevel.HIGH,
        permissions=frozenset({"experiment:execute"}),
        rate_limit_per_min=10,
        timeout_seconds=TOOL_TIMEOUT_SECONDS,
        audit_redact_fields=("files",),
        category="execution",
    )
)

SIMULATION = register_tool(
    ToolDefinition(
        name="simulation",
        description=(
            "Run a seeded simulation program in an isolated scientific-Python sandbox (no network, 1 vCPU, 1 GiB, at "
            "most 120 s). The seed is exported as SIMULATION_SEED and written to /workspace/input/params.json with "
            "the parameters. High risk: may require human approval."
        ),
        input_model=SimulationInput,
        handler=_simulation,
        risk_level=RiskLevel.HIGH,
        permissions=frozenset({"experiment:execute"}),
        rate_limit_per_min=10,
        timeout_seconds=TOOL_TIMEOUT_SECONDS,
        audit_redact_fields=("files",),
        category="execution",
    )
)


# ---------------------------------------------------------------------------------------------
# experiment_run
# ---------------------------------------------------------------------------------------------
class ExperimentRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: uuid.UUID = Field(description="A validated experiment of this project")


def _experiment_run(ctx: ToolExecutionContext, args: ExperimentRunInput) -> ToolOutput:
    try:
        experiments = importlib.import_module("aegis_api.lab.experiments.service")
    except ModuleNotFoundError as exc:
        raise ToolUnavailable("Experiment execution is not installed in this deployment") from exc
    request_execution = getattr(experiments, "request_execution", None)
    if not callable(request_execution):
        raise ToolUnavailable("Experiment execution is not available in this deployment")
    from aegis_api.lab.core.access import get_owned
    from aegis_api.lab.models import Experiment

    with tenant_uow(ctx.actor) as db:
        experiment = get_owned(db, Experiment, args.experiment_id, ctx.actor, label="Experiment")
        if experiment.project_id != ctx.project_id:
            raise ToolInputError("The experiment belongs to a different project")
        result = request_execution(db, ctx.actor, experiment.id)
    payload = dict(result) if isinstance(result, dict) else {"result": str(result)}
    payload.setdefault("experiment_id", str(args.experiment_id))
    return ToolOutput(content=payload, metadata={"experiment_id": str(args.experiment_id)})


EXPERIMENT_RUN = register_tool(
    ToolDefinition(
        name="experiment_run",
        description=(
            "Request execution of an existing, validated experiment of this project (runs asynchronously through "
            "the experiment workflow). High risk: may require human approval."
        ),
        input_model=ExperimentRunInput,
        handler=_experiment_run,
        risk_level=RiskLevel.HIGH,
        permissions=frozenset({"experiment:execute"}),
        rate_limit_per_min=10,
        category="execution",
    )
)
