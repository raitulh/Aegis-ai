"""Declared but not enabled execution backends (Cloud Run Jobs, AWS/GCP Batch, HPC schedulers).

They advertise their intended capabilities so clients can discover the roadmap through
``GET /execution/backends``, but every operation raises ``ExecutionUnavailable`` — nothing is simulated.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import NoReturn

from aegis_api.config import Settings, get_settings
from aegis_api.lab.core.errors import ExecutionUnavailable
from aegis_api.lab.execution.archive import ExtractionReport, ExtractLimits
from aegis_api.lab.execution.backends.base import (
    BackendHandle,
    BackendStatus,
    Capabilities,
    JobContext,
    LogBatch,
    ResourceSample,
    StartResult,
)


class _DisabledBackend:
    name = "disabled"
    label = "This backend"
    description = ""
    gpu = False
    multi_gpu = False

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _unavailable(self) -> NoReturn:
        raise ExecutionUnavailable(f"{self.label} is not enabled in this deployment")

    def capabilities(self) -> Capabilities:
        return Capabilities(
            name=self.name,
            enabled=False,
            cpu=True,
            gpu=self.gpu,
            multi_gpu=self.multi_gpu,
            max_timeout_seconds=self._settings.execution_max_timeout_seconds,
            network_modes=("none",),
            description=self.description,
            reason="not enabled in this deployment",
        )

    def health(self) -> bool:
        return False

    def start(self, ctx: JobContext) -> StartResult:
        self._unavailable()

    def status(self, handle: BackendHandle) -> BackendStatus:
        self._unavailable()

    def sample_usage(self, handle: BackendHandle, *, include_disk: bool = False) -> ResourceSample | None:
        self._unavailable()

    def read_logs(self, handle: BackendHandle, *, since_ns: int | None, max_bytes: int) -> LogBatch:
        self._unavailable()

    def fetch_logs(self, handle: BackendHandle, *, max_bytes: int) -> tuple[bytes, bool]:
        self._unavailable()

    def kill(self, handle: BackendHandle) -> None:
        self._unavailable()

    def collect_outputs(self, handle: BackendHandle, dest: Path, limits: ExtractLimits) -> ExtractionReport:
        self._unavailable()

    def cleanup(self, handle: BackendHandle) -> None:
        self._unavailable()

    def find_by_job_id(self, job_id: uuid.UUID | str) -> BackendHandle | None:
        self._unavailable()


class CloudRunBackend(_DisabledBackend):
    name = "cloud_run"
    label = "Cloud Run Jobs execution"
    description = "Google Cloud Run Jobs (gVisor sandbox); CPU and GPU (L4) tasks."
    gpu = True


class BatchBackend(_DisabledBackend):
    name = "batch"
    label = "Managed batch execution"
    description = "AWS Batch / Google Cloud Batch queues for long-running and multi-GPU jobs."
    gpu = True
    multi_gpu = True


class HPCBackend(_DisabledBackend):
    name = "hpc"
    label = "HPC scheduler execution"
    description = "Slurm / PBS clusters via a site gateway (Apptainer containers)."
    gpu = True
    multi_gpu = True
