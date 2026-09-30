"""Execution fabric contract.

Generated research code NEVER runs inside the API or worker process. It runs through an ``ExecutionBackend`` in
an isolated container with: no network (deny-by-default), a read-only root filesystem, a non-root user, all
capabilities dropped, no-new-privileges, PID/CPU/memory/disk limits, a wall-clock timeout, bounded logs and
outputs, and an *explicit* environment (nothing is inherited from the host). The backend — not the code under
test — measures runtime, memory and exit status.
"""

from __future__ import annotations

import io
import posixpath
import tarfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

WORKSPACE = "/workspace"
CODE_DIR = "code"
INPUT_DIR = "input"
DATA_DIR = "data"
OUTPUT_DIR = "output"

SAFE_ENV_PREFIXES = ("AEGIS_", "PYTHON", "OMP_", "MKL_", "OPENBLAS_")


class ExecutionError(Exception):
    """The backend failed to run the job (infrastructure problem, not a failure of the code under test)."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class ExecutionPolicyError(ExecutionError):
    """The request violates the sandbox policy (e.g. network or secrets without approval)."""


@dataclass(frozen=True)
class ResourceSpec:
    cpu: float = 1.0
    memory_mb: int = 1024
    disk_mb: int = 1024
    pids: int = 256
    gpu_count: int = 0
    gpu_type: str | None = None


@dataclass
class ExecutionRequest:
    job_id: str
    organization_id: str
    image: str
    command: list[str]
    files: dict[str, bytes] = field(default_factory=dict)  # relative paths under /workspace (code/, input/, data/)
    env: dict[str, str] = field(default_factory=dict)
    resources: ResourceSpec = field(default_factory=ResourceSpec)
    timeout_seconds: int = 600
    network: Literal["none", "allowlist"] = "none"
    egress_allowlist: list[str] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict)  # only populated after policy approval
    output_dir: str = OUTPUT_DIR
    labels: dict[str, str] = field(default_factory=dict)
    max_output_bytes: int = 50 * 1024 * 1024
    max_log_bytes: int = 2 * 1024 * 1024
    working_dir: str = WORKSPACE


@dataclass
class ExecutionResult:
    exit_code: int | None
    timed_out: bool
    oom_killed: bool
    runtime_seconds: float
    peak_memory_mb: float | None
    cpu_seconds: float | None
    stdout: str
    stderr: str
    logs_truncated: bool
    outputs: dict[str, bytes]
    outputs_truncated: bool
    image_digest: str | None
    backend: str
    backend_ref: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.oom_killed and self.error is None

    def measured(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "oom_killed": self.oom_killed,
            "runtime_seconds": round(self.runtime_seconds, 3),
            "peak_memory_mb": None if self.peak_memory_mb is None else round(self.peak_memory_mb, 2),
            "cpu_seconds": None if self.cpu_seconds is None else round(self.cpu_seconds, 3),
            "logs_truncated": self.logs_truncated,
            "outputs_truncated": self.outputs_truncated,
            "output_bytes": sum(len(v) for v in self.outputs.values()),
            "backend": self.backend,
        }


class ExecutionBackend(ABC):
    name: str = "base"
    supports_egress_allowlist: bool = False
    supports_gpu: bool = False

    @abstractmethod
    def run(self, request: ExecutionRequest) -> ExecutionResult:
        """Run to completion (or timeout) and return measured results. Must always clean up."""

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Best-effort cancellation of a running job."""

    @abstractmethod
    def health(self) -> tuple[bool, str]: ...

    def validate(self, request: ExecutionRequest) -> None:
        if request.network != "none" and not self.supports_egress_allowlist:
            raise ExecutionPolicyError(f"{self.name} backend cannot enforce egress allowlists; network must be 'none'")
        if request.resources.gpu_count and not self.supports_gpu:
            raise ExecutionPolicyError(f"{self.name} backend does not provide GPUs")
        for key in request.env:
            if not key.startswith(SAFE_ENV_PREFIXES):
                raise ExecutionPolicyError(
                    f"environment variable '{key}' is not allowed (explicit AEGIS_/PYTHON* only)"
                )
        for path in request.files:
            safe_relative_path(path)


def safe_relative_path(path: str) -> str:
    """Normalize a workspace-relative path; reject absolute paths, traversal and odd characters."""
    if not path or "\x00" in path or "\\" in path:
        raise ExecutionPolicyError(f"invalid path '{path}'")
    norm = posixpath.normpath(path)
    if norm.startswith(("/", "..")) or norm == "." or any(part == ".." for part in PurePosixPath(norm).parts):
        raise ExecutionPolicyError(f"path escapes the workspace: '{path}'")
    top = PurePosixPath(norm).parts[0]
    if top not in (CODE_DIR, INPUT_DIR, DATA_DIR, OUTPUT_DIR):
        raise ExecutionPolicyError(f"files must live under code/, input/, data/ or output/: '{path}'")
    return norm


def build_input_tar(files: dict[str, bytes], *, uid: int = 65534, gid: int = 65534) -> bytes:
    """Tar of the workspace skeleton + files, owned by the sandbox user."""
    buf = io.BytesIO()
    dirs = {CODE_DIR, INPUT_DIR, DATA_DIR, OUTPUT_DIR}
    for path in files:
        parts = PurePosixPath(safe_relative_path(path)).parts
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for d in sorted(dirs):
            info = tarfile.TarInfo(d)
            info.type = tarfile.DIRTYPE
            info.mode = 0o775 if d.startswith(OUTPUT_DIR) else 0o555
            info.uid, info.gid = uid, gid
            tar.addfile(info)
        for path, data in sorted(files.items()):
            info = tarfile.TarInfo(safe_relative_path(path))
            info.size = len(data)
            info.mode = 0o444
            info.uid, info.gid = uid, gid
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def extract_outputs(
    tar_bytes: bytes, *, root: str, max_bytes: int, max_files: int = 2000
) -> tuple[dict[str, bytes], bool]:
    """Safely read regular files from an output tar (no links/devices/absolute paths/traversal)."""
    out: dict[str, bytes] = {}
    total = 0
    truncated = False
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
        for member in tar:
            if not member.isreg():
                continue
            name = posixpath.normpath(member.name)
            if name.startswith(("/", "..")) or ".." in PurePosixPath(name).parts:
                continue
            rel = name[len(root) + 1 :] if name.startswith(root + "/") else name
            if not rel or len(out) >= max_files:
                truncated = True
                continue
            if total + member.size > max_bytes:
                truncated = True
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            data = handle.read(member.size + 1)[: member.size]
            total += len(data)
            out[rel] = data
    return out, truncated
