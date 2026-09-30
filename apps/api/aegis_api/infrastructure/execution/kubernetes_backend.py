"""KubernetesBackend: one hardened ``batch/v1`` Job per execution in a dedicated sandbox namespace.

Isolation (defense in depth):
* ``runtimeClassName`` (gVisor/Kata when configured), ``automountServiceAccountToken: false``;
* pod ``securityContext``: non-root uid/gid 65534, ``seccompProfile: RuntimeDefault``;
* container: read-only root FS, ``allowPrivilegeEscalation: false``, all capabilities dropped;
* resource requests = limits (CPU/memory/ephemeral-storage, optional ``nvidia.com/gpu``), ``activeDeadlineSeconds``;
* ``aegis.io/egress: deny`` label selected by a namespace NetworkPolicy that denies all egress (see
  ``deploy/k8s``); allowlisted egress uses ``aegis.io/egress: allowlist`` + an egress gateway policy.

I/O: code and inputs are delivered via a ConfigMap (≤ ~900 KiB) mounted read-only; the workspace is an
``emptyDir``. When the job finishes, a native sidecar ("collector") that has *no* access to the main
container's environment tars ``/workspace/output`` and uploads it to a single-object, short-lived presigned
PUT URL. Logs are read through the pods/log API with a byte limit.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from aegis_api.infrastructure.execution.base import (
    WORKSPACE,
    ExecutionBackend,
    ExecutionError,
    ExecutionPolicyError,
    ExecutionRequest,
    ExecutionResult,
    build_input_tar,
    extract_outputs,
)

MAX_CONFIGMAP_BYTES = 900 * 1024
COLLECTOR_IMAGE = "curlimages/curl:8.10.1"

# (put_url, get_bytes) — provided by the application layer (object storage presigning).
OutputChannel = tuple[str, Callable[[], bytes | None]]


class KubernetesBackend(ExecutionBackend):
    name = "kubernetes"
    supports_egress_allowlist = True
    supports_gpu = True

    def __init__(
        self,
        *,
        api_url: str,
        namespace: str,
        token: str | None = None,
        token_path: str | None = None,
        ca_path: str | None = None,
        runtime_class: str | None = "gvisor",
        output_channel_factory: Callable[[ExecutionRequest], OutputChannel] | None = None,
        transport: httpx.BaseTransport | None = None,
        poll_seconds: float = 2.0,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.namespace = namespace
        self._token = token
        self.token_path = token_path
        self.ca_path = ca_path
        self.runtime_class = runtime_class
        self.output_channel_factory = output_channel_factory
        self.transport = transport
        self.poll_seconds = poll_seconds

    # -- HTTP --------------------------------------------------------------------------------------
    def _client(self) -> httpx.Client:
        token = self._token
        if token is None and self.token_path and Path(self.token_path).exists():
            token = Path(self.token_path).read_text().strip()
        verify: Any = self.ca_path if self.ca_path and Path(self.ca_path).exists() else True
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.Client(base_url=self.api_url, headers=headers, verify=verify, timeout=30, transport=self.transport)

    def health(self) -> tuple[bool, str]:
        try:
            with self._client() as c:
                r = c.get(f"/api/v1/namespaces/{self.namespace}")
            return (r.status_code == 200, f"namespace {self.namespace}: HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            return False, f"kubernetes unreachable: {type(exc).__name__}"

    # -- manifests ---------------------------------------------------------------------------------
    def job_name(self, job_id: str) -> str:
        return f"aegis-job-{job_id[:40]}".lower()

    def configmap_manifest(self, request: ExecutionRequest) -> dict[str, Any]:
        tar = build_input_tar(request.files)
        if len(tar) > MAX_CONFIGMAP_BYTES:
            raise ExecutionPolicyError("inputs exceed the ConfigMap limit; stage large datasets through object storage")
        return {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": self.job_name(request.job_id) + "-inputs",
                "labels": {"app.kubernetes.io/managed-by": "aegis-lab"},
            },
            "binaryData": {"inputs.tar": base64.b64encode(tar).decode()},
            "immutable": True,
        }

    def job_manifest(self, request: ExecutionRequest, output_put_url: str | None) -> dict[str, Any]:
        r = request.resources
        limits: dict[str, str] = {
            "cpu": str(r.cpu),
            "memory": f"{r.memory_mb}Mi",
            "ephemeral-storage": f"{r.disk_mb}Mi",
        }
        if r.gpu_count:
            limits["nvidia.com/gpu"] = str(r.gpu_count)
        name = self.job_name(request.job_id)
        env = [
            {"name": k, "value": v}
            for k, v in sorted(
                {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                    "HOME": "/tmp",
                    "AEGIS_JOB_ID": request.job_id,
                    **request.env,
                }.items()
            )
        ]
        env += [{"name": k, "value": v} for k, v in sorted(request.secrets.items())]
        container_security = {
            "runAsNonRoot": True,
            "runAsUser": 65534,
            "runAsGroup": 65534,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        }
        volumes = [
            {"name": "workspace", "emptyDir": {"sizeLimit": f"{r.disk_mb}Mi"}},
            {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi", "medium": "Memory"}},
            {"name": "inputs", "configMap": {"name": name + "-inputs"}},
        ]
        init_containers: list[dict[str, Any]] = [
            {
                "name": "stage",
                "image": request.image,
                "command": [
                    "python",
                    "-c",
                    "import tarfile;tarfile.open('/inputs/inputs.tar').extractall('/workspace', filter='data')",
                ],
                "securityContext": container_security,
                "volumeMounts": [
                    {"name": "workspace", "mountPath": WORKSPACE},
                    {"name": "inputs", "mountPath": "/inputs", "readOnly": True},
                ],
                "resources": {"limits": {"cpu": "500m", "memory": "256Mi"}},
            }
        ]
        if output_put_url:
            init_containers.append(
                {
                    "name": "collector",
                    "image": COLLECTOR_IMAGE,
                    "restartPolicy": "Always",  # native sidecar: runs alongside main, terminated after it
                    "command": [
                        "sh",
                        "-c",
                        "trap 'tar -C /workspace -cf /tmp/out.tar output && curl -sf -X PUT --upload-file /tmp/out.tar \"$OUT_URL\"; exit 0' TERM; while true; do sleep 1; done",
                    ],
                    "env": [{"name": "OUT_URL", "value": output_put_url}],
                    "securityContext": container_security,
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": WORKSPACE, "readOnly": True},
                        {"name": "tmp", "mountPath": "/tmp"},
                    ],
                    "resources": {"limits": {"cpu": "200m", "memory": "128Mi"}},
                }
            )
        pod_spec: dict[str, Any] = {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65534,
                "fsGroup": 65534,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "initContainers": init_containers,
            "containers": [
                {
                    "name": "main",
                    "image": request.image,
                    "command": request.command,
                    "workingDir": request.working_dir,
                    "env": env,
                    "securityContext": container_security,
                    "resources": {"requests": limits, "limits": limits},
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": WORKSPACE},
                        {"name": "tmp", "mountPath": "/tmp"},
                    ],
                }
            ],
            "volumes": volumes,
        }
        if self.runtime_class:
            pod_spec["runtimeClassName"] = self.runtime_class
        if r.gpu_type:
            pod_spec["nodeSelector"] = {"aegis.io/gpu-type": r.gpu_type}
            pod_spec["tolerations"] = [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}]
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "labels": {"app.kubernetes.io/managed-by": "aegis-lab", "aegis.io/job": request.job_id[:63]},
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": request.timeout_seconds,
                "ttlSecondsAfterFinished": 600,
                "template": {
                    "metadata": {
                        "labels": {
                            "aegis.io/sandbox": "true",
                            "aegis.io/egress": "deny" if request.network == "none" else "allowlist",
                            "aegis.io/job": request.job_id[:63],
                        },
                        "annotations": {"aegis.io/egress-allowlist": ",".join(request.egress_allowlist)},
                    },
                    "spec": pod_spec,
                },
            },
        }

    # -- lifecycle ---------------------------------------------------------------------------------
    def run(self, request: ExecutionRequest) -> ExecutionResult:
        self.validate(request)
        ns = self.namespace
        name = self.job_name(request.job_id)
        channel = self.output_channel_factory(request) if self.output_channel_factory else None
        started = time.monotonic()
        with self._client() as c:
            try:
                resp = c.post(f"/api/v1/namespaces/{ns}/configmaps", json=self.configmap_manifest(request))
                if resp.status_code not in (201, 409):
                    raise ExecutionError(
                        f"configmap create failed: HTTP {resp.status_code}", transient=resp.status_code >= 500
                    )
                resp = c.post(
                    f"/apis/batch/v1/namespaces/{ns}/jobs",
                    json=self.job_manifest(request, channel[0] if channel else None),
                )
                if resp.status_code not in (201, 409):
                    raise ExecutionError(
                        f"job create failed: HTTP {resp.status_code}", transient=resp.status_code >= 500
                    )
                status: dict[str, Any] = {}
                deadline = started + request.timeout_seconds + 120
                while time.monotonic() < deadline:
                    job = c.get(f"/apis/batch/v1/namespaces/{ns}/jobs/{name}").json()
                    status = job.get("status") or {}
                    if status.get("succeeded") or status.get("failed"):
                        break
                    time.sleep(self.poll_seconds)
                runtime = time.monotonic() - started
                pods = (
                    c.get(f"/api/v1/namespaces/{ns}/pods", params={"labelSelector": f"job-name={name}"})
                    .json()
                    .get("items", [])
                )
                pod = pods[0] if pods else {}
                terminated: dict[str, Any] = {}
                for cs in (pod.get("status") or {}).get("containerStatuses", []) or []:
                    if cs.get("name") == "main":
                        terminated = (cs.get("state") or {}).get("terminated") or {}
                conditions = [cond.get("reason") for cond in status.get("conditions", []) or []]
                timed_out = "DeadlineExceeded" in conditions or not (status.get("succeeded") or status.get("failed"))
                logs = ""
                if pod:
                    lr = c.get(
                        f"/api/v1/namespaces/{ns}/pods/{pod['metadata']['name']}/log",
                        params={"container": "main", "limitBytes": request.max_log_bytes},
                    )
                    logs = lr.text if lr.status_code == 200 else ""
                outputs: dict[str, bytes] = {}
                outputs_truncated = False
                if channel is not None:
                    raw = channel[1]()
                    if raw:
                        outputs, outputs_truncated = extract_outputs(
                            raw, root="output", max_bytes=request.max_output_bytes
                        )
                return ExecutionResult(
                    exit_code=None if timed_out else terminated.get("exitCode"),
                    timed_out=timed_out,
                    oom_killed=terminated.get("reason") == "OOMKilled",
                    runtime_seconds=runtime,
                    peak_memory_mb=None,
                    cpu_seconds=None,
                    stdout=logs,
                    stderr="",
                    logs_truncated=len(logs.encode()) >= request.max_log_bytes,
                    outputs=outputs,
                    outputs_truncated=outputs_truncated,
                    image_digest=(pod.get("status") or {}).get("containerStatuses", [{}])[0].get("imageID")
                    if pod
                    else None,
                    backend=self.name,
                    backend_ref=name,
                )
            except httpx.HTTPError as exc:
                raise ExecutionError(f"kubernetes API error: {type(exc).__name__}", transient=True) from exc
            finally:
                try:
                    c.delete(f"/apis/batch/v1/namespaces/{ns}/jobs/{name}", params={"propagationPolicy": "Background"})
                    c.delete(f"/api/v1/namespaces/{ns}/configmaps/{name}-inputs")
                except httpx.HTTPError:
                    pass

    def cancel(self, job_id: str) -> bool:
        with self._client() as c:
            r = c.delete(
                f"/apis/batch/v1/namespaces/{self.namespace}/jobs/{self.job_name(job_id)}",
                params={"propagationPolicy": "Foreground"},
            )
        return r.status_code in (200, 202)
