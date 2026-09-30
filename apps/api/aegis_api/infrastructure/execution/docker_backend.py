"""LocalDockerBackend: hardened containers on a Docker Engine (local socket or a remote/dind daemon).

Inputs are copied into a per-job Docker *volume* via the Engine API (``put_archive``) and outputs are read back
with ``get_archive`` — no host bind mounts, so it works identically against a remote or docker-in-docker
daemon, and the sandbox never sees the worker's filesystem or the Docker socket.
"""

from __future__ import annotations

import contextlib
import threading
import time
from pathlib import Path
from typing import Any

import structlog

from aegis_api.infrastructure.execution.base import (
    OUTPUT_DIR,
    WORKSPACE,
    ExecutionBackend,
    ExecutionError,
    ExecutionRequest,
    ExecutionResult,
    build_input_tar,
    extract_outputs,
)

log = structlog.get_logger("aegis.execution.docker")
LABEL = "aegis.sandbox"


def _tail(data: bytes, limit: int) -> tuple[str, bool]:
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False
    return data[-limit:].decode("utf-8", errors="replace"), True


class _Monitor(threading.Thread):
    """Samples container stats while it runs: peak memory and cumulative CPU time (measured by the backend)."""

    def __init__(self, container: Any) -> None:
        super().__init__(daemon=True)
        self.container = container
        self.peak_bytes = 0
        self.cpu_ns = 0
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                stats = self.container.stats(stream=False)
                mem = stats.get("memory_stats") or {}
                usage = int(mem.get("max_usage") or mem.get("usage") or 0)
                self.peak_bytes = max(self.peak_bytes, usage)
                self.cpu_ns = max(
                    self.cpu_ns, int(((stats.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage") or 0)
                )
            except Exception:  # container finished between checks
                return
            self._stop_event.wait(0.5)

    def stop(self) -> None:
        self._stop_event.set()


class LocalDockerBackend(ExecutionBackend):
    name = "docker"
    supports_egress_allowlist = False
    supports_gpu = False

    def __init__(
        self,
        *,
        docker_host: str | None = None,
        user: str = "65534:65534",
        tmpfs_mb: int = 256,
        tls_cert_dir: str | None = None,
    ) -> None:
        self.docker_host = docker_host
        self.tls_cert_dir = tls_cert_dir
        self.user = user
        self.tmpfs_mb = tmpfs_mb
        self._client: Any = None
        self._running: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def client(self) -> Any:
        if self._client is None:
            import docker

            try:
                if self.docker_host:
                    tls: Any = None
                    if self.tls_cert_dir:
                        from docker.tls import TLSConfig

                        certs = Path(self.tls_cert_dir)
                        tls = TLSConfig(
                            client_cert=(str(certs / "cert.pem"), str(certs / "key.pem")),
                            ca_cert=str(certs / "ca.pem"),
                            verify=True,
                        )
                    self._client = docker.DockerClient(base_url=self.docker_host, tls=tls, timeout=60)
                else:
                    self._client = docker.from_env(timeout=60)
            except Exception as exc:
                raise ExecutionError(f"Docker daemon unavailable: {type(exc).__name__}", transient=True) from exc
        return self._client

    def health(self) -> tuple[bool, str]:
        try:
            self.client.ping()
            return True, "docker daemon reachable"
        except Exception as exc:
            return False, f"docker unavailable: {type(exc).__name__}"

    def _ensure_image(self, image: str) -> tuple[Any, str | None]:
        import docker.errors

        try:
            img = self.client.images.get(image)
        except docker.errors.ImageNotFound:
            try:
                img = self.client.images.pull(image)
            except docker.errors.APIError as exc:
                raise ExecutionError(
                    f"image '{image}' could not be pulled: {exc.explanation or exc}", transient=True
                ) from exc
        digests = img.attrs.get("RepoDigests") or []
        return img, (digests[0].split("@", 1)[1] if digests else img.id)

    def run(self, request: ExecutionRequest) -> ExecutionResult:
        import docker.errors
        import requests.exceptions

        self.validate(request)
        _, digest = self._ensure_image(request.image)
        volume = None
        container = None
        monitor: _Monitor | None = None
        started = time.monotonic()
        timed_out = False
        exit_code: int | None = None
        try:
            volume = self.client.volumes.create(
                name=f"aegis-job-{request.job_id}", labels={LABEL: "1", "aegis.job": request.job_id}
            )
            r = request.resources
            env = {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "HOME": "/tmp",
                "TMPDIR": "/tmp",
                "AEGIS_JOB_ID": request.job_id,
                **request.env,
                **request.secrets,
            }
            container = self.client.containers.create(
                request.image,
                request.command,
                name=f"aegis-job-{request.job_id}",
                user=self.user,
                working_dir=request.working_dir,
                environment=env,
                network_mode="none",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                pids_limit=r.pids,
                mem_limit=f"{r.memory_mb}m",
                memswap_limit=f"{r.memory_mb}m",
                nano_cpus=int(r.cpu * 1e9),
                tmpfs={"/tmp": f"rw,noexec,nosuid,size={self.tmpfs_mb}m"},
                volumes={volume.name: {"bind": WORKSPACE, "mode": "rw"}},
                ipc_mode="private",
                init=True,
                labels={
                    LABEL: "1",
                    "aegis.job": request.job_id,
                    **{f"aegis.{k}": v for k, v in request.labels.items()},
                },
                ulimits=[docker.types.Ulimit(name="nofile", soft=1024, hard=1024)],
                stop_signal="SIGKILL",
            )
            if not container.put_archive(
                WORKSPACE,
                build_input_tar(request.files, uid=int(self.user.split(":")[0]), gid=int(self.user.split(":")[-1])),
            ):
                raise ExecutionError("failed to stage inputs into the sandbox")
            with self._lock:
                self._running[request.job_id] = container
            container.start()
            monitor = _Monitor(container)
            monitor.start()
            try:
                status = container.wait(timeout=request.timeout_seconds)
                exit_code = int(status.get("StatusCode", -1))
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                timed_out = True
                with contextlib.suppress(docker.errors.APIError):
                    container.kill()
                container.wait(timeout=30)
            runtime = time.monotonic() - started
            container.reload()
            state = container.attrs.get("State") or {}
            oom = bool(state.get("OOMKilled")) or (exit_code == 137 and not timed_out)
            stdout, out_trunc = _tail(container.logs(stdout=True, stderr=False), request.max_log_bytes)
            stderr, err_trunc = _tail(container.logs(stdout=False, stderr=True), request.max_log_bytes)
            outputs: dict[str, bytes] = {}
            outputs_truncated = False
            try:
                stream, _stat = container.get_archive(f"{WORKSPACE}/{request.output_dir}")
                raw = bytearray()
                for chunk in stream:
                    raw.extend(chunk)
                    if len(raw) > request.max_output_bytes * 2:
                        outputs_truncated = True
                        break
                if not outputs_truncated:
                    outputs, outputs_truncated = extract_outputs(
                        bytes(raw),
                        root=request.output_dir.split("/")[-1] or OUTPUT_DIR,
                        max_bytes=request.max_output_bytes,
                    )
            except docker.errors.NotFound:
                pass
            if monitor is not None:
                monitor.stop()
            return ExecutionResult(
                exit_code=None if timed_out else exit_code,
                timed_out=timed_out,
                oom_killed=oom,
                runtime_seconds=runtime,
                peak_memory_mb=(monitor.peak_bytes / (1024 * 1024)) if monitor and monitor.peak_bytes else None,
                cpu_seconds=(monitor.cpu_ns / 1e9) if monitor and monitor.cpu_ns else None,
                stdout=stdout,
                stderr=stderr,
                logs_truncated=out_trunc or err_trunc,
                outputs=outputs,
                outputs_truncated=outputs_truncated,
                image_digest=digest,
                backend=self.name,
                backend_ref=container.id,
            )
        except docker.errors.DockerException as exc:
            raise ExecutionError(f"docker execution failed: {type(exc).__name__}: {exc}"[:500], transient=True) from exc
        finally:
            if monitor is not None:
                monitor.stop()
            with self._lock:
                self._running.pop(request.job_id, None)
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    log.warning("container_cleanup_failed", job_id=request.job_id)
            if volume is not None:
                try:
                    volume.remove(force=True)
                except Exception:
                    log.warning("volume_cleanup_failed", job_id=request.job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            container = self._running.get(job_id)
        if container is None:
            try:
                container = self.client.containers.get(f"aegis-job-{job_id}")
            except Exception:
                return False
        try:
            container.kill()
            return True
        except Exception:
            return False

    def reap_orphans(self, max_age_seconds: int = 7200) -> int:
        """Remove sandbox containers/volumes left behind by a crashed worker."""
        removed = 0
        now = time.time()
        for c in self.client.containers.list(all=True, filters={"label": LABEL}):
            created = c.attrs.get("Created", "")
            try:
                from datetime import datetime

                age = now - datetime.fromisoformat(created.replace("Z", "+00:00")[:26] + "+00:00").timestamp()
            except ValueError:
                age = max_age_seconds + 1
            if age > max_age_seconds:
                c.remove(force=True)
                removed += 1
        for v in self.client.volumes.list(filters={"label": LABEL}):
            try:
                v.remove(force=True)
            except Exception:  # in use by a live container
                log.debug("volume_in_use", volume=v.name)
        return removed
