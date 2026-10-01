"""Isolated execution of evaluator code.

Evaluation never runs inside the API process. The worker prepares an
ephemeral working directory containing copies of the submission and the
hidden ground truth, then runs the evaluator in a separate process:

* SubprocessSandbox (default): isolated Python (-I), scrubbed environment,
  new session, RLIMIT_CPU / RLIMIT_AS / RLIMIT_FSIZE / RLIMIT_NOFILE / no core
  dumps, wall-clock timeout, process-group kill on timeout.
* DockerSandbox (recommended for untrusted code): no network, read-only root,
  dropped capabilities, no-new-privileges, non-root user, pids/memory/cpu
  limits, read-only mount of the working directory. Never mounts the Docker
  socket or host paths other than the ephemeral workdir.

The working directory is deleted by the caller after every run.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings

RUNNER_PATH = str(Path(__file__).with_name("runner_entry.py"))
MAX_OUTPUT_BYTES = 1024 * 1024


class SandboxError(Exception):
    """The sandbox failed to produce a result (timeout, crash, resource limit)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Sandbox(Protocol):
    name: str

    def run(self, workdir: str, phase: str) -> dict[str, Any]: ...


def _parse_output(stdout: bytes) -> dict[str, Any]:
    if len(stdout) > MAX_OUTPUT_BYTES:
        raise SandboxError("output_too_large", "Evaluator output exceeded the size limit.")
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SandboxError("bad_output", "Evaluator produced unreadable output.") from None
    if not isinstance(data, dict):
        raise SandboxError("bad_output", "Evaluator produced unexpected output.")
    return data


class SubprocessSandbox:
    name = "subprocess"

    def __init__(self, *, timeout_s: int, cpu_s: int, memory_mb: int) -> None:
        self.timeout_s = timeout_s
        self.cpu_s = cpu_s
        self.memory_bytes = memory_mb * 1024 * 1024

    def _limits(self) -> None:  # runs in the child between fork and exec
        import resource

        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_s, self.cpu_s + 5))
        resource.setrlimit(resource.RLIMIT_AS, (self.memory_bytes, self.memory_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def run(self, workdir: str, phase: str) -> dict[str, Any]:
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0",
               "PYTHONDONTWRITEBYTECODE": "1", "HOME": workdir}
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-I", "-B", RUNNER_PATH, workdir, phase],
            cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=self._limits,  # noqa: PLW1509
        )
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
            raise SandboxError("timeout", f"Evaluation exceeded {self.timeout_s}s.") from None
        if proc.returncode != 0:
            reason = "memory_limit" if b"MemoryError" in stderr else ("cpu_limit" if proc.returncode in (-signal.SIGXCPU, -signal.SIGKILL) else "crash")
            raise SandboxError(reason, f"Evaluator exited with status {proc.returncode}.")
        return _parse_output(stdout)


class DockerSandbox:
    name = "docker"

    def __init__(self, *, image: str, timeout_s: int, memory_mb: int) -> None:
        self.image = image
        self.timeout_s = timeout_s
        self.memory_mb = memory_mb

    def run(self, workdir: str, phase: str) -> dict[str, Any]:
        cmd = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",  # noqa: S108 — tmpfs inside the sandbox container
            "--memory", f"{self.memory_mb}m", "--memory-swap", f"{self.memory_mb}m",
            "--cpus", "1", "--pids-limit", "64", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "65534:65534",
            "-v", f"{workdir}:/work:ro", self.image, "/work", phase,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout_s + 15, check=False)  # noqa: S603
        except subprocess.TimeoutExpired:
            raise SandboxError("timeout", f"Evaluation exceeded {self.timeout_s}s.") from None
        if proc.returncode != 0:
            raise SandboxError("crash", f"Evaluator container exited with status {proc.returncode}.")
        return _parse_output(proc.stdout)


def get_sandbox() -> Sandbox:
    if settings.EVALUATOR_SANDBOX == "docker":
        return DockerSandbox(image=settings.EVALUATOR_DOCKER_IMAGE, timeout_s=settings.EVALUATOR_TIMEOUT_SECONDS,
                             memory_mb=settings.EVALUATOR_MEMORY_MB)
    return SubprocessSandbox(timeout_s=settings.EVALUATOR_TIMEOUT_SECONDS, cpu_s=settings.EVALUATOR_CPU_SECONDS,
                             memory_mb=settings.EVALUATOR_MEMORY_MB)
