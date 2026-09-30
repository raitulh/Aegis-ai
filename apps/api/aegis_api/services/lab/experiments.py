"""Experiment design, validation, versioning and run planning.

Every experiment version holds exactly one validated ``ExperimentSpec`` (immutable; changes → new version).
Designs produced by agents go through the same ``ExperimentDesignValidator`` as human-authored ones; invalid
designs are stored with their validation report but can never be queued. Generated code is attached as a
new version referencing a checksummed code-bundle artifact.

Run planning is deterministic: baseline × seeds, candidate × seeds, ablations × min(seeds, 2), each with an
idempotency key so a replayed workflow never duplicates runs.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from aegis_api.db.session import session_scope
from aegis_api.errors import InvalidState, NotFound, ValidationFailed
from aegis_api.models.lab import (
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    Hypothesis,
    Mission,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import artifacts, environments, events, graph
from aegis_api.services.lab.access import accessible_project_ids, get_project, get_scoped
from aegis_api.services.lab.common import Actor, sha256_bytes
from engines.lab import harnesses
from engines.lab.agents.schemas import CodeBundle
from engines.lab.enums import ExperimentStatus, HypothesisStatus, LabEventType, RunKind
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.experiments.validator import ExperimentDesignValidator, ValidationReport
from engines.lab.state_machines import EXPERIMENT, HYPOTHESIS

IO_CONTRACT = {
    "workdir": "/workspace",
    "code": "/workspace/code (your files; entrypoint runs with cwd=/workspace)",
    "config": "/workspace/input/config.json → {seed, parameters, run_kind, variant}",
    "data": "/workspace/data/<path> (train/validation/test splits only; hidden labels are never mounted)",
    "outputs": "/workspace/output/ (only files written here are collected)",
    "network": "none",
    "limits": "cpu/memory/pids/timeout enforced by the sandbox; read-only root filesystem; non-root user",
}


def spec_contract() -> dict[str, Any]:
    return ExperimentSpec.model_json_schema()


def validate_spec(
    db: Session, organization_id: uuid.UUID, raw: dict[str, Any]
) -> tuple[ExperimentSpec | None, dict[str, Any]]:
    try:
        spec = ExperimentSpec.model_validate(raw)
    except ValidationError as exc:
        return None, {
            "valid": False,
            "issues": [
                {"code": "schema", "severity": "error", "field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                for e in exc.errors()[:30]
            ],
        }
    limits, envs = environments.validator_inputs(db, organization_id)
    validator = ExperimentDesignValidator(
        limits=limits,
        environments={e.environment_id: e for e in envs},
        known_harnesses=frozenset(harnesses.harnesses()),
    )
    report: ValidationReport = validator.validate(spec)
    return spec, report.to_dict()


def _next_version(db: Session, experiment_id: uuid.UUID) -> int:
    return 1 + int(
        db.scalar(
            select(func.coalesce(func.max(ExperimentVersion.version), 0)).where(
                ExperimentVersion.experiment_id == experiment_id
            )
        )
        or 0
    )


def _add_version(
    db: Session,
    experiment: Experiment,
    spec: dict[str, Any],
    validation: dict[str, Any],
    *,
    code_artifact_id: uuid.UUID | None = None,
    code_sha256: str | None = None,
    generated_by_run_id: uuid.UUID | None = None,
    change_note: str | None = None,
    created_by_id: uuid.UUID | None = None,
) -> ExperimentVersion:
    version = ExperimentVersion(
        organization_id=experiment.organization_id,
        experiment_id=experiment.id,
        version=_next_version(db, experiment.id),
        spec=spec,
        spec_sha256=sha256_bytes(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()),
        validation=validation,
        code_artifact_id=code_artifact_id,
        code_sha256=code_sha256,
        generated_by_run_id=generated_by_run_id,
        change_note=change_note,
        created_by_id=created_by_id,
    )
    db.add(version)
    db.flush()
    experiment.current_version_id = version.id
    experiment.version_count = version.version
    return version


# --- API ----------------------------------------------------------------------------------------------------


def create(db: Session, principal: Principal, data: dict[str, Any]) -> tuple[Experiment, ExperimentVersion]:
    principal.require("experiment:create")
    project = get_project(db, principal, data["project_id"])
    mission_id = data.get("mission_id")
    if mission_id:
        get_scoped(db, principal, Mission, mission_id, label="Mission")
    hypothesis_id = data.get("hypothesis_id")
    if hypothesis_id:
        get_scoped(db, principal, Hypothesis, hypothesis_id, label="Hypothesis")
    spec, validation = validate_spec(db, principal.organization_id, data["spec"])
    experiment = Experiment(
        organization_id=principal.organization_id,
        project_id=project.id,
        mission_id=mission_id,
        hypothesis_id=hypothesis_id,
        title=str(data["title"])[:300],
        status=ExperimentStatus.DRAFT,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(experiment)
    db.flush()
    version = _add_version(
        db,
        experiment,
        spec.model_dump(mode="json") if spec else dict(data["spec"]),
        validation,
        change_note=data.get("change_note") or "created",
        created_by_id=principal.user_id if principal.is_human else None,
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.experiment.created",
        resource_type="experiment",
        resource_id=experiment.id,
        principal=principal,
        after={"valid": validation.get("valid"), "version": version.version},
    )
    return experiment, version


def new_version(
    db: Session, principal: Principal, experiment_id: uuid.UUID | str, spec_raw: dict[str, Any], change_note: str | None
) -> ExperimentVersion:
    principal.require("experiment:create")
    experiment = get_scoped(db, principal, Experiment, experiment_id, label="Experiment")
    if experiment.status not in (ExperimentStatus.DRAFT, ExperimentStatus.FAILED):
        raise InvalidState(f"Cannot add a version while the experiment is {experiment.status}")
    spec, validation = validate_spec(db, principal.organization_id, spec_raw)
    if experiment.status == ExperimentStatus.FAILED:
        experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.RETRYING)
        experiment.retry_count += 1
    return _add_version(
        db,
        experiment,
        spec.model_dump(mode="json") if spec else spec_raw,
        validation,
        change_note=change_note,
        created_by_id=principal.user_id if principal.is_human else None,
    )


def list_experiments(
    db: Session,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Select[Experiment]:
    stmt = select(Experiment).where(Experiment.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Experiment.project_id.in_(visible))
    if project_id:
        stmt = stmt.where(Experiment.project_id == project_id)
    if mission_id:
        stmt = stmt.where(Experiment.mission_id == mission_id)
    if status:
        stmt = stmt.where(Experiment.status == status)
    return stmt.order_by(Experiment.created_at.desc())


def versions(db: Session, experiment: Experiment) -> list[ExperimentVersion]:
    return list(
        db.scalars(
            select(ExperimentVersion)
            .where(ExperimentVersion.experiment_id == experiment.id)
            .order_by(ExperimentVersion.version)
        ).all()
    )


def runs(db: Session, experiment_id: uuid.UUID) -> list[ExperimentRun]:
    return list(
        db.scalars(
            select(ExperimentRun).where(ExperimentRun.experiment_id == experiment_id).order_by(ExperimentRun.created_at)
        ).all()
    )


# --- agent-produced designs (activities) -----------------------------------------------------------------------


def persist_design(
    organization_id: uuid.UUID,
    *,
    project_id: uuid.UUID,
    mission_id: uuid.UUID | None,
    hypothesis_id: uuid.UUID | None,
    agent_run_id: uuid.UUID | None,
    spec_raw: dict[str, Any],
    title: str,
    defaults: dict[str, Any],
    actor: Actor,
) -> dict[str, Any]:
    raw = dict(spec_raw)
    raw.setdefault("hypothesis_id", str(hypothesis_id) if hypothesis_id else None)
    for key in ("harness", "dataset", "environment"):
        if defaults.get(key) and not raw.get(key):
            raw[key] = defaults[key]
    if defaults.get("min_seeds"):
        plan = dict(raw.get("statistical_plan") or {})
        plan["min_seeds"] = max(int(plan.get("min_seeds", 3)), int(defaults["min_seeds"]))
        raw["statistical_plan"] = plan
    with session_scope(organization_id) as db:
        spec, validation = validate_spec(db, organization_id, raw)
        experiment = Experiment(
            organization_id=organization_id,
            project_id=project_id,
            mission_id=mission_id,
            hypothesis_id=hypothesis_id,
            strategy_version_id=defaults.get("strategy_version_id"),
            title=title[:300],
            status=ExperimentStatus.DRAFT,
            designed_by_run_id=agent_run_id,
        )
        db.add(experiment)
        db.flush()
        version = _add_version(
            db,
            experiment,
            spec.model_dump(mode="json") if spec else raw,
            validation,
            generated_by_run_id=agent_run_id,
            change_note="designed by experiment designer agent",
        )
        valid = bool(validation.get("valid"))
        experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.VALIDATING)
        if not valid:
            experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.DRAFT)
            experiment.error = "design failed validation"
        if hypothesis_id and valid:
            h = db.get(Hypothesis, hypothesis_id)
            if h is not None and h.status == HypothesisStatus.SELECTED:
                h.status = HYPOTHESIS.ensure(h.status, HypothesisStatus.EXPERIMENT_DESIGNED)
        if mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=mission_id,
                project_id=project_id,
                event_type=LabEventType.EXPERIMENT_DESIGNED,
                message=f"Experiment designed: {experiment.title}" + ("" if valid else " (validation failed)"),
                data={
                    "experiment_id": str(experiment.id),
                    "valid": valid,
                    "issues": [i["code"] for i in validation.get("issues", []) if i.get("severity") == "error"][:10],
                },
                actor=actor,
            )
        if hypothesis_id:
            graph.link_refs(
                db,
                organization_id=organization_id,
                project_id=project_id,
                source=("experiment", str(experiment.id), experiment.title),
                target=("hypothesis", str(hypothesis_id), "hypothesis"),
                relation="tests",
            )
        return {
            "experiment_id": str(experiment.id),
            "version_id": str(version.id),
            "valid": valid,
            "validation": validation,
        }


def attach_code(
    organization_id: uuid.UUID,
    *,
    experiment_id: uuid.UUID,
    bundle: dict[str, Any],
    agent_run_id: uuid.UUID | None,
    actor: Actor,
) -> dict[str, Any]:
    code = CodeBundle.model_validate(bundle)
    files = {f.path: f.content for f in code.files}
    payload = json.dumps({"files": files, "entrypoint": code.entrypoint}, sort_keys=True).encode()
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        current = db.get(ExperimentVersion, experiment.current_version_id)
        assert current is not None
        project_id, mission_id = experiment.project_id, experiment.mission_id
    artifact_id, _version_id, digest = artifacts.store(
        organization_id,
        data=payload,
        name=f"code-bundle-{experiment_id}.json",
        kind="code_bundle",
        content_type="application/json",
        project_id=project_id,
        mission_id=mission_id,
        retention_class="evidence",
        created_by=actor.label,
        metadata={"entrypoint": code.entrypoint, "files": sorted(files)},
    )
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        assert experiment is not None
        current = db.get(ExperimentVersion, experiment.current_version_id)
        assert current is not None
        if current.code_sha256 == digest:
            return {"version_id": str(current.id), "code_artifact_id": str(current.code_artifact_id), "sha256": digest}
        spec = dict(current.spec)
        spec["code"] = {"entrypoint": code.entrypoint, "code_artifact_id": str(artifact_id), "code_sha256": digest}
        version = _add_version(
            db,
            experiment,
            spec,
            current.validation,
            code_artifact_id=artifact_id,
            code_sha256=digest,
            generated_by_run_id=agent_run_id,
            change_note="code generated by coding agent" if agent_run_id else "code attached",
        )
        return {"version_id": str(version.id), "code_artifact_id": str(artifact_id), "sha256": digest}


def load_code(organization_id: uuid.UUID, version: ExperimentVersion) -> tuple[dict[str, bytes], list[str]]:
    from aegis_api.infrastructure.storage import get_storage
    from aegis_api.models.lab import ArtifactVersion

    if version.code_artifact_id is None:
        raise ValidationFailed("experiment version has no code")
    with session_scope(organization_id) as db:
        from aegis_api.models.lab import Artifact

        artifact = db.get(Artifact, version.code_artifact_id)
        av = db.get(ArtifactVersion, artifact.current_version_id) if artifact and artifact.current_version_id else None
        if av is None:
            raise ValidationFailed("code artifact missing")
        key, sha = av.storage_key, av.sha256
    data = get_storage().get_bytes(key, max_bytes=20 * 1024 * 1024)
    if sha256_bytes(data) != sha or (version.code_sha256 and version.code_sha256 != sha):
        raise ValidationFailed("code bundle failed checksum verification")
    bundle = json.loads(data)
    return {f"code/{p}": c.encode() for p, c in bundle["files"].items()}, list(bundle["entrypoint"])


def queue(organization_id: uuid.UUID, experiment_id: uuid.UUID) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        if not (version.validation or {}).get("valid"):
            raise ValidationFailed("experiment design has not passed validation")
        if version.code_artifact_id is None:
            raise ValidationFailed("experiment has no code attached")
        if experiment.status == ExperimentStatus.DRAFT:
            experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.VALIDATING)
        if experiment.status in (ExperimentStatus.VALIDATING, ExperimentStatus.RETRYING):
            experiment.status = EXPERIMENT.ensure(experiment.status, ExperimentStatus.QUEUED)
        if experiment.mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=experiment.mission_id,
                project_id=experiment.project_id,
                event_type=LabEventType.EXPERIMENT_QUEUED,
                message=f"Experiment queued: {experiment.title}",
                data={"experiment_id": str(experiment.id), "version": version.version},
            )
        return {"experiment_id": str(experiment.id), "version_id": str(version.id), "status": experiment.status}


def plan_runs(
    organization_id: uuid.UUID, experiment_id: uuid.UUID, *, include_ablations: bool = True
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        spec = ExperimentSpec.model_validate(version.spec)
        seeds = list(dict.fromkeys(spec.seeds)) or list(range(1, spec.statistical_plan.min_seeds + 1))
        if len(seeds) < spec.statistical_plan.min_seeds:
            seeds += [max(seeds) + i + 1 for i in range(spec.statistical_plan.min_seeds - len(seeds))]
        planned: list[tuple[str, str | None, int, dict[str, Any]]] = []
        baseline_params = spec.baseline.parameters if spec.baseline else {}
        for seed in seeds:
            planned.append(
                (RunKind.BASELINE, spec.baseline.name if spec.baseline else "baseline", seed, baseline_params)
            )
            planned.append((RunKind.CANDIDATE, "candidate", seed, spec.parameters))
        if include_ablations:
            for ablation in spec.ablations[:4]:
                for seed in seeds[:2]:
                    planned.append(
                        (RunKind.ABLATION, ablation.name, seed, {**spec.parameters, **ablation.parameter_changes})
                    )
        ids: list[str] = []
        for kind, variant, seed, params in planned:
            key = f"{version.id}:{kind}:{variant}:{seed}"
            existing = db.scalar(
                select(ExperimentRun.id).where(
                    ExperimentRun.organization_id == organization_id, ExperimentRun.idempotency_key == key
                )
            )
            if existing:
                ids.append(str(existing))
                continue
            run = ExperimentRun(
                organization_id=organization_id,
                project_id=experiment.project_id,
                mission_id=experiment.mission_id,
                experiment_id=experiment.id,
                experiment_version_id=version.id,
                run_kind=str(kind),
                variant=variant,
                seed=int(seed),
                parameters=params,
                status="queued",
                idempotency_key=key,
            )
            db.add(run)
            db.flush()
            ids.append(str(run.id))
        return {"run_ids": ids, "seeds": seeds, "version_id": str(version.id)}


def set_status(
    organization_id: uuid.UUID,
    experiment_id: uuid.UUID,
    status: str,
    *,
    error: str | None = None,
    outcome: dict[str, Any] | None = None,
) -> str:
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        experiment.status = EXPERIMENT.ensure(experiment.status, status)
        if error is not None:
            experiment.error = error[:4000]
        if outcome is not None:
            experiment.outcome = {**(experiment.outcome or {}), **outcome}
        if status == ExperimentStatus.RETRYING:
            experiment.retry_count += 1
        return experiment.status


def experiment_dict(e: Experiment) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "title": e.title,
        "status": e.status,
        "project_id": str(e.project_id),
        "mission_id": str(e.mission_id) if e.mission_id else None,
        "hypothesis_id": str(e.hypothesis_id) if e.hypothesis_id else None,
        "current_version_id": str(e.current_version_id) if e.current_version_id else None,
        "version_count": e.version_count,
        "outcome": e.outcome,
        "error": e.error,
        "retry_count": e.retry_count,
    }
