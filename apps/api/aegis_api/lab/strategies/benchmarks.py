"""Internal benchmarks service: the built-in suite catalog and benchmark runs with baseline comparisons.

* :func:`seed_benchmark_suites` (start-up, owner connection) registers the suites of
  :mod:`engines.lab.benchmarks` as built-in ``benchmark_suites`` rows (``organization_id`` NULL). Rows are
  immutable and keyed by ``(key, version)``; changed cases without a version bump are reported, never applied.
* :func:`run_benchmark` runs a suite against a subject (a strategy version, a platform component or an
  experiment's recorded outputs — see :mod:`aegis_api.lab.strategies.adapters`) and compares the result with
  the latest run of the incumbent (the promoted strategy version, or the previous run of the same subject) by
  a case-paired bootstrap. Benchmark comparisons are the evidence the promotion gate requires: nothing is ever
  described as improved without them.
* Suite execution is CPU work: the service prepares the run in one short transaction, computes outside any
  transaction and records the result in another (:func:`execute_benchmark_run`).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ServiceUnavailable, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.pagination import paginate
from aegis_api.lab.models import (
    BenchmarkRun,
    BenchmarkSuite,
    Experiment,
    ExperimentMetric,
    ExperimentRun,
    ExperimentVersion,
    Strategy,
    StrategyVersion,
)
from aegis_api.lab.strategies import schemas
from aegis_api.lab.strategies.adapters import (
    SUBJECT_TYPES,
    SUITE_STRATEGY_KIND,
    SUITE_SUBJECT_TYPES,
    AdapterError,
    SubjectContext,
    build_subject,
)
from aegis_api.lab.strategies.service import (
    enforce_guardrails,
    get_active_strategy_version,
    incumbent_version,
    load_version,
    parse_uuid,
)
from aegis_api.schemas.common import Page, PageParams
from engines.lab.benchmarks.base import BenchComparison, BenchResult, CaseResult, compare
from engines.lab.benchmarks.base import BenchmarkSuite as EngineSuite
from engines.lab.benchmarks.registry import BENCHMARK_SUITES, suite_manifest
from engines.lab.evolution.types import ParameterSchema
from engines.lab.states import ExecutionStatus, RunState, assert_transition

log = structlog.get_logger("aegis.lab.strategies")

DbFactory = Callable[[], AbstractContextManager[Session]]
MAX_ERROR_CHARS = 2000
MAX_TASK_OUTPUTS = 500
TERMINAL_RUN_STATES = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})


# ---------------------------------------------------------------------------------------------
# Suite catalog
# ---------------------------------------------------------------------------------------------
def seed_benchmark_suites(session: Session) -> int:
    """Register the built-in suites (idempotent; owner connection at start-up). Returns rows inserted."""
    inserted = 0
    for manifest in suite_manifest():
        config = dict(manifest.get("config") or {})
        config.setdefault("name", manifest["name"])
        result = session.execute(
            insert(BenchmarkSuite)
            .values(
                id=uuid.uuid4(),
                organization_id=None,
                key=manifest["key"],
                version=manifest["version"],
                description=manifest["description"],
                component=manifest["component"],
                case_count=manifest["case_count"],
                content_hash=manifest["content_hash"],
                config=config,
                created_at=utcnow(),
            )
            .on_conflict_do_nothing(constraint="uq_benchmark_suites_key_version")
            .returning(BenchmarkSuite.id)
        )
        if result.scalar() is not None:
            inserted += 1
            continue
        stored = session.scalar(
            select(BenchmarkSuite.content_hash).where(
                BenchmarkSuite.organization_id.is_(None),
                BenchmarkSuite.key == manifest["key"],
                BenchmarkSuite.version == manifest["version"],
            )
        )
        if stored is not None and stored != manifest["content_hash"]:
            log.warning(
                "benchmark_suite_changed_without_version_bump",
                key=manifest["key"],
                version=manifest["version"],
                registered=stored,
                current=manifest["content_hash"],
            )
    if inserted:
        log.info("benchmark_suites_seeded", inserted=inserted)
    return inserted


def engine_suite(key: str) -> EngineSuite:
    suite = BENCHMARK_SUITES.get(key)
    if suite is None:
        raise NotFound(f"Benchmark suite '{key}' not found")
    return suite


def suite_row(db: Session, key: str) -> tuple[BenchmarkSuite, EngineSuite]:
    """The registered built-in row for the code's current suite version (content hash verified)."""
    suite = engine_suite(key)
    row = db.scalar(
        select(BenchmarkSuite).where(
            BenchmarkSuite.organization_id.is_(None),
            BenchmarkSuite.key == suite.key,
            BenchmarkSuite.version == suite.version,
        )
    )
    if row is None:
        raise ServiceUnavailable(
            f"Benchmark suite '{key}' {suite.version} is not registered yet (start-up seeding has not run)",
            code="benchmark_catalog_unavailable",
        )
    if row.content_hash != suite.content_hash:
        raise Conflict(
            f"Benchmark suite '{key}' {suite.version} changed without a version bump; results would not be comparable",
            code="benchmark_suite_mismatch",
        )
    return row, suite


def suite_out(row: BenchmarkSuite) -> schemas.BenchmarkSuiteOut:
    out = schemas.BenchmarkSuiteOut.model_validate(row)
    return out.model_copy(
        update={
            "strategy_kind": SUITE_STRATEGY_KIND.get(row.key),
            "subject_types": list(SUITE_SUBJECT_TYPES.get(row.key, SUBJECT_TYPES)),
        }
    )


def list_suites(db: Session, actor: Actor) -> list[schemas.BenchmarkSuiteOut]:
    actor.require("benchmark:read")
    rows = db.scalars(
        select(BenchmarkSuite)
        .where(or_(BenchmarkSuite.organization_id.is_(None), BenchmarkSuite.organization_id == actor.organization_id))
        .order_by(BenchmarkSuite.key, BenchmarkSuite.version)
    ).all()
    return [suite_out(r) for r in rows]


# ---------------------------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------------------------
def _task_ids_for_run(run: ExperimentRun, spec: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
    assert isinstance(params, Mapping)
    for key in ("task_id", "benchmark_task_id"):
        if isinstance(params.get(key), str):
            ids.append(params[key])
    key = run.run_key or ""
    ids.append(key.split(":", 1)[0])
    return ids


def recorded_experiment_outputs(
    db: Session, experiment: Experiment
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Outputs recorded by SUCCEEDED runs of ``experiment`` keyed by task id (latest run wins)."""
    runs = db.scalars(
        select(ExperimentRun)
        .where(
            ExperimentRun.experiment_id == experiment.id,
            ExperimentRun.status.in_([ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFIED]),
        )
        .order_by(ExperimentRun.completed_at.asc().nulls_first(), ExperimentRun.run_number)
        .limit(MAX_TASK_OUTPUTS)
    ).all()
    outputs: dict[str, dict[str, Any]] = {}
    run_ids: dict[str, str] = {}
    specs: dict[uuid.UUID, Mapping[str, Any]] = {}
    for run in runs:
        if run.experiment_version_id not in specs:
            version = db.get(ExperimentVersion, run.experiment_version_id)
            specs[run.experiment_version_id] = (version.spec or {}) if version is not None else {}
        recorded: dict[str, Any] = dict(run.metrics or {})
        for metric in db.scalars(
            select(ExperimentMetric)
            .where(ExperimentMetric.experiment_run_id == run.id)
            .order_by(ExperimentMetric.created_at, ExperimentMetric.id)
        ).all():
            recorded[metric.name] = metric.value
        for task_id in _task_ids_for_run(run, specs[run.experiment_version_id]):
            if task_id:
                outputs[task_id] = recorded
                run_ids[task_id] = str(run.id)
    return outputs, run_ids


@dataclass(frozen=True)
class ResolvedSubject:
    subject_type: str
    subject_id: str
    context: SubjectContext
    strategy_id: str | None = None
    project_id: uuid.UUID | None = None


def resolve_subject(
    db: Session,
    actor: Actor,
    suite_key: str,
    subject_type: str,
    subject_id: str,
    *,
    params: Mapping[str, Any] | None = None,
) -> ResolvedSubject:
    """Validate access to the subject and build the adapter context (422 when not benchmarkable)."""
    suite = engine_suite(suite_key)
    if subject_type not in SUBJECT_TYPES:
        raise ValidationFailed(f"subject_type must be one of {', '.join(SUBJECT_TYPES)}")
    allowed = SUITE_SUBJECT_TYPES.get(suite_key, SUBJECT_TYPES)
    if subject_type not in allowed:
        raise ValidationFailed(f"{suite_key} accepts subject types: {', '.join(allowed)}")
    if subject_type == "strategy_version":
        if params:
            raise ValidationFailed("A strategy version is benchmarked with its own (immutable) parameters")
        version, strategy = load_version(db, actor, subject_id, "strategy:read")
        context = SubjectContext(
            suite_key=suite_key,
            subject_type=subject_type,
            subject_id=str(version.id),
            strategy_kind=strategy.kind,
            parameters=dict(version.parameters),
        )
        return ResolvedSubject(subject_type, str(version.id), context, str(strategy.id), strategy.project_id)
    if subject_type == "component":
        if subject_id != suite.component:
            raise ValidationFailed(f"{suite_key} measures the component '{suite.component}'")
        kind = SUITE_STRATEGY_KIND.get(suite_key)
        parameters: dict[str, Any] = {}
        if kind is not None:
            active = get_active_strategy_version(db, actor.organization_id, kind)
            if active is not None:
                parameters = dict(active.parameters)
                if params:
                    owner = db.get(Strategy, active.strategy_id)
                    assert owner is not None
                    merged = {**parameters, **dict(params)}
                    enforce_guardrails(active.definition, merged, ParameterSchema.from_dict(owner.parameter_schema))
                    parameters = merged
            elif params:
                raise ValidationFailed(f"No active {kind} strategy to apply parameter overrides to")
        elif params:
            raise ValidationFailed(f"{suite_key} measures a fixed component and takes no parameters")
        context = SubjectContext(
            suite_key=suite_key,
            subject_type=subject_type,
            subject_id=subject_id,
            strategy_kind=kind,
            parameters=parameters,
        )
        return ResolvedSubject(subject_type, subject_id, context)
    # experiment
    if params:
        raise ValidationFailed("An experiment is benchmarked with its recorded outputs only")
    experiment = get_owned(db, Experiment, subject_id, actor, label="Experiment")
    load_project(db, actor, experiment.project_id, "experiment:read")
    outputs, run_ids = recorded_experiment_outputs(db, experiment)
    context = SubjectContext(
        suite_key=suite_key,
        subject_type=subject_type,
        subject_id=str(experiment.id),
        recorded_outputs=outputs,
        recorded_runs=run_ids,
    )
    return ResolvedSubject(subject_type, str(experiment.id), context, project_id=experiment.project_id)


def _check_subject_visible(db: Session, actor: Actor, run: BenchmarkRun) -> None:
    """NotFound when the run's subject lives in a project the actor cannot see."""
    try:
        if run.subject_type == "strategy_version":
            load_version(db, actor, run.subject_id)
        elif run.subject_type == "experiment":
            experiment = get_owned(db, Experiment, run.subject_id, actor, label="Experiment")
            load_project(db, actor, experiment.project_id)
    except NotFound as exc:
        raise NotFound("Benchmark run not found") from exc


# ---------------------------------------------------------------------------------------------
# Runs: create → prepare → compute → record
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class BenchmarkPlan:
    run_id: uuid.UUID
    suite_key: str
    seed: int
    context: SubjectContext


@dataclass
class BenchmarkOutcome:
    run_id: uuid.UUID
    result: BenchResult | None
    error: str | None
    wall_seconds: float
    cpu_seconds: float
    extra: dict[str, Any] = field(default_factory=dict)


def create_benchmark_run(
    db: Session,
    actor: Actor,
    suite_key: str,
    subject_type: str,
    subject_id: str,
    *,
    seed: int = 0,
    params: Mapping[str, Any] | None = None,
) -> BenchmarkRun:
    """Validate the subject and create a PENDING run (the subject's parameters are captured now)."""
    actor.require("benchmark:run")
    if isinstance(seed, bool) or not 0 <= int(seed) <= 0x7FFFFFFF:
        raise ValidationFailed("seed must be an integer in [0, 2^31-1]")
    row, suite = suite_row(db, suite_key)
    resolved = resolve_subject(db, actor, suite_key, subject_type, subject_id, params=params)
    try:
        build_subject(resolved.context)
    except AdapterError as exc:
        raise ValidationFailed(str(exc)) from exc
    run = BenchmarkRun(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        suite_id=row.id,
        suite_key=suite.key,
        suite_version=suite.version,
        subject_type=resolved.subject_type,
        subject_id=resolved.subject_id,
        status=RunState.PENDING,
        seed=int(seed),
        metrics={
            "subject": {
                "type": resolved.subject_type,
                "id": resolved.subject_id,
                "strategy_id": resolved.strategy_id,
                "strategy_kind": resolved.context.strategy_kind,
                "parameters": dict(resolved.context.parameters),
                "parameter_overrides": dict(params or {}),
            },
            "content_hash": suite.content_hash,
        },
        case_results=[],
        comparison={},
        created_by_id=actor.user_id,
    )
    db.add(run)
    db.flush()
    return run


def _lock_run(db: Session, run_id: uuid.UUID) -> BenchmarkRun:
    run = db.execute(
        select(BenchmarkRun)
        .where(BenchmarkRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        raise NotFound("Benchmark run not found")
    return run


def prepare_benchmark(db: Session, actor: Actor, run_id: uuid.UUID) -> BenchmarkPlan | None:
    """PENDING → RUNNING and the pure plan to compute (``None`` when the run is already finished)."""
    run = _lock_run(db, run_id)
    if run.organization_id != actor.organization_id:
        raise NotFound("Benchmark run not found")
    if run.status in TERMINAL_RUN_STATES:
        return None
    subject_doc = run.metrics.get("subject", {}) if isinstance(run.metrics, Mapping) else {}
    overrides = subject_doc.get("parameter_overrides") or None
    resolved = resolve_subject(db, actor, run.suite_key, run.subject_type, run.subject_id, params=overrides)
    context = resolved.context
    if run.subject_type != "experiment":
        # The parameters captured at creation are the subject under test (reproducible).
        context = SubjectContext(
            suite_key=context.suite_key,
            subject_type=context.subject_type,
            subject_id=context.subject_id,
            strategy_kind=context.strategy_kind,
            parameters=dict(subject_doc.get("parameters") or context.parameters),
        )
    if run.status == RunState.PENDING:
        assert_transition("run", run.status, RunState.RUNNING)
        run.status = RunState.RUNNING
        run.started_at = utcnow()
    db.flush()
    return BenchmarkPlan(run_id=run.id, suite_key=run.suite_key, seed=int(run.seed or 0), context=context)


def compute_benchmark(plan: BenchmarkPlan) -> BenchmarkOutcome:
    """Run the suite against the subject (pure CPU work; never inside a database transaction)."""
    wall0, cpu0 = time.perf_counter(), time.thread_time()
    try:
        subject = build_subject(plan.context)
        result = engine_suite(plan.suite_key).run(subject, seed=plan.seed)
        error = None
    except Exception as exc:
        log.warning("benchmark_run_failed", run_id=str(plan.run_id), suite=plan.suite_key, exc_info=True)
        result, error = None, f"{type(exc).__name__}: {str(exc)[:MAX_ERROR_CHARS]}"
    return BenchmarkOutcome(
        run_id=plan.run_id,
        result=result,
        error=error,
        wall_seconds=round(time.perf_counter() - wall0, 6),
        cpu_seconds=round(time.thread_time() - cpu0, 6),
    )


def result_from_run(run: BenchmarkRun, component: str) -> BenchResult:
    """Rebuild the engine result of a COMPLETED run (for paired comparisons)."""
    metrics = run.metrics or {}
    return BenchResult(
        suite_key=run.suite_key,
        suite_version=run.suite_version,
        component=component,
        content_hash=str(metrics.get("content_hash") or ""),
        score=float(run.score or 0.0),
        metrics={k: float(v) for k, v in (metrics.get("suite") or {}).items() if isinstance(v, int | float)},
        case_results=[CaseResult.model_validate(c) for c in run.case_results or []],
        n_cases=int(metrics.get("n_cases") or len(run.case_results or [])),
        n_passed=int(metrics.get("n_passed") or 0),
        n_errors=int(metrics.get("n_errors") or 0),
        seed=int(run.seed or 0),
    )


def find_baseline_run(db: Session, run: BenchmarkRun) -> tuple[BenchmarkRun | None, str | None]:
    """Latest COMPLETED run of the incumbent subject on the same suite version (same seed preferred)."""
    base = select(BenchmarkRun).where(
        BenchmarkRun.organization_id == run.organization_id,
        BenchmarkRun.suite_key == run.suite_key,
        BenchmarkRun.suite_version == run.suite_version,
        BenchmarkRun.status == RunState.COMPLETED,
        BenchmarkRun.id != run.id,
    )
    if run.subject_type == "strategy_version":
        version = db.get(StrategyVersion, parse_uuid(run.subject_id, "Strategy version"))
        strategy = db.get(Strategy, version.strategy_id) if version is not None else None
        if version is None or strategy is None:
            return None, None
        incumbent = incumbent_version(db, strategy)
        if incumbent is None or incumbent.id == version.id:
            return None, None
        stmt = base.where(BenchmarkRun.subject_type == "strategy_version", BenchmarkRun.subject_id == str(incumbent.id))
        baseline_subject = str(incumbent.id)
    else:
        stmt = base.where(
            BenchmarkRun.subject_type == run.subject_type,
            BenchmarkRun.subject_id == run.subject_id,
            BenchmarkRun.created_at < run.created_at,
        )
        baseline_subject = run.subject_id
    same_seed = BenchmarkRun.seed == run.seed
    baseline = db.scalar(
        stmt.order_by(same_seed.desc(), BenchmarkRun.completed_at.desc().nulls_last(), BenchmarkRun.id).limit(1)
    )
    return baseline, baseline_subject if baseline is not None else None


def comparison_document(comparison: BenchComparison, baseline: BenchmarkRun) -> dict[str, Any]:
    return {
        **comparison.model_dump(mode="json"),
        "baseline_run_id": str(baseline.id),
        "baseline_subject_type": baseline.subject_type,
        "baseline_subject_id": baseline.subject_id,
        "evidence": comparison.to_evidence(),
    }


def record_benchmark_result(db: Session, actor: Actor, outcome: BenchmarkOutcome) -> BenchmarkRun:
    """RUNNING → COMPLETED (with baseline comparison) or FAILED."""
    run = _lock_run(db, outcome.run_id)
    if run.organization_id != actor.organization_id:
        raise NotFound("Benchmark run not found")
    if run.status in TERMINAL_RUN_STATES:
        return run
    price = float(get_settings().execution_cpu_price_per_hour_usd or 0.0)
    usage = {
        "wall_seconds": outcome.wall_seconds,
        "cpu_seconds": outcome.cpu_seconds,
        "cost_usd": round(outcome.cpu_seconds * price / 3600.0, 9),
        "cpu_price_per_hour_usd": price,
    }
    metrics = dict(run.metrics or {})
    metrics["usage"] = usage
    now = utcnow()
    if outcome.result is None:
        assert_transition("run", run.status, RunState.FAILED)
        run.status = RunState.FAILED
        run.error = (outcome.error or "benchmark failed")[:MAX_ERROR_CHARS]
        run.metrics = metrics
        run.completed_at = now
        db.flush()
        return run
    result = outcome.result
    metrics.update(
        {
            "suite": result.metrics,
            "n_cases": result.n_cases,
            "n_passed": result.n_passed,
            "n_errors": result.n_errors,
            "content_hash": result.content_hash,
        }
    )
    run.metrics = metrics
    run.score = result.score
    run.case_results = [c.model_dump(mode="json") for c in result.case_results]
    assert_transition("run", run.status, RunState.COMPLETED)
    run.status = RunState.COMPLETED
    run.completed_at = now
    baseline, _subject = find_baseline_run(db, run)
    if baseline is not None:
        try:
            comparison = compare(result, result_from_run(baseline, result.component), seed=int(run.seed or 0))
            run.baseline_run_id = baseline.id
            run.comparison = comparison_document(comparison, baseline)
        except ValueError as exc:
            run.comparison = {"error": str(exc)[:500], "baseline_run_id": str(baseline.id)}
    db.flush()
    return run


def run_benchmark(
    db: Session,
    actor: Actor,
    suite_key: str,
    subject_type: str,
    subject_id: str,
    *,
    seed: int = 0,
    params: Mapping[str, Any] | None = None,
) -> BenchmarkRun:
    """Run a suite synchronously within ``db`` (small suites / tests; workers use :func:`execute_benchmark_run`)."""
    run = create_benchmark_run(db, actor, suite_key, subject_type, subject_id, seed=seed, params=params)
    plan = prepare_benchmark(db, actor, run.id)
    assert plan is not None
    outcome = compute_benchmark(plan)
    return record_benchmark_result(db, actor, outcome)


def execute_benchmark_run(db_factory: DbFactory, actor: Actor, run_id: uuid.UUID | str) -> uuid.UUID:
    """Prepare / compute / record in separate short transactions (idempotent for finished runs)."""
    rid = parse_uuid(run_id, "Benchmark run")
    with db_factory() as db:
        plan = prepare_benchmark(db, actor, rid)
    if plan is None:
        return rid
    outcome = compute_benchmark(plan)
    with db_factory() as db:
        record_benchmark_result(db, actor, outcome)
    return rid


@contextmanager
def _reuse(session: Session) -> Any:
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def run_benchmark_job(session: Session, run_id: str, actor_payload: dict[str, Any]) -> None:
    """Job entry point (inline thread pool or Celery): execute one queued benchmark run."""
    from aegis_api.lab.workflows.registry import actor_from_payload

    actor = actor_from_payload(actor_payload)
    session.commit()  # release the dispatcher's transaction; phases use their own short ones
    try:
        execute_benchmark_run(lambda: _reuse(session), actor, run_id)
    except Exception as exc:
        log.warning("benchmark_job_failed", run_id=run_id, exc_info=True)
        with _reuse(session) as db:
            run = db.get(BenchmarkRun, parse_uuid(run_id, "Benchmark run"))
            if run is not None and run.status not in TERMINAL_RUN_STATES:
                run.status = RunState.FAILED
                run.error = f"{type(exc).__name__}: {str(exc)[:MAX_ERROR_CHARS]}"
                run.completed_at = utcnow()


def queue_benchmark_run(db: Session, actor: Actor, suite_key: str, data: schemas.BenchmarkRunCreate) -> BenchmarkRun:
    """Create a PENDING run and execute it in the background after commit (HTTP 202)."""
    from aegis_api.lab.workflows.registry import actor_to_payload

    run = create_benchmark_run(
        db, actor, suite_key, data.subject_type, data.subject_id, seed=data.seed, params=data.params
    )
    db.info.setdefault("after_commit", []).append(
        (run_benchmark_job, actor.organization_id, str(run.id), actor_to_payload(actor))
    )
    return run


# ---------------------------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------------------------
def run_out(run: BenchmarkRun) -> schemas.BenchmarkRunOut:
    return schemas.BenchmarkRunOut.model_validate(run)


def run_detail_out(run: BenchmarkRun) -> schemas.BenchmarkRunDetailOut:
    return schemas.BenchmarkRunDetailOut.model_validate(run)


def get_benchmark_run(db: Session, actor: Actor, run_id: uuid.UUID | str) -> BenchmarkRun:
    actor.require("benchmark:read")
    run = get_owned(db, BenchmarkRun, run_id, actor, label="Benchmark run")
    _check_subject_visible(db, actor, run)
    return run


def list_benchmark_runs(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    suite_key: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    status: str | None = None,
) -> Page[schemas.BenchmarkRunOut]:
    actor.require("benchmark:read")
    stmt = select(BenchmarkRun).where(BenchmarkRun.organization_id == actor.organization_id)
    if suite_key:
        stmt = stmt.where(BenchmarkRun.suite_key == suite_key)
    if subject_type:
        if subject_type not in SUBJECT_TYPES:
            raise ValidationFailed(f"subject_type must be one of {', '.join(SUBJECT_TYPES)}")
        stmt = stmt.where(BenchmarkRun.subject_type == subject_type)
    if subject_id:
        stmt = stmt.where(BenchmarkRun.subject_id == subject_id)
    if status:
        if status.upper() not in RunState.__members__:
            raise ValidationFailed(f"status must be one of {', '.join(RunState.__members__)}")
        stmt = stmt.where(BenchmarkRun.status == status.upper())
    visible = visible_project_ids(db, actor)
    if visible is not None:
        hidden_versions = (
            select(cast(StrategyVersion.id, String))
            .join(Strategy, Strategy.id == StrategyVersion.strategy_id)
            .where(Strategy.project_id.is_not(None), Strategy.project_id.not_in(visible))
        )
        hidden_experiments = select(cast(Experiment.id, String)).where(Experiment.project_id.not_in(visible))
        stmt = stmt.where(
            ~and_(BenchmarkRun.subject_type == "strategy_version", BenchmarkRun.subject_id.in_(hidden_versions)),
            ~and_(BenchmarkRun.subject_type == "experiment", BenchmarkRun.subject_id.in_(hidden_experiments)),
        )
    stmt = stmt.order_by(BenchmarkRun.created_at.desc(), BenchmarkRun.id.desc())
    return paginate(db, stmt, params, run_out)


def latest_comparisons(
    db: Session, organization_id: uuid.UUID, version_id: uuid.UUID, suites: list[str], incumbent_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Benchmark evidence for a candidate: per suite, the latest comparison against the incumbent's runs."""
    evidence: list[dict[str, Any]] = []
    for key in suites:
        run = db.scalar(
            select(BenchmarkRun)
            .where(
                BenchmarkRun.organization_id == organization_id,
                BenchmarkRun.suite_key == key,
                BenchmarkRun.subject_type == "strategy_version",
                BenchmarkRun.subject_id == str(version_id),
                BenchmarkRun.status == RunState.COMPLETED,
                BenchmarkRun.baseline_run_id.is_not(None),
                BenchmarkRun.comparison.op("->>")("baseline_subject_id") == str(incumbent_id),
            )
            .order_by(BenchmarkRun.completed_at.desc().nulls_last(), BenchmarkRun.id)
            .limit(1)
        )
        if run is not None and isinstance(run.comparison.get("evidence"), Mapping):
            item = dict(run.comparison["evidence"])
            item["benchmark_run_id"] = str(run.id)
            item["baseline_run_id"] = str(run.baseline_run_id)
            evidence.append(item)
    return evidence
