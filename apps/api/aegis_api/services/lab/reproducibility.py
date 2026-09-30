"""Reproducibility: per-run manifests, completeness scoring, portable reproduction packages and replay.

A reproduction package is a zip with the experiment specification, the exact code bundle (checksum-verified),
every run's manifest and metrics, evaluation results, dataset checksums (not the data itself), environment
image digests and a README with the replay procedure. ``replay`` re-executes a run's exact configuration as a
new reproduction run through the normal (policy-gated, sandboxed) execution path.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.errors import ValidationFailed
from aegis_api.models.lab import (
    EvaluationRun,
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    WorkflowRun,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import experiments as experiment_service
from aegis_api.services.lab.access import get_scoped
from engines.lab.enums import RunKind
from engines.lab.reproducibility import ReproducibilityManifest, completeness

MAX_PACKAGE_RUNS = 200


def manifest_report(run: ExperimentRun) -> dict[str, Any]:
    if not run.manifest:
        return {"run_id": str(run.id), "manifest": None, "completeness": 0.0, "missing": ["manifest"]}
    manifest = ReproducibilityManifest.model_validate(run.manifest)
    score, missing = completeness(
        manifest,
        requires_dataset=bool(manifest.dataset_version_id),
        llm_generated_code=manifest.generated_by is not None,
    )
    return {
        "run_id": str(run.id),
        "manifest": run.manifest,
        "manifest_sha256": run.manifest_sha256,
        "digest_matches": manifest.digest() == run.manifest_sha256,
        "completeness": score,
        "missing": missing,
    }


def package(db: Session, principal: Principal, experiment_id: uuid.UUID | str) -> tuple[str, bytes]:
    principal.require("artifact:download")
    experiment = get_scoped(db, principal, Experiment, experiment_id, label="Experiment")
    version = db.get(ExperimentVersion, experiment.current_version_id) if experiment.current_version_id else None
    if version is None:
        raise ValidationFailed("experiment has no version")
    runs = list(
        db.scalars(
            select(ExperimentRun)
            .where(ExperimentRun.experiment_version_id == version.id)
            .order_by(ExperimentRun.created_at)
        ).all()
    )[:MAX_PACKAGE_RUNS]
    evaluations = db.scalars(select(EvaluationRun).where(EvaluationRun.experiment_version_id == version.id)).all()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("spec.json", json.dumps(version.spec, indent=2, sort_keys=True))
        zf.writestr(
            "experiment.json",
            json.dumps(
                {
                    "experiment_id": str(experiment.id),
                    "title": experiment.title,
                    "version": version.version,
                    "spec_sha256": version.spec_sha256,
                    "code_sha256": version.code_sha256,
                    "validation": version.validation,
                    "outcome": experiment.outcome,
                },
                indent=2,
                default=str,
            ),
        )
        if version.code_artifact_id:
            files, entrypoint = experiment_service.load_code(principal.organization_id, version)
            for path, data in files.items():
                zf.writestr(path, data)
            zf.writestr("code/ENTRYPOINT.json", json.dumps(entrypoint))
        metrics_csv = io.StringIO()
        writer = csv.writer(metrics_csv)
        writer.writerow(["run_id", "run_kind", "variant", "seed", "status", "metric", "value", "self_reported"])
        for r in runs:
            zf.writestr(f"manifests/{r.id}.json", json.dumps(manifest_report(r), indent=2, default=str))
            for name, value in (r.metrics or {}).items():
                writer.writerow([r.id, r.run_kind, r.variant, r.seed, r.status, name, value, r.self_reported])
        zf.writestr("metrics.csv", metrics_csv.getvalue())
        zf.writestr(
            "evaluations.json",
            json.dumps(
                [
                    {
                        "evaluator": f"{e.evaluator_key}@{e.evaluator_version}",
                        "fingerprint": e.evaluator_fingerprint,
                        "verdict": e.verdict,
                        "metrics": e.metrics,
                        "warnings": e.warnings,
                        "independent": e.independent,
                    }
                    for e in evaluations
                ],
                indent=2,
            ),
        )
        zf.writestr("README.md", _readme(experiment, version))
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.reproducibility.package_downloaded",
        resource_type="experiment",
        resource_id=experiment.id,
        principal=principal,
    )
    return f"reproduction-{experiment.id}-v{version.version}.zip", buf.getvalue()


def _readme(experiment: Experiment, version: ExperimentVersion) -> str:
    return f"""# Reproduction package — {experiment.title}

Experiment `{experiment.id}` version {version.version} (spec sha256 `{version.spec_sha256}`).

## Contents
- `spec.json` — the exact experiment specification.
- `code/` — the code bundle executed in the sandbox (sha256 `{version.code_sha256}`), entrypoint in `ENTRYPOINT.json`.
- `manifests/<run>.json` — per-run manifest: image digest, dataset checksum, seed, parameters, hardware, outputs.
- `metrics.csv` — measured metrics per run (harness-measured unless `self_reported` is true).
- `evaluations.json` — evaluator versions, fingerprints and verdicts.

## Replay
1. Pull the container image by the digest recorded in each manifest.
2. Stage `code/` under `/workspace/code`, the dataset version (verify its checksum) under `/workspace/data`, and
   `/workspace/input/config.json` = `{{"seed": <seed>, "parameters": <parameters>, "run_kind": ..., "variant": ...}}`.
3. Run the entrypoint with no network access; then run the recorded evaluation harness on `/workspace/output`.
4. Compare metrics with `metrics.csv` within the spec's `reproducibility.relative_tolerance`.

Or use the platform: `POST /api/v1/experiment-runs/<run_id>/replay`.

This package supports independent reproduction; it is not a certification of the result.
"""


def replay(db: Session, principal: Principal, run_id: uuid.UUID | str) -> tuple[ExperimentRun, WorkflowRun]:
    from aegis_api.workflows import client as workflow_client

    principal.require("experiment:execute")
    original = get_scoped(db, principal, ExperimentRun, run_id, label="Experiment run")
    if original.status != "completed":
        raise ValidationFailed("only completed runs can be replayed")
    key = f"replay:{original.id}:{uuid.uuid4().hex[:8]}"
    run = ExperimentRun(
        organization_id=original.organization_id,
        project_id=original.project_id,
        mission_id=original.mission_id,
        experiment_id=original.experiment_id,
        experiment_version_id=original.experiment_version_id,
        run_kind=RunKind.REPRODUCTION,
        variant=f"replay-of-{original.run_kind}",
        seed=original.seed,
        parameters=dict(original.parameters or {}),
        status="queued",
        reproduction_of_run_id=original.id,
        idempotency_key=key,
    )
    db.add(run)
    db.flush()
    wf = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="experiment",
        business_key=f"experiment-run:{run.id}",
        payload={"run_id": str(run.id), "experiment_id": str(run.experiment_id), "gate": True},
        principal=principal,
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.reproducibility.replay",
        resource_type="experiment_run",
        resource_id=original.id,
        principal=principal,
        after={"replay_run_id": str(run.id)},
    )
    return run, wf
