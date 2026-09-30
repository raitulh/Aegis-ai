"""Real sandbox runs on the local Docker daemon (marker ``docker``): isolation and lifecycle properties.

Every job here runs untrusted-style code inside the hardened container and the assertions check the
properties from the *inside* (uid, read-only root, no capabilities, no network, no Docker socket, noexec
/tmp) as well as from the outside (container configuration, timeouts, OOM, cancellation, output safety,
worker-restart re-attachment, cleanup of containers and volumes).
"""

from __future__ import annotations

import hashlib
import json
import os
import textwrap
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.lab.execution import dispatch, service
from aegis_api.lab.execution.backends import get_backend, reset_backends
from aegis_api.lab.execution.schemas import JobSpec
from aegis_api.lab.models import ArtifactVersion, ComputeJob, ComputeUsage, LabEvent
from tests.conftest import requires_db
from tests.lab.conftest import LabContext, make_lab

SOCKET = "/var/run/docker.sock"
IMAGE = "python:3.12-slim"


def _docker() -> httpx.Client:
    return httpx.Client(transport=httpx.HTTPTransport(uds=SOCKET), base_url="http://docker", timeout=30)


def _docker_ready() -> bool:
    if not Path(SOCKET).exists():
        return False
    try:
        with _docker() as client:
            return client.get(f"/images/{IMAGE}/json").status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    requires_db,
    pytest.mark.db,
    pytest.mark.docker,
    pytest.mark.skipif(not _docker_ready(), reason=f"Docker daemon with {IMAGE} is not available"),
]


@pytest.fixture
def lab(client: Any) -> LabContext:
    return make_lab(client, org=f"Exec Docker {uuid.uuid4().hex[:10]}")


@pytest.fixture(autouse=True)
def _sandbox_env(lab: LabContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from aegis_api.lab.storage import configure_storage
    from aegis_api.lab.storage.local import LocalFilesystemStorage

    reset_backends()
    configure_storage(LocalFilesystemStorage(tmp_path / "objects"))
    dispatch.set_job_dispatcher(lambda org, job: None)
    monkeypatch.setattr(
        service, "_evaluate_policy", lambda *a, **k: service.PolicyOutcome(effect="allow", reasons=("test",))
    )
    monkeypatch.setattr(service, "POLL_INTERVAL_SECONDS", 0.2)
    monkeypatch.setattr(service, "HEARTBEAT_INTERVAL_SECONDS", 0.5)
    monkeypatch.setattr(service, "LOG_EVENT_INTERVAL_SECONDS", 0.2)
    monkeypatch.setattr(service, "FOLLOW_POLL_SECONDS", 0.2)
    monkeypatch.setenv("AEGIS_TEST_HOST_MARKER", "host-only-value")
    yield
    dispatch.set_job_dispatcher(None)
    configure_storage(None)
    reset_backends()
    _remove_leftovers(lab.org_id)


def _remove_leftovers(org_id: uuid.UUID) -> None:
    """Never leave containers or volumes behind, even when an assertion failed mid-test."""
    label = json.dumps({"label": [f"aegis.org={org_id}"]})
    with _docker() as client:
        for item in client.get("/containers/json", params={"all": "true", "filters": label}).json():
            client.delete(f"/containers/{item['Id']}", params={"force": "true", "v": "true"})
        for volume in client.get("/volumes", params={"filters": label}).json().get("Volumes") or []:
            client.delete(f"/volumes/{volume['Name']}", params={"force": "true"})


def _python_job(lab: LabContext, code: str, **kw: Any) -> ComputeJob:
    body: dict[str, Any] = {
        "project_id": str(lab.project_id),
        "image": IMAGE,
        "command": ["python", "-c", textwrap.dedent(code)],
        "resources": {"cpu": 1, "memory_mb": 256, "disk_mb": 128},
        "timeout_seconds": 60,
    }
    body.update(kw)
    with lab.db() as db:
        job = service.submit_job(db, lab.actor(), JobSpec.model_validate(body))
        db.expunge(job)
    return job


def _assert_cleaned(job_id: uuid.UUID) -> None:
    assert get_backend("local_docker").find_by_job_id(job_id) is None
    with _docker() as client:
        assert client.get(f"/volumes/aegis-job-{job_id}").status_code == 404


def _row(lab: LabContext, job_id: uuid.UUID) -> ComputeJob:
    with lab.db() as db:
        job = db.get(ComputeJob, job_id)
        assert job is not None
        db.expunge(job)
        return job


def _logs(lab: LabContext, job_id: uuid.UUID) -> str:
    r = lab.get(f"/api/v1/compute-jobs/{job_id}/logs")
    assert r.status_code == 200, r.text
    return str(r.json()["text"])


# ---------------------------------------------------------------------------------------------
def test_job_outputs_metrics_usage_and_cleanup(lab: LabContext) -> None:
    job = _python_job(
        lab,
        """
        import json, os
        params = json.load(open("/workspace/input/params.json"))
        print("training with lr", params["lr"])
        with open("/workspace/output/metrics.json", "w") as f:
            json.dump([{"name": "accuracy", "value": 0.875, "step": 1}, {"name": "loss", "value": 0.31}], f)
        with open("/workspace/output/result.csv", "w") as f:
            f.write("epoch,acc\\n1,0.875\\n")
        os.makedirs("/workspace/output/plots")
        open("/workspace/output/plots/curve.svg", "w").write("<svg/>")
        print("done")
        """,
        parameters={"lr": 0.003},
    )
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "SUCCEEDED", result
    assert result.exit_code == 0
    assert set(result.outputs) == {"metrics.json", "result.csv", "plots/curve.svg"}
    assert result.metrics is not None and result.metrics["source"] == "self_reported"
    assert result.metrics["values"] == {"accuracy": 0.875, "loss": 0.31}
    assert result.metrics["items"][0] == {"name": "accuracy", "value": 0.875, "step": 1}
    with lab.db() as db:
        csv = db.get(ArtifactVersion, uuid.UUID(result.outputs["result.csv"]))
        assert csv is not None
        assert csv.checksum == hashlib.sha256(b"epoch,acc\n1,0.875\n").hexdigest()
        assert csv.size_bytes == len(b"epoch,acc\n1,0.875\n")
        usage = db.scalars(select(ComputeUsage).where(ComputeUsage.compute_job_id == job.id)).all()
        assert len(usage) == 1 and usage[0].backend == "local_docker" and usage[0].wall_seconds > 0
        log_lines = [
            line["text"]
            for event in db.scalars(
                select(LabEvent).where(LabEvent.subject_id == str(job.id), LabEvent.type == "EXPERIMENT_LOG")
            ).all()
            for line in event.payload["lines"]
        ]
    assert "training with lr 0.003" in log_lines and "done" in log_lines
    row = _row(lab, job.id)
    assert row.image_digest and row.image_digest.startswith("sha256:")
    assert "training with lr 0.003" in _logs(lab, job.id)
    _assert_cleaned(job.id)


def test_security_posture_inside_the_sandbox(lab: LabContext) -> None:
    job = _python_job(
        lab,
        """
        import errno, json, os, socket, subprocess
        facts = {"uid": os.getuid(), "gid": os.getgid(), "euid": os.geteuid()}
        def attempt(fn):
            try:
                fn()
                return "ok"
            except OSError as exc:
                return errno.errorcode.get(exc.errno, str(exc.errno))
        facts["write_root"] = attempt(lambda: open("/usr/pwned", "w").write("x"))
        facts["write_etc"] = attempt(lambda: open("/etc/pwned", "w").write("x"))
        facts["write_input"] = attempt(lambda: open("/workspace/input/params.json", "a").write("x"))
        facts["write_code_dir"] = attempt(lambda: open("/workspace/code/new.py", "w").write("x"))
        facts["write_output"] = attempt(lambda: open("/workspace/output/ok.txt", "w").write("x"))
        facts["write_tmp"] = attempt(lambda: open("/tmp/scratch.txt", "w").write("x"))
        with open("/tmp/run.sh", "w") as f:
            f.write("#!/bin/sh\\necho escaped\\n")
        os.chmod("/tmp/run.sh", 0o755)
        facts["exec_tmp"] = attempt(lambda: subprocess.run(["/tmp/run.sh"], check=True))
        facts["docker_socket"] = any(os.path.exists(p) for p in ("/var/run/docker.sock", "/run/docker.sock"))
        facts["connect"] = attempt(lambda: socket.create_connection(("1.1.1.1", 53), timeout=3))
        facts["resolve"] = attempt(lambda: socket.getaddrinfo("example.com", 80))
        facts["interfaces"] = sorted(line.split(":")[0].strip() for line in open("/proc/net/dev").readlines()[2:])
        status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
        facts["cap_eff"] = status["CapEff"].strip()
        facts["cap_bnd"] = status["CapBnd"].strip()
        facts["no_new_privs"] = status.get("NoNewPrivs", "").strip()
        facts["host_marker"] = os.environ.get("AEGIS_TEST_HOST_MARKER")
        facts["home"] = os.environ.get("HOME")
        facts["seed"] = os.environ.get("SEED")
        for path in ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory.max"):
            if os.path.exists(path):
                facts["memory_limit"] = open(path).read().strip()
        for path in ("/sys/fs/cgroup/pids/pids.max", "/sys/fs/cgroup/pids.max"):
            if os.path.exists(path):
                facts["pids_max"] = open(path).read().strip()
        json.dump(facts, open("/workspace/output/security.json", "w"))
        """,
        env={"SEED": "42"},
        resources={"cpu": 0.5, "memory_mb": 192, "disk_mb": 64, "pids": 64},
    )
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "SUCCEEDED", (result, _logs(lab, job.id))
    with lab.db() as db:
        version = db.get(ArtifactVersion, uuid.UUID(result.outputs["security.json"]))
        assert version is not None
        key = version.storage_key
    from aegis_api.lab.storage import get_storage

    facts = json.loads(get_storage().get_bytes(key))
    assert facts["uid"] == facts["gid"] == facts["euid"] == 65534
    assert facts["write_root"] == "EROFS" and facts["write_etc"] == "EROFS"
    assert facts["write_input"] == "EACCES" and facts["write_code_dir"] == "EACCES"
    assert facts["write_output"] == "ok" and facts["write_tmp"] == "ok"
    assert facts["exec_tmp"] == "EACCES"  # /tmp is mounted noexec
    assert facts["docker_socket"] is False
    assert facts["connect"] in {"ENETUNREACH", "EHOSTUNREACH", "EPERM", "ECONNREFUSED", "ETIMEDOUT"}
    assert facts["connect"] != "ok" and facts["resolve"] != "ok"
    assert facts["interfaces"] == ["lo"]
    assert int(facts["cap_eff"], 16) == 0 and int(facts["cap_bnd"], 16) == 0
    assert facts["no_new_privs"] == "1"
    assert facts["host_marker"] is None  # nothing is inherited from the worker's environment
    assert facts["home"] == "/tmp" and facts["seed"] == "42"
    if "memory_limit" in facts:
        assert int(facts["memory_limit"]) == 192 * 1024 * 1024
    if "pids_max" in facts:
        assert facts["pids_max"] == "64"
    _assert_cleaned(job.id)


def test_network_is_isolated(lab: LabContext) -> None:
    job = _python_job(
        lab,
        """
        import urllib.request
        urllib.request.urlopen("http://1.1.1.1", timeout=5)
        print("NETWORK REACHABLE")
        """,
    )
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "FAILED" and result.reason == "nonzero_exit" and result.exit_code == 1
    logs = _logs(lab, job.id)
    assert "NETWORK REACHABLE" not in logs and "URLError" in logs


def test_root_filesystem_is_read_only_and_user_is_not_root(lab: LabContext) -> None:
    job = _python_job(lab, 'open("/usr/x", "w").write("x")')
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "FAILED" and result.exit_code == 1
    assert "Read-only file system" in _logs(lab, job.id)

    who = _python_job(
        lab, "", command=["sh", "-c", "id -u > /workspace/output/uid.txt; id -g >> /workspace/output/uid.txt"]
    )
    result = service.run_job(lab.org_id, who.id)
    assert result.status == "SUCCEEDED", result
    with lab.db() as db:
        version = db.get(ArtifactVersion, uuid.UUID(result.outputs["uid.txt"]))
        assert version is not None
        assert version.checksum == hashlib.sha256(b"65534\n65534\n").hexdigest()


def test_timeout_kills_and_removes_the_container(lab: LabContext) -> None:
    job = _python_job(lab, "", command=["sleep", "60"], timeout_seconds=3)
    started = time.monotonic()
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "TIMED_OUT" and result.reason == "timeout"
    assert time.monotonic() - started < 30
    assert result.duration_seconds is not None and 2.5 <= result.duration_seconds < 30
    _assert_cleaned(job.id)


def test_out_of_memory_is_reported(lab: LabContext) -> None:
    job = _python_job(
        lab, "b = bytearray(256 * 1024 * 1024)\nprint(len(b))", resources={"cpu": 1, "memory_mb": 64, "disk_mb": 64}
    )
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "FAILED" and result.reason == "oom", result
    _assert_cleaned(job.id)


def test_malicious_output_links_are_not_followed(lab: LabContext) -> None:
    job = _python_job(
        lab,
        """
        import os
        os.symlink("/etc/passwd", "/workspace/output/passwd")
        os.symlink("/", "/workspace/output/rootfs")
        os.mkfifo("/workspace/output/pipe")
        open("/workspace/output/real.txt", "w").write("genuine")
        """,
    )
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "SUCCEEDED", result
    assert set(result.outputs) == {"real.txt"}
    row = _row(lab, job.id)
    rejected = {item["name"]: item["reason"] for item in row.output_manifest["rejected"]}
    assert rejected["output/passwd"] == "symbolic link" and rejected["output/rootfs"] == "symbolic link"
    assert rejected["output/pipe"] == "fifo"
    with lab.db() as db:
        checksums = set(
            db.scalars(select(ArtifactVersion.checksum).where(ArtifactVersion.organization_id == lab.org_id)).all()
        )
    host_passwd = hashlib.sha256(Path("/etc/passwd").read_bytes()).hexdigest()
    assert host_passwd not in checksums


def test_cancel_running_job_and_container_configuration(lab: LabContext) -> None:
    job = _python_job(lab, "", command=["sleep", "120"], timeout_seconds=120)
    results: list[Any] = []
    worker = threading.Thread(target=lambda: results.append(service.run_job(lab.org_id, job.id)))
    worker.start()
    deadline = time.monotonic() + 30
    while _row(lab, job.id).status != "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _row(lab, job.id).status == "RUNNING"
    with _docker() as client:
        info = client.get(f"/containers/aegis-job-{job.id}/json").json()
    hc = info["HostConfig"]
    assert info["Config"]["User"] == "65534:65534"
    assert hc["ReadonlyRootfs"] is True and hc["Privileged"] is False
    assert hc["CapDrop"] == ["ALL"] and not hc.get("CapAdd")
    assert "no-new-privileges:true" in hc["SecurityOpt"]
    assert hc["NetworkMode"] == "none" and info["Config"]["NetworkDisabled"] is True
    assert hc["Memory"] == hc["MemorySwap"] == 256 * 1024 * 1024
    assert hc["PidsLimit"] == get_settings().execution_pids_limit
    assert not hc.get("Binds")
    assert [(m["Type"], m["Destination"]) for m in info["Mounts"]] == [("volume", "/workspace")] or {
        (m["Type"], m["Destination"]) for m in info["Mounts"]
    } == {("volume", "/workspace"), ("tmpfs", "/tmp")}
    assert all("docker.sock" not in json.dumps(m) for m in info["Mounts"])
    assert info["Config"]["Labels"]["aegis.job_id"] == str(job.id)
    assert "AEGIS_TEST_HOST_MARKER=host-only-value" not in info["Config"]["Env"]

    r = lab.post(f"/api/v1/compute-jobs/{job.id}/cancel")
    assert r.status_code == 200 and r.json()["cancel_requested"] is True
    worker.join(timeout=60)
    assert results and results[0].status == "CANCELLED" and results[0].reason == "cancel_requested"
    _assert_cleaned(job.id)


class _WorkerCrash(Exception):
    """Simulates the worker process dying mid-run (the container keeps running)."""


def test_worker_restart_reattaches_to_the_running_sandbox(lab: LabContext, monkeypatch: pytest.MonkeyPatch) -> None:
    job = _python_job(
        lab,
        """
        import time
        time.sleep(3)
        open("/workspace/output/after-restart.txt", "w").write("survived")
        """,
    )

    def crash(self: Any) -> Any:
        raise _WorkerCrash()

    monkeypatch.setattr(service._JobRunner, "_monitor", crash)
    with pytest.raises(_WorkerCrash):
        service.run_job(lab.org_id, job.id)
    monkeypatch.undo()
    monkeypatch.setattr(service, "POLL_INTERVAL_SECONDS", 0.2)
    monkeypatch.setattr(service, "HEARTBEAT_INTERVAL_SECONDS", 0.5)
    row = _row(lab, job.id)
    assert row.status == "RUNNING" and row.attempt == 1
    assert get_backend("local_docker").find_by_job_id(job.id) is not None  # the sandbox outlived the worker

    # a fresh worker sees a stale heartbeat, takes over and re-attaches
    with lab.db() as db:
        stale = db.get(ComputeJob, job.id)
        assert stale is not None
        stale.heartbeat_at = utcnow() - timedelta(minutes=5)
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "SUCCEEDED", result
    assert set(result.outputs) == {"after-restart.txt"}
    row = _row(lab, job.id)
    assert row.attempt == 2
    _assert_cleaned(job.id)


def test_lost_sandbox_after_worker_crash_fails_the_job(lab: LabContext, monkeypatch: pytest.MonkeyPatch) -> None:
    job = _python_job(lab, "", command=["sleep", "30"], timeout_seconds=60)

    def crash(self: Any) -> Any:
        raise _WorkerCrash()

    monkeypatch.setattr(service._JobRunner, "_monitor", crash)
    with pytest.raises(_WorkerCrash):
        service.run_job(lab.org_id, job.id)
    monkeypatch.undo()
    handle = get_backend("local_docker").find_by_job_id(job.id)
    assert handle is not None
    get_backend("local_docker").cleanup(handle)  # the host lost the container too
    with lab.db() as db:
        stale = db.get(ComputeJob, job.id)
        assert stale is not None
        stale.heartbeat_at = utcnow() - timedelta(minutes=5)
    result = service.run_job(lab.org_id, job.id)
    assert result.status == "FAILED" and result.reason == "worker_lost"


def test_host_environment_is_not_inherited() -> None:
    assert os.environ.get("AEGIS_TEST_HOST_MARKER") == "host-only-value"
