"""Reproducibility packages and run lineage.

:func:`reproducibility_package` assembles everything needed to re-run an experiment version and to audit its
results: the immutable spec and its validation report, the code snapshot (manifest, content hash, git provenance,
storage key), the dataset versions (checksums, splits, licences, mount paths), the environment (image, digest,
pinned dependencies, lock text + hash, environment-variable policy), seeds and their delivery, the command,
resources and network policy, per-run hardware/backend and resource usage from the run manifests, execution logs
and output artifacts with checksums, metrics with their source, evaluator versions, the model provenance of the
agents that designed/coded it and the comparisons that used it. The manifest carries its own SHA-256.

:func:`stream_package_zip` streams ``manifest.json`` + ``README.md`` (with the exact reproduction commands) +
``params/seed-<n>.json`` + ``code/…`` as a zip without building it in memory at once; all inputs are loaded and
bounded *before* streaming starts, so no database session is used while the response is being sent.

:func:`lineage_for_run` returns the records the verification context feeds into
:func:`engines.lab.lineage.build_lineage`.
"""

from __future__ import annotations

import hashlib
import io
import json
import shlex
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound, PayloadTooLarge
from aegis_api.lab.core.access import get_owned
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.experiments import code as code_module
from aegis_api.lab.experiments.runs import run_metric_rows
from aegis_api.lab.experiments.service import load_experiment, version_spec
from aegis_api.lab.models import (
    AgentRun,
    ArtifactVersion,
    CodeSnapshot,
    Dataset,
    DatasetVersion,
    EvaluationRun,
    ExecutionEnvironment,
    Experiment,
    ExperimentComparison,
    ExperimentRun,
    ExperimentVersion,
)
from engines.lab.sandbox import CODE_DIR, INPUT_DIR, OUTPUT_DIR, WORKDIR

log = structlog.get_logger("aegis.lab.experiments.reproducibility")

PACKAGE_SCHEMA_VERSION = "aegis-reproducibility-1.0"
MAX_PACKAGE_CODE_BYTES = 64 * 1024 * 1024
MAX_RUNS_IN_PACKAGE = 1000
ZIP_CHUNK = 64 * 1024


def _s(value: Any) -> str | None:
    return str(value) if value is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _resolve_version(db: Session, experiment: Experiment, version: int | None) -> ExperimentVersion:
    if version is None:
        if experiment.current_version_id is None:
            raise NotFound("The experiment has no version")
        row = db.get(ExperimentVersion, experiment.current_version_id)
    else:
        row = db.scalar(
            select(ExperimentVersion).where(
                ExperimentVersion.experiment_id == experiment.id, ExperimentVersion.version == version
            )
        )
    if row is None or row.organization_id != experiment.organization_id:
        raise NotFound("Experiment version not found")
    return row


def _artifact_entry(db: Session, version_id: str | None, organization_id: uuid.UUID) -> dict[str, Any] | None:
    if not version_id:
        return None
    try:
        row = db.get(ArtifactVersion, uuid.UUID(str(version_id)))
    except ValueError:
        return None
    if row is None or row.organization_id != organization_id:
        return {"artifact_version_id": str(version_id), "available": False}
    return {
        "artifact_version_id": str(row.id),
        "artifact_id": str(row.artifact_id),
        "sha256": row.checksum,
        "size_bytes": row.size_bytes,
        "mime_type": row.mime_type,
        "scan_status": row.scan_status,
    }


def model_provenance(db: Session, organization_id: uuid.UUID, agent_run_ids: set[uuid.UUID]) -> list[dict[str, Any]]:
    """Model/prompt provenance of the agent runs that produced a design or its code."""
    if not agent_run_ids:
        return []
    rows = db.scalars(
        select(AgentRun).where(AgentRun.organization_id == organization_id, AgentRun.id.in_(sorted(agent_run_ids)))
    ).all()
    return [
        {
            "agent_run_id": str(r.id),
            "role": r.role,
            "agent_version_id": str(r.agent_version_id),
            "provider": r.provider,
            "model": r.model,
            "model_version": r.model_version,
            "prompt_key": r.prompt_key,
            "prompt_version": r.prompt_version,
            "prompt_hash": r.prompt_hash,
            "strategy_version_id": _s(r.strategy_version_id),
            "status": r.status,
        }
        for r in sorted(rows, key=lambda r: (r.created_at, str(r.id)))
    ]


def _dataset_entries(db: Session, version: ExperimentVersion) -> list[dict[str, Any]]:
    spec = version_spec(version)
    out: list[dict[str, Any]] = []
    for use in spec.datasets:
        entry: dict[str, Any] = {
            "dataset_version_id": use.dataset_version_id,
            "split": use.split,
            "role": use.role,
            "mount_path": f"{INPUT_DIR}/{use.effective_mount_path}",
        }
        try:
            row = db.get(DatasetVersion, uuid.UUID(use.dataset_version_id))
        except ValueError:
            row = None
        if row is None or row.organization_id != version.organization_id:
            out.append({**entry, "available": False})
            continue
        dataset = db.get(Dataset, row.dataset_id)
        split_info = (row.splits or {}).get(use.split) if use.split else None
        out.append(
            {
                **entry,
                "available": True,
                "dataset_id": str(row.dataset_id),
                "dataset_name": dataset.name if dataset is not None else None,
                "version": row.version,
                "format": row.format,
                "checksum": row.checksum,
                "size_bytes": row.size_bytes,
                "row_count": row.row_count,
                "license": row.license or (dataset.license if dataset is not None else None),
                "source": row.source or (dataset.source if dataset is not None else None),
                "parent_version_id": _s(row.parent_version_id),
                "split_checksum": split_info.get("checksum") if isinstance(split_info, dict) else None,
                "split_rows": split_info.get("rows") if isinstance(split_info, dict) else None,
                "split_visibility": split_info.get("visibility") if isinstance(split_info, dict) else None,
                "splits": {
                    name: {"visibility": info.get("visibility"), "checksum": info.get("checksum")}
                    for name, info in sorted((row.splits or {}).items())
                    if isinstance(info, dict)
                },
            }
        )
    return out


def _run_entries(db: Session, version: ExperimentVersion) -> list[dict[str, Any]]:
    runs = db.scalars(
        select(ExperimentRun)
        .where(ExperimentRun.experiment_version_id == version.id)
        .order_by(ExperimentRun.run_number, ExperimentRun.id)
        .limit(MAX_RUNS_IN_PACKAGE)
    ).all()
    out: list[dict[str, Any]] = []
    for run in runs:
        manifest = dict(run.environment_manifest or {})
        outputs = [
            {"path": path, **(_artifact_entry(db, vid, run.organization_id) or {})}
            for path, vid in sorted((manifest.get("outputs") or {}).items())
        ]
        metrics = [
            {
                "name": m.name,
                "value": m.value,
                "step": m.step,
                "split": m.split,
                "source": m.source,
                "evaluation_run_id": _s(m.evaluation_run_id),
            }
            for m in run_metric_rows(db, run.id)
        ]
        out.append(
            {
                "id": str(run.id),
                "run_key": run.run_key,
                "role": run.role,
                "seed": run.seed,
                "status": run.status,
                "status_reason": run.status_reason,
                "exit_code": run.exit_code,
                "compute_job_id": _s(run.compute_job_id),
                "attempt": run.attempt,
                "started_at": _iso(run.started_at),
                "completed_at": _iso(run.completed_at),
                "duration_seconds": run.duration_seconds,
                "cost_usd": str(run.cost_usd),
                "hardware": {
                    "backend": manifest.get("backend"),
                    "image": manifest.get("image"),
                    "image_digest": manifest.get("image_digest"),
                    "expected_image_digest": manifest.get("expected_image_digest"),
                    "digest_verified": manifest.get("digest_verified"),
                    "resources": manifest.get("resources"),
                    "resource_usage": manifest.get("resource_usage"),
                    "network_policy": manifest.get("network_policy"),
                },
                "logs": _artifact_entry(db, manifest.get("logs_artifact_version_id"), run.organization_id),
                "outputs": outputs,
                "metrics": metrics,
                "reproduction_of_run_id": _s(run.reproduction_of_run_id),
            }
        )
    return out


def _docker_command(
    image: str, digest: str | None, command: list[str], env: dict[str, str], user: str
) -> str:
    ref = image if (digest is None or "@" in image) else f"{image.split('@', 1)[0]}@{digest}"
    parts = [
        "docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges",
        f"--user {shlex.quote(user)}",
        f"--tmpfs /tmp:rw,noexec,nosuid,size=256m -w {WORKDIR}",
        *[f"-e {shlex.quote(f'{k}={v}')}" for k, v in sorted(env.items())],
        f'-v "$PWD/input:{INPUT_DIR}:ro" -v "$PWD/code:{CODE_DIR}:ro" -v "$PWD/output:{OUTPUT_DIR}"',
        shlex.quote(ref),
        " ".join(shlex.quote(a) for a in command),
    ]
    return " \\\n  ".join(parts)


def reproducibility_package(
    db: Session, actor: Actor, experiment_id: uuid.UUID | str, version: int | None = None
) -> dict[str, Any]:
    """The JSON reproducibility manifest of one experiment version (``experiment:read``)."""
    from aegis_api.config import get_settings

    experiment = load_experiment(db, actor, experiment_id, "experiment:read")
    ver = _resolve_version(db, experiment, version)
    spec = version_spec(ver)
    snapshot = db.get(CodeSnapshot, ver.code_snapshot_id) if ver.code_snapshot_id else None
    environment = db.get(ExecutionEnvironment, ver.environment_id) if ver.environment_id else None
    runs = _run_entries(db, ver)
    run_ids = [uuid.UUID(r["id"]) for r in runs]
    evaluations = (
        db.scalars(
            select(EvaluationRun)
            .where(
                EvaluationRun.organization_id == experiment.organization_id,
                or_(EvaluationRun.experiment_run_id.in_(run_ids), EvaluationRun.experiment_id == experiment.id),
            )
            .order_by(EvaluationRun.created_at, EvaluationRun.id)
        ).all()
        if run_ids
        else db.scalars(
            select(EvaluationRun).where(EvaluationRun.experiment_id == experiment.id).order_by(EvaluationRun.created_at)
        ).all()
    )
    comparisons = db.scalars(
        select(ExperimentComparison.id)
        .where(
            or_(
                ExperimentComparison.candidate_version_id == ver.id,
                ExperimentComparison.baseline_version_id == ver.id,
            )
        )
        .order_by(ExperimentComparison.created_at)
    ).all()
    agent_runs = {
        rid
        for rid in (
            experiment.created_by_agent_run_id,
            ver.created_by_agent_run_id,
            snapshot.created_by_agent_run_id if snapshot is not None else None,
        )
        if rid is not None
    }
    delivery = (ver.reproducibility or {}).get("seed_delivery") or code_module.seed_delivery(spec).as_dict()
    seed_env_name = delivery.get("seed_env_var")
    settings = get_settings()
    commands = []
    for seed in ver.seeds or []:
        env = dict(delivery.get("env_flags") or {})
        env[code_module.HASH_SEED_VAR] = str(seed)
        if delivery.get("seed_env_var_injected") and seed_env_name:
            env[str(seed_env_name)] = str(seed)
        commands.append(
            {
                "seed": seed,
                "params_file": f"params/seed-{seed}.json",
                "mount_params_as": code_module.PARAMS_FILE,
                "docker": _docker_command(
                    environment.image if environment else spec.environment.image,
                    environment.image_digest if environment else spec.environment.image_digest,
                    list(ver.command or []),
                    env,
                    settings.execution_user,
                ),
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "generated_at": utcnow().isoformat(),
        "experiment": {
            "id": str(experiment.id),
            "title": experiment.title,
            "kind": experiment.kind,
            "status": experiment.status,
            "project_id": str(experiment.project_id),
            "mission_id": _s(experiment.mission_id),
            "hypothesis_id": _s(experiment.hypothesis_id),
            "baseline_experiment_id": _s(experiment.baseline_experiment_id),
            "parent_experiment_id": _s(experiment.parent_experiment_id),
        },
        "version": {
            "id": str(ver.id),
            "version": ver.version,
            "spec_hash": ver.spec_hash,
            "spec_version": (ver.reproducibility or {}).get("spec_version"),
            "created_at": _iso(ver.created_at),
            "created_by_id": _s(ver.created_by_id),
            "created_by_agent_run_id": _s(ver.created_by_agent_run_id),
            "spec": ver.spec,
        },
        "validation": ver.validation_report,
        "code": (
            {
                "code_snapshot_id": str(snapshot.id),
                "content_hash": snapshot.content_hash,
                "source": snapshot.source,
                "git_repo": snapshot.git_repo,
                "git_commit": snapshot.git_commit,
                "entrypoint": snapshot.entrypoint,
                "language": snapshot.language,
                "files": snapshot.files_manifest,
                "storage_key": snapshot.storage_key,
            }
            if snapshot is not None
            else None
        ),
        "datasets": _dataset_entries(db, ver),
        "environment": (
            {
                "id": str(environment.id),
                "image": environment.image,
                "image_digest": environment.image_digest,
                "runtime": environment.runtime,
                "dependencies": list(environment.dependencies or []),
                "lockfile": environment.lockfile,
                "lockfile_hash": environment.lockfile_hash,
                "content_hash": environment.content_hash,
            }
            if environment is not None
            else None
        ),
        "env_var_policy": {
            "allowed_vars": list((environment.env_policy or {}).get("allowed_vars") or []) if environment else [],
            "host_passthrough": False,
            "secrets": "never injected",
            "seed_delivery": delivery,
        },
        "seeds": list(ver.seeds or []),
        "command": list(ver.command or []),
        "parameters": spec.parameters,
        "resources": dict(ver.resource_request or {}),
        "timeout_seconds": ver.timeout_seconds,
        "network_policy": dict(ver.network_policy or {}),
        "statistical_plan": dict(ver.statistical_plan or {}),
        "expected_cost_usd": str(ver.expected_cost_usd),
        "runs": runs,
        "evaluations": [
            {
                "id": str(e.id),
                "experiment_run_id": _s(e.experiment_run_id),
                "comparison_id": _s(e.comparison_id),
                "evaluator_key": e.evaluator_key,
                "evaluator_version": e.evaluator_version,
                "status": e.status,
                "passed": e.passed,
                "independent": e.independent,
            }
            for e in evaluations
        ],
        "model_provenance": model_provenance(db, experiment.organization_id, agent_runs),
        "comparison_ids": [str(c) for c in comparisons],
        "reproduction": {
            "platform": f"POST /api/v1/experiments/{experiment.id}/execute",
            "workspace_layout": {
                "input": INPUT_DIR,
                "code": CODE_DIR,
                "output": OUTPUT_DIR,
                "params": code_module.PARAMS_FILE,
            },
            "commands": commands,
        },
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return manifest


# =============================================================================================
# Zip streaming
# =============================================================================================
@dataclass
class PackageFiles:
    manifest: dict[str, Any]
    readme: str
    code_files: dict[str, bytes]
    params_files: dict[str, bytes]

    @property
    def filename(self) -> str:
        exp = self.manifest["experiment"]["id"]
        return f"experiment-{exp}-v{self.manifest['version']['version']}-reproducibility.zip"


def _readme(manifest: dict[str, Any]) -> str:
    exp = manifest["experiment"]
    ver = manifest["version"]
    lines = [
        f"# Reproducibility package — {exp['title']} (version {ver['version']})",
        "",
        f"* Experiment: `{exp['id']}` ({exp['kind']}, status {exp['status']})",
        f"* Specification hash: `{ver['spec_hash']}`",
        f"* Manifest SHA-256: `{manifest['manifest_sha256']}`",
    ]
    code = manifest.get("code")
    if code:
        lines.append(f"* Code snapshot: `{code['code_snapshot_id']}` (content hash `{code['content_hash']}`)")
    env = manifest.get("environment")
    if env:
        digest = f" @ `{env['image_digest']}`" if env.get("image_digest") else " (not digest-pinned)"
        lines.append(f"* Image: `{env['image']}`{digest}")
    lines += [
        "",
        "## Re-run on the platform",
        "",
        f"`{manifest['reproduction']['platform']}` re-executes this validated version in the sandbox.",
        "",
        "## Re-run locally",
        "",
        "1. Place the dataset files listed in `manifest.json` (`datasets[].mount_path`) under `./input` and verify "
        "their SHA-256 checksums.",
        "2. The code is in `./code` (verify it against `manifest.json` → `code.files`).",
        f"3. For each seed copy `params/seed-<seed>.json` to `./input/params.json` ({code_module.PARAMS_FILE} in the "
        "container) and run the command below; results appear in `./output` (metrics in `output/metrics.json`).",
        "",
    ]
    for entry in manifest["reproduction"]["commands"]:
        lines += [f"### Seed {entry['seed']}", "", "```sh", entry["docker"], "```", ""]
    if manifest.get("datasets"):
        lines += ["## Datasets", ""]
        for d in manifest["datasets"]:
            lines.append(
                f"* `{d['dataset_version_id']}` split `{d.get('split') or '(all)'}` → `{d['mount_path']}` "
                f"(sha256 `{d.get('split_checksum') or d.get('checksum')}`, licence {d.get('license') or 'unknown'})"
            )
        lines.append("")
    lines += [
        "Metrics marked `self_reported` were written by the experiment code; independent evaluator results are "
        "listed under `evaluations` in the manifest.",
        "",
    ]
    return "\n".join(lines)


def build_package_files(
    db: Session, actor: Actor, experiment_id: uuid.UUID | str, version: int | None = None
) -> PackageFiles:
    """Load everything the zip needs (bounded) while the session is open."""
    manifest = reproducibility_package(db, actor, experiment_id, version)
    code_files: dict[str, bytes] = {}
    code = manifest.get("code")
    if code:
        snapshot = get_owned(db, CodeSnapshot, code["code_snapshot_id"], actor, label="Code snapshot")
        code_files = code_module.read_snapshot_files(snapshot, max_bytes=MAX_PACKAGE_CODE_BYTES)
    if sum(len(v) for v in code_files.values()) > MAX_PACKAGE_CODE_BYTES:
        raise PayloadTooLarge(f"The code exceeds {MAX_PACKAGE_CODE_BYTES} bytes")
    params = manifest.get("parameters") or {}
    params_files = {
        f"params/seed-{seed}.json": json.dumps(
            {**params, code_module.SEED_PARAMETER: seed}, sort_keys=True, indent=2
        ).encode("utf-8")
        for seed in manifest.get("seeds") or []
    }
    return PackageFiles(manifest=manifest, readme=_readme(manifest), code_files=code_files, params_files=params_files)


class _Sink(io.RawIOBase):
    """Write-only buffer the zip writer appends to; drained after each member (non-seekable → streaming zip)."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._position = 0

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        chunk = bytes(data)
        self._chunks.append(chunk)
        self._position += len(chunk)
        return len(chunk)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        return None

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def stream_package_zip(files: PackageFiles) -> Iterator[bytes]:
    """Yield the zip archive incrementally (members are written one by one)."""
    sink = _Sink()
    members: list[tuple[str, bytes]] = [
        ("manifest.json", json.dumps(files.manifest, sort_keys=True, indent=2, default=str).encode("utf-8")),
        ("README.md", files.readme.encode("utf-8")),
        *sorted(files.params_files.items()),
        *[(f"code/{path}", data) for path, data in sorted(files.code_files.items())],
    ]
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with archive.open(info, mode="w") as fh:
                for start in range(0, len(data), ZIP_CHUNK):
                    fh.write(data[start : start + ZIP_CHUNK])
                    chunk = sink.drain()
                    if chunk:
                        yield chunk
            chunk = sink.drain()
            if chunk:
                yield chunk
    tail = sink.drain()
    if tail:
        yield tail


# =============================================================================================
# Lineage
# =============================================================================================
def lineage_for_run(db: Session, run_id: uuid.UUID | str, *, actor: Actor | None = None) -> dict[str, Any]:
    """Provenance records of one run, shaped for :func:`engines.lab.lineage.build_lineage` keyword arguments.

    With ``actor`` the run is ownership-checked; without it the (RLS-scoped) session decides visibility.
    """
    if actor is not None:
        run = get_owned(db, ExperimentRun, run_id, actor, label="Experiment run")
    else:
        try:
            run = db.get(ExperimentRun, uuid.UUID(str(run_id)))
        except ValueError:
            run = None
        if run is None:
            raise NotFound("Experiment run not found")
    experiment = db.get(Experiment, run.experiment_id)
    version = db.get(ExperimentVersion, run.experiment_version_id)
    if experiment is None or version is None:
        raise NotFound("Experiment run not found")
    snapshot = db.get(CodeSnapshot, version.code_snapshot_id) if version.code_snapshot_id else None
    environment = db.get(ExecutionEnvironment, version.environment_id) if version.environment_id else None
    manifest = dict(run.environment_manifest or {})
    artifact_ids = [str(v) for v in (manifest.get("outputs") or {}).values()]
    if manifest.get("logs_artifact_version_id"):
        artifact_ids.append(str(manifest["logs_artifact_version_id"]))
    artifacts = []
    for aid in artifact_ids:
        entry = _artifact_entry(db, aid, run.organization_id)
        if entry and entry.get("available", True):
            artifacts.append(
                {
                    "id": aid,
                    "name": next((p for p, v in (manifest.get("outputs") or {}).items() if str(v) == aid), "job.log"),
                    "kind": "output",
                    "sha256": entry.get("sha256"),
                    "experiment_run_id": str(run.id),
                }
            )
    dataset_rows = []
    for dv_id in version.dataset_version_ids or []:
        try:
            dv = db.get(DatasetVersion, uuid.UUID(str(dv_id)))
        except ValueError:
            dv = None
        if dv is not None and dv.organization_id == run.organization_id:
            dataset_rows.append(
                {
                    "id": str(dv.id),
                    "dataset_id": str(dv.dataset_id),
                    "version": dv.version,
                    "content_hash": dv.checksum,
                    "parent_version_id": _s(dv.parent_version_id),
                }
            )
    evaluations = db.scalars(
        select(EvaluationRun).where(EvaluationRun.experiment_run_id == run.id).order_by(EvaluationRun.created_at)
    ).all()
    agent_runs = {r for r in (experiment.created_by_agent_run_id, version.created_by_agent_run_id) if r}
    if snapshot is not None and snapshot.created_by_agent_run_id:
        agent_runs.add(snapshot.created_by_agent_run_id)
    return {
        "experiments": [
            {
                "id": str(experiment.id),
                "title": experiment.title,
                "kind": experiment.kind,
                "hypothesis_id": _s(experiment.hypothesis_id),
                "created_by_agent_run_id": _s(experiment.created_by_agent_run_id),
            }
        ],
        "experiment_versions": [
            {
                "id": str(version.id),
                "experiment_id": str(experiment.id),
                "version": version.version,
                "spec_hash": version.spec_hash,
                "code_snapshot_id": _s(version.code_snapshot_id),
                "environment_id": _s(version.environment_id),
                "dataset_version_ids": [str(d) for d in version.dataset_version_ids or []],
                "created_by_agent_run_id": _s(version.created_by_agent_run_id),
            }
        ],
        "runs": [
            {
                "id": str(run.id),
                "experiment_version_id": str(version.id),
                "experiment_id": str(experiment.id),
                "role": run.role,
                "seed": run.seed,
                "status": run.status,
                "code_snapshot_id": _s(version.code_snapshot_id),
                "environment_id": _s(version.environment_id),
                "dataset_version_ids": [str(d) for d in version.dataset_version_ids or []],
                "artifact_version_ids": artifact_ids,
                "agent_run_id": _s(version.created_by_agent_run_id),
                "reproduction_of_run_id": _s(run.reproduction_of_run_id),
            }
        ],
        "code_snapshots": (
            [
                {
                    "id": str(snapshot.id),
                    "content_hash": snapshot.content_hash,
                    "source": snapshot.source,
                    "git_commit": snapshot.git_commit,
                    "entrypoint": snapshot.entrypoint,
                    "created_by_agent_run_id": _s(snapshot.created_by_agent_run_id),
                }
            ]
            if snapshot is not None
            else []
        ),
        "dataset_versions": dataset_rows,
        "environments": (
            [
                {
                    "id": str(environment.id),
                    "image": environment.image,
                    "image_digest": manifest.get("image_digest") or environment.image_digest,
                    "lockfile_hash": environment.lockfile_hash,
                }
            ]
            if environment is not None
            else []
        ),
        "model_provenance": [
            p for p in model_provenance(db, run.organization_id, agent_runs) if p.get("provider") and p.get("model")
        ],
        "artifacts": artifacts,
        "evaluations": [
            {
                "id": str(e.id),
                "experiment_run_id": _s(e.experiment_run_id),
                "evaluator_key": e.evaluator_key,
                "evaluator_version": e.evaluator_version,
                "status": e.status,
                "passed": e.passed,
            }
            for e in evaluations
        ],
    }
