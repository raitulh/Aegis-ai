"""The execution backend contract.

A backend runs one :class:`~engines.lab.sandbox.SandboxSpec` in isolated compute and exposes a small,
synchronous, poll-based interface used by the worker-side job runner (``execution.service.run_job``):

``start`` (prepare + start, idempotent per job id) → ``status``/``sample_usage``/``read_logs`` while running →
``fetch_logs``/``kill``/``collect_outputs`` → ``cleanup``. ``find_by_job_id`` re-attaches to a job that is still
running after a worker restart. Backends never touch the database; they receive plain values and return plain
values, and they raise ``ExecutionUnavailable`` when their infrastructure is unreachable or not configured.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from aegis_api.lab.execution.archive import ExtractionReport, ExtractLimits
from engines.lab.sandbox import EgressConfig, SandboxSpec


class BackendState(StrEnum):
    CREATED = "created"  # provisioned / pending, not yet running
    RUNNING = "running"
    EXITED = "exited"  # the job's main process finished (or the backend terminated it)
    MISSING = "missing"  # the backend has no record of the job


@dataclass(frozen=True)
class Capabilities:
    name: str
    enabled: bool
    cpu: bool = True
    gpu: bool = False
    multi_gpu: bool = False
    max_timeout_seconds: int = 0
    network_modes: tuple[str, ...] = ("none",)
    description: str = ""
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "cpu": self.cpu,
            "gpu": self.gpu,
            "multi_gpu": self.multi_gpu,
            "max_timeout_seconds": self.max_timeout_seconds,
            "network_modes": list(self.network_modes),
            "description": self.description,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BackendHandle:
    """Opaque reference to a started job (serializable; reconstructible via ``find_by_job_id``)."""

    backend: str
    job_id: str
    backend_job_id: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendStatus:
    state: BackendState
    exit_code: int | None = None
    oom_killed: bool = False
    reason: str | None = None  # backend-specific reason (e.g. "deadline_exceeded", "image_pull_failed")
    error: str | None = None  # infrastructure error that prevented the job from running
    started_at: datetime | None = None
    finished_at: datetime | None = None
    image_digest: str | None = None  # when the backend learns it only after the job started (Kubernetes)


@dataclass(frozen=True)
class ResourceSample:
    cpu_seconds: float | None = None
    memory_bytes: int | None = None
    disk_bytes: int | None = None


@dataclass(frozen=True)
class LogLine:
    ts_ns: int
    stream: str  # stdout | stderr | main
    text: str


@dataclass(frozen=True)
class LogBatch:
    lines: list[LogLine]
    cursor_ns: int | None  # pass back as ``since_ns`` for the next batch
    truncated: bool = False


@dataclass(frozen=True)
class JobContext:
    """Everything a backend needs to start one job."""

    job_id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID
    spec: SandboxSpec
    inputs_path: Path | None
    inputs_size: int = 0
    egress: EgressConfig | None = None
    pids_limit: int = 256
    tmpfs_mb: int = 256
    log_max_bytes: int = 10 * 1024 * 1024
    allowed_images: tuple[str, ...] = ()


@dataclass(frozen=True)
class StartResult:
    handle: BackendHandle
    image_digest: str | None = None


@runtime_checkable
class ExecutionBackend(Protocol):
    name: str

    def capabilities(self) -> Capabilities: ...

    def health(self) -> bool: ...

    def start(self, ctx: JobContext) -> StartResult: ...

    def status(self, handle: BackendHandle) -> BackendStatus: ...

    def sample_usage(self, handle: BackendHandle, *, include_disk: bool = False) -> ResourceSample | None: ...

    def read_logs(self, handle: BackendHandle, *, since_ns: int | None, max_bytes: int) -> LogBatch: ...

    def fetch_logs(self, handle: BackendHandle, *, max_bytes: int) -> tuple[bytes, bool]: ...

    def kill(self, handle: BackendHandle) -> None: ...

    def collect_outputs(self, handle: BackendHandle, dest: Path, limits: ExtractLimits) -> ExtractionReport: ...

    def cleanup(self, handle: BackendHandle) -> None: ...

    def find_by_job_id(self, job_id: uuid.UUID | str) -> BackendHandle | None: ...


# ---------------------------------------------------------------------------------------------
# Helpers shared by backends
# ---------------------------------------------------------------------------------------------
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$")


def parse_rfc3339_ns(value: str) -> int | None:
    """RFC 3339 timestamp with optional nanoseconds → integer nanoseconds since the epoch."""
    match = _TS_RE.match(value.strip())
    if match is None:
        return None
    base, frac, tz = match.groups()
    try:
        dt = datetime.fromisoformat(base + ("+00:00" if tz == "Z" else tz))
    except ValueError:
        return None
    nanos = int((frac or "0").ljust(9, "0"))
    return int(dt.timestamp()) * 1_000_000_000 + nanos


def parse_rfc3339(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-01-01"):
        return None
    ns = parse_rfc3339_ns(value)
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC)


def split_timestamped_line(raw: str) -> tuple[int | None, str]:
    """``"<rfc3339nano> text"`` → ``(ts_ns, text)``."""
    head, sep, rest = raw.partition(" ")
    ts = parse_rfc3339_ns(head)
    if ts is None or not sep:
        return None, raw
    return ts, rest


def job_resource_name(job_id: uuid.UUID | str) -> str:
    """Deterministic backend object name for a job (container, volume, Kubernetes Job)."""
    return f"aegis-job-{uuid.UUID(str(job_id))}"
