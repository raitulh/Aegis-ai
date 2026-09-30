"""Content-addressed code snapshots, execution environments and seed delivery for experiment runs.

* **Code snapshots** — inline files are validated (safe relative paths, no collisions after normalisation),
  packed into a *deterministic* ``tar.gz`` (sorted members, zero mtimes/owners, fixed modes, gzip mtime 0) and
  stored once per project under ``object_key(org, project, "code", <content hash>)``. The content hash covers the
  file digests, the entrypoint, the language and the git provenance, so identical code is stored once and every
  experiment version references exactly the bytes it ran. The execution fabric re-verifies the manifest when it
  extracts the snapshot into the sandbox.
* **Environments** — image, digest, pinned dependencies, the resulting lock text (+ hash) and the environment
  variable policy are content-addressed per organization (append-only rows).
* **Seeds** — every run receives its seed in ``/workspace/input/params.json`` (``"seed"``) and ``PYTHONHASHSEED``;
  the spec's ``seed_env_var`` and ``NAME=VALUE`` deterministic flags are injected when the sandbox permits the
  variable name (``AEGIS_*`` names are reserved for the platform and are delivered through params.json instead).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import posixpath
import tarfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.errors import ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.execution.archive import UnsafeArchiveMember, normalize_member_path
from aegis_api.lab.models import CodeSnapshot, ExecutionEnvironment, Project
from aegis_api.lab.storage import get_storage
from aegis_api.lab.storage.keys import object_key
from aegis_api.lab.storage.tx import delete_on_rollback
from aegis_api.lab.usage.recorder import record_storage_usage
from engines.lab.design_validator import unsafe_relative_path
from engines.lab.experiment_spec import ExperimentSpec, GitRef, canonical_json
from engines.lab.sandbox import CODE_DIR, PARAMS_PATH, WORKDIR, validate_env

log = structlog.get_logger("aegis.lab.experiments.code")

CODE_CONTENT_TYPE = "application/gzip"
MAX_SNAPSHOT_READ_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_FILES = 5000
FILE_MODE = 0o644
PARAMS_FILE = f"{WORKDIR}/{PARAMS_PATH}"
SEED_PARAMETER = "seed"
HASH_SEED_VAR = "PYTHONHASHSEED"
LANGUAGES = frozenset({"python"})


class CodeSnapshotError(ValidationFailed):
    """The code cannot be snapshotted (unsafe or colliding paths)."""

    code = "invalid_code"


# =============================================================================================
# Code bundles
# =============================================================================================
@dataclass(frozen=True)
class CodeBundle:
    files: dict[str, bytes]
    manifest: dict[str, dict[str, Any]]
    content_hash: str
    archive: bytes
    problems: tuple[str, ...] = ()

    @property
    def total_bytes(self) -> int:
        return sum(len(v) for v in self.files.values())


def normalise_files(files: Mapping[str, str]) -> tuple[dict[str, bytes], list[str]]:
    """Validate inline file paths → (normalised path → UTF-8 bytes, problems). Unsafe or colliding paths are
    reported, never silently rewritten into something else."""
    out: dict[str, bytes] = {}
    problems: list[str] = []
    for raw_path in sorted(files):
        reason = unsafe_relative_path(raw_path)
        if reason:
            problems.append(f"file {raw_path!r} {reason}")
            continue
        try:
            member = normalize_member_path(raw_path)
        except UnsafeArchiveMember as exc:
            problems.append(f"file {raw_path!r}: {exc.reason}")
            continue
        if member is None:
            problems.append(f"file {raw_path!r} has an empty path")
            continue
        path = member.as_posix()
        if path in out:
            problems.append(f"file {raw_path!r} collides with another file after normalisation ({path!r})")
            continue
        out[path] = files[raw_path].encode("utf-8")
    return out, problems


def build_archive(files: Mapping[str, bytes]) -> bytes:
    """Deterministic ``tar.gz`` of ``files`` (identical input → identical bytes)."""
    raw = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for path in sorted(files):
            data = files[path]
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mtime = 0
            info.mode = FILE_MODE
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def snapshot_hash(manifest: Mapping[str, Mapping[str, Any]], entrypoint: str, language: str, git: GitRef | None) -> str:
    doc = {
        "files": {path: meta["sha256"] for path, meta in sorted(manifest.items())},
        "entrypoint": entrypoint,
        "language": language,
        "git": git.model_dump() if git is not None else None,
    }
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()


def bundle_code(
    files: Mapping[str, str], *, entrypoint: str, language: str = "python", git: GitRef | None = None
) -> CodeBundle:
    normalised, problems = normalise_files(files)
    manifest = {
        path: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)} for path, data in normalised.items()
    }
    archive = build_archive(normalised) if not problems else b""
    return CodeBundle(
        files=normalised,
        manifest=manifest,
        content_hash=snapshot_hash(manifest, entrypoint, language, git),
        archive=archive,
        problems=tuple(problems),
    )


def ensure_code_snapshot(
    db: Session,
    actor: Actor,
    project: Project,
    *,
    files: Mapping[str, str],
    entrypoint: str,
    language: str = "python",
    git: GitRef | None = None,
    source: str = "inline",
    agent_run_id: uuid.UUID | None = None,
) -> CodeSnapshot:
    """Return the project's snapshot of exactly these files, storing it first when it is new.

    Serialised per (project, hash) with a transaction-scoped advisory lock: a concurrent creator waits and then
    reuses the committed row, and an object written by a transaction that rolls back is deleted again.
    """
    if language not in LANGUAGES:
        raise CodeSnapshotError(f"Unsupported code language {language!r}")
    if not files:
        raise CodeSnapshotError("A code snapshot needs at least one file")
    bundle = bundle_code(files, entrypoint=entrypoint, language=language, git=git)
    if bundle.problems:
        raise CodeSnapshotError("The code cannot be snapshotted", details={"problems": list(bundle.problems)})
    advisory_xact_lock(db, f"code-snapshot:{project.id}:{bundle.content_hash}")
    existing = db.scalar(
        select(CodeSnapshot).where(
            CodeSnapshot.project_id == project.id, CodeSnapshot.content_hash == bundle.content_hash
        )
    )
    if existing is not None:
        return existing
    key = object_key(project.organization_id, project.id, "code", bundle.content_hash)
    storage = get_storage()
    stored = storage.put_bytes(key, bundle.archive, CODE_CONTENT_TYPE)
    delete_on_rollback(db, key, storage)
    if stored.sha256 != hashlib.sha256(bundle.archive).hexdigest():
        raise CodeSnapshotError("The stored code archive does not match the computed checksum")
    snapshot = CodeSnapshot(
        organization_id=project.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        source=source if git is None else "git",
        git_repo=git.repo if git is not None else None,
        git_commit=git.commit if git is not None else None,
        entrypoint=entrypoint,
        language=language,
        files_manifest=bundle.manifest,
        storage_key=key,
        content_hash=bundle.content_hash,
        created_by_id=actor.user_id,
        created_by_agent_run_id=agent_run_id or actor.agent_run_id,
    )
    db.add(snapshot)
    db.flush()
    record_storage_usage(
        db,
        organization_id=project.organization_id,
        bytes_delta=len(bundle.archive),
        reason="code_snapshot",
        project_id=project.id,
    )
    return snapshot


def read_snapshot_files(snapshot: CodeSnapshot, *, max_bytes: int = MAX_SNAPSHOT_READ_BYTES) -> dict[str, bytes]:
    """The snapshot's files (bounded; verified against the stored manifest)."""
    if not snapshot.storage_key:
        return {}
    data = get_storage().get_bytes(snapshot.storage_key, max_bytes=max_bytes)
    out: dict[str, bytes] = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for member in tar:
            if member.isdir():
                continue
            if not member.isfile():
                raise CodeSnapshotError(f"Code snapshot {snapshot.id} contains a non-regular member")
            try:
                member_path = normalize_member_path(member.name)
            except UnsafeArchiveMember as exc:
                raise CodeSnapshotError(f"Code snapshot {snapshot.id} contains an unsafe path: {exc.reason}") from exc
            if member_path is None:
                continue
            if len(out) >= MAX_SNAPSHOT_FILES:
                raise CodeSnapshotError(f"Code snapshot {snapshot.id} has too many files")
            total += member.size
            if total > max_bytes:
                raise CodeSnapshotError(f"Code snapshot {snapshot.id} exceeds {max_bytes} bytes")
            fh = tar.extractfile(member)
            content = fh.read() if fh is not None else b""
            out[member_path.as_posix()] = content
    manifest = snapshot.files_manifest or {}
    if manifest:
        for path, meta in manifest.items():
            sha = meta.get("sha256") if isinstance(meta, dict) else None
            if path not in out or (sha and hashlib.sha256(out[path]).hexdigest() != sha):
                raise CodeSnapshotError(f"Code snapshot {snapshot.id} failed its integrity check at {path!r}")
        if set(out) - set(manifest):
            raise CodeSnapshotError(f"Code snapshot {snapshot.id} contains unexpected files")
    return out


# =============================================================================================
# Seeds and environment variables
# =============================================================================================
@dataclass(frozen=True)
class SeedDelivery:
    """How a run's seed (and deterministic flags) reach the experiment code."""

    seed_env_var: str
    seed_env_var_injected: bool
    env_flags: dict[str, str] = field(default_factory=dict)
    ignored_flags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def allowed_env(self) -> list[str]:
        names = {HASH_SEED_VAR, *self.env_flags}
        if self.seed_env_var_injected:
            names.add(self.seed_env_var)
        return sorted(names)

    def env_for(self, seed: int) -> dict[str, str]:
        env = dict(self.env_flags)
        env[HASH_SEED_VAR] = str(seed)
        if self.seed_env_var_injected:
            env[self.seed_env_var] = str(seed)
        return env

    def as_dict(self) -> dict[str, Any]:
        return {
            "params_file": PARAMS_FILE,
            "params_key": SEED_PARAMETER,
            "hash_seed_env_var": HASH_SEED_VAR,
            "seed_env_var": self.seed_env_var,
            "seed_env_var_injected": self.seed_env_var_injected,
            "env_flags": dict(self.env_flags),
            "ignored_flags": list(self.ignored_flags),
            "notes": list(self.notes),
        }


def seed_delivery(spec: ExperimentSpec) -> SeedDelivery:
    name = spec.reproducibility.seed_env_var
    notes: list[str] = []
    injected = not validate_env({name: "0"})
    if not injected:
        notes.append(
            f"{name} is reserved by the sandbox; the seed is delivered as '{SEED_PARAMETER}' in {PARAMS_FILE} "
            f"and as {HASH_SEED_VAR}"
        )
    flags: dict[str, str] = {}
    ignored: list[str] = []
    for flag in spec.reproducibility.deterministic_flags:
        var, sep, value = flag.partition("=")
        var = var.strip()
        if sep and var and not validate_env({var: value}) and var not in (HASH_SEED_VAR, name):
            flags[var] = value
        else:
            ignored.append(flag)
    if ignored:
        notes.append(
            "deterministic flags that are not NAME=VALUE environment assignments allowed in the sandbox are recorded "
            f"but not applied by the platform: {', '.join(ignored)[:500]}"
        )
    return SeedDelivery(
        seed_env_var=name,
        seed_env_var_injected=injected,
        env_flags=flags,
        ignored_flags=tuple(ignored),
        notes=tuple(notes),
    )


def effective_command(spec: ExperimentSpec) -> list[str]:
    """``spec.command`` or ``python /workspace/code/<entrypoint>`` (code is mounted at ``/workspace/code``)."""
    if spec.command:
        return list(spec.command)
    entry = spec.code.entrypoint
    if entry and not unsafe_relative_path(entry):
        return ["python", posixpath.join(CODE_DIR, posixpath.normpath(entry))]
    return []


# =============================================================================================
# Environments
# =============================================================================================
def lockfile_text(dependencies: list[str]) -> str | None:
    deps = [d.strip() for d in dependencies if d.strip()]
    return ("\n".join(deps) + "\n") if deps else None


def environment_hash(spec: ExperimentSpec, env_policy: Mapping[str, Any]) -> str:
    env = spec.environment
    doc = {
        "image": env.image,
        "image_digest": env.image_digest,
        "dependencies": [d.strip() for d in env.dependencies],
        "python_version": env.python_version,
        "env_policy": dict(env_policy),
    }
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()


def ensure_environment(db: Session, actor: Actor, spec: ExperimentSpec, delivery: SeedDelivery) -> ExecutionEnvironment:
    """The organization's content-addressed environment for this spec (created once, then reused)."""
    env_policy = {"allowed_vars": delivery.allowed_env, "host_passthrough": False}
    content_hash = environment_hash(spec, env_policy)
    advisory_xact_lock(db, f"execution-environment:{actor.organization_id}:{content_hash}")
    existing = db.scalar(
        select(ExecutionEnvironment).where(
            ExecutionEnvironment.organization_id == actor.organization_id,
            ExecutionEnvironment.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing
    env = spec.environment
    lock = lockfile_text(env.dependencies)
    row = ExecutionEnvironment(
        organization_id=actor.organization_id,
        name=env.image[:160],
        image=env.image,
        image_digest=env.image_digest,
        runtime=f"python-{env.python_version}" if env.python_version else "python",
        dependencies=[d.strip() for d in env.dependencies],
        lockfile=lock,
        lockfile_hash=hashlib.sha256(lock.encode("utf-8")).hexdigest() if lock else None,
        env_policy=env_policy,
        content_hash=content_hash,
    )
    db.add(row)
    db.flush()
    return row
