"""Experiment design: creation, immutable versions, design validation, baselines, archival and execution requests.

An :class:`~aegis_api.lab.models.Experiment` is an identity; its specification lives in append-only
``experiment_versions``. Every version is validated *before* it is inserted, so the stored validation report is
the one that decided the experiment's fate:

* the design validator (:mod:`engines.lab.design_validator`) runs with the deployment ∩ organization execution
  limits, the project's dataset versions and split visibility, the hypothesis metric and the known evaluators;
* platform checks add what only the platform can know: sandbox image allowlist, egress availability/allowlist,
  code-snapshot availability, static code-quality errors, dataset mount collisions, seed delivery;
* ``DRAFT → VALIDATING → QUEUED`` (passed) or ``→ DRAFT`` (failed) via ``assert_transition``; a failing design is
  kept with its report and is never executed.

Code (inline files) becomes a content-addressed :class:`CodeSnapshot`; the runtime becomes a content-addressed
:class:`ExecutionEnvironment`. ``request_execution`` never runs anything in the request: it applies the governance
policy ``experiment.execute`` and launches the ``ExperimentWorkflow`` after commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import structlog
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.errors import Conflict, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.errors import PolicyDenied
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.evidence import append_evidence
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.org_settings import get_org_settings
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate, paginate_keyset
from aegis_api.lab.experiments import code as code_module
from aegis_api.lab.experiments.schemas import ExperimentCreate
from aegis_api.lab.hypotheses.schemas import parse_prediction
from aegis_api.lab.models import (
    CodeSnapshot,
    DatasetVersion,
    ExecutionEnvironment,
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    Hypothesis,
    LabEvaluator,
    Mission,
    Project,
    WorkflowRun,
)
from aegis_api.schemas.common import Page, PageParams
from engines.lab.compute_cost import ComputePrices, estimate_cost
from engines.lab.design_validator import (
    DatasetVersionInfo,
    ExperimentDesignValidator,
    Issue,
    SplitInfo,
    ValidationContext,
    ValidationLimits,
    ValidationReport,
)
from engines.lab.evaluators import BUILTIN_EVALUATOR_KEYS, ExperimentView
from engines.lab.evaluators.base import EvalContext
from engines.lab.evaluators.code_quality import CodeQualityEvaluator
from engines.lab.experiment_spec import (
    BASELINE_ONLY_COMPARATORS,
    EXPERIMENT_SPEC_VERSION,
    BaselineSpec,
    ExperimentSpec,
    diff_specs,
    parse_spec,
    spec_hash,
)
from engines.lab.sandbox import host_allowed, images_allowed, normalize_hosts
from engines.lab.states import (
    EXECUTION_TERMINAL,
    MISSION_TERMINAL,
    ExperimentStatus,
    HypothesisStatus,
    assert_transition,
)

log = structlog.get_logger("aegis.lab.experiments")

X = ExperimentStatus
VERSIONABLE_STATUSES = frozenset({X.DRAFT, X.FAILED, X.COMPLETED})
REVALIDATABLE_STATUSES = frozenset({X.DRAFT, X.QUEUED, X.FAILED, X.COMPLETED})
EXECUTABLE_STATUSES = frozenset({X.QUEUED, X.COMPLETED})
PATH_TO_DRAFT: dict[str, tuple[str, ...]] = {
    X.DRAFT: (),
    X.QUEUED: (X.DRAFT,),
    X.FAILED: (X.DRAFT,),
    X.COMPLETED: (X.QUEUED, X.DRAFT),
}
MAX_PLATFORM_FINDINGS = 20
GPU_TYPE_PATTERN = r"^[a-z0-9][a-z0-9.-]{0,47}$"
PROTECTED_BASELINE_FIELDS = frozenset(
    {"kind", "baseline", "hypothesis_id", "seeds", "statistical_plan", "metrics", "ablations", "sensitivity"}
)
BASELINE_OVERRIDES_KEY = "baseline_overrides"
EXECUTE_WORKFLOW_KIND = "ExperimentWorkflow"
ARCHIVE_AUDIT_ACTION = "EXPERIMENT_ARCHIVED"


# =============================================================================================
# Loading helpers
# =============================================================================================
def load_experiment(db: Session, actor: Actor, experiment_id: uuid.UUID | str, *permissions: str) -> Experiment:
    experiment = get_owned(db, Experiment, experiment_id, actor, label="Experiment")
    load_project(db, actor, experiment.project_id, *permissions)
    return experiment


def get_experiment(db: Session, actor: Actor, experiment_id: uuid.UUID | str) -> Experiment:
    return load_experiment(db, actor, experiment_id, "experiment:read")


def current_version(db: Session, experiment: Experiment) -> ExperimentVersion | None:
    if experiment.current_version_id is None:
        return None
    version = db.get(ExperimentVersion, experiment.current_version_id)
    if version is None or version.organization_id != experiment.organization_id:
        return None
    return version


def require_current_version(db: Session, experiment: Experiment) -> ExperimentVersion:
    version = current_version(db, experiment)
    if version is None:
        raise Conflict("The experiment has no version", code="experiment_without_version")
    return version


def version_spec(version: ExperimentVersion) -> ExperimentSpec:
    return parse_spec(version.spec)


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def set_status(db: Session, actor: Actor, experiment: Experiment, target: str, reason: str | None) -> None:
    if experiment.status == target:
        return
    assert_transition("experiment", experiment.status, target)
    experiment.status = target
    experiment.status_reason = reason[:2000] if reason else None
    db.flush()


def _emit(db: Session, actor: Actor, experiment: Experiment, event_type: str, payload: dict[str, Any]) -> None:
    emit(
        db,
        organization_id=experiment.organization_id,
        type=event_type,
        payload={"experiment_id": str(experiment.id), "status": experiment.status, **payload},
        mission_id=experiment.mission_id,
        project_id=experiment.project_id,
        workspace_id=experiment.workspace_id,
        subject_type="experiment",
        subject_id=experiment.id,
        actor=actor,
    )


# =============================================================================================
# Validation
# =============================================================================================
def compute_prices() -> ComputePrices:
    settings = get_settings()
    return ComputePrices.from_values(
        settings.execution_cpu_price_per_hour_usd,
        settings.execution_memory_gb_price_per_hour_usd,
        settings.gpu_prices,
    )


def _limits(db: Session, organization_id: uuid.UUID) -> tuple[ValidationLimits, Any, list[str] | None]:
    from aegis_api.lab.execution.service import execution_limits

    sandbox_limits, org_images = execution_limits(db, organization_id)
    policy = dict(get_org_settings(db, organization_id).execution_policy or {})
    limits = ValidationLimits(
        max_cpu=float(sandbox_limits.max_cpu),
        max_memory_mb=int(sandbox_limits.max_memory_mb),
        max_disk_mb=int(sandbox_limits.max_disk_mb),
        max_timeout_seconds=int(sandbox_limits.max_timeout_seconds),
        gpu_enabled=bool(sandbox_limits.gpu_allowed),
        allowed_images=[],  # checked with the sandbox's exact/prefix semantics below
        strict=bool(policy.get("require_digest_pin") or policy.get("strict_reproducibility")),
    )
    return limits, sandbox_limits, org_images


def known_evaluators(db: Session, organization_id: uuid.UUID) -> set[str]:
    keys = set(BUILTIN_EVALUATOR_KEYS)
    rows = db.scalars(
        select(LabEvaluator.key).where(
            or_(LabEvaluator.organization_id == organization_id, LabEvaluator.organization_id.is_(None)),
            LabEvaluator.status == "active",
        )
    ).all()
    keys.update(str(k) for k in rows)
    return keys


def _dataset_context(
    db: Session, actor: Actor, project: Project, spec: ExperimentSpec
) -> tuple[dict[str, DatasetVersionInfo], dict[str, DatasetVersion]]:
    infos: dict[str, DatasetVersionInfo] = {}
    rows: dict[str, DatasetVersion] = {}
    for use in spec.datasets:
        if use.dataset_version_id in infos:
            continue
        version_id = _uuid_or_none(use.dataset_version_id)
        row = db.get(DatasetVersion, version_id) if version_id is not None else None
        if row is None or row.organization_id != actor.organization_id or row.project_id != project.id:
            continue  # unknown to the validator → UNKNOWN_DATASET
        splits: dict[str, SplitInfo] = {}
        for name, info in (row.splits or {}).items():
            visibility = info.get("visibility") if isinstance(info, dict) else None
            splits[str(name)] = SplitInfo(
                visibility="evaluator_only" if visibility == "evaluator_only" else "experiment"
            )
        infos[use.dataset_version_id] = DatasetVersionInfo(splits=splits)
        rows[use.dataset_version_id] = row
    return infos, rows


def _issue(code: str, severity: Literal["error", "warning"], field: str, message: str, suggestion: str) -> Issue:
    return Issue(code=code, severity=severity, field=field, message=message, suggestion=suggestion)


def merge_report(report: ValidationReport, extra: Sequence[Issue]) -> ValidationReport:
    """The validator's report plus platform issues, with ``passed`` and the summary recomputed."""
    issues = [*report.issues, *extra]
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    if not issues:
        summary = "Design passed validation with no issues."
    else:
        codes = sorted({i.code for i in issues})
        state = "failed" if errors else "passed with warnings"
        summary = f"Design {state}: {len(errors)} error(s), {len(warnings)} warning(s) ({', '.join(codes)})."
    return ValidationReport(
        passed=not errors, issues=issues, summary=summary, validator_version=report.validator_version
    )


def _code_quality(spec: ExperimentSpec) -> dict[str, Any] | None:
    if not spec.code.files:
        return None
    result = CodeQualityEvaluator().evaluate(
        ExperimentView(spec=spec), None, EvalContext(config={"seed_env_var": spec.reproducibility.seed_env_var})
    )
    return result.model_dump(mode="json")


@dataclass
class PreparedVersion:
    spec: ExperimentSpec
    report: ValidationReport
    code_snapshot: CodeSnapshot | None
    environment: ExecutionEnvironment
    delivery: code_module.SeedDelivery
    command: list[str]
    expected_cost: Decimal
    cost_basis: str
    baseline_experiment: Experiment | None
    code_quality: dict[str, Any] | None


def _normalise_spec(
    db: Session,
    actor: Actor,
    project: Project,
    experiment: Experiment | None,
    spec: ExperimentSpec,
    *,
    hypothesis: Hypothesis | None,
    explicit_baseline: Experiment | None,
) -> tuple[ExperimentSpec, Experiment | None, list[Issue]]:
    """Fill ``hypothesis_id`` / baseline references and resolve the baseline experiment (ownership-checked)."""
    issues: list[Issue] = []
    data = spec.model_dump(mode="json")
    if hypothesis is not None:
        if spec.hypothesis_id is not None and spec.hypothesis_id != str(hypothesis.id):
            raise ValidationFailed("spec.hypothesis_id does not match hypothesis_id")
        data["hypothesis_id"] = str(hypothesis.id)
    elif spec.hypothesis_id is not None:
        raise ValidationFailed("spec.hypothesis_id is set but the experiment is not linked to that hypothesis")
    baseline: Experiment | None = None
    if explicit_baseline is not None:
        if spec.baseline.kind == "reference_values":
            raise ValidationFailed("baseline_experiment_id conflicts with a reference_values baseline in the spec")
        if spec.baseline.kind == "experiment" and spec.baseline.experiment_id not in (None, str(explicit_baseline.id)):
            raise ValidationFailed("spec.baseline.experiment_id does not match baseline_experiment_id")
        data["baseline"] = {**data["baseline"], "kind": "experiment", "experiment_id": str(explicit_baseline.id)}
        baseline = explicit_baseline
    elif spec.baseline.kind == "experiment" and spec.baseline.experiment_id:
        ref = _uuid_or_none(spec.baseline.experiment_id)
        row = db.get(Experiment, ref) if ref is not None else None
        if row is None or row.organization_id != actor.organization_id or row.project_id != project.id:
            issues.append(
                _issue(
                    "MISSING_BASELINE",
                    "error",
                    "baseline.experiment_id",
                    f"baseline experiment {spec.baseline.experiment_id!r} does not exist in this project",
                    "reference an experiment of the same project (or create one with ensure_baseline)",
                )
            )
        elif experiment is not None and row.id == experiment.id:
            issues.append(
                _issue(
                    "MISSING_BASELINE",
                    "error",
                    "baseline.experiment_id",
                    "an experiment cannot be its own baseline",
                    "reference a separate baseline experiment",
                )
            )
        else:
            baseline = row
    if baseline is not None and baseline.status == X.ARCHIVED:
        issues.append(
            _issue(
                "MISSING_BASELINE",
                "warning",
                "baseline.experiment_id",
                "the baseline experiment is archived",
                "use an active baseline experiment",
            )
        )
    return parse_spec(data), baseline, issues


def _platform_issues(
    db: Session,
    actor: Actor,
    project: Project,
    spec: ExperimentSpec,
    *,
    sandbox_limits: Any,
    org_images: list[str] | None,
    delivery: code_module.SeedDelivery,
    snapshot_problem: list[str],
    code_quality: dict[str, Any] | None,
) -> list[Issue]:
    from aegis_api.lab.execution.archive import normalize_relative_path

    settings = get_settings()
    issues: list[Issue] = []
    image = spec.environment.image
    if not images_allowed(image, settings.execution_allowed_image_list, org_images):
        allowed = ", ".join(settings.execution_allowed_image_list) or "(none)"
        issues.append(
            _issue(
                "IMAGE_NOT_ALLOWED",
                "error",
                "environment.image",
                f"image {image!r} is not allowed for sandboxed execution in this organization",
                f"use one of: {allowed}",
            )
        )
    if spec.network.mode == "allowlist":
        if not sandbox_limits.egress_available:
            issues.append(
                _issue(
                    "NETWORK_REQUESTED",
                    "error",
                    "network.mode",
                    "network egress is not available in this deployment (no egress proxy is configured)",
                    "use network.mode='none'",
                )
            )
        try:
            hosts = normalize_hosts(spec.network.hosts)
        except ValueError as exc:
            issues.append(_issue("NETWORK_REQUESTED", "error", "network.hosts", str(exc), "use bare host names"))
        else:
            denied = [h for h in hosts if not host_allowed(h, sandbox_limits.egress_allowlist)]
            if denied:
                issues.append(
                    _issue(
                        "NETWORK_REQUESTED",
                        "error",
                        "network.hosts",
                        f"hosts not in the organization egress allowlist: {', '.join(sorted(denied))}",
                        "ask an administrator to allowlist them or remove them",
                    )
                )
    gpu_type = spec.resources.gpu_type
    if gpu_type:
        import re

        if not re.match(GPU_TYPE_PATTERN, gpu_type):
            issues.append(
                _issue(
                    "IMPOSSIBLE_RESOURCES",
                    "error",
                    "resources.gpu_type",
                    f"GPU type {gpu_type!r} is not a valid sandbox GPU type",
                    "use a lower-case GPU type such as 'nvidia-a100'",
                )
            )
    if spec.resources.memory_mb < sandbox_limits.min_memory_mb:
        issues.append(
            _issue(
                "IMPOSSIBLE_RESOURCES",
                "error",
                "resources.memory_mb",
                f"memory below the sandbox minimum of {sandbox_limits.min_memory_mb} MB",
                "request more memory",
            )
        )
    seen_paths: dict[str, int] = {}
    for i, use in enumerate(spec.datasets):
        try:
            path = normalize_relative_path(f"input/{use.effective_mount_path}", allowed_roots=("input",))
        except ValueError as exc:
            issues.append(
                _issue("UNSAFE_MOUNT_PATH", "error", f"datasets.{i}.mount_path", str(exc), "use a simple relative path")
            )
            continue
        if path == "input/params.json":
            issues.append(
                _issue(
                    "UNSAFE_MOUNT_PATH",
                    "error",
                    f"datasets.{i}.mount_path",
                    "params.json is reserved for run parameters",
                    "mount the dataset elsewhere",
                )
            )
        seen_paths[path] = i
    for problem in snapshot_problem:
        issues.append(
            _issue("UNSAFE_MOUNT_PATH", "error", "code.files", problem, "give every inline file a distinct path")
        )
    code = spec.code
    if code.code_snapshot_id is not None:
        ref = _uuid_or_none(code.code_snapshot_id)
        snap = db.get(CodeSnapshot, ref) if ref is not None else None
        if snap is None or snap.organization_id != actor.organization_id or snap.project_id != project.id:
            issues.append(
                _issue(
                    "MISSING_REPRODUCIBILITY",
                    "error",
                    "code.code_snapshot_id",
                    f"code snapshot {code.code_snapshot_id!r} does not exist in this project",
                    "reference a snapshot of this project or provide the files inline",
                )
            )
        elif not snap.storage_key:
            issues.append(
                _issue(
                    "MISSING_REPRODUCIBILITY",
                    "error",
                    "code.code_snapshot_id",
                    "the referenced code snapshot has no stored files",
                    "provide the files inline",
                )
            )
        elif code.entrypoint and not code.files and code.entrypoint not in (snap.files_manifest or {}):
            issues.append(
                _issue(
                    "MISSING_REPRODUCIBILITY",
                    "error",
                    "code.entrypoint",
                    f"entrypoint {code.entrypoint!r} is not part of the code snapshot",
                    "fix the entrypoint path",
                )
            )
    elif code.git is not None and not code.files:
        issues.append(
            _issue(
                "MISSING_REPRODUCIBILITY",
                "error",
                "code.git",
                "the platform does not fetch git repositories into the network-isolated sandbox",
                f"provide the files of commit {code.git.commit} inline (code.files) or as a code snapshot",
            )
        )
    if code_quality is not None:
        findings = [f for f in (code_quality.get("details") or {}).get("findings") or [] if isinstance(f, dict)]
        errors = [f for f in findings if f.get("severity") == "error"]
        for finding in errors[:MAX_PLATFORM_FINDINGS]:
            issues.append(
                _issue(
                    "CODE_QUALITY",
                    "error",
                    f'code.files["{finding.get("file")}"]',
                    f"line {finding.get('line')}: {finding.get('code')}: {str(finding.get('message'))[:300]}",
                    "fix the code: no process execution, forbidden imports, shell escapes or parse errors",
                )
            )
        warnings = [f for f in findings if f.get("severity") == "warning"]
        if warnings:
            codes = sorted({str(f.get("code")) for f in warnings})
            issues.append(
                _issue(
                    "CODE_QUALITY",
                    "warning",
                    "code.files",
                    f"{len(warnings)} static-analysis warning(s): {', '.join(codes)}",
                    "review the code-quality findings in the version's reproducibility record",
                )
            )
    if not delivery.seed_env_var_injected or delivery.ignored_flags:
        issues.append(
            _issue(
                "MISSING_REPRODUCIBILITY",
                "warning",
                "reproducibility",
                "; ".join(delivery.notes)[:1000],
                f"read the seed from /workspace/input/params.json ('{code_module.SEED_PARAMETER}')",
            )
        )
    return issues


def prepare_version(
    db: Session,
    actor: Actor,
    project: Project,
    spec: ExperimentSpec,
    *,
    experiment: Experiment | None,
    hypothesis: Hypothesis | None,
    explicit_baseline: Experiment | None = None,
    agent_run_id: uuid.UUID | None = None,
    code_source: str = "inline",
) -> PreparedVersion:
    """Normalise the spec, store its code and environment, and validate it (nothing is inserted as a version)."""
    spec, baseline, baseline_issues = _normalise_spec(
        db, actor, project, experiment, spec, hypothesis=hypothesis, explicit_baseline=explicit_baseline
    )
    delivery = code_module.seed_delivery(spec)
    snapshot: CodeSnapshot | None = None
    snapshot_problems: list[str] = []
    if spec.code.files:
        bundle = code_module.bundle_code(spec.code.files, entrypoint=spec.code.entrypoint or "", git=spec.code.git)
        # Unsafe paths are reported by the design validator; collisions are a platform finding.
        snapshot_problems = [p for p in bundle.problems if "collides" in p]
        if not bundle.problems:
            snapshot = code_module.ensure_code_snapshot(
                db,
                actor,
                project,
                files=spec.code.files,
                entrypoint=spec.code.entrypoint or "",
                git=spec.code.git,
                source=code_source,
                agent_run_id=agent_run_id,
            )
    elif spec.code.code_snapshot_id is not None:
        ref = _uuid_or_none(spec.code.code_snapshot_id)
        row = db.get(CodeSnapshot, ref) if ref is not None else None
        if row is not None and row.organization_id == actor.organization_id and row.project_id == project.id:
            snapshot = row
    environment = code_module.ensure_environment(db, actor, spec, delivery)
    limits, sandbox_limits, org_images = _limits(db, actor.organization_id)
    datasets, _rows = _dataset_context(db, actor, project, spec)
    prediction = parse_prediction(hypothesis.measurable_prediction) if hypothesis is not None else None
    context = ValidationContext(
        dataset_versions=datasets,
        hypothesis_metric=prediction.metric if prediction is not None else None,
        known_evaluators=known_evaluators(db, actor.organization_id),
    )
    report = ExperimentDesignValidator(limits).validate(spec, context)
    quality = _code_quality(spec) if not snapshot_problems else None
    extra = baseline_issues + _platform_issues(
        db,
        actor,
        project,
        spec,
        sandbox_limits=sandbox_limits,
        org_images=org_images,
        delivery=delivery,
        snapshot_problem=snapshot_problems,
        code_quality=quality,
    )
    report = merge_report(report, extra)
    per_run, basis = estimate_cost(spec.resources, spec.timeout_seconds, compute_prices())
    runs = max(1, len(set(spec.seeds)))
    return PreparedVersion(
        spec=spec,
        report=report,
        code_snapshot=snapshot,
        environment=environment,
        delivery=delivery,
        command=code_module.effective_command(spec),
        expected_cost=per_run * runs,
        cost_basis=f"{runs} run(s) x {basis}"[:200],
        baseline_experiment=baseline,
        code_quality=quality,
    )


def _next_version_number(db: Session, experiment: Experiment) -> int:
    advisory_xact_lock(db, f"experiment-version:{experiment.id}")
    current = db.scalar(
        select(func.max(ExperimentVersion.version)).where(ExperimentVersion.experiment_id == experiment.id)
    )
    return int(current or 0) + 1


def _insert_version(
    db: Session,
    actor: Actor,
    experiment: Experiment,
    prepared: PreparedVersion,
    *,
    agent_run_id: uuid.UUID | None,
    extra_reproducibility: dict[str, Any] | None = None,
) -> ExperimentVersion:
    spec = prepared.spec
    snapshot = prepared.code_snapshot
    reproducibility: dict[str, Any] = {
        **spec.reproducibility.model_dump(mode="json"),
        "spec_version": EXPERIMENT_SPEC_VERSION,
        "seed_delivery": prepared.delivery.as_dict(),
        "code_hash": snapshot.content_hash if snapshot is not None else None,
        "environment_hash": prepared.environment.content_hash,
        "cost_basis": prepared.cost_basis,
    }
    if prepared.code_quality is not None:
        reproducibility["code_quality"] = {
            "passed": prepared.code_quality.get("passed"),
            "evaluator_version": prepared.code_quality.get("evaluator_version"),
            "metrics": prepared.code_quality.get("metrics"),
            "findings": ((prepared.code_quality.get("details") or {}).get("findings") or [])[:100],
        }
    reproducibility.update(extra_reproducibility or {})
    version = ExperimentVersion(
        organization_id=experiment.organization_id,
        experiment_id=experiment.id,
        version=_next_version_number(db, experiment),
        spec=spec.model_dump(mode="json"),
        spec_hash=spec_hash(spec),
        objective=spec.objective,
        hypothesis_id=_uuid_or_none(spec.hypothesis_id),
        baseline=spec.baseline.model_dump(mode="json"),
        method=spec.method,
        variables=[v.model_dump(mode="json") for v in spec.variables],
        controls=[c.model_dump(mode="json") for c in spec.controls],
        dataset_version_ids=[d.dataset_version_id for d in spec.datasets],
        metrics=[m.model_dump(mode="json") for m in spec.metrics],
        success_criteria=[c.model_dump(mode="json") for c in spec.success_criteria],
        statistical_plan=spec.statistical_plan.model_dump(mode="json"),
        seeds=list(spec.seeds),
        ablations=[a.model_dump(mode="json") for a in spec.ablations],
        environment_id=prepared.environment.id,
        code_snapshot_id=snapshot.id if snapshot is not None else None,
        command=prepared.command,
        resource_request=spec.resources.model_dump(mode="json"),
        timeout_seconds=spec.timeout_seconds,
        expected_cost_usd=prepared.expected_cost,
        reproducibility=reproducibility,
        network_policy=spec.network.model_dump(mode="json"),
        validation_report=prepared.report.model_dump(mode="json"),
        validation_passed=prepared.report.passed,
        created_by_id=actor.user_id,
        created_by_agent_run_id=agent_run_id or actor.agent_run_id,
    )
    db.add(version)
    db.flush()
    experiment.current_version_id = version.id
    if prepared.baseline_experiment is not None:
        experiment.baseline_experiment_id = prepared.baseline_experiment.id
    db.flush()
    append_evidence(
        db,
        organization_id=experiment.organization_id,
        kind="experiment_version",
        title=f"Experiment {experiment.id} version {version.version} registered",
        content={
            "experiment_id": str(experiment.id),
            "version_id": str(version.id),
            "version": version.version,
            "spec_hash": version.spec_hash,
            "code_hash": reproducibility["code_hash"],
            "environment_hash": reproducibility["environment_hash"],
            "dataset_version_ids": list(version.dataset_version_ids),
            "validation_passed": version.validation_passed,
            "validator_version": prepared.report.validator_version,
        },
    )
    return version


def _report_codes(report: dict[str, Any] | ValidationReport) -> dict[str, list[str]]:
    data = report.model_dump(mode="json") if isinstance(report, ValidationReport) else report
    issues = data.get("issues") or []
    return {
        "errors": sorted({str(i.get("code")) for i in issues if i.get("severity") == "error"}),
        "warnings": sorted({str(i.get("code")) for i in issues if i.get("severity") == "warning"}),
    }


def _apply_validation(db: Session, actor: Actor, experiment: Experiment, version: ExperimentVersion) -> None:
    """Drive ``DRAFT → VALIDATING → QUEUED|DRAFT`` for a freshly validated version (legal paths only)."""
    for step in PATH_TO_DRAFT.get(experiment.status, ()):
        set_status(db, actor, experiment, step, f"re-validating version {version.version}")
    if experiment.status != X.DRAFT:
        return
    set_status(db, actor, experiment, X.VALIDATING, f"validating version {version.version}")
    codes = _report_codes(version.validation_report)
    if version.validation_passed:
        set_status(db, actor, experiment, X.QUEUED, f"version {version.version} passed design validation")
    else:
        set_status(
            db,
            actor,
            experiment,
            X.DRAFT,
            f"version {version.version} failed design validation: {', '.join(codes['errors'])}",
        )
    _emit_validated(db, actor, experiment, version)
    if version.validation_passed:
        _mark_hypothesis_designed(db, actor, experiment)


def _emit_validated(db: Session, actor: Actor, experiment: Experiment, version: ExperimentVersion) -> None:
    codes = _report_codes(version.validation_report)
    _emit(
        db,
        actor,
        experiment,
        EventType.EXPERIMENT_VALIDATED,
        {
            "version_id": str(version.id),
            "version": version.version,
            "passed": version.validation_passed,
            "errors": codes["errors"],
            "warnings": codes["warnings"],
            "summary": (version.validation_report or {}).get("summary"),
        },
    )


def _mark_hypothesis_designed(db: Session, actor: Actor, experiment: Experiment) -> None:
    if experiment.hypothesis_id is None or experiment.kind == "baseline":
        return
    hypothesis = db.get(Hypothesis, experiment.hypothesis_id)
    if hypothesis is None:
        return
    if hypothesis.status in (HypothesisStatus.SELECTED, HypothesisStatus.INCONCLUSIVE):
        from aegis_api.lab.hypotheses.service import platform_advance

        platform_advance(
            db,
            actor,
            hypothesis,
            HypothesisStatus.EXPERIMENT_DESIGNED,
            f"validated experiment design (experiment {experiment.id})",
        )


# =============================================================================================
# Create / version / validate
# =============================================================================================
def _mission_in_project(db: Session, actor: Actor, mission_id: uuid.UUID | str, project: Project) -> Mission:
    mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    if mission.project_id != project.id:
        raise ValidationFailed("mission_id belongs to a different project")
    if mission.status in MISSION_TERMINAL:
        raise Conflict(f"Mission is {mission.status}; no new experiments can be added", code="mission_not_active")
    return mission


def _in_project[T](
    db: Session, actor: Actor, model: type[T], row_id: uuid.UUID | str, project: Project, label: str
) -> T:
    row = get_owned(db, model, row_id, actor, label=label)
    if getattr(row, "project_id", None) != project.id:
        raise ValidationFailed(f"{label} belongs to a different project")
    return row


def create_experiment(
    db: Session,
    actor: Actor,
    data: ExperimentCreate,
    *,
    agent_run_id: uuid.UUID | None = None,
    provenance: dict[str, Any] | None = None,
    code_source: str = "inline",
) -> Experiment:
    """Create an experiment and its validated version 1 (``experiment:create``)."""
    project = load_project(db, actor, data.project_id, "experiment:create")
    if data.kind is not None and data.kind != data.spec.kind:
        raise ValidationFailed(f"kind {data.kind!r} does not match spec.kind {data.spec.kind!r}")
    mission = _mission_in_project(db, actor, data.mission_id, project) if data.mission_id else None
    hypothesis: Hypothesis | None = None
    if data.hypothesis_id is not None:
        hypothesis = _in_project(db, actor, Hypothesis, data.hypothesis_id, project, "Hypothesis")
        if hypothesis.status == HypothesisStatus.ARCHIVED:
            raise Conflict("The hypothesis is archived", code="hypothesis_archived")
        if mission is not None and hypothesis.mission_id not in (None, mission.id):
            raise ValidationFailed("The hypothesis belongs to a different mission")
    explicit_baseline = (
        _in_project(db, actor, Experiment, data.baseline_experiment_id, project, "Baseline experiment")
        if data.baseline_experiment_id
        else None
    )
    parent = (
        _in_project(db, actor, Experiment, data.parent_experiment_id, project, "Parent experiment")
        if data.parent_experiment_id
        else None
    )
    run_id = agent_run_id or actor.agent_run_id
    strategy_version_id = _uuid_or_none((provenance or {}).get("strategy_version_id"))
    experiment = Experiment(
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=mission.id if mission else None,
        hypothesis_id=hypothesis.id if hypothesis else None,
        title=data.title.strip(),
        kind=data.spec.kind,
        status=X.DRAFT,
        parent_experiment_id=parent.id if parent else None,
        strategy_version_id=strategy_version_id,
        retry_count=0,
        max_retries=data.max_retries,
        created_by_id=actor.user_id,
        created_by_agent_run_id=run_id,
    )
    db.add(experiment)
    db.flush()
    prepared = prepare_version(
        db,
        actor,
        project,
        data.spec,
        experiment=experiment,
        hypothesis=hypothesis,
        explicit_baseline=explicit_baseline,
        agent_run_id=run_id,
        code_source=code_source,
    )
    version = _insert_version(
        db, actor, experiment, prepared, agent_run_id=run_id, extra_reproducibility={"provenance": provenance or {}}
    )
    audit(
        db,
        actor,
        AuditAction.EXPERIMENT_CREATED,
        "experiment",
        experiment.id,
        after={
            "title": experiment.title,
            "kind": experiment.kind,
            "version": version.version,
            "spec_hash": version.spec_hash,
            "validation_passed": version.validation_passed,
            "agent_run_id": str(run_id) if run_id else None,
        },
    )
    _emit(
        db,
        actor,
        experiment,
        EventType.EXPERIMENT_CREATED,
        {
            "title": experiment.title,
            "kind": experiment.kind,
            "version_id": str(version.id),
            "spec_hash": version.spec_hash,
            "hypothesis_id": str(experiment.hypothesis_id) if experiment.hypothesis_id else None,
        },
    )
    _apply_validation(db, actor, experiment, version)
    return experiment


def create_version(
    db: Session,
    actor: Actor,
    experiment_id: uuid.UUID | str,
    spec: ExperimentSpec,
    *,
    change_summary: str | None = None,
    agent_run_id: uuid.UUID | None = None,
    provenance: dict[str, Any] | None = None,
    code_source: str = "inline",
) -> ExperimentVersion:
    """Add a new immutable version (never overwrites) and re-validate. Allowed from DRAFT, FAILED or COMPLETED."""
    experiment = load_experiment(db, actor, experiment_id, "experiment:create")
    if experiment.status not in VERSIONABLE_STATUSES:
        raise Conflict(
            f"A new version cannot be created while the experiment is {experiment.status} "
            "(allowed: DRAFT, FAILED, COMPLETED)",
            code="experiment_not_versionable",
        )
    if spec.kind != experiment.kind:
        raise ValidationFailed(f"spec.kind must remain {experiment.kind!r}")
    project = load_project(db, actor, experiment.project_id)
    hypothesis = db.get(Hypothesis, experiment.hypothesis_id) if experiment.hypothesis_id else None
    explicit_baseline: Experiment | None = None
    if spec.baseline.kind == "experiment" and not spec.baseline.experiment_id and experiment.baseline_experiment_id:
        # The spec asks for an experiment baseline without naming it: keep the experiment's linked baseline.
        explicit_baseline = db.get(Experiment, experiment.baseline_experiment_id)
    run_id = agent_run_id or actor.agent_run_id
    previous = current_version(db, experiment)
    prepared = prepare_version(
        db,
        actor,
        project,
        spec,
        experiment=experiment,
        hypothesis=hypothesis,
        explicit_baseline=explicit_baseline,
        agent_run_id=run_id,
        code_source=code_source,
    )
    extra: dict[str, Any] = {"provenance": provenance or {}, "change_summary": change_summary}
    if previous is not None:
        extra["previous_version_id"] = str(previous.id)
        extra["diff_from_previous"] = diff_specs(previous.spec, prepared.spec)
    version = _insert_version(db, actor, experiment, prepared, agent_run_id=run_id, extra_reproducibility=extra)
    if prepared.baseline_experiment is None and prepared.spec.baseline.kind != "experiment":
        experiment.baseline_experiment_id = None
    _apply_validation(db, actor, experiment, version)
    return version


@dataclass
class ValidationOutcome:
    experiment: Experiment
    version: ExperimentVersion
    report: dict[str, Any]
    new_version_created: bool


def _same_outcome(stored: dict[str, Any], fresh: ValidationReport) -> bool:
    def key(issues: Sequence[dict[str, Any]]) -> set[tuple[str, str, str]]:
        return {(str(i.get("code")), str(i.get("severity")), str(i.get("field"))) for i in issues}

    fresh_data = fresh.model_dump(mode="json")
    return bool(stored.get("passed")) == fresh.passed and key(stored.get("issues") or []) == key(fresh_data["issues"])


def _active_runs(db: Session, version_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count(ExperimentRun.id)).where(
                ExperimentRun.experiment_version_id == version_id,
                ExperimentRun.status.not_in(list(EXECUTION_TERMINAL)),
            )
        )
        or 0
    )


def validate_experiment(db: Session, actor: Actor, experiment_id: uuid.UUID | str) -> ValidationOutcome:
    """Re-run validation on the current version's spec.

    Versions are immutable: an unchanged outcome only drives the status (DRAFT → VALIDATING → QUEUED/DRAFT;
    QUEUED → DRAFT when it no longer passes). A changed outcome (e.g. a dataset or limit changed) is recorded as a
    new version with the same spec and the fresh report — when no run of the current version is active.
    """
    experiment = load_experiment(db, actor, experiment_id, "experiment:create")
    version = require_current_version(db, experiment)
    project = load_project(db, actor, experiment.project_id)
    hypothesis = db.get(Hypothesis, experiment.hypothesis_id) if experiment.hypothesis_id else None
    prepared = prepare_version(db, actor, project, version_spec(version), experiment=experiment, hypothesis=hypothesis)
    created = False
    if (
        not _same_outcome(version.validation_report or {}, prepared.report)
        and experiment.status in REVALIDATABLE_STATUSES
        and _active_runs(db, version.id) == 0
    ):
        version = _insert_version(
            db,
            actor,
            experiment,
            prepared,
            agent_run_id=None,
            extra_reproducibility={"revalidation_of": str(version.id), "provenance": {}},
        )
        created = True
    if experiment.status in (X.DRAFT, X.FAILED) or (experiment.status == X.QUEUED and not version.validation_passed):
        _apply_validation(db, actor, experiment, version)
    else:
        _emit_validated(db, actor, experiment, version)
    return ValidationOutcome(
        experiment=experiment,
        version=version,
        report=prepared.report.model_dump(mode="json") if not created else dict(version.validation_report),
        new_version_created=created,
    )


# =============================================================================================
# Queries
# =============================================================================================
EXPERIMENT_SORTS = {
    "-created_at": ("created_at", True),
    "created_at": ("created_at", False),
    "-updated_at": ("updated_at", True),
    "updated_at": ("updated_at", False),
}


def list_experiments(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    project_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    hypothesis_id: uuid.UUID | str | None = None,
    status: str | None = None,
    kind: str | None = None,
    sort: str | None = None,
    mapper: Callable[[Experiment], Any] | None = None,
) -> CursorPage[Any]:
    stmt = select(Experiment).where(Experiment.organization_id == actor.organization_id)
    if project_id is not None:
        project = load_project(db, actor, project_id, "experiment:read")
        stmt = stmt.where(Experiment.project_id == project.id)
    else:
        actor.require("experiment:read")
        visible = visible_project_ids(db, actor)
        if visible is not None:
            stmt = stmt.where(Experiment.project_id.in_(visible))
    if mission_id is not None:
        stmt = stmt.where(Experiment.mission_id == mission_id)
    if hypothesis_id is not None:
        stmt = stmt.where(Experiment.hypothesis_id == hypothesis_id)
    if status is not None:
        if status.upper() not in {s.value for s in ExperimentStatus}:
            raise ValidationFailed(f"Unknown experiment status {status!r}")
        stmt = stmt.where(Experiment.status == status.upper())
    if kind is not None:
        stmt = stmt.where(Experiment.kind == kind)
    choice = EXPERIMENT_SORTS.get(sort or "-created_at")
    if choice is None:
        raise ValidationFailed(f"Unsupported sort {sort!r}. Allowed: {', '.join(EXPERIMENT_SORTS)}")
    column, descending = choice
    return paginate_keyset(
        db,
        stmt,
        params,
        time_col=getattr(Experiment, column),
        id_col=Experiment.id,
        mapper=mapper or (lambda e: e),
        descending=descending,
    )


def list_versions(
    db: Session,
    actor: Actor,
    experiment_id: uuid.UUID | str,
    params: PageParams,
    *,
    mapper: Callable[[ExperimentVersion], Any] | None = None,
) -> Page[Any]:
    experiment = get_experiment(db, actor, experiment_id)
    stmt = (
        select(ExperimentVersion)
        .where(ExperimentVersion.experiment_id == experiment.id)
        .order_by(ExperimentVersion.version.desc())
    )
    return paginate(db, stmt, params, mapper or (lambda v: v))


def list_for_mission(
    db: Session, actor: Actor, mission_id: uuid.UUID | str, statuses: Sequence[str] | None = None
) -> list[Experiment]:
    mission = get_owned(db, Mission, mission_id, actor, label="Mission")
    load_project(db, actor, mission.project_id, "experiment:read")
    stmt = select(Experiment).where(Experiment.mission_id == mission.id)
    if statuses:
        wanted = [str(s).upper() for s in statuses]
        unknown = set(wanted) - {s.value for s in ExperimentStatus}
        if unknown:
            raise ValidationFailed(f"Unknown experiment status(es): {', '.join(sorted(unknown))}")
        stmt = stmt.where(Experiment.status.in_(wanted))
    return list(db.scalars(stmt.order_by(Experiment.created_at, Experiment.id)))


def archive_experiment(
    db: Session, actor: Actor, experiment_id: uuid.UUID | str, reason: str | None = None
) -> Experiment:
    experiment = load_experiment(db, actor, experiment_id, "experiment:create")
    if experiment.current_version_id is not None and _active_runs(db, experiment.current_version_id):
        raise Conflict("Cancel the experiment's active runs before archiving it", code="experiment_has_active_runs")
    assert_transition("experiment", experiment.status, X.ARCHIVED)
    before = experiment.status
    experiment.status = X.ARCHIVED
    experiment.status_reason = (reason or "archived").strip()[:2000]
    db.flush()
    audit(
        db,
        actor,
        ARCHIVE_AUDIT_ACTION,
        "experiment",
        experiment.id,
        before={"status": before},
        after={"status": X.ARCHIVED, "reason": experiment.status_reason},
    )
    return experiment


# =============================================================================================
# Baselines
# =============================================================================================
def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def derive_baseline_spec(candidate: ExperimentSpec) -> ExperimentSpec:
    """The baseline arm of a candidate: the candidate spec with ``parameters.baseline_overrides`` applied.

    ``baseline_overrides`` is a partial spec (e.g. ``{"parameters": {"method": "standard"}}`` or new
    ``success_criteria``). Measurement fields (metrics, seeds, statistical plan) and identity fields cannot be
    overridden so the two arms stay comparable; criteria relative to a baseline are dropped for the baseline itself.
    The resulting config difference is recorded by every comparison (``diff_specs``).
    """
    overrides = candidate.parameters.get(BASELINE_OVERRIDES_KEY)
    if not isinstance(overrides, dict) or not overrides:
        raise ValidationFailed(
            "The candidate does not describe its baseline configuration",
            code="baseline_undefined",
            details={
                "guidance": f"set spec.parameters.{BASELINE_OVERRIDES_KEY} to the partial spec that turns the candidate "
                "into its baseline, e.g. {'parameters': {'method': 'standard'}}",
            },
        )
    protected = sorted(PROTECTED_BASELINE_FIELDS & set(overrides))
    if protected:
        raise ValidationFailed(
            f"{BASELINE_OVERRIDES_KEY} may not change {', '.join(protected)} (both arms must be measured identically)"
        )
    data = candidate.model_dump(mode="json")
    params = {k: v for k, v in data["parameters"].items() if k != BASELINE_OVERRIDES_KEY}
    patch = dict(overrides)
    params = _deep_merge(params, patch.pop("parameters", {}) if isinstance(patch.get("parameters"), dict) else {})
    data = _deep_merge(data, patch)
    data["parameters"] = params
    data["kind"] = "baseline"
    data["baseline"] = BaselineSpec().model_dump(mode="json")
    data["ablations"] = []
    data["sensitivity"] = []
    data["objective"] = f"Baseline for: {candidate.objective}"[:4000]
    if "success_criteria" not in overrides:
        data["success_criteria"] = [
            c
            for c in data["success_criteria"]
            if c.get("relative_to") != "baseline" and c.get("comparator") not in BASELINE_ONLY_COMPARATORS
        ]
    try:
        return ExperimentSpec.model_validate(data)
    except ValidationError as exc:
        raise ValidationFailed(
            f"{BASELINE_OVERRIDES_KEY} does not produce a valid specification",
            details={"errors": exc.errors(include_url=False, include_context=False)},
        ) from exc


def ensure_baseline(
    db: Session,
    actor: Actor,
    *,
    mission_id: uuid.UUID | str | None,
    candidate_experiment_id: uuid.UUID | str,
    hypothesis_id: uuid.UUID | str | None = None,
) -> tuple[Experiment | None, bool]:
    """Return the candidate's baseline experiment, creating it (kind ``baseline``) from the candidate spec's
    ``baseline_overrides`` when the candidate needs an experiment baseline and none exists → ``(baseline, created)``.

    The candidate is linked to the baseline (and receives a new version referencing it when its spec still lacks
    the reference). ``(None, False)`` when the candidate compares against reference values or nothing.
    """
    candidate = load_experiment(db, actor, candidate_experiment_id, "experiment:create")
    if mission_id is not None and candidate.mission_id is not None and str(candidate.mission_id) != str(mission_id):
        raise ValidationFailed("The candidate experiment belongs to a different mission")
    version = require_current_version(db, candidate)
    spec = version_spec(version)
    if spec.baseline.kind != "experiment":
        return None, False
    advisory_xact_lock(db, f"experiment-baseline:{candidate.id}")
    existing: Experiment | None = None
    if candidate.baseline_experiment_id is not None:
        row = db.get(Experiment, candidate.baseline_experiment_id)
        if row is not None and row.status != X.ARCHIVED and row.project_id == candidate.project_id:
            existing = row
    if existing is None and spec.baseline.experiment_id:
        ref = _uuid_or_none(spec.baseline.experiment_id)
        row = db.get(Experiment, ref) if ref is not None else None
        if (
            row is not None
            and row.organization_id == candidate.organization_id
            and row.project_id == candidate.project_id
            and row.status != X.ARCHIVED
        ):
            existing = row
    if existing is None:
        existing = db.scalars(
            select(Experiment)
            .where(
                Experiment.parent_experiment_id == candidate.id,
                Experiment.kind == "baseline",
                Experiment.status != X.ARCHIVED,
            )
            .order_by(Experiment.created_at)
            .limit(1)
        ).first()
    created = False
    if existing is None:
        baseline_spec = derive_baseline_spec(spec)
        hyp_id = _uuid_or_none(str(hypothesis_id)) if hypothesis_id else candidate.hypothesis_id
        existing = create_experiment(
            db,
            actor,
            ExperimentCreate(
                project_id=candidate.project_id,
                mission_id=candidate.mission_id,
                hypothesis_id=hyp_id,
                title=f"Baseline: {candidate.title}"[:300],
                kind="baseline",
                spec=baseline_spec,
                parent_experiment_id=candidate.id,
                max_retries=candidate.max_retries,
            ),
            provenance={"derived_from": str(candidate.id), "derived_from_version": str(version.id)},
        )
        created = True
    candidate.baseline_experiment_id = existing.id
    db.flush()
    if spec.baseline.experiment_id != str(existing.id) and candidate.status in VERSIONABLE_STATUSES:
        data = spec.model_dump(mode="json")
        data["baseline"] = {**data["baseline"], "kind": "experiment", "experiment_id": str(existing.id)}
        create_version(
            db,
            actor,
            candidate.id,
            parse_spec(data),
            change_summary=f"linked baseline experiment {existing.id}",
        )
    return existing, created


# =============================================================================================
# Execution requests
# =============================================================================================
def _policy_context(
    experiment: Experiment, version: ExperimentVersion, actor: Actor, mission: Mission | None
) -> dict[str, Any]:
    network = version.network_policy or {}
    resources = version.resource_request or {}
    hosts: list[str] = []
    try:
        hosts = normalize_hosts(network.get("hosts") or []) if network.get("mode") == "allowlist" else []
    except ValueError:
        hosts = list(network.get("hosts") or [])
    context: dict[str, Any] = {
        "autonomy_level": actor.autonomy_level or (mission.autonomy_level if mission is not None else None),
        "risk_level": mission.risk_level if mission is not None else None,
        "estimated_cost_usd": float(version.expected_cost_usd or 0),
        "network_mode": network.get("mode", "none"),
        "egress_hosts": hosts,
        "gpu_count": int(resources.get("gpu_count") or 0),
        "secrets_requested": [],
        "actor_kind": actor.kind,
        "is_human": actor.is_human,
        "production": False,
    }
    return {k: v for k, v in context.items() if v is not None}


def _workflow_key(db: Session, experiment: Experiment, version: ExperimentVersion) -> tuple[WorkflowRun | None, str]:
    runs = db.scalars(
        select(WorkflowRun)
        .where(
            WorkflowRun.organization_id == experiment.organization_id,
            WorkflowRun.kind == EXECUTE_WORKFLOW_KIND,
            WorkflowRun.subject_type == "experiment",
            WorkflowRun.subject_id == str(experiment.id),
        )
        .order_by(WorkflowRun.created_at)
    ).all()
    from aegis_api.lab.workflows.runs import TERMINAL_STATUSES

    active = [r for r in runs if r.status not in TERMINAL_STATUSES]
    if active:
        return active[-1], ""
    return None, f"v{version.version}-{len(runs) + 1}"


@dataclass
class ExecutionRequest:
    experiment: Experiment
    version: ExperimentVersion
    status: str
    workflow_run_id: uuid.UUID | None = None
    approval_id: uuid.UUID | None = None
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": str(self.experiment.id),
            "version_id": str(self.version.id),
            "workflow_run_id": str(self.workflow_run_id) if self.workflow_run_id else None,
            "status": self.status,
            "approval_id": str(self.approval_id) if self.approval_id else None,
            "reasons": list(self.reasons),
        }


def request_execution(db: Session, actor: Actor, experiment_id: uuid.UUID | str) -> ExecutionRequest:
    """Govern and launch the ``ExperimentWorkflow`` for the current version (``experiment:execute``).

    Returns ``awaiting_approval`` (with the approval id) when policy requires a human decision; the workflow is
    launched by the approval hook (or a repeated request) once approved. Repeated requests while a run is active
    return that run.
    """
    experiment = load_experiment(db, actor, experiment_id, "experiment:execute")
    version = require_current_version(db, experiment)
    if not version.validation_passed:
        raise Conflict(
            "The current version has not passed design validation",
            code="experiment_not_validated",
            details={"summary": (version.validation_report or {}).get("summary")},
        )
    active, key = _workflow_key(db, experiment, version)
    if active is not None:
        return ExecutionRequest(experiment, version, status=str(active.status), workflow_run_id=active.id)
    if experiment.status not in EXECUTABLE_STATUSES:
        raise Conflict(
            f"An experiment in status {experiment.status} cannot be executed (QUEUED or COMPLETED required)",
            code="invalid_state_transition",
        )
    mission = db.get(Mission, experiment.mission_id) if experiment.mission_id else None
    if mission is not None and mission.status in MISSION_TERMINAL:
        raise Conflict(f"Mission is {mission.status}", code="mission_not_active")
    from aegis_api.lab.governance.approvals import is_approved, request_approval
    from aegis_api.lab.governance.policies import evaluate_policy

    decision = evaluate_policy(
        db,
        actor,
        "experiment.execute",
        _policy_context(experiment, version, actor, mission),
        project_id=experiment.project_id,
        mission=mission,
    )
    reasons = tuple(str(r) for r in (decision.reasons or ()))
    if decision.effect == "deny":
        raise PolicyDenied(
            "Experiment execution denied by governance policy: " + ("; ".join(reasons) or "denied"),
            details={"reasons": list(reasons)},
        )
    if decision.effect == "require_approval" and not is_approved(
        db, experiment.organization_id, "experiment.execute", "experiment", str(experiment.id)
    ):
        approval = request_approval(
            db,
            actor,
            action="experiment.execute",
            subject_type="experiment",
            subject_id=str(experiment.id),
            title=f"Execute experiment '{experiment.title}' (version {version.version})"[:300],
            payload={
                "experiment_id": str(experiment.id),
                "version_id": str(version.id),
                "seeds": list(version.seeds or []),
                "expected_cost_usd": float(version.expected_cost_usd or 0),
                "network": version.network_policy,
                "resources": version.resource_request,
                "reasons": list(reasons),
            },
            risk_level="HIGH" if (version.network_policy or {}).get("mode") == "allowlist" else "MEDIUM",
            estimated_cost_usd=version.expected_cost_usd or Decimal(0),
            decision=decision,
            project_id=experiment.project_id,
            mission_id=experiment.mission_id,
        )
        _emit(
            db,
            actor,
            experiment,
            EventType.APPROVAL_REQUESTED,
            {"approval_id": str(approval.id), "action": "experiment.execute", "version_id": str(version.id)},
        )
        return ExecutionRequest(
            experiment, version, status="awaiting_approval", approval_id=approval.id, reasons=reasons
        )
    return _launch(db, actor, experiment, version, key, reasons)


def _launch(
    db: Session, actor: Actor, experiment: Experiment, version: ExperimentVersion, key: str, reasons: tuple[str, ...]
) -> ExecutionRequest:
    try:
        from aegis_api.lab.workflows.definitions import has_flow
        from aegis_api.lab.workflows.launcher import launch_workflow
    except ImportError as exc:  # pragma: no cover - the workflows context is part of the platform
        raise ServiceUnavailable("The workflow engine is unavailable") from exc
    if not has_flow(EXECUTE_WORKFLOW_KIND):
        raise ServiceUnavailable(
            f"The {EXECUTE_WORKFLOW_KIND} is not registered in this deployment; experiments cannot be executed yet",
            code="workflow_unavailable",
        )
    run = launch_workflow(
        db,
        actor,
        EXECUTE_WORKFLOW_KIND,
        subject_type="experiment",
        subject_id=str(experiment.id),
        input={"experiment_id": str(experiment.id), "version_id": str(version.id)},
        project_id=experiment.project_id,
        mission_id=experiment.mission_id,
        workflow_key=key,
    )
    audit(
        db,
        actor,
        AuditAction.EXPERIMENT_EXECUTED,
        "experiment",
        experiment.id,
        after={"workflow_run_id": str(run.id), "version": version.version, "workflow_key": key},
    )
    return ExecutionRequest(experiment, version, status=str(run.status), workflow_run_id=run.id, reasons=reasons)


def on_approval_decided(db: Session, actor: Actor, approval: Any) -> None:
    """Approval hook for ``experiment.execute``: launch the workflow once a human approved it."""
    if approval.action != "experiment.execute" or approval.status != "APPROVED":
        return
    experiment = db.get(Experiment, _uuid_or_none(approval.subject_id))
    if experiment is None or experiment.organization_id != approval.organization_id:
        return
    if experiment.status not in EXECUTABLE_STATUSES:
        return
    request_execution(db, actor, experiment.id)


def _register_hooks() -> None:
    try:
        from aegis_api.lab.governance.approvals import register_approval_hook
    except ImportError:  # pragma: no cover
        return
    register_approval_hook("experiment.execute", on_approval_decided)


_register_hooks()


__all__ = [
    "ExecutionRequest",
    "PreparedVersion",
    "ValidationOutcome",
    "archive_experiment",
    "create_experiment",
    "create_version",
    "current_version",
    "derive_baseline_spec",
    "ensure_baseline",
    "get_experiment",
    "list_experiments",
    "list_for_mission",
    "list_versions",
    "load_experiment",
    "on_approval_decided",
    "prepare_version",
    "request_execution",
    "validate_experiment",
    "version_spec",
]
