"""Kubernetes backend against a fake Kubernetes REST API (``httpx.MockTransport``; tests only).

The fake API server stores Jobs, reports Pod/Job status transitions, serves logs and — standing in for the
``upload-outputs`` sidecar — writes the outputs tar to object storage when the main container terminates.
"""

from __future__ import annotations

import io
import json
import tarfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from aegis_api.config import get_settings
from aegis_api.lab.core.errors import ExecutionUnavailable, PermanentError, TransientError
from aegis_api.lab.execution import dispatch, service
from aegis_api.lab.execution.archive import ExtractLimits
from aegis_api.lab.execution.backends import BackendState, JobContext, register_backend
from aegis_api.lab.execution.backends.kubernetes import KubernetesBackend
from aegis_api.lab.execution.schemas import JobSpec
from engines.lab.sandbox import ResourceRequest, SandboxSpec
from tests.conftest import requires_db
from tests.lab.conftest import LabContext, make_lab

NS = "aegis-sandbox"
TOKEN = "test-service-account-token"
DIGEST = "sha256:" + "c" * 64


class FakeKubernetes:
    """Minimal Kubernetes API: batch/v1 Jobs, Pods (by job-name selector), pod logs, /version."""

    def __init__(self, storage: Any, *, script: list[str] | None = None, exit_code: int = 0, reason: str = "Completed"):
        self.storage = storage
        self.jobs: dict[str, dict[str, Any]] = {}
        self.polls: dict[str, int] = {}
        self.deleted: list[tuple[str, str | None]] = []
        self.requests: list[httpx.Request] = []
        self.script = script or ["pending", "running", "terminated"]
        self.exit_code = exit_code
        self.reason = reason
        self.outputs: dict[str, bytes] = {"metrics.json": b'{"f1": 0.7}', "model.txt": b"weights"}
        self.job_conditions: list[dict[str, str]] = []
        self.status_code: int | None = None
        self.uploaded: set[str] = set()

    # -- helpers -----------------------------------------------------------------------------------
    def _env(self, job: dict[str, Any], container: str, name: str) -> str:
        pod = job["spec"]["template"]["spec"]
        for c in pod["initContainers"] + pod["containers"]:
            if c["name"] == container:
                return next(e["value"] for e in c["env"] if e["name"] == name)
        raise KeyError(name)

    def _upload_outputs(self, job: dict[str, Any]) -> None:
        url = self._env(job, "upload-outputs", "AEGIS_UPLOAD_URL")
        key = unquote(url.split("/put/", 1)[1])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("output")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
            for path, data in self.outputs.items():
                entry = tarfile.TarInfo(f"output/{path}")
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
        self.storage.put_bytes(key, buf.getvalue(), "application/x-tar")

    def _phase(self, name: str) -> str:
        return self.script[min(self.polls.get(name, 0), len(self.script) - 1)]

    def _pod(self, name: str) -> dict[str, Any]:
        phase = self._phase(name)
        state: dict[str, Any]
        if phase == "pending":
            state = {"waiting": {"reason": "ContainerCreating"}}
        elif phase == "image_error":
            state = {"waiting": {"reason": "ImagePullBackOff", "message": "pull access denied"}}
        elif phase == "running":
            state = {"running": {"startedAt": "2026-09-30T10:00:00Z"}}
        else:
            state = {
                "terminated": {
                    "exitCode": self.exit_code,
                    "reason": self.reason,
                    "startedAt": "2026-09-30T10:00:00Z",
                    "finishedAt": "2026-09-30T10:00:05Z",
                }
            }
        return {
            "metadata": {"name": f"{name}-pod1", "creationTimestamp": "2026-09-30T10:00:00Z"},
            "status": {
                "initContainerStatuses": [{"name": "fetch-inputs", "state": {"terminated": {"exitCode": 0}}}],
                "containerStatuses": [
                    {"name": "main", "imageID": f"docker.io/library/python@{DIGEST}", "state": state}
                ],
            },
        }

    # -- transport ------------------------------------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status_code is not None:
            return httpx.Response(self.status_code, json={"message": "injected failure"})
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"message": "Unauthorized"})
        path = request.url.path
        jobs = f"/apis/batch/v1/namespaces/{NS}/jobs"
        if path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.33.0"})
        if path == jobs and request.method == "POST":
            manifest = json.loads(request.content)
            name = manifest["metadata"]["name"]
            if name in self.jobs:
                return httpx.Response(409, json={"message": "AlreadyExists"})
            self.jobs[name] = manifest
            return httpx.Response(201, json=manifest)
        if path == jobs and request.method == "GET":
            selector = request.url.params.get("labelSelector", "")
            key, _, value = selector.partition("=")
            items = [j for j in self.jobs.values() if j["metadata"]["labels"].get(key) == value]
            return httpx.Response(200, json={"items": items})
        if path.startswith(jobs + "/"):
            name = path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                self.deleted.append((name, request.url.params.get("propagationPolicy")))
                self.jobs.pop(name, None)
                return httpx.Response(200, json={"status": "Success"})
            if name not in self.jobs:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json={**self.jobs[name], "status": {"conditions": self.job_conditions}})
        if path == f"/api/v1/namespaces/{NS}/pods":
            name = request.url.params["labelSelector"].split("=", 1)[1]
            if name not in self.jobs:
                return httpx.Response(200, json={"items": []})
            pod = self._pod(name)
            if self._phase(name) == "terminated" and name not in self.uploaded:
                self.uploaded.add(name)  # the upload sidecar runs once the main container terminated
                self._upload_outputs(self.jobs[name])
            self.polls[name] = self.polls.get(name, 0) + 1
            return httpx.Response(200, json={"items": [pod]})
        if path.startswith(f"/api/v1/namespaces/{NS}/pods/") and path.endswith("/log"):
            body = "2026-09-30T10:00:01.000000001Z epoch 1\n2026-09-30T10:00:02.5Z epoch 2\n"
            if request.url.params.get("timestamps") != "true":
                body = "epoch 1\nepoch 2\n"
            limit = int(request.url.params.get("limitBytes", "1000000"))
            return httpx.Response(200, content=body.encode()[:limit])
        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[Any]:
    from aegis_api.lab.storage import configure_storage
    from aegis_api.lab.storage.local import LocalFilesystemStorage

    store = LocalFilesystemStorage(tmp_path / "objects")
    configure_storage(store)
    yield store
    configure_storage(None)


@pytest.fixture
def k8s_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    settings = get_settings()
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n")
    monkeypatch.setattr(settings, "k8s_namespace", NS)
    monkeypatch.setattr(settings, "k8s_token_path", str(token))
    monkeypatch.setattr(settings, "k8s_ca_path", str(tmp_path / "missing-ca.crt"))
    monkeypatch.setattr(settings, "k8s_api_url", "https://k8s.test")
    return settings


def _backend(fake: FakeKubernetes, storage: Any, **kw: Any) -> KubernetesBackend:
    backend = KubernetesBackend(
        get_settings(),
        client=httpx.Client(transport=httpx.MockTransport(fake), base_url="https://k8s.test"),
        storage=storage,
        presign_put=lambda key, ttl: f"https://s3.test/put/{key}",
        **kw,
    )
    backend.output_wait_seconds = 1.0
    backend.output_poll_seconds = 0.05
    return backend


def _ctx(tmp_path: Path, **kw: Any) -> JobContext:
    inputs = tmp_path / "inputs.tar"
    with tarfile.open(inputs, "w") as tar:
        data = b'{"lr": 0.1}'
        info = tarfile.TarInfo("input/params.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    spec = SandboxSpec(
        image="python:3.12-slim",
        command=["python", "-c", "print(1)"],
        resources=ResourceRequest(cpu=1, memory_mb=512, disk_mb=256),
        timeout_seconds=600,
    )
    base: dict[str, Any] = {
        "job_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "spec": spec,
        "inputs_path": inputs,
        "inputs_size": inputs.stat().st_size,
    }
    base.update(kw)
    return JobContext(**base)


# ---------------------------------------------------------------------------------------------
def test_start_posts_a_restricted_job_with_transfer_urls(storage: Any, k8s_settings: Any, tmp_path: Path) -> None:
    fake = FakeKubernetes(storage)
    backend = _backend(fake, storage)
    ctx = _ctx(tmp_path)
    started = backend.start(ctx)
    name = f"aegis-job-{ctx.job_id}"
    assert started.handle.backend_job_id == name and started.image_digest is None
    post = next(r for r in fake.requests if r.method == "POST")
    assert post.url.path == f"/apis/batch/v1/namespaces/{NS}/jobs"
    assert post.headers["authorization"] == f"Bearer {TOKEN}"  # read from the service-account token file
    manifest = fake.jobs[name]
    labels = manifest["metadata"]["labels"]
    assert labels["aegis.io/job-id"] == str(ctx.job_id)
    assert labels["aegis.io/org"] == str(ctx.organization_id) and labels["aegis.io/project"] == str(ctx.project_id)
    pod = manifest["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False and pod["securityContext"]["runAsNonRoot"] is True
    assert manifest["spec"]["activeDeadlineSeconds"] == 600 and manifest["spec"]["backoffLimit"] == 0
    in_key, out_key = started.handle.details["input_key"], started.handle.details["output_key"]
    assert storage.exists(in_key)  # staged inputs uploaded to object storage
    assert fake._env(manifest, "upload-outputs", "AEGIS_UPLOAD_URL") == f"https://s3.test/put/{out_key}"
    assert fake._env(manifest, "fetch-inputs", "AEGIS_INPUT_URL")
    # starting again (retried provisioning) reuses the existing Job instead of failing
    assert backend.start(ctx).handle.backend_job_id == name


def test_status_transitions_logs_outputs_and_cleanup(storage: Any, k8s_settings: Any, tmp_path: Path) -> None:
    fake = FakeKubernetes(storage)
    backend = _backend(fake, storage)
    handle = backend.start(_ctx(tmp_path)).handle
    assert backend.status(handle).state == BackendState.CREATED
    running = backend.status(handle)
    assert running.state == BackendState.RUNNING and running.image_digest == DIGEST
    batch = backend.read_logs(handle, since_ns=None, max_bytes=4096)
    assert [line.text for line in batch.lines] == ["epoch 1", "epoch 2"]
    again = backend.read_logs(handle, since_ns=batch.cursor_ns, max_bytes=4096)
    assert again.lines == []
    log_request = [r for r in fake.requests if r.url.path.endswith("/log")][-1]
    assert log_request.url.params["container"] == "main" and log_request.url.params["sinceTime"]
    done = backend.status(handle)
    assert done.state == BackendState.EXITED and done.exit_code == 0 and not done.oom_killed
    data, truncated = backend.fetch_logs(handle, max_bytes=5)
    assert data == b"epoch" and truncated is True
    report = backend.collect_outputs(handle, tmp_path / "out", ExtractLimits(max_total_bytes=10_000, max_files=10))
    assert set(report.by_path()) == {"metrics.json", "model.txt"}
    found = backend.find_by_job_id(handle.job_id)
    assert found is not None and found.details["output_key"] == handle.details["output_key"]
    backend.cleanup(handle)
    assert fake.deleted[-1] == (handle.backend_job_id, "Background")
    assert not storage.exists(handle.details["input_key"]) and not storage.exists(handle.details["output_key"])
    assert backend.status(handle).state == BackendState.MISSING
    assert backend.find_by_job_id(handle.job_id) is None


@pytest.mark.parametrize(
    ("script", "exit_code", "reason", "conditions", "expected"),
    [
        (["terminated"], 137, "OOMKilled", [], {"exit_code": 137, "oom_killed": True, "reason": "oom"}),
        (["terminated"], 2, "Error", [], {"exit_code": 2, "oom_killed": False}),
        (
            ["running"],
            0,
            "",
            [{"type": "Failed", "status": "True", "reason": "DeadlineExceeded"}],
            {"reason": "deadline_exceeded"},
        ),
        (["image_error"], 0, "", [], {"reason": "image_pull_failed"}),
    ],
)
def test_status_mapping(
    storage: Any,
    k8s_settings: Any,
    tmp_path: Path,
    script: list[str],
    exit_code: int,
    reason: str,
    conditions: list[dict[str, str]],
    expected: dict[str, Any],
) -> None:
    fake = FakeKubernetes(storage, script=script, exit_code=exit_code, reason=reason)
    fake.job_conditions = conditions
    backend = _backend(fake, storage)
    status = backend.status(backend.start(_ctx(tmp_path)).handle)
    assert status.state == BackendState.EXITED
    for key, value in expected.items():
        assert getattr(status, key) == value, (key, status)


def test_missing_outputs_and_api_failures(storage: Any, k8s_settings: Any, tmp_path: Path) -> None:
    fake = FakeKubernetes(storage, script=["running"])
    backend = _backend(fake, storage)
    handle = backend.start(_ctx(tmp_path)).handle
    with pytest.raises(PermanentError, match="did not upload"):
        backend.collect_outputs(handle, tmp_path / "o", ExtractLimits(max_total_bytes=100, max_files=5))
    fake.status_code = 500
    with pytest.raises(TransientError):
        backend.status(handle)
    fake.status_code = 403
    with pytest.raises(ExecutionUnavailable):
        backend.status(handle)
    assert backend.health() is False
    fake.status_code = None
    assert backend.health() is True
    wrong = _backend(fake, storage, token="stolen-or-expired")
    with pytest.raises(ExecutionUnavailable):
        wrong.status(handle)


def test_unreachable_api_and_missing_token(storage: Any, k8s_settings: Any, tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    down = KubernetesBackend(
        get_settings(),
        client=httpx.Client(transport=httpx.MockTransport(refuse), base_url="https://k8s.test"),
        storage=storage,
    )
    with pytest.raises(ExecutionUnavailable, match="unreachable"):
        down.find_by_job_id(uuid.uuid4())
    Path(k8s_settings.k8s_token_path).unlink()
    tokenless = _backend(FakeKubernetes(storage), storage)
    with pytest.raises(ExecutionUnavailable, match="token"):
        tokenless.find_by_job_id(uuid.uuid4())


def test_local_object_storage_is_refused(k8s_settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(k8s_settings, "object_storage_backend", "local")
    backend = KubernetesBackend(
        k8s_settings, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    with pytest.raises(ExecutionUnavailable, match="S3-compatible"):
        backend.start(_ctx(tmp_path))
    assert "S3" in (backend.capabilities().reason or "")


def test_image_allowlist_is_enforced_by_the_backend(storage: Any, k8s_settings: Any, tmp_path: Path) -> None:
    backend = _backend(FakeKubernetes(storage), storage)
    ctx = _ctx(tmp_path)
    evil = JobContext(**{**ctx.__dict__, "spec": ctx.spec.model_copy(update={"image": "evil/miner:latest"})})
    with pytest.raises(PermanentError, match="allowed image"):
        backend.start(evil)


@pytest.fixture
def lab(client: Any) -> LabContext:
    return make_lab(client, org=f"Exec K8s {uuid.uuid4().hex[:10]}")


@requires_db
@pytest.mark.db
def test_run_job_end_to_end_on_kubernetes(
    lab: LabContext, storage: Any, k8s_settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeKubernetes(storage, script=["pending", "running", "running", "terminated"])
    register_backend("kubernetes", _backend(fake, storage))
    dispatch.set_job_dispatcher(lambda org, job: None)
    monkeypatch.setattr(k8s_settings, "execution_backend", "kubernetes")
    monkeypatch.setattr(service, "POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(
        service, "_evaluate_policy", lambda *a, **k: service.PolicyOutcome(effect="allow", reasons=("test",))
    )
    try:
        with lab.db() as db:
            job = service.submit_job(
                db,
                lab.actor(),
                JobSpec.model_validate(
                    {"project_id": str(lab.project_id), "command": ["python", "train.py"], "parameters": {"k": 1}}
                ),
            )
            job_id = job.id
            assert job.backend == "kubernetes"
        result = service.run_job(lab.org_id, job_id)
        assert result.status == "SUCCEEDED", result
        assert set(result.outputs) == {"metrics.json", "model.txt"}
        assert result.metrics is not None and result.metrics["values"] == {"f1": 0.7}
        with lab.db() as db:
            from aegis_api.lab.models import ComputeJob

            row = db.get(ComputeJob, job_id)
            assert row is not None and row.image_digest == DIGEST and row.backend_job_id == f"aegis-job-{job_id}"
        assert (f"aegis-job-{job_id}", "Background") in fake.deleted
    finally:
        register_backend("kubernetes", None)
        dispatch.set_job_dispatcher(None)
