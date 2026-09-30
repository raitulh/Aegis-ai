"""Docker sandbox isolation (requires a Docker engine): generated code runs without network, credentials,
privileges or a writable root filesystem, under hard resource limits, and only declared outputs come back."""

from __future__ import annotations

import json
import uuid

import pytest

from aegis_api.infrastructure.execution.base import ExecutionPolicyError, ExecutionRequest, ResourceSpec


def _docker() -> bool:
    try:
        import docker

        docker.from_env(timeout=3).ping()
        return True
    except Exception:
        return False


pytestmark = [pytest.mark.docker, pytest.mark.skipif(not _docker(), reason="Docker engine is not available")]

PROBE = r"""
import json, os, socket, subprocess, pathlib
report = {"uid": os.getuid(), "env": sorted(os.environ)}
try:
    socket.create_connection(("1.1.1.1", 53), timeout=2); report["network"] = "open"
except OSError as exc:
    report["network"] = "blocked"
try:
    socket.getaddrinfo("example.com", 443); report["dns"] = "resolved"
except OSError:
    report["dns"] = "blocked"
for target in ("/etc/pwned", "/usr/pwned"):
    try:
        pathlib.Path(target).write_text("x"); report[target] = "writable"
    except OSError:
        report[target] = "read-only"
try:
    pathlib.Path("/tmp/probe.sh").write_text("#!/bin/sh\necho hi\n"); os.chmod("/tmp/probe.sh", 0o755)
    subprocess.run(["/tmp/probe.sh"], check=True, capture_output=True); report["tmp_exec"] = "allowed"
except Exception:
    report["tmp_exec"] = "denied"
caps = [l for l in open("/proc/self/status") if l.startswith("CapEff")]
report["cap_eff"] = caps[0].split()[1] if caps else None
report["no_new_privs"] = [l.split()[1] for l in open("/proc/self/status") if l.startswith("NoNewPrivs")][0]
pathlib.Path("/workspace/output").mkdir(parents=True, exist_ok=True)
pathlib.Path("/workspace/output/report.json").write_text(json.dumps(report))
"""


@pytest.fixture
def backend():
    from aegis_api.infrastructure.execution.docker_backend import LocalDockerBackend

    return LocalDockerBackend()


def _request(command: list[str], **kw) -> ExecutionRequest:
    return ExecutionRequest(
        job_id=f"test-{uuid.uuid4().hex[:12]}",
        organization_id=str(uuid.uuid4()),
        image="python:3.12-alpine",
        command=command,
        **kw,
    )


def test_generated_code_is_isolated(backend, monkeypatch) -> None:
    # Platform credentials present in the host process must never be inherited by the sandbox.
    monkeypatch.setenv("GEMINI_API_KEY", "host-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://host-secret")
    result = backend.run(
        _request(["python", "/workspace/code/probe.py"], files={"code/probe.py": PROBE.encode()}, timeout_seconds=60)
    )
    assert result.succeeded, (result.stderr, result.error)
    report = json.loads(result.outputs["report.json"])
    assert report["uid"] == 65534  # never root
    assert report["network"] == "blocked" and report["dns"] == "blocked"
    assert report["/etc/pwned"] == "read-only" and report["/usr/pwned"] == "read-only"
    assert report["tmp_exec"] == "denied"  # /tmp is noexec
    assert int(report["cap_eff"], 16) == 0 and report["no_new_privs"] == "1"
    leaked = [k for k in report["env"] if k.startswith(("GEMINI", "AWS_", "DATABASE", "OPENAI", "ANTHROPIC", "REDIS"))]
    assert leaked == []  # no platform credentials leak into the sandbox
    assert "host-secret" not in result.stdout + result.stderr
    assert result.backend == "docker" and result.runtime_seconds > 0


def test_timeouts_and_memory_limits_are_enforced(backend) -> None:
    slow = backend.run(_request(["python", "-c", "import time; time.sleep(30)"], timeout_seconds=2))
    assert slow.timed_out and not slow.succeeded
    hog = backend.run(
        _request(
            ["python", "-c", "b = bytearray(512 * 1024 * 1024); print(len(b))"],
            resources=ResourceSpec(memory_mb=64),
            timeout_seconds=30,
        )
    )
    assert not hog.succeeded and (hog.oom_killed or hog.exit_code not in (0, None))


def test_policy_rejects_unsafe_requests(backend) -> None:
    with pytest.raises(ExecutionPolicyError):
        backend.validate(_request(["true"], network="allowlist", egress_allowlist=["example.com"]))
    with pytest.raises(ExecutionPolicyError):
        backend.validate(_request(["true"], env={"AWS_SECRET_ACCESS_KEY": "x"}))
    with pytest.raises(Exception, match="escapes the workspace"):
        backend.validate(_request(["true"], files={"../etc/passwd": b"x"}))


def test_output_size_is_bounded(backend) -> None:
    code = "import pathlib; p = pathlib.Path('/workspace/output'); p.mkdir(exist_ok=True); (p / 'big.bin').write_bytes(b'x' * 3_000_000)"
    result = backend.run(_request(["python", "-c", code], max_output_bytes=1_000_000, timeout_seconds=60))
    assert result.outputs_truncated and sum(len(v) for v in result.outputs.values()) <= 1_000_000
