"""Kubernetes Job manifest: restricted security context, resources, GPU, deadline, transfer helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

from engines.lab.sandbox import (
    LABEL_JOB_ID,
    MAIN_WRAPPER,
    UPLOAD_SCRIPT,
    NetworkPolicy,
    ResourceRequest,
    SandboxSpec,
    build_k8s_job,
)

JOB_ID = "11111111-2222-3333-4444-555555555555"


def _spec(**kw: Any) -> SandboxSpec:
    base: dict[str, Any] = {
        "image": "python:3.12-slim",
        "command": ["python", "/workspace/code/main.py", "--seed", "1"],
        "env": {"SEED": "1"},
        "resources": ResourceRequest(cpu=2, memory_mb=4096, disk_mb=2048),
        "timeout_seconds": 900,
        "labels": {LABEL_JOB_ID: JOB_ID},
    }
    base.update(kw)
    return SandboxSpec(**base)


def _job(spec: SandboxSpec | None = None, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "namespace": "aegis-sandbox",
        "job_name": f"aegis-job-{JOB_ID}",
        "fetch_url": "https://s3.example/inputs.tar?X-Amz-Signature=abc",
        "upload_url": "https://s3.example/outputs.tar?X-Amz-Signature=def",
        "fetcher_image": "curlimages/curl:8.10.1",
    }
    args.update(kw)
    return build_k8s_job(spec or _spec(), **args)


def _containers(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pod = job["spec"]["template"]["spec"]
    return {c["name"]: c for c in pod["initContainers"] + pod["containers"]}


def test_job_level_settings() -> None:
    job = _job()
    assert job["apiVersion"] == "batch/v1" and job["kind"] == "Job"
    assert job["metadata"]["namespace"] == "aegis-sandbox"
    spec = job["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 900
    assert spec["ttlSecondsAfterFinished"] == 600
    labels = spec["template"]["metadata"]["labels"]
    assert labels["aegis.io/job-id"] == JOB_ID and labels["aegis.io/network"] == "none"
    assert "aegis.io/egress" not in labels
    assert job["metadata"]["labels"] == labels


def test_pod_security_context_is_restricted() -> None:
    pod = _job()["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["hostNetwork"] is False and pod["hostPID"] is False and pod["hostIPC"] is False
    assert pod["restartPolicy"] == "Never"
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 65534,
        "runAsGroup": 65534,
        "fsGroup": 65534,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    for name, container in _containers(_job()).items():
        sc = container["securityContext"]
        assert sc["allowPrivilegeEscalation"] is False, name
        assert sc["readOnlyRootFilesystem"] is True, name
        assert sc["privileged"] is False, name
        assert sc["capabilities"] == {"drop": ["ALL"]}, name
        assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 65534, name
    assert "runtimeClassName" not in pod and "imagePullSecrets" not in pod


def test_main_container_resources_requests_equal_limits() -> None:
    main = _containers(_job())["main"]
    assert (
        main["resources"]["requests"]
        == main["resources"]["limits"]
        == {
            "cpu": "2000m",
            "memory": "4096Mi",
            "ephemeral-storage": "2048Mi",
        }
    )
    assert main["workingDir"] == "/workspace"
    env = {e["name"]: e["value"] for e in main["env"]}
    assert env["SEED"] == "1" and env["HOME"] == "/tmp"
    # the presigned transfer URLs never reach the job's own container
    assert "AEGIS_INPUT_URL" not in env and "AEGIS_UPLOAD_URL" not in env
    assert "X-Amz-Signature" not in repr(main)
    mounts = {m["mountPath"] for m in main["volumeMounts"]}
    assert mounts == {"/workspace", "/tmp"}


def test_volumes_are_size_limited_empty_dirs() -> None:
    volumes = {v["name"]: v for v in _job(tmpfs_mb=128)["spec"]["template"]["spec"]["volumes"]}
    assert volumes["workspace"] == {"name": "workspace", "emptyDir": {"sizeLimit": "2048Mi"}}
    assert volumes["tmp"] == {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "128Mi"}}
    assert all("hostPath" not in v for v in volumes.values())


def test_transfer_helpers() -> None:
    containers = _containers(_job())
    fetch, upload = containers["fetch-inputs"], containers["upload-outputs"]
    pod = _job()["spec"]["template"]["spec"]
    assert [c["name"] for c in pod["initContainers"]] == ["fetch-inputs", "upload-outputs"]
    assert "restartPolicy" not in fetch
    assert upload["restartPolicy"] == "Always"  # native sidecar
    assert fetch["image"] == upload["image"] == "curlimages/curl:8.10.1"
    assert {e["name"]: e["value"] for e in fetch["env"]}["AEGIS_INPUT_URL"].startswith("https://s3.example/inputs")
    assert {e["name"]: e["value"] for e in upload["env"]}["AEGIS_UPLOAD_URL"].startswith("https://s3.example/outputs")
    assert "/workspace/.done" in upload["command"][2]
    main = containers["main"]
    assert main["command"][:4] == ["/bin/sh", "-c", MAIN_WRAPPER, "aegis-sandbox"]
    assert main["command"][4:] == ["python", "/workspace/code/main.py", "--seed", "1"]


def test_gpu_scheduling() -> None:
    job = _job(_spec(resources=ResourceRequest(cpu=4, memory_mb=16384, gpu_type="nvidia-a100", gpu_count=2)))
    pod = job["spec"]["template"]["spec"]
    main = _containers(job)["main"]
    assert main["resources"]["limits"]["nvidia.com/gpu"] == "2" == main["resources"]["requests"]["nvidia.com/gpu"]
    assert pod["nodeSelector"] == {"aegis.io/gpu-type": "nvidia-a100"}
    assert pod["tolerations"] == [{"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}]
    cpu_only = _job()["spec"]["template"]["spec"]
    assert "nodeSelector" not in cpu_only and "tolerations" not in cpu_only


def test_allowlist_networking_runtime_class_and_pull_secret() -> None:
    spec = _spec(network=NetworkPolicy(mode="allowlist", hosts=["api.openalex.org"]))
    with pytest.raises(ValueError, match="proxy"):
        _job(spec)
    job = _job(spec, proxy="http://egress-proxy.aegis:3128", runtime_class="gvisor", image_pull_secret="regcred")
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels["aegis.io/egress"] == "allowlist" and labels["aegis.io/network"] == "allowlist"
    env = {e["name"]: e["value"] for e in _containers(job)["main"]["env"]}
    assert env["HTTPS_PROXY"] == "http://egress-proxy.aegis:3128"
    pod = job["spec"]["template"]["spec"]
    assert pod["runtimeClassName"] == "gvisor"
    assert pod["imagePullSecrets"] == [{"name": "regcred"}]


def test_invalid_names_and_root_are_refused() -> None:
    with pytest.raises(ValueError):
        _job(job_name="Not_A_DNS_Label")
    with pytest.raises(ValueError):
        _job(_spec(user="0:0"))


# -- the shell protocol itself (executed locally with substituted paths) ------------------------------
@pytest.mark.skipif(shutil.which("sh") is None or shutil.which("tar") is None, reason="needs sh and tar")
def test_main_wrapper_preserves_exit_code_and_signals_completion(tmp_path: Path) -> None:
    script = MAIN_WRAPPER.replace("/workspace", str(tmp_path))
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/sh", "-c", script, "aegis-sandbox", "/bin/sh", "-c", "echo hi; exit 3"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 3
    assert result.stdout == b"hi\n"
    assert (tmp_path / ".done").exists()


@pytest.mark.skipif(shutil.which("sh") is None or shutil.which("tar") is None, reason="needs sh and tar")
def test_upload_sidecar_waits_for_completion_then_uploads(tmp_path: Path) -> None:
    workspace, transfer, bin_dir = tmp_path / "workspace", tmp_path / "transfer", tmp_path / "bin"
    for d in (workspace / "output", transfer, bin_dir):
        d.mkdir(parents=True)
    (workspace / "output" / "result.csv").write_text("a,b\n1,2\n")
    uploaded = tmp_path / "uploaded.tar"
    fake_curl = bin_dir / "curl"
    # Records the upload: copies the file given with -T and the URL (last argument).
    fake_curl.write_text(
        "#!/bin/sh\n"
        'src=""; while [ $# -gt 1 ]; do if [ "$1" = "-T" ]; then src="$2"; fi; shift; done\n'
        f'cp "$src" "{uploaded}"; echo "$1" > "{tmp_path}/url"\n'
    )
    fake_curl.chmod(0o755)
    script = UPLOAD_SCRIPT.replace("/workspace", str(workspace)).replace("/transfer", str(transfer))
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "AEGIS_UPLOAD_URL": "https://u",
    }
    proc = subprocess.Popen(["/bin/sh", "-c", script], env=env)  # noqa: S603 - fixed argv
    try:
        time.sleep(1.5)
        assert not uploaded.exists()  # nothing is uploaded before the job signals completion
        (workspace / ".done").touch()
        deadline = time.monotonic() + 10
        while not (transfer / ".uploaded").exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert (transfer / ".uploaded").exists()
        assert (tmp_path / "url").read_text().strip() == "https://u"
        with tarfile.open(uploaded) as tar:
            names = tar.getnames()
        assert "output/result.csv" in names
        assert proc.poll() is None  # the sidecar idles instead of exiting (restartPolicy: Always)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
