"""Sandbox policy engine: validation, allowlists and the hardened Docker create body."""

from __future__ import annotations

import os

import pytest

from engines.lab.sandbox import (
    LABEL_EGRESS_HOSTS,
    LABEL_JOB_ID,
    LABEL_ORG,
    NetworkPolicy,
    ResourceRequest,
    SandboxLimits,
    SandboxSpec,
    audit_docker_body,
    build_docker_create_body,
    host_allowed,
    image_allowed,
    images_allowed,
    normalize_hosts,
    parse_image_reference,
    parse_user,
    validate_command,
    validate_env,
    validate_spec,
)

ALLOWED = ["python:3.12-slim", "ghcr.io/acme/lab-*"]


def _limits(**kw: object) -> SandboxLimits:
    base: dict[str, object] = {
        "max_cpu": 4.0,
        "max_memory_mb": 8192,
        "max_disk_mb": 10240,
        "max_timeout_seconds": 3600,
        "pids_limit": 256,
        "tmpfs_mb": 128,
    }
    base.update(kw)
    return SandboxLimits(**base)  # type: ignore[arg-type]


def _spec(**kw: object) -> SandboxSpec:
    base: dict[str, object] = {
        "image": "python:3.12-slim",
        "command": ["python", "-c", "print(1)"],
        "timeout_seconds": 60,
        "user": "65534:65534",
        "labels": {LABEL_JOB_ID: "00000000-0000-0000-0000-000000000001", LABEL_ORG: "org-1"},
    }
    base.update(kw)
    return SandboxSpec(**base)  # type: ignore[arg-type]


# -- docker create body ---------------------------------------------------------------------------
def test_docker_body_has_every_security_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_ONLY_SECRET_VALUE", "must-not-leak")
    spec = _spec(env={"SEED": "7"}, resources=ResourceRequest(cpu=1.5, memory_mb=512, pids=64))
    body = build_docker_create_body(spec, volume_name="aegis-job-x", pids_limit=64, tmpfs_mb=128, log_max_bytes=4096)
    hc = body["HostConfig"]
    assert body["User"] == "65534:65534"
    assert hc["ReadonlyRootfs"] is True
    assert hc["Privileged"] is False
    assert hc["CapDrop"] == ["ALL"] and hc["CapAdd"] == []
    assert "no-new-privileges:true" in hc["SecurityOpt"]
    assert hc["PidsLimit"] == 64
    assert hc["Memory"] == 512 * 1024 * 1024 == hc["MemorySwap"]
    assert hc["NanoCpus"] == 1_500_000_000
    ulimits = {u["Name"]: u for u in hc["Ulimits"]}
    assert ulimits["nofile"]["Hard"] == 1024 and ulimits["nproc"]["Hard"] == 64 and ulimits["core"]["Hard"] == 0
    tmp = hc["Tmpfs"]["/tmp"]
    assert "noexec" in tmp and "nosuid" in tmp and "size=128m" in tmp
    assert hc["Mounts"] == [
        {
            "Type": "volume",
            "Source": "aegis-job-x",
            "Target": "/workspace",
            "ReadOnly": False,
            "VolumeOptions": {"NoCopy": True},
        }
    ]
    assert hc["Binds"] == [] and hc["Devices"] == []
    assert hc["NetworkMode"] == "none" and body["NetworkDisabled"] is True
    assert hc["IpcMode"] == "private" and hc["PidMode"] == "" and hc["UsernsMode"] == ""
    assert hc["RestartPolicy"] == {"Name": "no"} and hc["AutoRemove"] is False
    assert hc["LogConfig"] == {"Type": "json-file", "Config": {"max-size": "4096", "max-file": "1"}}
    assert body["Tty"] is False and body["OpenStdin"] is False
    # explicit environment only: nothing inherited from the host process
    env = dict(item.split("=", 1) for item in body["Env"])
    assert env["SEED"] == "7" and env["HOME"] == "/tmp"
    assert set(env) == {
        "SEED",
        "HOME",
        "TMPDIR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "MPLCONFIGDIR",
        "AEGIS_WORKSPACE",
        "AEGIS_INPUT_DIR",
        "AEGIS_OUTPUT_DIR",
        "AEGIS_CODE_DIR",
    }
    assert os.environ["HOST_ONLY_SECRET_VALUE"] == "must-not-leak"
    assert all("must-not-leak" not in item for item in body["Env"])
    assert body["Labels"][LABEL_JOB_ID] == "00000000-0000-0000-0000-000000000001"
    assert body["Labels"][LABEL_ORG] == "org-1"
    assert body["Entrypoint"] == ["python"] and body["Cmd"] == ["-c", "print(1)"]
    assert "docker.sock" not in repr(body)
    assert audit_docker_body(body) == []


def test_docker_body_pins_image_and_requests_gpus() -> None:
    spec = _spec(resources=ResourceRequest(gpu_type="nvidia-a100", gpu_count=2))
    body = build_docker_create_body(spec, volume_name="v", image_ref="sha256:" + "a" * 64)
    assert body["Image"] == "sha256:" + "a" * 64
    assert body["HostConfig"]["DeviceRequests"] == [{"Driver": "nvidia", "Count": 2, "Capabilities": [["gpu"]]}]


def test_docker_body_allowlist_uses_egress_network_and_proxy() -> None:
    spec = _spec(network=NetworkPolicy(mode="allowlist", hosts=["API.OpenAlex.org."]))
    with pytest.raises(ValueError, match="egress network"):
        build_docker_create_body(spec, volume_name="v")
    body = build_docker_create_body(spec, volume_name="v", egress_network="aegis-egress", proxy="http://proxy:3128")
    env = dict(item.split("=", 1) for item in body["Env"])
    assert body["HostConfig"]["NetworkMode"] == "aegis-egress" and body["NetworkDisabled"] is False
    assert env["HTTPS_PROXY"] == env["https_proxy"] == "http://proxy:3128"
    assert body["Labels"][LABEL_EGRESS_HOSTS] == "api.openalex.org"
    assert audit_docker_body(body, allowed_networks=["aegis-egress"]) == []
    assert audit_docker_body(body) != []  # an unapproved network is flagged


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda b: b["HostConfig"].update(Privileged=True), "privileged"),
        (lambda b: b["HostConfig"].update(Binds=["/var/run/docker.sock:/var/run/docker.sock"]), "bind"),
        (
            lambda b: b["HostConfig"]["Mounts"].append(
                {"Type": "bind", "Source": "/var/run/docker.sock", "Target": "/var/run/docker.sock"}
            ),
            "mount",
        ),
        (lambda b: b["HostConfig"].update(NetworkMode="host"), "network"),
        (lambda b: b["HostConfig"].update(CapAdd=["SYS_ADMIN"]), "capabilities"),
        (lambda b: b["HostConfig"].update(ReadonlyRootfs=False), "read-only"),
        (lambda b: b["HostConfig"].update(PidMode="host"), "PidMode"),
        (lambda b: b["HostConfig"].update(SecurityOpt=["seccomp=unconfined", "no-new-privileges:true"]), "unconfined"),
        (lambda b: b["HostConfig"].update(MemorySwap=-1), "swap"),
        (lambda b: b.update(User="0:0"), "non-root"),
        (lambda b: b["HostConfig"].update(Devices=[{"PathOnHost": "/dev/kvm"}]), "devices"),
    ],
)
def test_audit_flags_weakened_bodies(mutate: object, fragment: str) -> None:
    body = build_docker_create_body(_spec(), volume_name="v")
    mutate(body)  # type: ignore[operator]
    problems = audit_docker_body(body)
    assert problems and any(fragment.lower() in p.lower() for p in problems), problems


def test_builder_refuses_root_user() -> None:
    with pytest.raises(ValueError, match="root"):
        build_docker_create_body(_spec(user="0:0"), volume_name="v")
    with pytest.raises(ValueError):
        parse_user("root")
    assert parse_user("1000") == (1000, 1000)


# -- environment -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONSTARTUP",
        "PYTHONPATH",
        "BASH_ENV",
        "ENV",
        "MY_SECRET",
        "GITHUB_TOKEN",
        "DB_PASSWORD",
        "DB_PASSWD",
        "OPENAI_API_KEY",
        "APIKEY",
        "SERVICE_CREDENTIALS",
        "SSH_PRIVATE_KEY",
        "AWS_REGION",
        "GCP_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_TENANT",
        "HOME",
        "PATH",
        "HTTP_PROXY",
        "AEGIS_WORKSPACE",
        "lowercase",
        "1STARTS_WITH_DIGIT",
        "HAS-DASH",
        "A" * 65,
    ],
)
def test_env_rejections(name: str) -> None:
    assert validate_env({name: "x"}), name


def test_env_accepts_plain_names_and_bounds_values() -> None:
    assert validate_env({"SEED": "1", "MODEL_SIZE": "small", "_X": ""}) == []
    assert validate_env({"SEED": "x" * 5000})
    assert validate_env({"SEED": "a\x00b"})
    assert validate_env({f"V{i}": "1" for i in range(65)})


def test_command_validation() -> None:
    assert validate_command([]) == ["command: must not be empty"]
    assert validate_command(["python", "a\x00b"])
    assert validate_command(["  "])
    assert validate_command(["x"] * 257)
    assert validate_command(["python", "-c", "print(1)"]) == []


# -- images ---------------------------------------------------------------------------------------
def test_image_allowlist_exact_and_prefix() -> None:
    assert image_allowed("python:3.12-slim", ALLOWED)
    assert not image_allowed("python:3.12", ALLOWED)
    assert not image_allowed("python:3.12-slim-evil", ALLOWED)
    assert image_allowed("ghcr.io/acme/lab-torch:2.4", ALLOWED)
    assert not image_allowed("ghcr.io/acme/other:1", ALLOWED)
    assert not image_allowed("python:3.12-slim;rm -rf /", ALLOWED)
    assert not image_allowed("Python:3.12-slim", ["Python:3.12-slim"])  # upper-case repositories are invalid
    assert not image_allowed("python:3.12-slim", ["*"])  # a bare '*' never matches (no allow-all)
    assert images_allowed("python:3.12-slim", ALLOWED, None)
    assert not images_allowed("python:3.12-slim", ALLOWED, ["ghcr.io/acme/*"])
    assert images_allowed("ghcr.io/acme/lab-x:1", ALLOWED, ["ghcr.io/acme/*"])


def test_parse_image_reference() -> None:
    digest = "sha256:" + "b" * 64
    ref = parse_image_reference(f"registry.example.com:5000/team/img:1.2@{digest}")
    assert ref.repository == "registry.example.com:5000/team/img" and ref.tag == "1.2" and ref.digest == digest
    assert ref.pull_tag == digest
    assert parse_image_reference("python").pull_tag == "latest"
    for bad in ("", "../etc", "python:", "a b", "python@sha256:xyz", "x" * 300):
        with pytest.raises(ValueError):
            parse_image_reference(bad)


# -- network ----------------------------------------------------------------------------------------
def test_hosts_normalization_and_matching() -> None:
    assert normalize_hosts(["Api.OpenAlex.org.", "api.openalex.org", ".Wikipedia.org"]) == [
        "api.openalex.org",
        ".wikipedia.org",
    ]
    for bad in ("1.1.1.1", "*.example.com", "https://x.org", "localhost", "a..b", "x.org:443", "user@x.org"):
        with pytest.raises(ValueError):
            normalize_hosts([bad])
    allow = ["api.openalex.org", ".wikipedia.org"]
    assert host_allowed("api.openalex.org", allow)
    assert host_allowed("en.wikipedia.org", allow) and host_allowed("wikipedia.org", allow)
    assert host_allowed(".wikipedia.org", allow)
    assert not host_allowed("evil-wikipedia.org", allow)
    assert not host_allowed("openalex.org", allow)
    assert not host_allowed(".openalex.org", allow)


def test_allowlist_mode_rejected_without_egress_configuration() -> None:
    spec = _spec(network=NetworkPolicy(mode="allowlist", hosts=["api.openalex.org"]))
    problems = validate_spec(spec, _limits(egress_available=False, egress_allowlist=("api.openalex.org",)), ALLOWED)
    assert any("egress proxy" in p for p in problems)
    ok = validate_spec(spec, _limits(egress_available=True, egress_allowlist=("api.openalex.org",)), ALLOWED)
    assert ok == []
    outside = _spec(network=NetworkPolicy(mode="allowlist", hosts=["evil.example.com"]))
    problems = validate_spec(outside, _limits(egress_available=True, egress_allowlist=("api.openalex.org",)), ALLOWED)
    assert any("not in the organization egress allowlist" in p for p in problems)
    none_with_hosts = _spec(network=NetworkPolicy(mode="none", hosts=["api.openalex.org"]))
    assert validate_spec(none_with_hosts, _limits(), ALLOWED)


# -- resources ---------------------------------------------------------------------------------------
def test_gpu_gating() -> None:
    gpu = _spec(resources=ResourceRequest(gpu_type="nvidia-a100", gpu_count=1))
    assert any("GPU execution is not enabled" in p for p in validate_spec(gpu, _limits(gpu_allowed=False), ALLOWED))
    assert validate_spec(gpu, _limits(gpu_allowed=True), ALLOWED) == []
    many = _spec(resources=ResourceRequest(gpu_type="nvidia-a100", gpu_count=16))
    assert any("gpu_count" in p for p in validate_spec(many, _limits(gpu_allowed=True, max_gpu_count=8), ALLOWED))
    type_only = _spec(resources=ResourceRequest(gpu_type="nvidia-a100", gpu_count=0))
    assert validate_spec(type_only, _limits(gpu_allowed=True), ALLOWED)


def test_resource_and_timeout_caps() -> None:
    big = _spec(resources=ResourceRequest(cpu=8, memory_mb=99999, disk_mb=99999, pids=100000), timeout_seconds=7200)
    problems = validate_spec(big, _limits(), ALLOWED)
    for fragment in ("resources.cpu", "resources.memory_mb", "resources.disk_mb", "resources.pids", "timeout_seconds"):
        assert any(p.startswith(fragment) for p in problems), (fragment, problems)
    tiny = _spec(resources=ResourceRequest(memory_mb=4))
    assert any("at least" in p for p in validate_spec(tiny, _limits(), ALLOWED))
    assert any(p.startswith("image") for p in validate_spec(_spec(image="ubuntu:24.04"), _limits(), ALLOWED))
    assert any(p.startswith("user") for p in validate_spec(_spec(user="0"), _limits(), ALLOWED))
    assert validate_spec(_spec(), _limits(), ALLOWED) == []
