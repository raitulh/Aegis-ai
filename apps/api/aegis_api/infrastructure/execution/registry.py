"""Select the configured execution backend.

Extension points (documented in docs/execution-fabric.md) — Cloud Run jobs, AWS Batch / GCP Batch and HPC
schedulers (Slurm) implement the same ``ExecutionBackend`` contract; they are intentionally not shipped as
stubs. ``disabled`` refuses all execution (e.g. API-only deployments).
"""

from __future__ import annotations

from functools import lru_cache

from aegis_api.config import get_settings
from aegis_api.infrastructure.execution.base import ExecutionBackend, ExecutionError


@lru_cache(maxsize=1)
def get_backend() -> ExecutionBackend:
    settings = get_settings()
    if settings.execution_backend == "docker":
        from aegis_api.infrastructure.execution.docker_backend import LocalDockerBackend

        return LocalDockerBackend(
            docker_host=settings.docker_host,
            user=settings.execution_user,
            tmpfs_mb=settings.execution_tmpfs_mb,
            tls_cert_dir=settings.docker_tls_cert_dir,
        )
    if settings.execution_backend == "kubernetes":
        from aegis_api.infrastructure.execution.kubernetes_backend import KubernetesBackend
        from aegis_api.infrastructure.storage import output_channel_factory

        if not settings.kubernetes_api_url:
            raise ExecutionError("KUBERNETES_API_URL is not configured")
        return KubernetesBackend(
            api_url=settings.kubernetes_api_url,
            namespace=settings.kubernetes_namespace,
            token_path=settings.kubernetes_token_path,
            ca_path=settings.kubernetes_ca_path,
            runtime_class=settings.kubernetes_runtime_class,
            output_channel_factory=output_channel_factory,
        )
    raise ExecutionError("Execution is disabled on this deployment (EXECUTION_BACKEND=disabled)")


def reset_backend() -> None:
    get_backend.cache_clear()
