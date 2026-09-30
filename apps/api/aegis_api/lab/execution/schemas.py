"""Execution API contract: job specification, job results and HTTP response models."""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from aegis_api.lab.execution.archive import normalize_relative_path
from aegis_api.schemas.common import ORMModel
from engines.lab.sandbox import PARAMS_PATH, NetworkPolicy, ResourceRequest

# Money is Decimal internally (Numeric(18,6)) and a JSON number on the wire.
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]

InputKind = Literal["dataset_version", "artifact_version", "code_snapshot", "inline"]
JobPurpose = Literal["experiment", "reproduction", "tool", "evaluation"]
MAX_INLINE_CONTENT_CHARS = 2 * 1024 * 1024


class JobInput(BaseModel):
    """One input placed into the sandbox workspace (read-only for the job).

    ``path`` is relative to ``/workspace`` and must be under ``input/`` (data, inline files) or ``code/``
    (code snapshots, inline source files). ``input/params.json`` is reserved for ``JobSpec.parameters``.
    Dataset inputs may name a ``split``; evaluator-only splits are never mounted into sandboxes.
    """

    model_config = ConfigDict(extra="forbid")

    kind: InputKind
    ref_id: uuid.UUID | None = Field(default=None, description="Dataset/artifact version or code snapshot id")
    path: str | None = Field(default=None, max_length=512, description="Destination relative to /workspace")
    content: str | None = Field(default=None, max_length=MAX_INLINE_CONTENT_CHARS, description="Inline file content")
    encoding: Literal["utf-8", "base64"] = "utf-8"
    split: str | None = Field(default=None, min_length=1, max_length=64, description="Dataset split name")

    @model_validator(mode="after")
    def _check(self) -> JobInput:
        if self.kind == "inline":
            if self.content is None:
                raise ValueError("inline inputs require content")
            if self.ref_id is not None:
                raise ValueError("inline inputs cannot reference a stored object")
            if self.encoding == "base64":
                try:
                    base64.b64decode(self.content, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("content is not valid base64") from exc
        else:
            if self.ref_id is None:
                raise ValueError(f"{self.kind} inputs require ref_id")
            if self.content is not None:
                raise ValueError(f"{self.kind} inputs cannot carry inline content")
        if self.split is not None and self.kind != "dataset_version":
            raise ValueError("split is only valid for dataset_version inputs")
        if self.path is None:
            if self.kind != "code_snapshot":
                raise ValueError(f"{self.kind} inputs require a path (e.g. 'input/train.csv')")
            self.path = "code"
        roots = ("code",) if self.kind == "code_snapshot" else ("input", "code")
        normalized = normalize_relative_path(self.path, allowed_roots=roots)
        if self.kind != "code_snapshot" and normalized in ("input", "code"):
            raise ValueError("path must name a file below input/ or code/")
        if normalized == PARAMS_PATH:
            raise ValueError(f"{PARAMS_PATH} is reserved for job parameters")
        self.path = normalized
        return self

    def decoded_content(self) -> bytes:
        if self.content is None:
            return b""
        if self.encoding == "base64":
            return base64.b64decode(self.content, validate=True)
        return self.content.encode("utf-8")


class JobSpec(BaseModel):
    """A compute job request (contract §4.4). Unset values fall back to deployment defaults."""

    model_config = ConfigDict(extra="forbid")

    project_id: uuid.UUID
    image: str | None = Field(
        default=None, max_length=255, description="Allowlisted image (default: deployment default)"
    )
    command: list[str] = Field(..., min_length=1, max_length=256, description="argv; no shell unless you call one")
    env: dict[str, str] = Field(default_factory=dict, description="Explicit, non-secret environment variables")
    inputs: list[JobInput] = Field(default_factory=list, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict, description="Written to /workspace/input/params.json")
    resources: ResourceRequest = ResourceRequest()
    timeout_seconds: int | None = Field(
        default=None, ge=1, description="Wall-clock limit (default: deployment default)"
    )
    network: NetworkPolicy = NetworkPolicy()
    secrets: list[str] = Field(default_factory=list, max_length=32, description="Requested secret names")
    mission_id: uuid.UUID | None = None
    experiment_id: uuid.UUID | None = None
    experiment_run_id: uuid.UUID | None = None
    code_snapshot_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None
    purpose: JobPurpose = "experiment"
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200, pattern=r"^[A-Za-z0-9_\-:.]+$")


class JobResult(BaseModel):
    """Outcome of a finished job (worker-side ``run_job``; also derivable from a stored job).

    ``metrics`` are parsed from ``/workspace/output/metrics.json`` and are SELF-REPORTED by the experiment
    code (``metrics["source"] == "self_reported"``); they are never sufficient on their own for verification.
    """

    job_id: str
    status: str
    reason: str | None = None
    exit_code: int | None = None
    duration_seconds: float | None = None
    outputs: dict[str, str] = Field(default_factory=dict, description="output path → artifact version id")
    metrics: dict[str, Any] | None = None
    logs_artifact_version_id: str | None = None
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    cost_usd: Money = Decimal(0)
    error: str | None = None


# ---------------------------------------------------------------------------------------------
# HTTP response models
# ---------------------------------------------------------------------------------------------
class JobInputOut(BaseModel):
    kind: str
    ref_id: str | None = None
    path: str
    split: str | None = None
    size: int | None = None


class ComputeJobOut(ORMModel):
    id: str
    workspace_id: str
    project_id: str
    mission_id: str | None = None
    experiment_id: str | None = None
    experiment_run_id: str | None = None
    code_snapshot_id: str | None = None
    environment_id: str | None = None
    purpose: str
    backend: str
    image: str
    image_digest: str | None = None
    command: list[str]
    env: dict[str, Any]
    inputs: list[JobInputOut]
    resource_request: dict[str, Any]
    timeout_seconds: int
    network_policy: dict[str, Any]
    filesystem_policy: dict[str, Any]
    secrets_policy: dict[str, Any]
    status: str
    status_reason: str | None = None
    cancel_requested: bool
    approval_id: str | None = None
    idempotency_key: str
    attempt: int
    exit_code: int | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    outputs: dict[str, str] = Field(default_factory=dict, description="output path → artifact version id")
    metrics: dict[str, Any] | None = Field(default=None, description="Self-reported metrics from metrics.json")
    logs_artifact_version_id: str | None = None
    resource_usage: dict[str, Any]
    estimated_cost_usd: Money
    cost_usd: Money
    error: str | None = None
    workflow_run_id: str | None = None
    created_by_id: str | None = None
    created_by_agent_run_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, job: Any) -> ComputeJobOut:
        manifest = job.output_manifest or {}
        files = manifest.get("files") or {}
        inputs = [
            JobInputOut(
                kind=str(item.get("kind")),
                ref_id=item.get("ref_id"),
                path=str(item.get("path") or ""),
                split=item.get("split"),
                size=item.get("size"),
            )
            for item in (job.inputs or [])
            if isinstance(item, dict)
        ]
        return cls(
            id=str(job.id),
            workspace_id=str(job.workspace_id),
            project_id=str(job.project_id),
            mission_id=_s(job.mission_id),
            experiment_id=_s(job.experiment_id),
            experiment_run_id=_s(job.experiment_run_id),
            code_snapshot_id=_s(job.code_snapshot_id),
            environment_id=_s(job.environment_id),
            purpose=job.purpose,
            backend=job.backend,
            image=job.image,
            image_digest=job.image_digest,
            command=list(job.command or []),
            env=dict(job.env or {}),
            inputs=inputs,
            resource_request=dict(job.resource_request or {}),
            timeout_seconds=job.timeout_seconds,
            network_policy=dict(job.network_policy or {}),
            filesystem_policy=dict(job.filesystem_policy or {}),
            secrets_policy=dict(job.secrets_policy or {}),
            status=job.status,
            status_reason=job.status_reason,
            cancel_requested=bool(job.cancel_requested),
            approval_id=_s(job.approval_id),
            idempotency_key=job.idempotency_key,
            attempt=job.attempt,
            exit_code=job.exit_code,
            queued_at=job.queued_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            heartbeat_at=job.heartbeat_at,
            outputs={
                path: str(info.get("artifact_version_id")) for path, info in files.items() if isinstance(info, dict)
            },
            metrics=manifest.get("metrics"),
            logs_artifact_version_id=(manifest.get("logs") or {}).get("artifact_version_id"),
            resource_usage=dict(job.resource_usage or {}),
            estimated_cost_usd=job.estimated_cost_usd or Decimal(0),
            cost_usd=job.cost_usd or Decimal(0),
            error=job.error,
            workflow_run_id=_s(job.workflow_run_id),
            created_by_id=_s(job.created_by_id),
            created_by_agent_run_id=_s(job.created_by_agent_run_id),
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


def _s(value: Any) -> str | None:
    return str(value) if value is not None else None


class LogLineOut(BaseModel):
    ts: str | None = None
    stream: str | None = None
    text: str


class ComputeJobLogsOut(BaseModel):
    job_id: str
    status: str
    source: Literal["artifact", "events", "none"]
    text: str = Field(default="", description="Log text (untrusted output of the sandboxed program)")
    lines: list[LogLineOut] = Field(default_factory=list)
    truncated: bool = False
    logs_artifact_version_id: str | None = None


class BackendInfoOut(BaseModel):
    name: str
    enabled: bool
    configured: bool
    healthy: bool | None = None
    cpu: bool
    gpu: bool
    multi_gpu: bool
    max_timeout_seconds: int
    network_modes: list[str]
    description: str
    reason: str | None = None


class ExecutionLimitsOut(BaseModel):
    default_image: str
    allowed_images: list[str] = Field(description="Deployment allowlist (exact refs or prefixes ending with '*')")
    org_allowed_images: list[str] | None = Field(
        default=None, description="Organization allowlist; an image must match both lists when set"
    )
    max_cpu: float
    max_memory_mb: int
    max_disk_mb: int
    max_timeout_seconds: int
    default_timeout_seconds: int
    max_output_bytes: int
    max_output_files: int
    max_log_bytes: int
    gpu_enabled: bool
    egress_available: bool


class ExecutionBackendsOut(BaseModel):
    configured: str
    backends: list[BackendInfoOut]
    limits: ExecutionLimitsOut
