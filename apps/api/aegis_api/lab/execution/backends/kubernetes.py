"""Kubernetes backend: one ``batch/v1`` Job per compute job, driven through the Kubernetes REST API (httpx).

* Authentication uses the in-cluster service-account token (re-read on every request because projected
  tokens rotate) and the cluster CA from ``K8S_CA_PATH``. The worker's service account needs only
  ``create/get/list/delete`` on ``jobs`` and ``get/list`` on ``pods`` + ``pods/log`` in ``K8S_NAMESPACE``.
* The Job manifest comes from :func:`engines.lab.sandbox.build_k8s_job` (restricted Pod Security profile,
  requests = limits, ``activeDeadlineSeconds``, ``backoffLimit: 0``, no service-account token, no service
  links). Network isolation is a default-deny ``NetworkPolicy`` managed by the Helm chart; pods labelled
  ``aegis.io/egress=allowlist`` may reach only the egress proxy.
* Inputs and outputs move through object storage: the platform uploads the staged inputs tar and hands the
  pod a presigned GET URL (``fetch-inputs`` init container); the ``upload-outputs`` native sidecar PUTs the
  outputs tar to a presigned URL once the main container finishes. This requires S3-compatible object
  storage — the local filesystem backend cannot mint URLs reachable from inside the cluster, so the backend
  refuses with ``ExecutionUnavailable``.
"""

from __future__ import annotations

import io
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

from aegis_api.config import Settings, get_settings
from aegis_api.lab.core.errors import ExecutionUnavailable, PermanentError, TransientError
from aegis_api.lab.execution.archive import ChunkReader, ExtractionReport, ExtractLimits, safe_extract, stream_budget
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
    K8S_LABEL_JOB_ID,
    K8S_LABEL_ORG,
    K8S_LABEL_PROJECT,
    LABEL_JOB_ID,
    LABEL_ORG,
    LABEL_PROJECT,
    build_k8s_job,
    image_allowed,
)

log = structlog.get_logger("aegis.lab.execution.kubernetes")

REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
MAX_PRESIGN_TTL_SECONDS = 7 * 24 * 3600
TRANSFER_TTL_GRACE_SECONDS = 3600
_IMAGE_ERRORS = frozenset({"ErrImagePull", "ImagePullBackOff", "InvalidImageName", "ErrImageNeverPull"})
_CONFIG_ERRORS = frozenset({"CreateContainerConfigError", "CreateContainerError", "RunContainerError"})

PresignPut = Callable[[str, int], str]


def _transfer_keys(organization_id: str, project_id: str, job_id: str) -> tuple[str, str]:
    from aegis_api.lab.storage.keys import object_key

    return (
        object_key(organization_id, project_id, "compute-jobs", job_id, "inputs.tar"),
        object_key(organization_id, project_id, "compute-jobs", job_id, "outputs.tar"),
    )


class KubernetesBackend:
    name = "kubernetes"
    output_wait_seconds: float = 120.0
    output_poll_seconds: float = 2.0

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        storage: Any | None = None,
        presign_put: PresignPut | None = None,
        token: str | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._storage = storage
        self._presign_put = presign_put
        self._token = token
        self._lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                ca = Path(self._settings.k8s_ca_path)
                verify: str | bool = str(ca) if ca.is_file() else True
                self._client = httpx.Client(
                    base_url=self._settings.k8s_api_url.rstrip("/"),
                    verify=verify,
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=False,
                )
            return self._client

    def _auth(self) -> dict[str, str]:
        token = self._token
        if token is None:
            try:
                token = Path(self._settings.k8s_token_path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ExecutionUnavailable("Kubernetes service-account token is not available") from exc
        if not token:
            raise ExecutionUnavailable("Kubernetes service-account token is empty")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _request(
        self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201, 202), **kwargs: Any
    ) -> httpx.Response:
        headers = {**self._auth(), **kwargs.pop("headers", {})}
        try:
            response = self._http().request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise TransientError(f"Kubernetes API timeout on {method} {path}") from exc
        except httpx.TransportError as exc:
            raise ExecutionUnavailable("The Kubernetes API is unreachable") from exc
        if response.status_code in ok:
            return response
        message = _k8s_message(response)
        if response.status_code in (401, 403):
            raise ExecutionUnavailable(f"The Kubernetes API denied access ({response.status_code}): {message}")
        if response.status_code >= 500 or response.status_code == 429:
            raise TransientError(f"Kubernetes API error {response.status_code} on {method} {path}: {message}")
        raise PermanentError(f"Kubernetes API error {response.status_code} on {method} {path}: {message}")

    @property
    def _ns(self) -> str:
        return self._settings.k8s_namespace

    def _jobs_path(self, name: str | None = None) -> str:
        base = f"/apis/batch/v1/namespaces/{self._ns}/jobs"
        return f"{base}/{name}" if name else base

    def _get_storage(self) -> Any:
        if self._storage is not None:
            return self._storage
        from aegis_api.lab.storage import get_storage

        return get_storage()

    def _presign(self, key: str, ttl: int) -> str:
        if self._presign_put is not None:
            return self._presign_put(key, ttl)
        return _boto3_presign_put(self._settings, key, ttl)

    def _require_transfer_storage(self) -> None:
        if self._storage is None and self._settings.object_storage_backend != "s3":
            raise ExecutionUnavailable(
                "Kubernetes execution needs S3-compatible object storage (OBJECT_STORAGE_BACKEND=s3) to move "
                "inputs and outputs; the local filesystem backend is not reachable from the cluster"
            )

    # -- contract ---------------------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        s = self._settings
        modes: tuple[str, ...] = ("none", "allowlist") if s.execution_egress_proxy_url else ("none",)
        reason = None
        if s.object_storage_backend != "s3":
            reason = "requires S3-compatible object storage for input/output transfer"
        return Capabilities(
            name=self.name,
            enabled=s.execution_backend == self.name,
            cpu=True,
            gpu=bool(s.execution_gpu_enabled),
            multi_gpu=bool(s.execution_gpu_enabled),
            max_timeout_seconds=s.execution_max_timeout_seconds,
            network_modes=modes,
            description="Kubernetes Jobs in a dedicated sandbox namespace (restricted Pod Security profile).",
            reason=reason,
        )

    def health(self) -> bool:
        try:
            self._request("GET", "/version", timeout=httpx.Timeout(3.0, connect=2.0))
        except (ExecutionUnavailable, TransientError, PermanentError):
            return False
        return True

    def start(self, ctx: JobContext) -> StartResult:
        self._require_transfer_storage()
        allowlist = ctx.allowed_images or tuple(self._settings.execution_allowed_image_list)
        if not image_allowed(ctx.spec.image, allowlist):
            raise PermanentError(f"Image {ctx.spec.image!r} is not in the allowed image list")
        job_id = str(ctx.job_id)
        name = job_resource_name(ctx.job_id)
        in_key, out_key = _transfer_keys(str(ctx.organization_id), str(ctx.project_id), job_id)
        storage = self._get_storage()
        ttl = min(ctx.spec.timeout_seconds + TRANSFER_TTL_GRACE_SECONDS, MAX_PRESIGN_TTL_SECONDS)
        fetch_url: str | None = None
        if ctx.inputs_path is not None:
            with ctx.inputs_path.open("rb") as fh:
                storage.put_stream(in_key, fh, "application/x-tar", max_bytes=ctx.inputs_path.stat().st_size)
            fetch_url = storage.presign_get(
                in_key, ttl_seconds=ttl, filename="inputs.tar", content_type="application/x-tar"
            )
        upload_url = self._presign(out_key, ttl)
        spec = ctx.spec.model_copy(
            update={
                "labels": {
                    **ctx.spec.labels,
                    LABEL_JOB_ID: job_id,
                    LABEL_ORG: str(ctx.organization_id),
                    LABEL_PROJECT: str(ctx.project_id),
                }
            }
        )
        manifest = build_k8s_job(
            spec,
            namespace=self._ns,
            job_name=name,
            fetch_url=fetch_url,
            upload_url=upload_url,
            fetcher_image=self._settings.k8s_fetcher_image,
            runtime_class=self._settings.k8s_runtime_class,
            image_pull_secret=self._settings.k8s_image_pull_secret,
            tmpfs_mb=ctx.tmpfs_mb,
            proxy=ctx.egress.proxy_url if ctx.egress else None,
            input_max_bytes=max(ctx.inputs_size, 1),
            extra_labels={K8S_LABEL_ORG: str(ctx.organization_id), K8S_LABEL_PROJECT: str(ctx.project_id)},
        )
        # 409 = the Job already exists (a retried provisioning of the same compute job): reuse it.
        self._request("POST", self._jobs_path(), json=manifest, ok=(200, 201, 202, 409))
        handle = BackendHandle(
            backend=self.name,
            job_id=job_id,
            backend_job_id=name,
            details={"namespace": self._ns, "input_key": in_key, "output_key": out_key},
        )
        return StartResult(handle=handle, image_digest=None)

    def _pod(self, handle: BackendHandle) -> dict[str, Any] | None:
        response = self._request(
            "GET",
            f"/api/v1/namespaces/{self._ns}/pods",
            params={"labelSelector": f"job-name={handle.backend_job_id}"},
        )
        items = response.json().get("items") or []
        if not items:
            return None
        items.sort(key=lambda p: str((p.get("metadata") or {}).get("creationTimestamp") or ""))
        pod: dict[str, Any] = items[-1]
        return pod

    def status(self, handle: BackendHandle) -> BackendStatus:
        response = self._request("GET", self._jobs_path(handle.backend_job_id), ok=(200, 404))
        if response.status_code == 404:
            return BackendStatus(state=BackendState.MISSING)
        job = response.json()
        conditions = {
            str(c.get("type")): c
            for c in (job.get("status") or {}).get("conditions") or []
            if str(c.get("status")) == "True"
        }
        pod = self._pod(handle)
        pod_status = (pod or {}).get("status") or {}
        main = _container_status(pod_status.get("containerStatuses"), "main")
        digest = _image_digest(main)
        terminated = ((main or {}).get("state") or {}).get("terminated")
        if terminated:
            reason = str(terminated.get("reason") or "")
            return BackendStatus(
                state=BackendState.EXITED,
                exit_code=int(terminated.get("exitCode", -1)),
                oom_killed=reason == "OOMKilled",
                reason="oom" if reason == "OOMKilled" else (reason or None),
                started_at=parse_rfc3339(terminated.get("startedAt")),
                finished_at=parse_rfc3339(terminated.get("finishedAt")),
                image_digest=digest,
            )
        failed = conditions.get("Failed") or conditions.get("FailureTarget")
        if failed is not None:
            reason = str(failed.get("reason") or "")
            if reason == "DeadlineExceeded":
                return BackendStatus(state=BackendState.EXITED, reason="deadline_exceeded", image_digest=digest)
            return BackendStatus(
                state=BackendState.EXITED,
                reason="backend_failed",
                error=f"{reason}: {str(failed.get('message') or '')[:300]}",
                image_digest=digest,
            )
        fetch = _container_status(pod_status.get("initContainerStatuses"), "fetch-inputs")
        fetch_term = ((fetch or {}).get("state") or {}).get("terminated")
        if fetch_term and int(fetch_term.get("exitCode", 0)) != 0:
            return BackendStatus(state=BackendState.EXITED, reason="input_fetch_failed", error="Fetching inputs failed")
        waiting = ((main or {}).get("state") or {}).get("waiting") or {}
        waiting_reason = str(waiting.get("reason") or "")
        if waiting_reason in _IMAGE_ERRORS:
            return BackendStatus(
                state=BackendState.EXITED,
                reason="image_pull_failed",
                error=f"{waiting_reason}: {str(waiting.get('message') or '')[:300]}",
            )
        if waiting_reason in _CONFIG_ERRORS:
            return BackendStatus(
                state=BackendState.EXITED,
                reason="container_config_error",
                error=f"{waiting_reason}: {str(waiting.get('message') or '')[:300]}",
            )
        running = ((main or {}).get("state") or {}).get("running")
        if running:
            return BackendStatus(
                state=BackendState.RUNNING, started_at=parse_rfc3339(running.get("startedAt")), image_digest=digest
            )
        return BackendStatus(state=BackendState.CREATED)

    def sample_usage(self, handle: BackendHandle, *, include_disk: bool = False) -> ResourceSample | None:
        # Usage is billed on reserved resources; metrics-server is optional and not queried.
        return None

    def read_logs(self, handle: BackendHandle, *, since_ns: int | None, max_bytes: int) -> LogBatch:
        pod = self._pod(handle)
        if pod is None:
            return LogBatch(lines=[], cursor_ns=since_ns)
        params: dict[str, str] = {"container": "main", "timestamps": "true", "limitBytes": str(max(max_bytes, 1))}
        if since_ns:
            params["sinceTime"] = datetime.fromtimestamp(since_ns // 1_000_000_000, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        name = str((pod.get("metadata") or {}).get("name"))
        response = self._request(
            "GET", f"/api/v1/namespaces/{self._ns}/pods/{name}/log", params=params, ok=(200, 400, 404)
        )
        if response.status_code != 200:
            return LogBatch(lines=[], cursor_ns=since_ns)
        lines: list[LogLine] = []
        cursor = since_ns
        for raw in response.content.decode("utf-8", "replace").splitlines():
            ts, text = split_timestamped_line(raw)
            if ts is None or (since_ns is not None and ts <= since_ns):
                continue
            lines.append(LogLine(ts_ns=ts, stream="main", text=text))
            cursor = max(cursor or 0, ts)
        return LogBatch(lines=lines, cursor_ns=cursor, truncated=len(response.content) >= max_bytes)

    def fetch_logs(self, handle: BackendHandle, *, max_bytes: int) -> tuple[bytes, bool]:
        pod = self._pod(handle)
        if pod is None:
            return b"", False
        name = str((pod.get("metadata") or {}).get("name"))
        response = self._request(
            "GET",
            f"/api/v1/namespaces/{self._ns}/pods/{name}/log",
            params={"container": "main", "limitBytes": str(max(max_bytes, 1))},
            ok=(200, 400, 404),
        )
        if response.status_code != 200:
            return b"", False
        data = response.content[:max_bytes]
        return data, len(response.content) >= max_bytes

    def kill(self, handle: BackendHandle) -> None:
        self._request(
            "DELETE",
            self._jobs_path(handle.backend_job_id),
            params={"propagationPolicy": "Background"},
            ok=(200, 202, 404),
        )

    def collect_outputs(self, handle: BackendHandle, dest: Path, limits: ExtractLimits) -> ExtractionReport:
        key = str(handle.details.get("output_key") or "")
        if not key:
            raise PermanentError("Output location for this job is unknown")
        storage = self._get_storage()
        deadline = time.monotonic() + self.output_wait_seconds
        while not storage.exists(key):
            if time.monotonic() >= deadline:
                raise PermanentError("The sandbox did not upload its outputs")
            time.sleep(self.output_poll_seconds)
        reader = ChunkReader(storage.open_stream(key), stream_budget(limits))
        return safe_extract(io.BufferedReader(reader), dest, limits, strip_prefix="output", strict=False)

    def cleanup(self, handle: BackendHandle) -> None:
        try:
            self.kill(handle)
        except Exception:
            log.warning("k8s_job_delete_failed", job_id=handle.job_id, exc_info=True)
        storage = None
        for key in (handle.details.get("input_key"), handle.details.get("output_key")):
            if not key:
                continue
            try:
                storage = storage or self._get_storage()
                storage.delete(str(key))
            except Exception:
                log.warning("k8s_transfer_object_delete_failed", job_id=handle.job_id, exc_info=True)

    def find_by_job_id(self, job_id: uuid.UUID | str) -> BackendHandle | None:
        jid = str(uuid.UUID(str(job_id)))
        response = self._request("GET", self._jobs_path(), params={"labelSelector": f"{K8S_LABEL_JOB_ID}={jid}"})
        items = response.json().get("items") or []
        if not items:
            return None
        metadata = items[0].get("metadata") or {}
        labels = metadata.get("labels") or {}
        details: dict[str, Any] = {"namespace": self._ns}
        org, project = labels.get(K8S_LABEL_ORG), labels.get(K8S_LABEL_PROJECT)
        if org and project:
            details["input_key"], details["output_key"] = _transfer_keys(org, project, jid)
        return BackendHandle(backend=self.name, job_id=jid, backend_job_id=str(metadata.get("name")), details=details)


# ---------------------------------------------------------------------------------------------
def _k8s_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message", ""))[:300]
    except (ValueError, AttributeError):
        return response.text[:300]


def _container_status(statuses: Any, name: str) -> dict[str, Any] | None:
    for status in statuses or []:
        if isinstance(status, dict) and status.get("name") == name:
            return status
    return None


def _image_digest(status: dict[str, Any] | None) -> str | None:
    image_id = str((status or {}).get("imageID") or "")
    _, _, digest = image_id.partition("@")
    return digest if digest.startswith("sha256:") else None


def _boto3_presign_put(settings: Settings, key: str, ttl: int) -> str:
    """Presigned ``PUT`` for the outputs tar (credentials never leave the server; the URL is single-object)."""
    import boto3
    from botocore.config import Config

    endpoint = getattr(settings, "object_storage_public_endpoint", None) or settings.object_storage_endpoint
    client = boto3.session.Session().client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=settings.object_storage_region,
        aws_access_key_id=settings.object_storage_access_key or None,
        aws_secret_access_key=settings.object_storage_secret_key or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.object_storage_force_path_style else "auto"},
        ),
    )
    url: str = client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.object_storage_bucket, "Key": key, "ContentType": "application/x-tar"},
        ExpiresIn=max(1, min(int(ttl), MAX_PRESIGN_TTL_SECONDS)),
        HttpMethod="PUT",
    )
    return url
