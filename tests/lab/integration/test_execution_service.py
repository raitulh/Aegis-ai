"""Execution service + HTTP API against PostgreSQL (RLS), with a tests-only in-memory sandbox backend.

The real Docker backend is exercised in ``test_execution_docker.py``; here the backend is a deterministic
fake so admission, governance, idempotency, tenancy, lifecycle, re-attachment and reconciliation logic can
be tested precisely.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import session_factory
from aegis_api.lab.core.errors import ApprovalRequired, ExecutionUnavailable, PolicyDenied
from aegis_api.lab.execution import dispatch, service
from aegis_api.lab.execution.archive import ExtractionReport, ExtractLimits, TarBuilder, safe_extract
from aegis_api.lab.execution.backends import (
    BackendHandle,
    BackendState,
    BackendStatus,
    Capabilities,
    JobContext,
    LogBatch,
    LogLine,
    ResourceSample,
    StartResult,
    register_backend,
)
from aegis_api.lab.execution.schemas import JobSpec
from aegis_api.lab.models import (
    Approval,
    ArtifactVersion,
    ComputeJob,
    ComputeUsage,
    Dataset,
    DatasetVersion,
    LabEvent,
    Mission,
    OrganizationSettings,
)
from aegis_api.lab.workflows.registry import ActivityContext, get_activity
from aegis_api.models import AuditLog, Evidence
from tests.conftest import requires_db
from tests.lab.conftest import LabContext, make_lab

pytestmark = [requires_db, pytest.mark.db]


@pytest.fixture
def lab(client: Any) -> LabContext:
    # Unique organization names keep signup slugs collision-free when many suites run concurrently.
    return make_lab(client, org=f"Exec Lab {uuid.uuid4().hex[:10]}")


@pytest.fixture
def other_lab(client: Any) -> LabContext:
    return make_lab(client, org=f"Exec Other {uuid.uuid4().hex[:10]}")


# ---------------------------------------------------------------------------------------------
# Test doubles (tests only)
# ---------------------------------------------------------------------------------------------
class FakeBackend:
    """Deterministic in-memory sandbox: no code is executed; outcomes are scripted per test."""

    name = "local_docker"

    def __init__(
        self,
        *,
        exit_code: int = 0,
        oom: bool = False,
        outputs: dict[str, bytes] | None = None,
        logs: bytes = b"step 1\nstep 2\n",
        never_exit: bool = False,
        start_error: Exception | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.oom = oom
        self.outputs = outputs or {}
        self.logs = logs
        self.never_exit = never_exit
        self.start_error = start_error
        self.contexts: dict[str, JobContext] = {}
        self.staged: dict[str, dict[str, bytes]] = {}
        self.alive: set[str] = set()
        self.killed: set[str] = set()
        self.cleaned: set[str] = set()

    def capabilities(self) -> Capabilities:
        return Capabilities(name=self.name, enabled=True, max_timeout_seconds=3600, description="fake")

    def health(self) -> bool:
        return True

    def start(self, ctx: JobContext) -> StartResult:
        if self.start_error is not None:
            raise self.start_error
        jid = str(ctx.job_id)
        self.contexts[jid] = ctx
        staged: dict[str, bytes] = {}
        if ctx.inputs_path is not None:
            with tarfile.open(ctx.inputs_path) as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        fh = tar.extractfile(member)
                        staged[member.name] = fh.read() if fh else b""
        self.staged[jid] = staged
        self.alive.add(jid)
        return StartResult(handle=self._handle(jid), image_digest="sha256:" + "f" * 64)

    def _handle(self, jid: str) -> BackendHandle:
        return BackendHandle(backend=self.name, job_id=jid, backend_job_id=f"fake-{jid}", details={})

    def status(self, handle: BackendHandle) -> BackendStatus:
        if handle.job_id not in self.alive:
            return BackendStatus(state=BackendState.MISSING)
        if handle.job_id in self.killed:
            return BackendStatus(state=BackendState.EXITED, exit_code=137)
        if self.never_exit:
            return BackendStatus(state=BackendState.RUNNING)
        return BackendStatus(state=BackendState.EXITED, exit_code=self.exit_code, oom_killed=self.oom)

    def sample_usage(self, handle: BackendHandle, *, include_disk: bool = False) -> ResourceSample | None:
        return ResourceSample(cpu_seconds=0.25, memory_bytes=5 * 1024 * 1024, disk_bytes=1024 if include_disk else None)

    def read_logs(self, handle: BackendHandle, *, since_ns: int | None, max_bytes: int) -> LogBatch:
        if since_ns is not None:
            return LogBatch(lines=[], cursor_ns=since_ns)
        now = time.time_ns()
        lines = [LogLine(ts_ns=now + i, stream="stdout", text=t) for i, t in enumerate(self.logs.decode().splitlines())]
        return LogBatch(lines=lines, cursor_ns=now + len(lines))

    def fetch_logs(self, handle: BackendHandle, *, max_bytes: int) -> tuple[bytes, bool]:
        return self.logs[:max_bytes], len(self.logs) > max_bytes

    def kill(self, handle: BackendHandle) -> None:
        self.killed.add(handle.job_id)

    def collect_outputs(self, handle: BackendHandle, dest: Path, limits: ExtractLimits) -> ExtractionReport:
        buf = io.BytesIO()
        with TarBuilder(buf) as tar:
            tar.add_dir("output", mode=0o755)
            for path, data in self.outputs.items():
                tar.add_bytes(f"output/{path}", data, mode=0o644, dir_mode=0o755)
        buf.seek(0)
        return safe_extract(buf, dest, limits, strip_prefix="output", strict=False)

    def cleanup(self, handle: BackendHandle) -> None:
        self.cleaned.add(handle.job_id)
        self.alive.discard(handle.job_id)

    def find_by_job_id(self, job_id: uuid.UUID | str) -> BackendHandle | None:
        jid = str(job_id)
        return self._handle(jid) if jid in self.alive else None


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _execution_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[uuid.UUID, uuid.UUID]]]:
    from aegis_api.lab.storage import configure_storage
    from aegis_api.lab.storage.local import LocalFilesystemStorage

    dispatched: list[tuple[uuid.UUID, uuid.UUID]] = []
    configure_storage(LocalFilesystemStorage(tmp_path / "objects"))
    dispatch.set_job_dispatcher(lambda org, job: dispatched.append((org, job)))
    monkeypatch.setattr(service, "POLL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(service, "HEARTBEAT_INTERVAL_SECONDS", 0.3)
    monkeypatch.setattr(service, "FOLLOW_POLL_SECONDS", 0.1)
    monkeypatch.setattr(service, "LOG_EVENT_INTERVAL_SECONDS", 0.1)
    yield dispatched
    dispatch.set_job_dispatcher(None)
    configure_storage(None)
    register_backend("local_docker", None)


@pytest.fixture
def allow_policy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []

    def fake(db: Any, actor: Any, action: str, context: dict[str, Any], **kw: Any) -> service.PolicyOutcome:
        contexts.append({"action": action, **context})
        return service.PolicyOutcome(effect="allow", reasons=("test allow",))

    monkeypatch.setattr(service, "_evaluate_policy", fake)
    return contexts


@pytest.fixture
def fake_backend() -> FakeBackend:
    backend = FakeBackend(outputs={"metrics.json": b'{"accuracy": 0.91, "loss": 0.2}', "result.csv": b"a,b\n1,2\n"})
    register_backend("local_docker", backend)
    return backend


def _body(lab: Any, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "project_id": str(lab.project_id),
        "command": ["python", "-c", "print('hi')"],
        "resources": {"cpu": 1, "memory_mb": 256, "disk_mb": 64},
        "timeout_seconds": 60,
    }
    body.update(kw)
    return body


def _submit(lab: Any, **kw: Any) -> ComputeJob:
    with lab.db() as db:
        job = service.submit_job(db, lab.actor(), JobSpec.model_validate(_body(lab, **kw)))
        db.expunge(job)
    return job


def _job(lab: Any, job_id: Any) -> ComputeJob:
    with lab.db() as db:
        job = db.get(ComputeJob, uuid.UUID(str(job_id)))
        assert job is not None
        db.expunge(job)
        return job


def _events(lab: Any, job_id: Any) -> list[str]:
    with lab.db() as db:
        rows = db.scalars(select(LabEvent.type).where(LabEvent.subject_id == str(job_id)).order_by(LabEvent.id)).all()
    return list(rows)


# ---------------------------------------------------------------------------------------------
# HTTP admission
# ---------------------------------------------------------------------------------------------
def test_submit_over_http_is_202_and_dispatched_after_commit(lab: Any, allow_policy: Any, _execution_env: Any) -> None:
    r = lab.post("/api/v1/compute-jobs", json=_body(lab, env={"SEED": "3"}, parameters={"lr": 0.1}))
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["status"] == "QUEUED" and out["image"] == "python:3.12-slim"
    assert out["network_policy"] == {"mode": "none", "hosts": []}
    assert out["filesystem_policy"]["root"] == "read_only"
    assert out["secrets_policy"] == {"allowed": [], "requested": []}
    assert out["resource_request"]["pids"] == get_settings().execution_pids_limit
    assert {k: out["inputs"][0][k] for k in ("kind", "path", "ref_id", "split")} == {
        "kind": "parameters",
        "path": "input/params.json",
        "ref_id": None,
        "split": None,
    }
    assert (uuid.UUID(str(lab.org_id)), uuid.UUID(out["id"])) in _execution_env
    assert allow_policy[-1]["action"] == "execution.submit"
    assert allow_policy[-1]["network_mode"] == "none" and allow_policy[-1]["is_human"] is True
    got = lab.get(f"/api/v1/compute-jobs/{out['id']}")
    assert got.status_code == 200 and got.json()["id"] == out["id"]
    listed = lab.get("/api/v1/compute-jobs", params={"status": "queued", "project_id": str(lab.project_id)})
    assert listed.status_code == 200 and out["id"] in [j["id"] for j in listed.json()["items"]]
    assert _events(lab, out["id"]) == ["EXPERIMENT_QUEUED"]


def test_http_idempotency_key_never_creates_a_second_job(lab: Any, allow_policy: Any) -> None:
    headers = {"Idempotency-Key": f"submit-{uuid.uuid4().hex}"}
    first = lab.post("/api/v1/compute-jobs", json=_body(lab), headers=headers)
    second = lab.post("/api/v1/compute-jobs", json=_body(lab), headers=headers)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert second.headers.get("Idempotent-Replayed") == "true"
    key = f"job-key-{uuid.uuid4().hex}"
    a = _submit(lab, idempotency_key=key)
    b = _submit(lab, idempotency_key=key)
    assert a.id == b.id
    with lab.db() as db:
        count = db.scalar(select(text("count(*)")).select_from(ComputeJob).where(ComputeJob.idempotency_key == key))
    assert count == 1


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"image": "ubuntu:24.04"}, "allowed image list"),
        ({"env": {"LD_PRELOAD": "/tmp/x.so"}}, "LD_PRELOAD"),
        ({"env": {"AWS_SECRET_ACCESS_KEY": "x"}}, "credential"),
        ({"network": {"mode": "allowlist", "hosts": ["api.openalex.org"]}}, "egress proxy"),
        ({"resources": {"cpu": 1, "memory_mb": 256, "gpu_type": "nvidia-a100", "gpu_count": 1}}, "GPU"),
        ({"timeout_seconds": 10**7}, "timeout_seconds"),
        ({"resources": {"cpu": 64}}, "resources.cpu"),
    ],
)
def test_invalid_specs_are_rejected(lab: Any, allow_policy: Any, overrides: dict[str, Any], fragment: str) -> None:
    r = lab.post("/api/v1/compute-jobs", json=_body(lab, **overrides))
    assert r.status_code == 422, r.text
    assert fragment in json.dumps(r.json()["error"]), r.json()


def test_input_paths_are_validated(lab: Any, allow_policy: Any) -> None:
    for path in ("../escape", "/etc/passwd", "output/x", "input/params.json"):
        r = lab.post("/api/v1/compute-jobs", json=_body(lab, inputs=[{"kind": "inline", "path": path, "content": "x"}]))
        assert r.status_code == 422, (path, r.text)
    dup = [{"kind": "inline", "path": "input/a", "content": "1"}, {"kind": "inline", "path": "input/a", "content": "2"}]
    assert lab.post("/api/v1/compute-jobs", json=_body(lab, inputs=dup)).status_code == 422


def test_permissions_and_cross_tenant_isolation(lab: Any, other_lab: Any, allow_policy: Any) -> None:
    job = _submit(lab)
    viewer = lab.actor(role="viewer")
    with lab.db() as db, pytest.raises(Exception) as exc:
        service.submit_job(db, viewer, JobSpec.model_validate(_body(lab)))
    assert getattr(exc.value, "status_code", None) == 403
    with lab.db() as db:
        assert service.get_job(db, viewer, job.id).id == job.id  # viewers can read
        with pytest.raises(Exception) as denied:
            service.cancel_job(db, viewer, job.id)
        assert getattr(denied.value, "status_code", None) == 403
    for path in (f"/api/v1/compute-jobs/{job.id}", f"/api/v1/compute-jobs/{job.id}/logs"):
        assert other_lab.get(path).status_code == 404
    assert other_lab.post(f"/api/v1/compute-jobs/{job.id}/cancel").status_code == 404
    assert job.id not in [j["id"] for j in other_lab.get("/api/v1/compute-jobs").json()["items"]]
    # another tenant cannot submit into this project either
    r = other_lab.post("/api/v1/compute-jobs", json=_body(lab))
    assert r.status_code == 404
    with lab.db() as db, pytest.raises(Exception) as bad_project:
        service.run_job(other_lab.org_id, job.id)
    assert getattr(bad_project.value, "status_code", None) == 404
    del db


def test_cancel_queued_then_finished_job_is_409(lab: Any, allow_policy: Any) -> None:
    job = _submit(lab)
    r = lab.post(f"/api/v1/compute-jobs/{job.id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED" and r.json()["cancel_requested"] is True
    again = lab.post(f"/api/v1/compute-jobs/{job.id}/cancel")
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_state_transition"
    with lab.db() as db:
        actions = db.scalars(select(AuditLog.action).where(AuditLog.resource_id == str(job.id))).all()
    assert "EXPERIMENT_CANCELLED" in actions
    assert service.run_job(lab.org_id, job.id).status == "CANCELLED"


# ---------------------------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------------------------
def test_policy_deny_creates_no_job(lab: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "_evaluate_policy",
        lambda *a, **k: service.PolicyOutcome(effect="deny", reasons=("no compute on fridays",)),
    )
    r = lab.post("/api/v1/compute-jobs", json=_body(lab, idempotency_key=f"deny-{uuid.uuid4().hex}"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "policy_denied"
    assert "no compute on fridays" in r.json()["error"]["message"]
    with lab.db() as db:
        assert db.scalar(select(ComputeJob.id).where(ComputeJob.organization_id == lab.org_id)) is None


def test_governance_unavailable_fails_closed(lab: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "aegis_api.lab.governance.policies", None)
    with lab.db() as db, pytest.raises(PolicyDenied, match="fail closed"):
        service.submit_job(db, lab.actor(), JobSpec.model_validate(_body(lab)))


def test_secrets_are_denied_by_default(lab: Any) -> None:
    pytest.importorskip("aegis_api.lab.governance.policies")
    with lab.db() as db, pytest.raises(PolicyDenied):
        service.submit_job(db, lab.actor(), JobSpec.model_validate(_body(lab, secrets=["DB_URL"])))


def test_require_approval_holds_the_job_until_decided(
    lab: Any, fake_backend: FakeBackend, monkeypatch: Any, _execution_env: Any
) -> None:
    monkeypatch.setattr(
        service,
        "_evaluate_policy",
        lambda *a, **k: service.PolicyOutcome(effect="require_approval", reasons=("expensive",)),
    )
    r = lab.post("/api/v1/compute-jobs", json=_body(lab))
    assert r.status_code == 202, r.text
    out = r.json()
    assert out["status"] == "QUEUED" and out["approval_id"] and out["status_reason"] == "awaiting_approval"
    assert all(job_id != uuid.UUID(out["id"]) for _, job_id in _execution_env)  # not dispatched
    with pytest.raises(ApprovalRequired) as exc:
        service.run_job(lab.org_id, uuid.UUID(out["id"]))
    assert exc.value.approval_id == out["approval_id"]
    admin = session_factory(admin=True)()
    try:
        admin.execute(
            text("update approvals set status = 'APPROVED', decided_at = now() where id = :id"),
            {"id": out["approval_id"]},
        )
        admin.commit()
    finally:
        admin.close()
    result = service.run_job(lab.org_id, uuid.UUID(out["id"]))
    assert result.status == "SUCCEEDED"

    second = lab.post("/api/v1/compute-jobs", json=_body(lab)).json()
    admin = session_factory(admin=True)()
    try:
        admin.execute(text("update approvals set status = 'REJECTED' where id = :id"), {"id": second["approval_id"]})
        admin.commit()
    finally:
        admin.close()
    rejected = service.run_job(lab.org_id, uuid.UUID(second["id"]))
    assert rejected.status == "CANCELLED" and rejected.reason == "approval_rejected"


def test_quota_limits_concurrent_jobs(lab: Any, allow_policy: Any) -> None:
    with lab.db() as db:
        from aegis_api.lab.core.org_settings import get_org_settings

        row = get_org_settings(db, lab.org_id)
        row.quotas = {**(row.quotas or {}), "max_concurrent_experiments": 1}
    _submit(lab)
    r = lab.post("/api/v1/compute-jobs", json=_body(lab))
    assert r.status_code == 403 and r.json()["error"]["code"] == "quota_exceeded"
    with lab.db() as db:
        settings_row = db.scalar(select(OrganizationSettings).where(OrganizationSettings.organization_id == lab.org_id))
        assert settings_row is not None


def test_exhausted_mission_budget_is_refused(lab: Any, allow_policy: Any) -> None:
    with lab.db() as db:
        mission = Mission(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            title="Budgeted",
            objective="Test budgets",
            status="RUNNING",
            budget={"max_compute_cost_usd": 1.0},
            spent_compute_usd=Decimal("1.0"),
        )
        db.add(mission)
        db.flush()
        mission_id = mission.id
    r = lab.post("/api/v1/compute-jobs", json=_body(lab, mission_id=str(mission_id)))
    assert r.status_code == 409 and r.json()["error"]["code"] == "budget_exceeded", r.text


# ---------------------------------------------------------------------------------------------
# Worker-side execution
# ---------------------------------------------------------------------------------------------
def test_run_job_success_records_outputs_usage_evidence(
    lab: Any, allow_policy: Any, fake_backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "execution_cpu_price_per_hour_usd", 36.0)
    job = _submit(
        lab,
        inputs=[
            {"kind": "inline", "path": "code/main.py", "content": "print('hi')\n"},
            {"kind": "inline", "path": "input/blob.bin", "content": "AAEC", "encoding": "base64"},
        ],
        parameters={"lr": 0.01, "seed": 7},
        env={"SEED": "7"},
    )
    result = service.run_job(lab.org_id, job.id, heartbeat=lambda d: None)
    assert result.status == "SUCCEEDED" and result.exit_code == 0 and result.error is None
    # inputs staged read-only with parameters, inline text and decoded base64
    staged = fake_backend.staged[str(job.id)]
    assert staged["code/main.py"] == b"print('hi')\n"
    assert staged["input/blob.bin"] == b"\x00\x01\x02"
    assert json.loads(staged["input/params.json"]) == {"lr": 0.01, "seed": 7}
    ctx = fake_backend.contexts[str(job.id)]
    assert ctx.spec.user == settings.execution_user and ctx.spec.network.mode == "none"
    # outputs → immutable evidence artifacts with verified checksums
    assert set(result.outputs) == {"metrics.json", "result.csv"}
    with lab.db() as db:
        for path, version_id in result.outputs.items():
            version = db.get(ArtifactVersion, uuid.UUID(version_id))
            assert version is not None
            expected = fake_backend.outputs[path]
            assert version.checksum == hashlib.sha256(expected).hexdigest() and version.size_bytes == len(expected)
        usage = db.scalars(select(ComputeUsage).where(ComputeUsage.compute_job_id == job.id)).all()
        evidence = db.scalars(
            select(Evidence).where(Evidence.organization_id == lab.org_id, Evidence.kind == "lab.compute_job")
        ).all()
        actions = db.scalars(select(AuditLog.action).where(AuditLog.resource_id == str(job.id))).all()
        stored = db.get(ComputeJob, job.id)
        assert stored is not None
        assert stored.image_digest == "sha256:" + "f" * 64 and stored.started_at and stored.completed_at
        assert stored.logs_artifact_id is not None and len(stored.artifact_ids) == 2
    assert result.metrics is not None and result.metrics["source"] == "self_reported"
    assert result.metrics["values"] == {"accuracy": 0.91, "loss": 0.2}
    assert len(usage) == 1 and usage[0].cost_usd == result.cost_usd
    # the job exited before the first usage sample → CPU time falls back to reserved vCPU × wall time
    assert stored.resource_usage["cpu_seconds_measured"] is False
    assert usage[0].cpu_seconds == pytest.approx(stored.resource_usage["cpu_seconds"], abs=1e-3)
    assert usage[0].wall_seconds == pytest.approx(stored.resource_usage["wall_seconds"], abs=1e-3)
    assert result.cost_usd >= Decimal("0")
    assert any(e.content["compute_job_id"] == str(job.id) for e in evidence)
    ev = next(e for e in evidence if e.content["compute_job_id"] == str(job.id))
    assert ev.content["outputs"]["result.csv"] == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert ev.content["metrics_source"] == "self_reported"
    assert "EXPERIMENT_EXECUTED" in actions
    types = _events(lab, job.id)
    assert types[0] == "EXPERIMENT_QUEUED" and types[-1] == "EXPERIMENT_COMPLETED"
    assert "EXPERIMENT_STARTED" in types and "EXPERIMENT_LOG" in types
    assert str(job.id) in fake_backend.cleaned
    # idempotent: a second call returns the stored result without re-running
    again = service.run_job(lab.org_id, job.id)
    assert again.model_dump() == result.model_dump()
    logs = lab.get(f"/api/v1/compute-jobs/{job.id}/logs")
    assert logs.status_code == 200 and logs.json()["source"] == "artifact" and "step 2" in logs.json()["text"]
    out = lab.get(f"/api/v1/compute-jobs/{job.id}").json()
    assert out["metrics"]["values"]["accuracy"] == 0.91 and out["outputs"] == result.outputs


@pytest.mark.parametrize(
    ("backend_kwargs", "timeout", "status", "reason"),
    [
        ({"exit_code": 3}, 60, "FAILED", "nonzero_exit"),
        ({"exit_code": 137, "oom": True}, 60, "FAILED", "oom"),
        ({"never_exit": True}, 1, "TIMED_OUT", "timeout"),
        ({"start_error": ExecutionUnavailable("daemon down")}, 60, "FAILED", "backend_unavailable"),
    ],
)
def test_run_job_failure_modes(
    lab: Any, allow_policy: Any, backend_kwargs: dict[str, Any], timeout: int, status: str, reason: str
) -> None:
    backend = FakeBackend(**backend_kwargs)
    register_backend("local_docker", backend)
    job = _submit(lab, timeout_seconds=timeout)
    result = service.run_job(lab.org_id, job.id)
    assert (result.status, result.reason) == (status, reason)
    stored = _job(lab, job.id)
    assert stored.status == status and stored.completed_at is not None
    if status == "TIMED_OUT":
        assert str(job.id) in backend.killed and str(job.id) in backend.cleaned
    types = _events(lab, job.id)
    assert types[-1] == "EXPERIMENT_FAILED"


def test_output_limits_fail_the_job(lab: Any, allow_policy: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "execution_max_output_files", 2)
    register_backend("local_docker", FakeBackend(outputs={f"f{i}.txt": b"x" for i in range(5)}))
    job = _submit(lab)
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "FAILED" and result.reason == "output_limit_exceeded" and result.outputs == {}


def test_cancel_running_job_is_enforced_by_the_worker(lab: Any, allow_policy: Any) -> None:
    backend = FakeBackend(never_exit=True)
    register_backend("local_docker", backend)
    job = _submit(lab, timeout_seconds=120)
    results: list[Any] = []
    worker = threading.Thread(target=lambda: results.append(service.run_job(lab.org_id, job.id)))
    worker.start()
    deadline = time.monotonic() + 10
    while _job(lab, job.id).status != "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.05)
    r = lab.post(f"/api/v1/compute-jobs/{job.id}/cancel")
    assert r.status_code == 200 and r.json()["cancel_requested"] is True and r.json()["status"] == "RUNNING"
    worker.join(timeout=15)
    assert results and results[0].status == "CANCELLED" and results[0].reason == "cancel_requested"
    assert str(job.id) in backend.killed


def test_workflow_cancellation_callback(lab: Any, allow_policy: Any) -> None:
    register_backend("local_docker", FakeBackend(never_exit=True))
    job = _submit(lab, timeout_seconds=120)
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    result = service.run_job(lab.org_id, job.id, is_cancelled=cancelled)
    assert result.status == "CANCELLED" and result.reason == "workflow_cancelled"


def _make_stale(lab: Any, job_id: uuid.UUID, status: str) -> None:
    with lab.db() as db:
        job = db.get(ComputeJob, job_id)
        assert job is not None
        job.status = status
        job.started_at = utcnow() - timedelta(seconds=5)
        job.heartbeat_at = utcnow() - timedelta(minutes=10)


def test_reattach_after_worker_restart(lab: Any, allow_policy: Any) -> None:
    backend = FakeBackend(outputs={"out.txt": b"done"})
    register_backend("local_docker", backend)
    job = _submit(lab)
    _make_stale(lab, job.id, "RUNNING")
    backend.alive.add(str(job.id))  # the sandbox kept running while the worker was gone
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "SUCCEEDED" and set(result.outputs) == {"out.txt"}
    stored = _job(lab, job.id)
    assert stored.attempt == 2
    assert "COMPUTE_JOB_UPDATED" in _events(lab, job.id)


def test_stale_job_without_sandbox_is_worker_lost(lab: Any, allow_policy: Any) -> None:
    register_backend("local_docker", FakeBackend())
    job = _submit(lab)
    _make_stale(lab, job.id, "RUNNING")
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "FAILED" and result.reason == "worker_lost"


def test_live_owner_is_followed_not_duplicated(lab: Any, allow_policy: Any) -> None:
    backend = FakeBackend()
    register_backend("local_docker", backend)
    job = _submit(lab)
    with lab.db() as db:
        row = db.get(ComputeJob, job.id)
        assert row is not None
        row.status, row.started_at, row.heartbeat_at = "RUNNING", utcnow(), utcnow()
    results: list[Any] = []
    follower = threading.Thread(target=lambda: results.append(service.run_job(lab.org_id, job.id)))
    follower.start()
    time.sleep(0.4)
    with lab.db() as db:
        row = db.get(ComputeJob, job.id)
        assert row is not None
        row.status, row.exit_code, row.completed_at = "SUCCEEDED", 0, utcnow()
    follower.join(timeout=10)
    assert results and results[0].status == "SUCCEEDED"
    assert backend.contexts == {}  # the follower never started a second sandbox


def test_reconcile_requeues_fails_and_cancels(
    lab: Any, allow_policy: Any, _execution_env: Any, monkeypatch: Any
) -> None:
    register_backend("local_docker", FakeBackend())
    provisioning = _submit(lab)
    running = _submit(lab)
    _make_stale(lab, provisioning.id, "PROVISIONING")
    _make_stale(lab, running.id, "RUNNING")
    monkeypatch.setattr(
        service, "_evaluate_policy", lambda *a, **k: service.PolicyOutcome(effect="require_approval", reasons=("x",))
    )
    held = _submit(lab)
    admin = session_factory(admin=True)()
    try:
        admin.execute(text("update approvals set status = 'REJECTED' where id = :id"), {"id": str(held.approval_id)})
        admin.commit()
    finally:
        admin.close()
    activity = get_activity("execution.reconcile_stale_jobs")
    report = activity.fn(ActivityContext(actor=lab.actor()), {"limit": 50})
    assert str(provisioning.id) in report["requeued"]
    assert str(running.id) in report["failed"]
    assert str(held.id) in report["cancelled"]
    assert _job(lab, provisioning.id).status == "QUEUED"
    assert (_job(lab, running.id).status, _job(lab, running.id).status_reason) == ("FAILED", "worker_lost")
    assert _job(lab, held.id).status == "CANCELLED"
    assert (uuid.UUID(str(lab.org_id)), provisioning.id) in _execution_env


def test_activities_run_and_cancel(lab: Any, allow_policy: Any, fake_backend: FakeBackend) -> None:
    job = _submit(lab)
    ctx = ActivityContext(actor=lab.actor())
    result = get_activity("execution.run_job").fn(ctx, {"job_id": str(job.id)})
    assert result["status"] == "SUCCEEDED" and isinstance(result["cost_usd"], float)
    assert get_activity("execution.run_job").task_queue == "execution"
    cancelled = get_activity("execution.cancel_job").fn(ctx, {"job_id": str(job.id)})
    assert cancelled == {"job_id": str(job.id), "status": "SUCCEEDED", "cancel_requested": False}
    queued = _submit(lab)
    assert get_activity("execution.cancel_job").fn(ctx, {"job_id": str(queued.id)})["status"] == "CANCELLED"


# ---------------------------------------------------------------------------------------------
# Data inputs
# ---------------------------------------------------------------------------------------------
def _dataset_version(lab: Any, splits: dict[str, bytes], *, evaluator_only: tuple[str, ...] = ()) -> uuid.UUID:
    from aegis_api.lab.storage import get_storage
    from aegis_api.lab.storage.keys import object_key

    storage = get_storage()
    with lab.db() as db:
        dataset = Dataset(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            name=f"ds-{uuid.uuid4().hex[:8]}",
        )
        db.add(dataset)
        db.flush()
        split_meta: dict[str, Any] = {}
        for name, data in splits.items():
            stored = storage.put_bytes(object_key(lab.org_id, lab.project_id, "datasets", dataset.id, name), data)
            split_meta[name] = {
                "storage_key": stored.key,
                "checksum": stored.sha256,
                "size_bytes": stored.size,
                "filename": f"{name}.csv",
                "visibility": "evaluator_only" if name in evaluator_only else "experiment",
            }
        main = storage.put_bytes(object_key(lab.org_id, lab.project_id, "datasets", dataset.id, "all.csv"), b"all\n")
        version = DatasetVersion(
            organization_id=lab.org_id,
            dataset_id=dataset.id,
            project_id=lab.project_id,
            version=1,
            checksum=main.sha256,
            size_bytes=main.size,
            format="csv",
            splits=split_meta,
            storage_key=main.key,
        )
        db.add(version)
        db.flush()
        return version.id


def test_dataset_inputs_respect_evaluator_only_splits(lab: Any, allow_policy: Any, fake_backend: FakeBackend) -> None:
    version_id = _dataset_version(lab, {"train": b"x,y\n1,2\n", "heldout": b"x,y\n9,9\n"}, evaluator_only=("heldout",))
    whole = {"kind": "dataset_version", "ref_id": str(version_id), "path": "input/data.csv"}
    r = lab.post("/api/v1/compute-jobs", json=_body(lab, inputs=[whole]))
    assert r.status_code == 422 and "evaluator-only" in json.dumps(r.json())
    heldout = {**whole, "split": "heldout"}
    r = lab.post("/api/v1/compute-jobs", json=_body(lab, inputs=[heldout]))
    assert r.status_code == 422 and "evaluator-only" in json.dumps(r.json())
    train = {**whole, "split": "train", "path": "input/train.csv"}
    job = _submit(lab, inputs=[train])
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "SUCCEEDED", result
    assert fake_backend.staged[str(job.id)]["input/train.csv"] == b"x,y\n1,2\n"
    assert "input/heldout.csv" not in fake_backend.staged[str(job.id)]


def test_foreign_dataset_version_is_not_found(lab: Any, other_lab: Any, allow_policy: Any) -> None:
    version_id = _dataset_version(lab, {"train": b"a\n"})
    ref = {"kind": "dataset_version", "ref_id": str(version_id), "split": "train", "path": "input/t.csv"}
    r = other_lab.post(
        "/api/v1/compute-jobs",
        json={"project_id": str(other_lab.project_id), "command": ["true"], "inputs": [ref]},
    )
    assert r.status_code == 404


def test_tampered_input_fails_integrity_check(lab: Any, allow_policy: Any, fake_backend: FakeBackend) -> None:
    from aegis_api.lab.storage import get_storage

    version_id = _dataset_version(lab, {"train": b"original\n"})
    job = _submit(
        lab, inputs=[{"kind": "dataset_version", "ref_id": str(version_id), "split": "train", "path": "input/t.csv"}]
    )
    with lab.db() as db:
        version = db.get(DatasetVersion, version_id)
        assert version is not None
        key = version.splits["train"]["storage_key"]
    get_storage().put_bytes(key, b"tampered\n")
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "FAILED" and result.reason == "inputs_unavailable"
    assert "integrity" in (result.error or "").lower() or "checksum" in (result.error or "").lower()


def test_backends_endpoint(lab: Any) -> None:
    r = lab.get("/api/v1/execution/backends")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] == get_settings().execution_backend
    names = {b["name"]: b for b in body["backends"]}
    assert set(names) == {"local_docker", "kubernetes", "cloud_run", "batch", "hpc"}
    assert names["cloud_run"]["enabled"] is False and "not enabled" in names["cloud_run"]["reason"]
    assert body["limits"]["default_image"] == get_settings().execution_default_image
    assert body["limits"]["egress_available"] is False


def test_disabled_execution_is_503(lab: Any, allow_policy: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "execution_backend", "disabled")
    r = lab.post("/api/v1/compute-jobs", json=_body(lab))
    assert r.status_code == 503 and r.json()["error"]["code"] == "execution_unavailable"


def test_approval_rows_are_real(lab: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("aegis_api.lab.governance.approvals")
    monkeypatch.setattr(
        service, "_evaluate_policy", lambda *a, **k: service.PolicyOutcome(effect="require_approval", reasons=("r",))
    )
    job = _submit(lab)
    with lab.db() as db:
        approval = db.get(Approval, job.approval_id)
        assert approval is not None
        assert approval.action == "execution.submit" and approval.subject_type == "compute_job"
        assert approval.subject_id == str(job.id) and approval.status == "PENDING"


# ---------------------------------------------------------------------------------------------
# Durable dispatch through the workflows context
# ---------------------------------------------------------------------------------------------
def test_http_submission_runs_through_the_execution_workflow(lab: Any, allow_policy: Any) -> None:
    pytest.importorskip("aegis_api.lab.workflows.launcher")
    from aegis_api.lab.models import WorkflowRun

    backend = FakeBackend(outputs={"out.txt": b"via workflow"})
    register_backend("local_docker", backend)
    dispatch.set_job_dispatcher(None)  # use the real launcher (local workflow engine in tests)
    r = lab.post("/api/v1/compute-jobs", json=_body(lab))
    assert r.status_code == 202, r.text
    job_id = uuid.UUID(r.json()["id"])
    row = _job(lab, job_id)
    assert row.workflow_run_id is not None
    with lab.db() as db:
        run = db.get(WorkflowRun, row.workflow_run_id)
        assert run is not None and run.kind == "ExecutionJobWorkflow" and run.subject_id == str(job_id)
    # Whether the workflow engine's activity or this call gets there first, exactly one runner executes the
    # job (row lock + attempt token) and both observe the same terminal result.
    result = service.run_job(lab.org_id, job_id)
    final = _job(lab, job_id)
    assert result.status == final.status == "SUCCEEDED", (final.status, final.status_reason, final.error)
    assert len(backend.contexts) == 1
    assert set((final.output_manifest or {}).get("files", {})) == {"out.txt"}
    # a replayed submission with the same key reuses the same workflow run (no duplicate runner)
    with lab.db() as db:
        job = db.get(ComputeJob, job_id)
        assert job is not None
        assert dispatch.dispatch_in_session(db, lab.actor(), job) == "workflow"
        assert job.workflow_run_id == row.workflow_run_id


def test_reconcile_leaves_live_workflows_alone(lab: Any, allow_policy: Any) -> None:
    from aegis_api.lab.models import WorkflowRun

    backend = FakeBackend(never_exit=True)
    register_backend("local_docker", backend)
    job = _submit(lab)
    with lab.db() as db:
        run = WorkflowRun(
            organization_id=lab.org_id,
            project_id=lab.project_id,
            kind="ExecutionJobWorkflow",
            subject_type="compute_job",
            subject_id=str(job.id),
            engine="local",
            external_id=f"test-{uuid.uuid4().hex}",
            status="RUNNING",
        )
        db.add(run)
        db.flush()
        run_id = run.id
        row = db.get(ComputeJob, job.id)
        assert row is not None
        row.workflow_run_id = run_id
    _make_stale(lab, job.id, "RUNNING")
    backend.alive.add(str(job.id))
    report = service.reconcile_stale_jobs(lab.org_id)
    assert str(job.id) in report["workflow_managed"]
    assert _job(lab, job.id).status == "RUNNING"


def test_cancelling_a_held_job_withdraws_its_approval(lab: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("aegis_api.lab.governance.approvals")
    monkeypatch.setattr(
        service, "_evaluate_policy", lambda *a, **k: service.PolicyOutcome(effect="require_approval", reasons=("r",))
    )
    job = _submit(lab)
    r = lab.post(f"/api/v1/compute-jobs/{job.id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    with lab.db() as db:
        approval = db.get(Approval, job.approval_id)
        assert approval is not None and approval.status == "CANCELLED"


def test_idempotency_key_reuse_with_a_different_job_is_rejected(lab: Any, allow_policy: Any) -> None:
    key = f"reuse-{uuid.uuid4().hex}"
    _submit(lab, idempotency_key=key)
    r = lab.post("/api/v1/compute-jobs", json=_body(lab, idempotency_key=key, command=["python", "other.py"]))
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_key_reused"


def test_allowlisted_egress_when_the_operator_configured_a_proxy(
    lab: Any, allow_policy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "execution_egress_network", "aegis-egress")
    monkeypatch.setattr(settings, "execution_egress_proxy_url", "http://egress-proxy:3128")
    ok = lab.post(
        "/api/v1/compute-jobs",
        json=_body(lab, network={"mode": "allowlist", "hosts": ["API.OpenAlex.org", "en.wikipedia.org"]}),
    )
    assert ok.status_code == 202, ok.text
    assert ok.json()["network_policy"] == {"mode": "allowlist", "hosts": ["api.openalex.org", "en.wikipedia.org"]}
    assert allow_policy[-1]["network_mode"] == "allowlist"
    assert allow_policy[-1]["egress_hosts"] == ["api.openalex.org", "en.wikipedia.org"]
    denied = lab.post("/api/v1/compute-jobs", json=_body(lab, network={"mode": "allowlist", "hosts": ["evil.example"]}))
    assert denied.status_code == 422 and "egress allowlist" in json.dumps(denied.json())
    backends = lab.get("/api/v1/execution/backends").json()
    assert backends["limits"]["egress_available"] is True
