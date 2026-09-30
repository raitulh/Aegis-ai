"""Execution backend registry.

``get_backend()`` returns the backend selected by ``EXECUTION_BACKEND`` (``local_docker`` | ``kubernetes`` |
``disabled``). Instances are cached per process (they hold HTTP connection pools). Tests may install a
backend with :func:`register_backend` and restore defaults with :func:`reset_backends`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from aegis_api.config import Settings, get_settings
from aegis_api.lab.core.errors import ExecutionUnavailable
from aegis_api.lab.execution.backends.base import (
    BackendHandle,
    BackendState,
    BackendStatus,
    Capabilities,
    ExecutionBackend,
    JobContext,
    LogBatch,
    LogLine,
    ResourceSample,
    StartResult,
)

__all__ = [
    "BACKEND_NAMES",
    "BackendHandle",
    "BackendState",
    "BackendStatus",
    "Capabilities",
    "ExecutionBackend",
    "JobContext",
    "LogBatch",
    "LogLine",
    "ResourceSample",
    "StartResult",
    "describe_backends",
    "get_backend",
    "register_backend",
    "reset_backends",
]

BACKEND_NAMES: tuple[str, ...] = ("local_docker", "kubernetes", "cloud_run", "batch", "hpc")

_lock = threading.Lock()
_instances: dict[str, ExecutionBackend] = {}
_overrides: dict[str, ExecutionBackend] = {}


def _build(name: str, settings: Settings) -> ExecutionBackend:
    if name == "local_docker":
        from aegis_api.lab.execution.backends.docker import LocalDockerBackend

        return LocalDockerBackend(settings)
    if name == "kubernetes":
        from aegis_api.lab.execution.backends.kubernetes import KubernetesBackend

        return KubernetesBackend(settings)
    from aegis_api.lab.execution.backends.future import BatchBackend, CloudRunBackend, HPCBackend

    future: dict[str, Callable[[Settings], ExecutionBackend]] = {
        "cloud_run": CloudRunBackend,
        "batch": BatchBackend,
        "hpc": HPCBackend,
    }
    if name in future:
        return future[name](settings)
    raise ExecutionUnavailable(f"Unknown execution backend {name!r}")


def get_backend(name: str | None = None) -> ExecutionBackend:
    """The named backend (default: the configured one). Raises ``ExecutionUnavailable`` when disabled."""
    settings = get_settings()
    selected = name or settings.execution_backend
    if selected == "disabled":
        raise ExecutionUnavailable("Sandboxed execution is disabled in this deployment (EXECUTION_BACKEND=disabled)")
    if selected in _overrides:
        return _overrides[selected]
    with _lock:
        backend = _instances.get(selected)
        if backend is None:
            backend = _build(selected, settings)
            _instances[selected] = backend
        return backend


def register_backend(name: str, backend: ExecutionBackend | None) -> None:
    """Install (or with ``None`` remove) an explicit backend instance for ``name``."""
    with _lock:
        if backend is None:
            _overrides.pop(name, None)
        else:
            _overrides[name] = backend


def reset_backends() -> None:
    with _lock:
        _instances.clear()
        _overrides.clear()


def describe_backends(*, check_health: bool = True) -> list[tuple[Capabilities, bool | None]]:
    """Capabilities of every known backend (+ health for the configured one)."""
    settings = get_settings()
    out: list[tuple[Capabilities, bool | None]] = []
    for name in BACKEND_NAMES:
        # The configured backend is the cached instance (reuses its connection pool); the others are
        # constructed only to report their capabilities (construction performs no IO).
        backend = (
            get_backend(name) if name == settings.execution_backend else _overrides.get(name) or _build(name, settings)
        )
        caps = backend.capabilities()
        healthy: bool | None = None
        if check_health and name == settings.execution_backend:
            try:
                healthy = backend.health()
            except Exception:
                healthy = False
        out.append((caps, healthy))
    return out
