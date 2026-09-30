"""Local Docker backend: hardened containers through the Docker Engine API (httpx over the Unix socket).

Lifecycle for one job (all objects are named ``aegis-job-<job id>`` and labelled ``aegis.job_id``):

1. negotiate the API version (``GET /version``), inspect the image and pull it only if it is allowlisted
   and missing (``POST /images/create``); the container is created from the inspected image *id* so the
   recorded digest is exactly what ran;
2. create a named local volume for ``/workspace`` (never a host bind mount, never the Docker socket);
3. create the container from :func:`engines.lab.sandbox.build_docker_create_body` after re-auditing the body
   (:func:`engines.lab.sandbox.audit_docker_body`);
4. upload the staged inputs with ``PUT /containers/{id}/archive?path=/workspace`` *before* start — this works
   with ``ReadonlyRootfs`` because the target is the volume mount, and tar-header ownership is preserved so
   ``/workspace/output`` belongs to the sandbox user while inputs stay root-owned and read-only;
5. start; the worker polls state, samples ``stats`` (CPU/memory) and volume usage (``/system/df``), streams
   logs (multiplexed 8-byte frames) and enforces the wall-clock timeout by killing the container;
6. collect ``/workspace/output`` via ``GET /containers/{id}/archive`` straight into
   :func:`~aegis_api.lab.execution.archive.safe_extract`; always remove the container and the volume.

Only ``unix://`` Docker hosts are supported (a TCP daemon would need mutual TLS; use Kubernetes instead).
Private-registry pulls are not supported: pre-pull such images on the host or pin public digests.
"""

from __future__ import annotations

import io
import json
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import structlog

from aegis_api.config import Settings, get_settings
from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.errors import ExecutionUnavailable, PermanentError, TransientError
from aegis_api.lab.execution.archive import (
    ChunkReader,
    ExtractionReport,
    ExtractLimits,
    TarBuilder,
    safe_extract,
    stream_budget,
)
from aegis_api.lab.execution.backends.base import (
    BackendHandle,
    BackendState,
    BackendStatus,
    Capabilities,
    JobContext,
    LogBatch,
    LogLine,
    ResourceSample,
    StartResult,
    job_resource_name,
    parse_rfc3339,
    split_timestamped_line,
)
from engines.lab.sandbox import (
    LABEL_JOB_ID,
    LABEL_MANAGED,
    LABEL_ORG,
    LABEL_PROJECT,
    WORKDIR,
    audit_docker_body,
    build_docker_create_body,
    image_allowed,
    parse_image_reference,
    parse_user,
)

log = structlog.get_logger("aegis.lab.execution.docker")

API_MIN = (1, 41)  # one-shot stats, CgroupnsMode
API_MAX = (1, 47)
PULL_TIMEOUT_SECONDS = 900.0
REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
TRANSFER_TIMEOUT = httpx.Timeout(600.0, connect=5.0)
_STREAM_NAMES = {0: "stdin", 1: "stdout", 2: "stderr"}


def _parse_version(value: str) -> tuple[int, int]:
    major, _, minor = value.partition(".")
    return int(major), int(minor or 0)


def _iter_frames(chunks: Iterator[bytes]) -> Iterator[tuple[int, bytes]]:
    """Demultiplex Docker's ``application/vnd.docker.multiplexed-stream`` (8-byte frame headers)."""
    buf = bytearray()
    for chunk in chunks:
        buf += chunk
        while len(buf) >= 8:
            size = int.from_bytes(buf[4:8], "big")
            if len(buf) < 8 + size:
                break
            stream_type = buf[0]
            payload = bytes(buf[8 : 8 + size])
            del buf[: 8 + size]
            yield stream_type, payload
    if buf:
        # Trailing bytes without a complete header (should not happen): surface them as stdout.
        yield 1, bytes(buf)


class LocalDockerBackend:
    name = "local_docker"

    def __init__(self, settings: Settings | None = None, *, client: httpx.Client | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._prefix: str | None = None
        self._lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                host = self._settings.docker_host or ""
                if not host.startswith("unix://"):
                    raise ExecutionUnavailable(
                        "The local Docker backend only supports unix:// Docker hosts (set DOCKER_HOST)"
                    )
                path = host.removeprefix("unix://")
                if not path.startswith("/"):
                    raise ExecutionUnavailable("DOCKER_HOST must be an absolute unix socket path")
                self._client = httpx.Client(
                    transport=httpx.HTTPTransport(uds=path), base_url="http://docker", timeout=REQUEST_TIMEOUT
                )
            return self._client

    def _api(self) -> str:
        if self._prefix is not None:
            return self._prefix
        info = self._raw("GET", "/version").json()
        server = _parse_version(str(info.get("ApiVersion", "0.0")))
        minimum = _parse_version(str(info.get("MinAPIVersion", "1.12")))
        chosen = min(server, API_MAX)
        if chosen < API_MIN or chosen < minimum:
            raise ExecutionUnavailable(
                f"Docker API {server[0]}.{server[1]} is not supported (need {API_MIN[0]}.{API_MIN[1]}+)"
            )
        self._prefix = f"/v{chosen[0]}.{chosen[1]}"
        return self._prefix

    def _raw(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._http().request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise TransientError(f"Docker API timeout on {method} {path.split('?')[0]}") from exc
        except httpx.TransportError as exc:
            raise ExecutionUnavailable("The Docker daemon is unreachable") from exc

    def _call(self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201, 204), **kwargs: Any) -> httpx.Response:
        response = self._raw(method, self._api() + path, **kwargs)
        if response.status_code not in ok:
            raise self._error(response, method, path)
        return response

    @contextmanager
    def _stream(
        self, method: str, path: str, *, ok: tuple[int, ...] = (200,), **kwargs: Any
    ) -> Iterator[httpx.Response]:
        try:
            with self._http().stream(method, self._api() + path, **kwargs) as response:
                if response.status_code not in ok:
                    response.read()
                    raise self._error(response, method, path)
                yield response
        except httpx.TimeoutException as exc:
            raise TransientError(f"Docker API timeout on {method} {path.split('?')[0]}") from exc
        except httpx.TransportError as exc:
            raise ExecutionUnavailable("The Docker daemon is unreachable") from exc

    @staticmethod
    def _error(response: httpx.Response, method: str, path: str) -> Exception:
        try:
            message = str(response.json().get("message", ""))[:500]
        except (ValueError, AttributeError):
            message = response.text[:500]
        where = f"{method} {path.split('?')[0]}"
        if response.status_code >= 500:
            return TransientError(f"Docker API error {response.status_code} on {where}: {message}")
        return PermanentError(f"Docker API error {response.status_code} on {where}: {message}")

    # -- contract ---------------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        s = self._settings
        modes: tuple[str, ...] = ("none",)
        if s.execution_egress_network and s.execution_egress_proxy_url:
            modes = ("none", "allowlist")
        return Capabilities(
            name=self.name,
            enabled=s.execution_backend == self.name,
            cpu=True,
            gpu=bool(s.execution_gpu_enabled),
            multi_gpu=bool(s.execution_gpu_enabled),
            max_timeout_seconds=s.execution_max_timeout_seconds,
            network_modes=modes,
            description="Hardened containers on the local Docker daemon (development and single-node).",
        )

    def health(self) -> bool:
        try:
            response = self._raw("GET", "/_ping", timeout=httpx.Timeout(3.0, connect=2.0))
        except (ExecutionUnavailable, TransientError):
            return False
        return response.status_code == 200

    def start(self, ctx: JobContext) -> StartResult:
        name = job_resource_name(ctx.job_id)
        image_id, digest = self._ensure_image(ctx.spec.image, ctx.allowed_images)
        labels = {
            LABEL_MANAGED: "true",
            LABEL_JOB_ID: str(ctx.job_id),
            LABEL_ORG: str(ctx.organization_id),
            LABEL_PROJECT: str(ctx.project_id),
        }
        egress_network = ctx.egress.network if ctx.egress else None
        proxy = ctx.egress.proxy_url if ctx.egress else None
        spec = ctx.spec.model_copy(update={"labels": {**ctx.spec.labels, **labels}})
        body = build_docker_create_body(
            spec,
            volume_name=name,
            egress_network=egress_network,
            proxy=proxy,
            pids_limit=ctx.pids_limit,
            tmpfs_mb=ctx.tmpfs_mb,
            log_max_bytes=ctx.log_max_bytes,
            image_ref=image_id,
        )
        problems = audit_docker_body(body, allowed_networks=[egress_network] if egress_network else [])
        if problems:
            raise PermanentError("Refusing to create an insecure sandbox: " + "; ".join(problems))

        self._call("POST", "/volumes/create", json={"Name": name, "Driver": "local", "Labels": labels})
        container_id = self._create_container(name, body)
        handle = BackendHandle(
            backend=self.name,
            job_id=str(ctx.job_id),
            backend_job_id=container_id,
            details={"container": name, "volume": name},
        )
        try:
            self._put_inputs(container_id, ctx)
            self._call("POST", f"/containers/{container_id}/start", ok=(204, 304))
        except BaseException:
            self.cleanup(handle)
            raise
        return StartResult(handle=handle, image_digest=digest)

    def status(self, handle: BackendHandle) -> BackendStatus:
        response = self._call("GET", f"/containers/{handle.backend_job_id}/json", ok=(200, 404))
        if response.status_code == 404:
            return BackendStatus(state=BackendState.MISSING)
        state = response.json().get("State") or {}
        raw = str(state.get("Status") or "")
        started = parse_rfc3339(state.get("StartedAt"))
        finished = parse_rfc3339(state.get("FinishedAt"))
        oom = bool(state.get("OOMKilled"))
        error = str(state.get("Error") or "") or None
        if raw in ("exited", "dead"):
            return BackendStatus(
                state=BackendState.EXITED,
                exit_code=int(state.get("ExitCode", -1)),
                oom_killed=oom,
                reason="oom" if oom else None,
                error=error,
                started_at=started,
                finished_at=finished,
            )
        if raw == "created":
            return BackendStatus(state=BackendState.CREATED, error=error)
        return BackendStatus(state=BackendState.RUNNING, oom_killed=oom, started_at=started)

    def sample_usage(self, handle: BackendHandle, *, include_disk: bool = False) -> ResourceSample | None:
        response = self._call(
            "GET",
            f"/containers/{handle.backend_job_id}/stats",
            params={"stream": "false", "one-shot": "true"},
            ok=(200, 404, 409),
        )
        if response.status_code != 200:
            return None
        stats = response.json()
        cpu_ns = ((stats.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage") or 0
        memory = stats.get("memory_stats") or {}
        mem = max(int(memory.get("max_usage") or 0), int(memory.get("usage") or 0))
        disk: int | None = None
        if include_disk:
            disk = self._volume_size(str(handle.details.get("volume") or ""))
        return ResourceSample(
            cpu_seconds=(cpu_ns / 1_000_000_000) if cpu_ns else None,
            memory_bytes=mem or None,
            disk_bytes=disk,
        )

    def read_logs(self, handle: BackendHandle, *, since_ns: int | None, max_bytes: int) -> LogBatch:
        params: dict[str, str] = {"stdout": "1", "stderr": "1", "timestamps": "1", "follow": "0"}
        if since_ns:
            params["since"] = f"{since_ns // 1_000_000_000}.{since_ns % 1_000_000_000:09d}"
        lines: list[LogLine] = []
        cursor = since_ns
        consumed = 0
        truncated = False
        try:
            with self._stream("GET", f"/containers/{handle.backend_job_id}/logs", params=params) as response:
                for stream_type, payload in _iter_frames(response.iter_bytes()):
                    consumed += len(payload)
                    if consumed > max_bytes:
                        truncated = True
                        break
                    for raw in payload.decode("utf-8", "replace").splitlines():
                        ts, text = split_timestamped_line(raw)
                        if ts is None:
                            continue
                        if since_ns is not None and ts <= since_ns:
                            continue
                        lines.append(LogLine(ts_ns=ts, stream=_STREAM_NAMES.get(stream_type, "stdout"), text=text))
                        cursor = max(cursor or 0, ts)
        except PermanentError:
            return LogBatch(lines=[], cursor_ns=since_ns)
        return LogBatch(lines=lines, cursor_ns=cursor, truncated=truncated)

    def fetch_logs(self, handle: BackendHandle, *, max_bytes: int) -> tuple[bytes, bool]:
        params = {"stdout": "1", "stderr": "1", "timestamps": "0", "follow": "0"}
        out = bytearray()
        truncated = False
        try:
            with self._stream("GET", f"/containers/{handle.backend_job_id}/logs", params=params) as response:
                for _stream_type, payload in _iter_frames(response.iter_bytes()):
                    room = max_bytes - len(out)
                    if len(payload) > room:
                        out += payload[: max(room, 0)]
                        truncated = True
                        break
                    out += payload
        except PermanentError:
            return b"", False
        return bytes(out), truncated

    def kill(self, handle: BackendHandle) -> None:
        self._call(
            "POST",
            f"/containers/{handle.backend_job_id}/kill",
            params={"signal": "SIGKILL"},
            ok=(204, 304, 404, 409),
        )

    def collect_outputs(self, handle: BackendHandle, dest: Path, limits: ExtractLimits) -> ExtractionReport:
        path = f"/containers/{handle.backend_job_id}/archive"
        with self._stream(
            "GET", path, params={"path": f"{WORKDIR}/output"}, ok=(200, 404), timeout=TRANSFER_TIMEOUT
        ) as response:
            if response.status_code == 404:
                return ExtractionReport()
            reader = ChunkReader(response.iter_bytes(), stream_budget(limits))
            return safe_extract(io.BufferedReader(reader), dest, limits, strip_prefix="output", strict=False)

    def cleanup(self, handle: BackendHandle) -> None:
        try:
            self._call(
                "DELETE",
                f"/containers/{handle.backend_job_id}",
                params={"force": "true", "v": "true"},
                ok=(204, 404),
            )
        except Exception:
            log.warning("docker_container_remove_failed", job_id=handle.job_id, exc_info=True)
        volume = str(handle.details.get("volume") or job_resource_name(handle.job_id))
        for attempt in range(5):
            try:
                response = self._call("DELETE", f"/volumes/{volume}", params={"force": "true"}, ok=(204, 404, 409))
            except Exception:
                log.warning("docker_volume_remove_failed", job_id=handle.job_id, exc_info=True)
                return
            if response.status_code != 409:
                return
            time.sleep(0.2 * (attempt + 1))  # container removal still releasing the volume
        log.warning("docker_volume_in_use", job_id=handle.job_id, volume=volume)

    def find_by_job_id(self, job_id: uuid.UUID | str) -> BackendHandle | None:
        filters = json.dumps({"label": [f"{LABEL_JOB_ID}={uuid.UUID(str(job_id))}"]})
        response = self._call("GET", "/containers/json", params={"all": "true", "filters": filters})
        items = response.json() or []
        if not items:
            return None
        item = items[0]
        name = job_resource_name(job_id)
        return BackendHandle(
            backend=self.name,
            job_id=str(uuid.UUID(str(job_id))),
            backend_job_id=str(item["Id"]),
            details={"container": name, "volume": name},
        )

    # -- helpers ----------------------------------------------------------------------------------
    def _ensure_image(self, image: str, allowed: tuple[str, ...]) -> tuple[str, str | None]:
        allowlist = allowed or tuple(self._settings.execution_allowed_image_list)
        if not image_allowed(image, allowlist):
            raise ValidationFailed(f"Image {image!r} is not in the allowed image list")
        ref = parse_image_reference(image)
        info = self._inspect_image(image)
        if info is None:
            self._pull(ref.repository, ref.pull_tag)
            info = self._inspect_image(image)
            if info is None:
                raise PermanentError(f"Image {image!r} is not available after pull")
        image_id = str(info.get("Id") or "")
        if not image_id.startswith("sha256:"):
            raise PermanentError(f"Unexpected image id for {image!r}")
        # Prefer the registry digest of the requested repository; fall back to any registry digest, then to
        # the local image id (locally built images have no registry digest).
        exact: str | None = None
        fallback: str | None = None
        for entry in info.get("RepoDigests") or []:
            name, _, candidate = str(entry).partition("@")
            if not candidate.startswith("sha256:"):
                continue
            if name == ref.repository and exact is None:
                exact = candidate
            elif fallback is None:
                fallback = candidate
        return image_id, exact or fallback or image_id

    def _inspect_image(self, image: str) -> dict[str, Any] | None:
        response = self._call("GET", f"/images/{image}/json", ok=(200, 404))
        if response.status_code == 404:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None

    def _pull(self, repository: str, tag: str) -> None:
        log.info("docker_image_pull", repository=repository, tag=tag)
        deadline = time.monotonic() + PULL_TIMEOUT_SECONDS
        with self._stream(
            "POST",
            "/images/create",
            params={"fromImage": repository, "tag": tag},
            timeout=httpx.Timeout(PULL_TIMEOUT_SECONDS, connect=5.0),
        ) as response:
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    raise TransientError(f"Pulling {repository}:{tag} timed out")
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and (event.get("error") or event.get("errorDetail")):
                    message = str(event.get("error") or event.get("errorDetail"))[:300]
                    raise PermanentError(f"Pulling {repository}:{tag} failed: {message}")

    def _create_container(self, name: str, body: dict[str, Any]) -> str:
        for attempt in range(2):
            response = self._call("POST", "/containers/create", params={"name": name}, json=body, ok=(201, 409))
            if response.status_code == 201:
                return str(response.json()["Id"])
            if attempt == 0:
                # Left over from a crashed provisioning attempt of the same job: remove and recreate.
                self._call("DELETE", f"/containers/{name}", params={"force": "true", "v": "true"}, ok=(204, 404))
        raise PermanentError(f"Container {name} already exists and could not be replaced")

    def _put_inputs(self, container_id: str, ctx: JobContext) -> None:
        if ctx.inputs_path is not None:
            size = ctx.inputs_path.stat().st_size
            with ctx.inputs_path.open("rb") as fh:
                self._call(
                    "PUT",
                    f"/containers/{container_id}/archive",
                    params={"path": WORKDIR, "noOverwriteDirNonDir": "true"},
                    content=_file_chunks(fh),
                    headers={"Content-Type": "application/x-tar", "Content-Length": str(size)},
                    timeout=TRANSFER_TIMEOUT,
                )
            return
        uid, gid = parse_user(ctx.spec.user)
        buf = io.BytesIO()
        with TarBuilder(buf) as tar:
            tar.add_dir("input", mode=0o555)
            tar.add_dir("code", mode=0o555)
            tar.add_dir("output", mode=0o755, uid=uid, gid=gid)
        self._call(
            "PUT",
            f"/containers/{container_id}/archive",
            params={"path": WORKDIR, "noOverwriteDirNonDir": "true"},
            content=buf.getvalue(),
            headers={"Content-Type": "application/x-tar"},
        )

    def _volume_size(self, volume: str) -> int | None:
        if not volume:
            return None
        response = self._call("GET", "/system/df", params={"type": "volume"}, ok=(200, 400))
        if response.status_code != 200:
            return None
        for item in response.json().get("Volumes") or []:
            if item.get("Name") == volume:
                size = int((item.get("UsageData") or {}).get("Size", -1))
                return size if size >= 0 else None
        return None


def _file_chunks(fh: Any, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    while True:
        chunk = fh.read(chunk_size)
        if not chunk:
            return
        yield chunk
