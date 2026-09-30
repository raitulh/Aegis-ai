"""Sandbox policy for sandboxed compute jobs (pure: no IO, no framework imports).

Everything that decides *how* generated code is confined lives here so it can be unit-tested and audited in
isolation: validation of a job specification against deployment and organization limits, the image and egress
allowlists, environment-variable hygiene, and the builders that turn a validated :class:`SandboxSpec` into a
Docker Engine API ``/containers/create`` body or a Kubernetes ``batch/v1`` Job manifest.

Security posture (both backends):

* non-root user, read-only root filesystem, every Linux capability dropped, ``no-new-privileges``;
* no network by default (``NetworkMode: none`` / default-deny NetworkPolicy); allowlisted egress only through
  an operator-managed proxy network;
* bounded CPU, memory (no swap), PIDs, open files, ``/tmp`` size, log size and wall-clock time;
* an isolated per-job ``/workspace`` (never a host bind mount, never the container runtime socket);
* explicit environment only — nothing is inherited from the host, and credential-like names are refused.

The builders never weaken these properties based on caller input; :func:`audit_docker_body` re-checks a create
body before it is sent (defence in depth against a future refactor of the builder).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------------------------
# Filesystem layout inside the sandbox
# ---------------------------------------------------------------------------------------------
WORKDIR = "/workspace"
INPUT_DIR = "/workspace/input"
CODE_DIR = "/workspace/code"
OUTPUT_DIR = "/workspace/output"
PARAMS_PATH = "input/params.json"
METRICS_FILE = "metrics.json"
DONE_MARKER = "/workspace/.done"
TRANSFER_DIR = "/transfer"
SANDBOX_TMP = "/tmp"  # noqa: S108 - the sandbox's private tmpfs, not a host temporary directory

# Docker labels / Kubernetes labels
LABEL_MANAGED = "aegis.managed"
LABEL_JOB_ID = "aegis.job_id"
LABEL_ORG = "aegis.org"
LABEL_PROJECT = "aegis.project"
LABEL_NETWORK = "aegis.network"
LABEL_EGRESS_HOSTS = "aegis.egress_hosts"
K8S_LABEL_JOB_ID = "aegis.io/job-id"
K8S_LABEL_ORG = "aegis.io/org"
K8S_LABEL_PROJECT = "aegis.io/project"
K8S_LABEL_NETWORK = "aegis.io/network"
K8S_LABEL_EGRESS = "aegis.io/egress"
K8S_LABEL_COMPONENT = "app.kubernetes.io/component"
K8S_LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"
K8S_GPU_NODE_LABEL = "aegis.io/gpu-type"
K8S_GPU_RESOURCE = "nvidia.com/gpu"

NetworkMode = Literal["none", "allowlist"]

# ---------------------------------------------------------------------------------------------
# Environment variable hygiene
# ---------------------------------------------------------------------------------------------
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
# Loader / interpreter hooks that could subvert the process before user code runs.
FORBIDDEN_ENV_NAMES = frozenset({"PYTHONSTARTUP", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "BASH_ENV", "ENV"})
FORBIDDEN_ENV_PREFIXES = ("LD_", "DYLD_")
# Credential-looking names are refused: secrets never travel as plain job environment.
SENSITIVE_ENV_RE = re.compile(
    r"(?i)(secret|token|password|passwd|api_?key|credential|private_key|aws_|gcp_|google_|azure_)"
)
# Set by the platform only.
RESERVED_ENV_NAMES = frozenset({"HOME", "PATH", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "TMPDIR"})
RESERVED_ENV_PREFIXES = ("AEGIS_",)
MAX_ENV_VARS = 64
MAX_ENV_VALUE_CHARS = 4096

MAX_COMMAND_ARGS = 256
MAX_ARG_CHARS = 8192
MAX_COMMAND_CHARS = 65536

MAX_EGRESS_HOSTS = 50
MAX_IMAGE_REF_CHARS = 255
_HOST_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_IMAGE_REF_RE = re.compile(
    r"^(?P<name>[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?::[0-9]{1,5})?"
    r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
    r"(?:@(?P<digest>sha256:[a-f0-9]{64}))?$"
)
_USER_RE = re.compile(r"^(?P<uid>[0-9]{1,10})(?::(?P<gid>[0-9]{1,10}))?$")

# Default per-process ulimits inside the sandbox.
DEFAULT_NOFILE = 1024
DEFAULT_SHM_MB = 64
K8S_TTL_AFTER_FINISHED_SECONDS = 600
K8S_TERMINATION_GRACE_SECONDS = 60


# ---------------------------------------------------------------------------------------------
# Specification types
# ---------------------------------------------------------------------------------------------
class ResourceRequest(BaseModel):
    """Requested resources. Requests equal limits (no burst) on every backend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cpu: float = Field(default=1.0, gt=0, le=1024, description="vCPUs (fractional allowed)")
    memory_mb: int = Field(default=1024, ge=1, le=10_000_000, description="Memory limit in MiB (swap disabled)")
    disk_mb: int = Field(default=1024, ge=1, le=10_000_000, description="Workspace disk in MiB")
    gpu_type: str | None = Field(default=None, max_length=48, pattern=r"^[a-z0-9][a-z0-9.-]{0,47}$")
    gpu_count: int = Field(default=0, ge=0, le=64)
    pids: int | None = Field(
        default=None, ge=1, le=1_000_000, description="Max processes/threads (default: deployment cap)"
    )


class NetworkPolicy(BaseModel):
    """``none`` (default: no network at all) or ``allowlist`` (egress proxy restricted to ``hosts``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: NetworkMode = "none"
    hosts: list[str] = Field(default_factory=list, max_length=MAX_EGRESS_HOSTS)


class SandboxSpec(BaseModel):
    """A fully-resolved sandbox run: what to run and inside which envelope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    image: str
    command: list[str]
    env: dict[str, str] = Field(default_factory=dict)
    resources: ResourceRequest = ResourceRequest()
    timeout_seconds: int
    network: NetworkPolicy = NetworkPolicy()
    user: str = "65534:65534"
    workdir: str = WORKDIR
    labels: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class SandboxLimits:
    """Caps a specification is validated against (deployment settings ∩ organization execution policy)."""

    max_cpu: float
    max_memory_mb: int
    max_disk_mb: int
    max_timeout_seconds: int
    pids_limit: int
    tmpfs_mb: int = 256
    max_log_bytes: int = 10 * 1024 * 1024
    min_memory_mb: int = 16
    gpu_allowed: bool = False
    max_gpu_count: int = 8
    egress_available: bool = False
    egress_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageReference:
    repository: str
    tag: str | None
    digest: str | None

    @property
    def pull_tag(self) -> str:
        """The ``tag`` query parameter for ``POST /images/create`` (digest wins over tag)."""
        return self.digest or self.tag or "latest"

    @property
    def canonical(self) -> str:
        ref = self.repository
        if self.tag:
            ref += f":{self.tag}"
        if self.digest:
            ref += f"@{self.digest}"
        return ref


@dataclass(frozen=True)
class EgressConfig:
    """Operator-managed egress: an internal network whose only route out is an allowlisting proxy."""

    network: str | None
    proxy_url: str


# ---------------------------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------------------------
def parse_image_reference(image: str) -> ImageReference:
    """Parse ``[registry[:port]/]name[:tag][@sha256:<hex>]``; raises ``ValueError`` for anything else."""
    if not image or len(image) > MAX_IMAGE_REF_CHARS:
        raise ValueError("image reference is empty or too long")
    match = _IMAGE_REF_RE.match(image)
    if match is None:
        raise ValueError(f"invalid image reference: {image!r}")
    return ImageReference(repository=match["name"], tag=match["tag"], digest=match["digest"])


def image_allowed(image: str, allowed: Iterable[str]) -> bool:
    """True when ``image`` matches an allowlist entry exactly, or a prefix entry ending with ``*``."""
    try:
        parse_image_reference(image)
    except ValueError:
        return False
    for entry in allowed:
        pattern = entry.strip()
        if not pattern:
            continue
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if prefix and image.startswith(prefix):
                return True
        elif image == pattern:
            return True
    return False


def images_allowed(image: str, deployment_allowlist: Sequence[str], org_allowlist: Sequence[str] | None) -> bool:
    """Deployment allowlist ∩ organization allowlist (when the organization configured one)."""
    if not image_allowed(image, deployment_allowlist):
        return False
    return org_allowlist is None or image_allowed(image, org_allowlist)


# ---------------------------------------------------------------------------------------------
# Hosts (egress allowlist)
# ---------------------------------------------------------------------------------------------
def _normalize_host(raw: str) -> str:
    host = raw.strip().lower().rstrip(".")
    if not host:
        raise ValueError("empty host")
    suffix = host.startswith(".")
    body = host[1:] if suffix else host
    if not body or "*" in body or "/" in body or ":" in body or "@" in body:
        raise ValueError(f"invalid host {raw!r}: use a hostname or a leading-dot domain suffix")
    try:
        ipaddress.ip_address(body)
    except ValueError:
        pass
    else:
        raise ValueError(f"invalid host {raw!r}: IP addresses are not allowed in egress allowlists")
    labels = body.split(".")
    if len(body) > 253 or len(labels) < 2 or not all(_HOST_LABEL_RE.match(label) for label in labels):
        raise ValueError(f"invalid host {raw!r}")
    if labels[-1].isdigit():
        raise ValueError(f"invalid host {raw!r}")
    return f".{body}" if suffix else body


def normalize_hosts(hosts: Iterable[str]) -> list[str]:
    """Lower-case, strip trailing dots, validate and de-duplicate (order preserved). Raises ``ValueError``."""
    out: list[str] = []
    for raw in hosts:
        host = _normalize_host(raw)
        if host not in out:
            out.append(host)
    return out


def host_allowed(host: str, allowlist: Iterable[str]) -> bool:
    """Exact match, or suffix match against a leading-dot entry (``.example.org`` covers ``a.example.org``).

    A requested suffix entry (``.example.org``) is only allowed by an equal or broader suffix entry.
    """
    for entry in allowlist:
        e = entry.strip().lower().rstrip(".")
        if not e:
            continue
        if host == e:
            return True
        if e.startswith("."):
            if host.startswith("."):
                if host.endswith(e):
                    return True
            elif host.endswith(e) or host == e[1:]:
                return True
    return False


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------
def parse_user(user: str) -> tuple[int, int]:
    """``"uid[:gid]"`` numeric only; root (uid or gid 0) is refused."""
    match = _USER_RE.match(user or "")
    if match is None:
        raise ValueError(f"sandbox user must be numeric 'uid:gid', got {user!r}")
    uid = int(match["uid"])
    gid = int(match["gid"]) if match["gid"] is not None else uid
    if uid == 0 or gid == 0:
        raise ValueError("sandbox user must not be root")
    return uid, gid


def validate_env(env: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    if len(env) > MAX_ENV_VARS:
        problems.append(f"env: at most {MAX_ENV_VARS} variables are allowed")
    for name, value in env.items():
        if not isinstance(name, str) or not ENV_NAME_RE.match(name):
            problems.append(f"env: invalid variable name {name!r} (must match {ENV_NAME_RE.pattern})")
            continue
        if name in FORBIDDEN_ENV_NAMES or name.startswith(FORBIDDEN_ENV_PREFIXES):
            problems.append(f"env: {name} is not allowed (loader/interpreter hook)")
        elif SENSITIVE_ENV_RE.search(name):
            problems.append(f"env: {name} looks like a credential; secrets cannot be passed as environment")
        elif name in RESERVED_ENV_NAMES or name.startswith(RESERVED_ENV_PREFIXES):
            problems.append(f"env: {name} is reserved for the platform")
        if not isinstance(value, str):
            problems.append(f"env: value of {name} must be a string")
        elif len(value) > MAX_ENV_VALUE_CHARS:
            problems.append(f"env: value of {name} exceeds {MAX_ENV_VALUE_CHARS} characters")
        elif "\x00" in value:
            problems.append(f"env: value of {name} contains a NUL byte")
    return problems


def validate_command(command: Sequence[str]) -> list[str]:
    problems: list[str] = []
    if not command:
        return ["command: must not be empty"]
    if len(command) > MAX_COMMAND_ARGS:
        problems.append(f"command: at most {MAX_COMMAND_ARGS} arguments are allowed")
    total = 0
    for i, arg in enumerate(command):
        if not isinstance(arg, str):
            problems.append(f"command[{i}]: must be a string")
            continue
        total += len(arg)
        if len(arg) > MAX_ARG_CHARS:
            problems.append(f"command[{i}]: exceeds {MAX_ARG_CHARS} characters")
        if "\x00" in arg:
            problems.append(f"command[{i}]: contains a NUL byte")
    if not command[0].strip():
        problems.append("command[0]: executable must not be blank")
    if total > MAX_COMMAND_CHARS:
        problems.append(f"command: total length exceeds {MAX_COMMAND_CHARS} characters")
    return problems


def effective_pids(spec: SandboxSpec, limits: SandboxLimits) -> int:
    return min(spec.resources.pids or limits.pids_limit, limits.pids_limit)


def validate_spec(
    spec: SandboxSpec,
    limits: SandboxLimits,
    allowed_images: Sequence[str],
    org_allowed_images: Sequence[str] | None = None,
) -> list[str]:
    """Every reason ``spec`` may not run under ``limits`` (empty list = valid)."""
    problems: list[str] = []
    try:
        parse_image_reference(spec.image)
    except ValueError as exc:
        problems.append(f"image: {exc}")
    else:
        if not images_allowed(spec.image, allowed_images, org_allowed_images):
            problems.append(f"image: {spec.image!r} is not in the allowed image list")
    problems += validate_command(spec.command)
    problems += validate_env(spec.env)
    try:
        parse_user(spec.user)
    except ValueError as exc:
        problems.append(f"user: {exc}")
    if spec.workdir != WORKDIR:
        problems.append(f"workdir: must be {WORKDIR}")

    r = spec.resources
    if r.cpu > limits.max_cpu:
        problems.append(f"resources.cpu: {r.cpu} exceeds the maximum of {limits.max_cpu}")
    if r.memory_mb > limits.max_memory_mb:
        problems.append(f"resources.memory_mb: {r.memory_mb} exceeds the maximum of {limits.max_memory_mb}")
    if r.memory_mb < limits.min_memory_mb:
        problems.append(f"resources.memory_mb: must be at least {limits.min_memory_mb}")
    if r.disk_mb > limits.max_disk_mb:
        problems.append(f"resources.disk_mb: {r.disk_mb} exceeds the maximum of {limits.max_disk_mb}")
    if r.pids is not None and r.pids > limits.pids_limit:
        problems.append(f"resources.pids: {r.pids} exceeds the maximum of {limits.pids_limit}")
    if r.gpu_count > 0 or r.gpu_type:
        if not limits.gpu_allowed:
            problems.append("resources.gpu: GPU execution is not enabled for this deployment/organization")
        elif r.gpu_count <= 0:
            problems.append("resources.gpu_count: must be positive when gpu_type is set")
        elif r.gpu_count > limits.max_gpu_count:
            problems.append(f"resources.gpu_count: {r.gpu_count} exceeds the maximum of {limits.max_gpu_count}")
    if spec.timeout_seconds < 1:
        problems.append("timeout_seconds: must be at least 1")
    elif spec.timeout_seconds > limits.max_timeout_seconds:
        problems.append(f"timeout_seconds: {spec.timeout_seconds} exceeds the maximum of {limits.max_timeout_seconds}")

    net = spec.network
    if net.mode == "none":
        if net.hosts:
            problems.append("network.hosts: must be empty when network.mode is 'none'")
    else:
        if not limits.egress_available:
            problems.append(
                "network.mode: 'allowlist' requires an egress proxy (EXECUTION_EGRESS_NETWORK and "
                "EXECUTION_EGRESS_PROXY_URL) which is not configured in this deployment; use mode 'none'"
            )
        if not net.hosts:
            problems.append("network.hosts: at least one host is required in allowlist mode")
        try:
            hosts = normalize_hosts(net.hosts)
        except ValueError as exc:
            problems.append(f"network.hosts: {exc}")
        else:
            denied = [h for h in hosts if not host_allowed(h, limits.egress_allowlist)]
            if denied:
                problems.append(f"network.hosts: not in the organization egress allowlist: {', '.join(sorted(denied))}")
    for key, value in spec.labels.items():
        if not re.match(r"^[a-z0-9][a-z0-9._/-]{0,62}$", key) or len(value) > 63:
            problems.append(f"labels: invalid label {key!r}")
    return problems


# ---------------------------------------------------------------------------------------------
# Docker Engine API
# ---------------------------------------------------------------------------------------------
def proxy_env(proxy_url: str) -> dict[str, str]:
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }


def base_env(spec: SandboxSpec) -> dict[str, str]:
    """The complete environment of the sandboxed process: platform defaults + the validated job env."""
    env = {
        "HOME": SANDBOX_TMP,
        "TMPDIR": SANDBOX_TMP,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "MPLCONFIGDIR": SANDBOX_TMP,
        "AEGIS_WORKSPACE": WORKDIR,
        "AEGIS_INPUT_DIR": INPUT_DIR,
        "AEGIS_OUTPUT_DIR": OUTPUT_DIR,
        "AEGIS_CODE_DIR": CODE_DIR,
    }
    env.update(spec.env)
    return env


def _mb(value: int) -> int:
    return int(value) * 1024 * 1024


def build_docker_create_body(
    spec: SandboxSpec,
    *,
    volume_name: str,
    egress_network: str | None = None,
    proxy: str | None = None,
    pids_limit: int | None = None,
    tmpfs_mb: int = 256,
    log_max_bytes: int = 10 * 1024 * 1024,
    image_ref: str | None = None,
) -> dict[str, Any]:
    """Docker Engine API ``POST /containers/create`` body for a hardened, isolated sandbox.

    ``image_ref`` lets the backend pin the exact image it inspected (``sha256:<image id>``); defaults to
    ``spec.image``. Allowlist networking requires both ``egress_network`` and ``proxy``.
    """
    uid, gid = parse_user(spec.user)
    if not volume_name or "/" in volume_name:
        raise ValueError("volume_name must be a plain named-volume name")
    env = base_env(spec)
    network_mode = "none"
    labels = dict(spec.labels)
    labels[LABEL_MANAGED] = "true"
    labels[LABEL_NETWORK] = spec.network.mode
    if spec.network.mode == "allowlist":
        if not egress_network or not proxy:
            raise ValueError("allowlist networking requires an egress network and proxy")
        network_mode = egress_network
        env.update(proxy_env(proxy))
        labels[LABEL_EGRESS_HOSTS] = ",".join(normalize_hosts(spec.network.hosts))[:4096]
    pids = pids_limit or spec.resources.pids or 256
    memory = _mb(spec.resources.memory_mb)
    host_config: dict[str, Any] = {
        "NetworkMode": network_mode,
        "ReadonlyRootfs": True,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "CapAdd": [],
        "SecurityOpt": ["no-new-privileges:true"],
        "PidsLimit": pids,
        "Memory": memory,
        "MemorySwap": memory,
        "MemoryReservation": 0,
        "OomKillDisable": False,
        "NanoCpus": round(spec.resources.cpu * 1_000_000_000),
        "Ulimits": [
            {"Name": "nofile", "Soft": DEFAULT_NOFILE, "Hard": DEFAULT_NOFILE},
            {"Name": "nproc", "Soft": pids, "Hard": pids},
            {"Name": "core", "Soft": 0, "Hard": 0},
        ],
        "Tmpfs": {SANDBOX_TMP: f"rw,noexec,nosuid,nodev,size={int(tmpfs_mb)}m"},
        "ShmSize": _mb(DEFAULT_SHM_MB),
        "Mounts": [
            {
                "Type": "volume",
                "Source": volume_name,
                "Target": WORKDIR,
                "ReadOnly": False,
                "VolumeOptions": {"NoCopy": True},
            }
        ],
        "Binds": [],
        "Devices": [],
        "IpcMode": "private",
        "PidMode": "",
        "UTSMode": "",
        "UsernsMode": "",
        "CgroupnsMode": "private",
        "PublishAllPorts": False,
        "PortBindings": {},
        "ExtraHosts": [],
        "AutoRemove": False,
        "RestartPolicy": {"Name": "no"},
        "LogConfig": {
            "Type": "json-file",
            "Config": {"max-size": str(max(int(log_max_bytes), 1024)), "max-file": "1"},
        },
    }
    if spec.resources.gpu_count > 0:
        host_config["DeviceRequests"] = [
            {"Driver": "nvidia", "Count": spec.resources.gpu_count, "Capabilities": [["gpu"]]}
        ]
    return {
        "Image": image_ref or spec.image,
        "Entrypoint": [spec.command[0]],
        "Cmd": list(spec.command[1:]),
        "User": f"{uid}:{gid}",
        "WorkingDir": WORKDIR,
        "Env": [f"{k}={v}" for k, v in env.items()],
        "Labels": labels,
        "NetworkDisabled": spec.network.mode == "none",
        "AttachStdin": False,
        "AttachStdout": False,
        "AttachStderr": False,
        "OpenStdin": False,
        "StdinOnce": False,
        "Tty": False,
        "StopTimeout": 5,
        "HostConfig": host_config,
    }


_DOCKER_SOCKET_MARKERS = ("docker.sock", "containerd.sock", "crio.sock", "/var/run", "/run/")


def audit_docker_body(body: Mapping[str, Any], *, allowed_networks: Iterable[str] = ()) -> list[str]:
    """Re-verify the security invariants of a container create body (empty list = hardened)."""
    problems: list[str] = []
    hc = body.get("HostConfig") or {}
    if hc.get("Privileged"):
        problems.append("privileged containers are forbidden")
    if hc.get("ReadonlyRootfs") is not True:
        problems.append("root filesystem must be read-only")
    if hc.get("CapDrop") != ["ALL"] or hc.get("CapAdd"):
        problems.append("all capabilities must be dropped and none added")
    if "no-new-privileges:true" not in (hc.get("SecurityOpt") or []):
        problems.append("no-new-privileges must be set")
    if any("unconfined" in str(opt) for opt in hc.get("SecurityOpt") or []):
        problems.append("unconfined security profiles are forbidden")
    if hc.get("Binds"):
        problems.append("host bind mounts are forbidden")
    if hc.get("Devices"):
        problems.append("host devices are forbidden")
    for mount in hc.get("Mounts") or []:
        if mount.get("Type") != "volume":
            problems.append(f"mount type {mount.get('Type')!r} is forbidden (volumes only)")
        source = str(mount.get("Source") or "")
        if "/" in source or any(marker in source for marker in _DOCKER_SOCKET_MARKERS):
            problems.append("mount source must be a named volume")
    network = hc.get("NetworkMode")
    if network in ("host", "bridge", "default") or str(network).startswith("container:"):
        problems.append(f"network mode {network!r} is forbidden")
    elif network != "none" and network not in set(allowed_networks):
        problems.append(f"network {network!r} is not an approved egress network")
    if network == "none" and body.get("NetworkDisabled") is not True:
        problems.append("networking must be disabled")
    for key in ("PidMode", "IpcMode", "UTSMode", "UsernsMode"):
        if str(hc.get(key) or "").startswith(("host", "container:")):
            problems.append(f"{key} must not share a host or container namespace")
    if not hc.get("PidsLimit") or int(hc.get("PidsLimit") or 0) <= 0:
        problems.append("a PIDs limit is required")
    if not hc.get("Memory") or hc.get("MemorySwap") != hc.get("Memory"):
        problems.append("a memory limit without swap is required")
    if not hc.get("NanoCpus"):
        problems.append("a CPU limit is required")
    user = str(body.get("User") or "")
    try:
        parse_user(user)
    except ValueError:
        problems.append("container must run as a non-root numeric user")
    if (hc.get("RestartPolicy") or {}).get("Name") not in (None, "", "no"):
        problems.append("restart policies are forbidden")
    return problems


# ---------------------------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------------------------
def _cpu_quantity(cpu: float) -> str:
    return f"{round(cpu * 1000)}m"


def _restricted_container_security(uid: int, gid: int) -> dict[str, Any]:
    return {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "privileged": False,
        "runAsNonRoot": True,
        "runAsUser": uid,
        "runAsGroup": gid,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }


FETCH_SCRIPT = (
    "set -eu\n"
    "mkdir -p /workspace/input /workspace/code /workspace/output\n"
    'if [ -n "${AEGIS_INPUT_URL:-}" ]; then\n'
    '  curl -fsS --retry 3 --max-filesize "$AEGIS_INPUT_MAX_BYTES" -o /transfer/inputs.tar "$AEGIS_INPUT_URL"\n'
    "  tar -xf /transfer/inputs.tar -C /workspace\n"
    "  rm -f /transfer/inputs.tar\n"
    "fi\n"
    "chmod -R a-w /workspace/input /workspace/code\n"
)

UPLOAD_SCRIPT = (
    "set -u\n"
    "upload() {\n"
    "  trap '' TERM\n"
    "  if [ ! -e /transfer/.uploaded ]; then\n"
    "    tar -cf /transfer/outputs.tar -C /workspace output || return 1\n"
    "    curl -fsS --retry 3 -X PUT -H 'Content-Type: application/x-tar' "
    '-T /transfer/outputs.tar "$AEGIS_UPLOAD_URL" || return 1\n'
    "    : > /transfer/.uploaded\n"
    "  fi\n"
    "}\n"
    "trap 'if [ -e /workspace/.done ]; then upload; fi; exit 0' TERM\n"
    "while [ ! -e /workspace/.done ]; do sleep 1; done\n"
    "upload || exit 1\n"
    "trap - TERM\n"
    "exec sleep 2147483647\n"
)

# Runs the job command, then signals the upload sidecar while preserving the command's exit code.
MAIN_WRAPPER = '"$@"; rc=$?; : > /workspace/.done; exit $rc'


def build_k8s_job(
    spec: SandboxSpec,
    *,
    namespace: str,
    job_name: str,
    fetch_url: str | None,
    upload_url: str,
    fetcher_image: str,
    runtime_class: str | None = None,
    image_pull_secret: str | None = None,
    tmpfs_mb: int = 256,
    proxy: str | None = None,
    input_max_bytes: int | None = None,
    extra_labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """A ``batch/v1`` Job running ``spec`` under the restricted Pod Security profile.

    Inputs are fetched by the ``fetch-inputs`` init container from a presigned GET URL; outputs are tarred
    and PUT to a presigned URL by the ``upload-outputs`` native sidecar once the main container signals
    completion. The main container never sees either URL (the transfer helpers reach object storage directly;
    the egress proxy only applies to the job itself). PID limits are enforced by the kubelet
    (``podPidsLimit``) — the Pod API has no per-pod field for them.
    """
    uid, gid = parse_user(spec.user)
    if not re.match(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$", job_name):
        raise ValueError("job_name must be a DNS-1123 label")
    network_mode = spec.network.mode
    labels: dict[str, str] = {
        K8S_LABEL_MANAGED_BY: "aegis-lab",
        K8S_LABEL_COMPONENT: "sandbox",
        K8S_LABEL_NETWORK: network_mode,
    }
    labels.update(extra_labels or {})
    job_id = spec.labels.get(LABEL_JOB_ID)
    if job_id:
        labels[K8S_LABEL_JOB_ID] = job_id
    env = base_env(spec)
    if network_mode == "allowlist":
        if not proxy:
            raise ValueError("allowlist networking requires an egress proxy")
        labels[K8S_LABEL_EGRESS] = "allowlist"
        env.update(proxy_env(proxy))
    r = spec.resources
    resources: dict[str, str] = {
        "cpu": _cpu_quantity(r.cpu),
        "memory": f"{r.memory_mb}Mi",
        "ephemeral-storage": f"{r.disk_mb}Mi",
    }
    if r.gpu_count > 0:
        resources[K8S_GPU_RESOURCE] = str(r.gpu_count)
    helper_resources = {
        "requests": {"cpu": "100m", "memory": "64Mi", "ephemeral-storage": "64Mi"},
        "limits": {"cpu": "500m", "memory": "128Mi", "ephemeral-storage": "64Mi"},
    }
    security = _restricted_container_security(uid, gid)
    fetch_env = [
        {"name": "AEGIS_INPUT_URL", "value": fetch_url or ""},
        {"name": "AEGIS_INPUT_MAX_BYTES", "value": str(input_max_bytes or _mb(r.disk_mb))},
        {"name": "HOME", "value": SANDBOX_TMP},
    ]
    upload_env = [{"name": "AEGIS_UPLOAD_URL", "value": upload_url}, {"name": "HOME", "value": SANDBOX_TMP}]
    workspace_mount = {"name": "workspace", "mountPath": WORKDIR}
    transfer_mount = {"name": "transfer", "mountPath": TRANSFER_DIR}
    helper_tmp_mount = {"name": "helper-tmp", "mountPath": SANDBOX_TMP}
    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "hostNetwork": False,
        "hostPID": False,
        "hostIPC": False,
        "shareProcessNamespace": False,
        "terminationGracePeriodSeconds": K8S_TERMINATION_GRACE_SECONDS,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": uid,
            "runAsGroup": gid,
            "fsGroup": gid,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "initContainers": [
            {
                "name": "fetch-inputs",
                "image": fetcher_image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["/bin/sh", "-c", FETCH_SCRIPT],
                "env": fetch_env,
                "securityContext": security,
                "resources": helper_resources,
                "volumeMounts": [workspace_mount, transfer_mount, helper_tmp_mount],
            },
            {
                "name": "upload-outputs",
                "image": fetcher_image,
                "imagePullPolicy": "IfNotPresent",
                "restartPolicy": "Always",
                "command": ["/bin/sh", "-c", UPLOAD_SCRIPT],
                "env": upload_env,
                "securityContext": security,
                "resources": helper_resources,
                "volumeMounts": [workspace_mount, transfer_mount, helper_tmp_mount],
            },
        ],
        "containers": [
            {
                "name": "main",
                "image": spec.image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["/bin/sh", "-c", MAIN_WRAPPER, "aegis-sandbox", *spec.command],
                "workingDir": WORKDIR,
                "env": [{"name": k, "value": v} for k, v in env.items()],
                "resources": {"requests": dict(resources), "limits": dict(resources)},
                "securityContext": security,
                "terminationMessagePolicy": "FallbackToLogsOnError",
                "volumeMounts": [workspace_mount, {"name": "tmp", "mountPath": SANDBOX_TMP}],
            }
        ],
        "volumes": [
            {"name": "workspace", "emptyDir": {"sizeLimit": f"{r.disk_mb}Mi"}},
            {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": f"{int(tmpfs_mb)}Mi"}},
            {"name": "transfer", "emptyDir": {"sizeLimit": f"{r.disk_mb}Mi"}},
            {"name": "helper-tmp", "emptyDir": {"sizeLimit": "64Mi"}},
        ],
    }
    if runtime_class:
        pod_spec["runtimeClassName"] = runtime_class
    if image_pull_secret:
        pod_spec["imagePullSecrets"] = [{"name": image_pull_secret}]
    if r.gpu_count > 0:
        if r.gpu_type:
            pod_spec["nodeSelector"] = {K8S_GPU_NODE_LABEL: r.gpu_type}
        pod_spec["tolerations"] = [{"key": K8S_GPU_RESOURCE, "operator": "Exists", "effect": "NoSchedule"}]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name, "namespace": namespace, "labels": dict(labels)},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": int(spec.timeout_seconds),
            "ttlSecondsAfterFinished": K8S_TTL_AFTER_FINISHED_SECONDS,
            "completions": 1,
            "parallelism": 1,
            "template": {"metadata": {"labels": dict(labels)}, "spec": pod_spec},
        },
    }
