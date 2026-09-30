"""Evolution runs over the pure :class:`~engines.lab.evolution.engine.EvolutionEngine`.

A run evolves one strategy's parameters under its ``parameter_schema`` (the guardrail):

1. :func:`start_evolution_run` — feature flag ``evolution``, ``strategy:evolve``, automated actors only inside a
   mission whose effective autonomy is ≥ L4, mission budget, a per-organization concurrency quota; then the
   ``EvolutionWorkflow`` is launched (lazily, when the workflow engine registers it).
2. :func:`evolution_step` — generation 0 seeds guardrail-valid mutations of the incumbent's parameters as
   CANDIDATE versions (+ ``strategy_mutations`` rows); every later step gathers fitness from
   ``strategy_evaluations``, runs one NSGA-II generation, moves survivors to SURVIVING and the others to
   RETIRED, stores the ε-Pareto archive and creates the next children.
3. :func:`evaluate_version` — runs the configured internal benchmark suites for a version over several seeds
   and records a multi-objective :class:`~aegis_api.lab.models.StrategyEvaluation` (CANDIDATE → EXPERIMENTAL).
4. :func:`finalize_evolution` — best versions = the rank-0 front; promotion candidates = front members the
   promotion gate finds eligible. Nothing is ever promoted automatically: auto-promotion is a governance
   action that the baseline policy denies, and promotion always requires a human.

Objectives recorded per evaluation (the eight canonical ones):

* ``scientific_performance`` — mean benchmark score (mean over suites, then seeds);
* ``robustness`` — minimum per-seed score; ``reproducibility`` — ``1 − var(per-seed score) / 0.25``;
* ``cost`` — measured CPU time of the benchmark runs priced at ``execution_cpu_price_per_hour_usd``;
* ``latency`` — measured wall-clock seconds per seed (0.1 s resolution);
  ``compute_efficiency`` — score per ``1 + CPU second``;
* ``novelty`` — parameter-space novelty vs. the run's Pareto archive (or the strategy's other versions);
* ``safety`` — 1.0 unless a guardrail or policy violation was observed (hard constraint ``safety ≥ 1``).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.lab.core.access import get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.errors import PolicyDenied, QuotaExceeded
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.features import ensure_feature
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.org_settings import get_org_settings, quota
from aegis_api.lab.core.pagination import paginate
from aegis_api.lab.models import (
    BenchmarkRun,
    EvolutionRun,
    Mission,
    Project,
    Strategy,
    StrategyEvaluation,
    StrategyVersion,
)
from aegis_api.lab.strategies import schemas
from aegis_api.lab.strategies.adapters import (
    SUITE_SUBJECT_TYPES,
    default_suites_for_kind,
    suite_measures_kind,
)
from aegis_api.lab.strategies.benchmarks import (
    BenchmarkOutcome,
    BenchmarkPlan,
    compute_benchmark,
    create_benchmark_run,
    engine_suite,
    prepare_benchmark,
    record_benchmark_result,
)
from aegis_api.lab.strategies.seeds import version_content_hash
from aegis_api.lab.strategies.service import (
    LIVE_STATUSES,
    find_by_content,
    guardrail_report,
    incumbent_version,
    insert_version,
    latest_version,
    load_strategy,
    load_version,
    parse_uuid,
    record_mutation,
    set_version_status,
    version_out,
)
from aegis_api.schemas.common import Page, PageParams
from engines.lab import autonomy as autonomy_engine
from engines.lab.evolution.diversity import DiversityManager
from engines.lab.evolution.engine import EvolutionConfig, EvolutionEngine, survival_transitions
from engines.lab.evolution.fitness import CANONICAL_OBJECTIVES, resolve_objectives
from engines.lab.evolution.guardrails import GuardrailViolation
from engines.lab.evolution.mutation import MutationError
from engines.lab.evolution.types import Candidate, ParameterSchema, SchemaError
from engines.lab.states import AUTONOMY_RANK, RunState, StrategyStatus, assert_transition

log = structlog.get_logger("aegis.lab.strategies")

DbFactory = Callable[[], AbstractContextManager[Session]]
EVALUATOR_VERSION = "strategy-eval-1.0.0"
ACTIVE_RUN_STATES: tuple[str, ...] = (RunState.PENDING, RunState.RUNNING, RunState.WAITING)
TERMINAL_RUN_STATES: frozenset[str] = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})
DEFAULT_EVALUATION_SEEDS: tuple[int, ...] = (0, 1, 2)
DEFAULT_MAX_CONCURRENT_RUNS = 3
QUOTA_KEY = "max_concurrent_evolution_runs"
LATENCY_RESOLUTION = 0.1
MAX_NOVELTY_REFERENCE = 200
UNSAFE_ERROR_MARKERS: tuple[str, ...] = ("PolicyDenied", "GuardrailViolation", "ApprovalRequired", "Forbidden")
WORKFLOW_KIND = "EvolutionWorkflow"


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------
def run_settings(run: EvolutionRun) -> dict[str, Any]:
    settings = (run.summary or {}).get("settings")
    return dict(settings) if isinstance(settings, Mapping) else {}


def engine_config(run: EvolutionRun) -> EvolutionConfig:
    return EvolutionConfig.model_validate(run.config)


def load_evolution_run(db: Session, actor: Actor, run_id: uuid.UUID | str, *permissions: str) -> EvolutionRun:
    run = get_owned(db, EvolutionRun, run_id, actor, label="Evolution run")
    try:
        load_strategy(db, actor, run.strategy_id, *permissions)
        if run.project_id is not None:
            load_project(db, actor, run.project_id)
    except NotFound as exc:
        raise NotFound("Evolution run not found") from exc
    return run


def _lock_run(db: Session, run: EvolutionRun) -> EvolutionRun:
    advisory_xact_lock(db, f"evolution-run:{run.id}")
    return db.execute(
        select(EvolutionRun).where(EvolutionRun.id == run.id).execution_options(populate_existing=True)
    ).scalar_one()


def _transition_run(run: EvolutionRun, target: str) -> None:
    assert_transition("run", run.status, target)
    run.status = target
    now = utcnow()
    if target == RunState.RUNNING and run.started_at is None:
        run.started_at = now
    if target in TERMINAL_RUN_STATES:
        run.completed_at = now


def _effective_autonomy(db: Session, actor: Actor, mission: Mission) -> str:
    """The mission's autonomy, lowered to the actor's own level and the organization/project ceilings."""
    project = db.get(Project, mission.project_id)
    org = get_org_settings(db, actor.organization_id)
    levels: list[str] = []
    for raw in (mission.autonomy_level, actor.autonomy_level):
        if not raw:
            continue
        try:
            levels.append(autonomy_engine.normalize_level(raw))
        except ValueError:  # unknown levels fail closed
            levels.append(autonomy_engine.normalize_level("L0"))
    if not levels:
        levels.append(autonomy_engine.normalize_level("L0"))
    lowest = min(levels, key=lambda level: AUTONOMY_RANK[level])
    try:
        return autonomy_engine.clamp(lowest, org.max_autonomy_level, project.max_autonomy_level if project else None)
    except ValueError:
        return autonomy_engine.normalize_level("L0")


def _event(db: Session, actor: Actor, run: EvolutionRun, type_: str, payload: dict[str, Any]) -> None:
    emit(
        db,
        organization_id=run.organization_id,
        type=type_,
        payload=payload,
        mission_id=run.mission_id,
        project_id=run.project_id,
        workspace_id=run.workspace_id,
        subject_type="evolution_run",
        subject_id=run.id,
        actor=actor,
    )


def run_out(run: EvolutionRun) -> schemas.EvolutionRunOut:
    return schemas.EvolutionRunOut.model_validate(run)


# ---------------------------------------------------------------------------------------------
# Start / query / cancel
# ---------------------------------------------------------------------------------------------
def _validate_suites(kind: str, suites: Sequence[str]) -> list[str]:
    keys = list(dict.fromkeys(suites))
    for key in keys:
        engine_suite(key)
        allowed = SUITE_SUBJECT_TYPES.get(key)
        if allowed is not None and "strategy_version" not in allowed:
            raise ValidationFailed(f"{key} cannot benchmark strategy versions")
    return keys


def start_evolution_run(db: Session, actor: Actor, data: schemas.EvolutionRunCreate) -> EvolutionRun:
    """Create an evolution run (PENDING) and launch its workflow after commit."""
    ensure_feature(db, actor.organization_id, "evolution")
    strategy = load_strategy(db, actor, data.strategy_id, "strategy:evolve")
    if strategy.status != "active":
        raise Conflict("The strategy is not active", code="strategy_inactive")
    mission: Mission | None = None
    project_id = strategy.project_id
    if data.mission_id:
        mission = get_owned(db, Mission, data.mission_id, actor, label="Mission")
        load_project(db, actor, mission.project_id, "strategy:evolve")
        if strategy.project_id is not None and strategy.project_id != mission.project_id:
            raise ValidationFailed("The mission belongs to another project than the strategy")
        project_id = mission.project_id
    if not actor.is_human:
        if mission is None:
            raise PolicyDenied("Automated evolution runs must belong to a mission with autonomy level L4 or higher")
        level = _effective_autonomy(db, actor, mission)
        if autonomy_engine.requires_human(level, autonomy_engine.Capability.STRATEGY_MUTATION.value):
            raise PolicyDenied(
                f"Automated strategy evolution requires autonomy L4 (closed-loop evolution); effective level is {level}",
                details={"effective_autonomy_level": level},
            )
    if mission is not None:
        from aegis_api.lab.governance.budgets import enforce_budget

        enforce_budget(db, actor, mission, "total", estimated_usd=Decimal("0"))

    try:
        schema = ParameterSchema.from_dict(strategy.parameter_schema)
    except SchemaError as exc:
        raise ValidationFailed("The strategy's parameter schema is invalid", details={"error": str(exc)}) from exc
    if not schema.mutable_names:
        raise ValidationFailed("The strategy declares no mutable parameters to evolve")
    cfg = data.config
    suites = _validate_suites(strategy.kind, cfg.benchmark_suites or default_suites_for_kind(strategy.kind))
    if not any(suite_measures_kind(key, strategy.kind) for key in suites):
        raise ValidationFailed(
            f"No selected benchmark measures {strategy.kind} strategies; evolution needs at least one suite that is "
            "sensitive to the strategy's parameters",
            details={"benchmark_suites": suites, "suites_for_kind": default_suites_for_kind(strategy.kind)},
        )
    try:
        objectives = tuple(resolve_objectives(list(cfg.objectives or CANONICAL_OBJECTIVES)))
        engine_cfg = EvolutionConfig(
            population_size=cfg.population_size,
            mutation_rate=cfg.mutation_rate,
            crossover_rate=cfg.crossover_rate,
            objectives=objectives,
            archive_size=cfg.archive_size,
            epsilon=cfg.epsilon,
            seed=cfg.seed,
            novelty_weight=cfg.novelty_weight,
            generations=cfg.generations,
            max_candidates=cfg.population_size * (cfg.generations + 1),
            benchmark_suite=suites[0],
        )
    except ValueError as exc:
        raise ValidationFailed("Invalid evolution configuration", details={"error": str(exc)[:2000]}) from exc

    base = incumbent_version(db, strategy)
    if base is None or base.strategy_id != strategy.id:
        base = latest_version(db, strategy.id)
    if base is None:
        raise Conflict("The strategy has no version to evolve from", code="strategy_without_versions")

    advisory_xact_lock(db, f"evolution-runs:{actor.organization_id}")
    active_for_strategy = db.scalar(
        select(func.count(EvolutionRun.id)).where(
            EvolutionRun.strategy_id == strategy.id, EvolutionRun.status.in_(ACTIVE_RUN_STATES)
        )
    )
    if active_for_strategy:
        raise Conflict("An evolution run is already active for this strategy", code="evolution_run_active")
    limit = quota(db, actor.organization_id, QUOTA_KEY)
    limit = (
        int(limit) if isinstance(limit, int | float) and not isinstance(limit, bool) else DEFAULT_MAX_CONCURRENT_RUNS
    )
    active = int(
        db.scalar(
            select(func.count(EvolutionRun.id)).where(
                EvolutionRun.organization_id == actor.organization_id, EvolutionRun.status.in_(ACTIVE_RUN_STATES)
            )
        )
        or 0
    )
    if active + 1 > limit:
        raise QuotaExceeded(
            f"Organization quota '{QUOTA_KEY}' reached ({active} of {limit})",
            details={"key": QUOTA_KEY, "limit": limit, "current": active, "requested": 1, "unit": "count"},
        )

    project = db.get(Project, project_id) if project_id is not None else None
    run = EvolutionRun(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id if project is not None else None,
        project_id=project.id if project is not None else None,
        mission_id=mission.id if mission is not None else None,
        strategy_id=strategy.id,
        status=RunState.PENDING,
        config=engine_cfg.model_dump(mode="json"),
        current_generation=0,
        archive={},
        best_version_ids=[],
        summary={
            "settings": {"benchmark_suites": suites, "evaluation_seeds": list(cfg.evaluation_seeds)},
            "base_version_id": str(base.id),
            "initialized": False,
            "generations": [],
        },
        created_by_id=actor.user_id,
    )
    db.add(run)
    db.flush()
    audit(
        db,
        actor,
        AuditAction.EVOLUTION_STARTED,
        "evolution_run",
        run.id,
        after={
            "strategy_id": strategy.id,
            "mission_id": run.mission_id,
            "population_size": cfg.population_size,
            "generations": cfg.generations,
            "benchmark_suites": suites,
        },
    )
    _launch_workflow(db, actor, run)
    return run


def _launch_workflow(db: Session, actor: Actor, run: EvolutionRun) -> None:
    """Start the EvolutionWorkflow when the workflow engine provides it (never pretends otherwise)."""
    note: dict[str, Any]
    try:
        from aegis_api.lab.workflows.definitions import has_flow
        from aegis_api.lab.workflows.launcher import launch_workflow

        if not has_flow(WORKFLOW_KIND):
            note = {
                "workflow": None,
                "detail": f"{WORKFLOW_KIND} is not registered; drive the run with the "
                "strategies.evolution_step / strategies.evaluate_version / strategies.finalize_evolution activities",
            }
        else:
            with db.begin_nested():
                workflow = launch_workflow(
                    db,
                    actor,
                    WORKFLOW_KIND,
                    subject_type="evolution_run",
                    subject_id=run.id,
                    input={"evolution_run_id": str(run.id)},
                    project_id=run.project_id,
                    mission_id=run.mission_id,
                )
            run.workflow_run_id = workflow.id
            note = {"workflow": WORKFLOW_KIND, "workflow_run_id": str(workflow.id)}
    except ImportError:
        note = {"workflow": None, "detail": "workflow engine unavailable"}
    except Exception as exc:
        log.warning("evolution_workflow_launch_failed", evolution_run_id=str(run.id), exc_info=True)
        note = {"workflow": None, "detail": f"workflow launch failed: {type(exc).__name__}"}
    run.summary = {**(run.summary or {}), "orchestration": note}
    db.flush()


def get_evolution_run(db: Session, actor: Actor, run_id: uuid.UUID | str) -> EvolutionRun:
    return load_evolution_run(db, actor, run_id, "strategy:read")


def evolution_run_detail(db: Session, actor: Actor, run_id: uuid.UUID | str) -> schemas.EvolutionRunDetailOut:
    run = get_evolution_run(db, actor, run_id)
    front: list[StrategyVersion] = []
    front_ids = list(run.best_version_ids or [])
    if not front_ids:
        generations = (run.summary or {}).get("generations") or []
        if generations and isinstance(generations[-1], Mapping):
            front_ids = [str(i) for i in generations[-1].get("front") or []]
    for raw in front_ids:
        try:
            version = db.get(StrategyVersion, uuid.UUID(str(raw)))
        except ValueError:
            continue
        if version is not None and version.organization_id == actor.organization_id:
            front.append(version)
    base = run_out(run).model_dump()
    return schemas.EvolutionRunDetailOut(**base, archive=run.archive or {}, front=[version_out(v) for v in front])


def list_evolution_runs(
    db: Session,
    actor: Actor,
    params: PageParams,
    *,
    strategy_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    status: str | None = None,
) -> Page[schemas.EvolutionRunOut]:
    actor.require("strategy:read")
    stmt = select(EvolutionRun).where(EvolutionRun.organization_id == actor.organization_id)
    if strategy_id:
        stmt = stmt.where(EvolutionRun.strategy_id == load_strategy(db, actor, strategy_id, "strategy:read").id)
    if mission_id:
        stmt = stmt.where(EvolutionRun.mission_id == parse_uuid(mission_id, "Mission"))
    if status:
        if status.upper() not in RunState.__members__:
            raise ValidationFailed(f"status must be one of {', '.join(RunState.__members__)}")
        stmt = stmt.where(EvolutionRun.status == status.upper())
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where((EvolutionRun.project_id.is_(None)) | (EvolutionRun.project_id.in_(visible)))
    stmt = stmt.order_by(EvolutionRun.created_at.desc(), EvolutionRun.id.desc())
    return paginate(db, stmt, params, run_out)


def cancel_evolution_run(
    db: Session, actor: Actor, run_id: uuid.UUID | str, *, reason: str | None = None
) -> EvolutionRun:
    run = load_evolution_run(db, actor, run_id, "strategy:evolve")
    run = _lock_run(db, run)
    if run.status in TERMINAL_RUN_STATES:
        raise Conflict(f"The evolution run is already {run.status}", code="invalid_state_transition")
    _transition_run(run, RunState.CANCELLED)
    run.error = (reason or "cancelled")[:2000]
    if run.workflow_run_id is not None:
        try:
            from aegis_api.lab.workflows.launcher import cancel_workflow

            with db.begin_nested():
                cancel_workflow(db, actor, run.workflow_run_id)
        except Exception:
            log.warning("evolution_workflow_cancel_failed", evolution_run_id=str(run.id), exc_info=True)
    db.flush()
    audit(
        db,
        actor,
        "EVOLUTION_CANCELLED",
        "evolution_run",
        run.id,
        after={"reason": run.error, "generation": run.current_generation},
    )
    return run


# ---------------------------------------------------------------------------------------------
# Evaluation of one version
# ---------------------------------------------------------------------------------------------
def _find_evaluation(db: Session, version_id: uuid.UUID, request_key: str) -> StrategyEvaluation | None:
    return db.scalar(
        select(StrategyEvaluation)
        .where(
            StrategyEvaluation.strategy_version_id == version_id,
            StrategyEvaluation.raw_metrics.op("->>")("request_key") == request_key,
        )
        .limit(1)
    )


def _evaluation_result(evaluation: StrategyEvaluation, *, replayed: bool) -> dict[str, Any]:
    return {
        "evaluation_id": str(evaluation.id),
        "strategy_version_id": str(evaluation.strategy_version_id),
        "objectives": dict(evaluation.objectives or {}),
        "constraints_satisfied": evaluation.constraints_satisfied,
        "benchmark_run_ids": [e.get("benchmark_run_id") for e in evaluation.evidence or [] if isinstance(e, Mapping)],
        "replayed": replayed,
    }


def novelty_reference(
    db: Session, strategy: Strategy, version: StrategyVersion, run: EvolutionRun | None
) -> list[dict[str, Any]]:
    """Parameter vectors the version's novelty is measured against (run archive, else sibling versions)."""
    if run is not None and isinstance(run.archive, Mapping) and run.archive.get("entries"):
        return [
            dict(e["params"])
            for e in run.archive["entries"]
            if isinstance(e, Mapping) and e.get("id") != str(version.id) and isinstance(e.get("params"), Mapping)
        ][:MAX_NOVELTY_REFERENCE]
    rows: Sequence[dict[str, Any]] = db.scalars(
        select(StrategyVersion.parameters)
        .where(
            StrategyVersion.strategy_id == strategy.id,
            StrategyVersion.id != version.id,
            StrategyVersion.status != StrategyStatus.ROLLED_BACK,
        )
        .order_by(StrategyVersion.version.desc())
        .limit(MAX_NOVELTY_REFERENCE)
    ).all()
    return [dict(p) for p in rows]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize_benchmarks(
    runs: Sequence[BenchmarkRun], seeds: Sequence[int], *, guardrails_ok: bool, novelty: float
) -> tuple[dict[str, float], dict[str, list[float]], dict[str, Any]]:
    """Objectives, per-seed samples (for the promotion gate) and raw metrics from benchmark runs."""
    per_seed: dict[int, list[BenchmarkRun]] = {int(s): [] for s in seeds}
    for run in runs:
        per_seed.setdefault(int(run.seed or 0), []).append(run)
    unsafe: list[str] = []
    failed: list[str] = []
    perf: list[float] = []
    cost: list[float] = []
    latency: list[float] = []
    efficiency: list[float] = []
    safety: list[float] = []
    for seed in seeds:
        seed_runs = per_seed.get(int(seed), [])
        scores: list[float] = []
        cpu = wall = usd = 0.0
        seed_unsafe = False
        for run in seed_runs:
            usage = (run.metrics or {}).get("usage") or {}
            cpu += float(usage.get("cpu_seconds") or 0.0)
            wall += float(usage.get("wall_seconds") or 0.0)
            usd += float(usage.get("cost_usd") or 0.0)
            if run.status != RunState.COMPLETED or run.score is None:
                failed.append(str(run.id))
                scores.append(0.0)
                continue
            scores.append(float(run.score))
            for case in run.case_results or []:
                error = str((case.get("detail") or {}).get("error") or "") if isinstance(case, Mapping) else ""
                if any(marker in error for marker in UNSAFE_ERROR_MARKERS):
                    seed_unsafe = True
                    unsafe.append(f"{run.suite_key}:{case.get('case_id')}")
        score = _mean(scores)
        perf.append(score)
        cost.append(usd)
        latency.append(round(round(wall / LATENCY_RESOLUTION) * LATENCY_RESOLUTION, 6))
        efficiency.append(score / (1.0 + cpu))
        safety.append(1.0 if guardrails_ok and not seed_unsafe else 0.0)
    mean_perf = _mean(perf)
    variance = _mean([(p - mean_perf) ** 2 for p in perf])
    reproducibility_samples = [max(0.0, 1.0 - 4.0 * (p - mean_perf) ** 2) for p in perf]
    objectives = {
        "scientific_performance": mean_perf,
        "cost": _mean(cost),
        "latency": _mean(latency),
        "compute_efficiency": _mean(efficiency),
        "robustness": min(perf) if perf else 0.0,
        "reproducibility": max(0.0, min(1.0, 1.0 - variance / 0.25)),
        "novelty": novelty,
        "safety": min(safety) if safety else 0.0,
    }
    samples = {
        "scientific_performance": perf,
        "cost": cost,
        "latency": latency,
        "compute_efficiency": efficiency,
        "robustness": perf,
        "reproducibility": reproducibility_samples,
        "novelty": [novelty] * len(perf),
        "safety": safety,
    }
    raw = {"failed_benchmark_runs": failed, "unsafe_observations": unsafe[:50], "per_seed_score": perf}
    return (
        {k: round(float(v), 9) for k, v in objectives.items()},
        {k: [round(float(x), 9) for x in v] for k, v in samples.items()},
        raw,
    )


def default_evaluation_suites(kind: str) -> list[str]:
    suites = default_suites_for_kind(kind)
    return suites or ["failure_analysis_bench"]


def evaluate_version(
    db_factory: DbFactory,
    actor: Actor,
    version_id: uuid.UUID | str,
    *,
    benchmark_keys: Sequence[str] | None = None,
    seeds: Sequence[int] | None = None,
    evolution_run_id: uuid.UUID | str | None = None,
    mission_id: uuid.UUID | str | None = None,
    request_key: str | None = None,
    heartbeat: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """Benchmark a version over suites × seeds and record a multi-objective ``StrategyEvaluation``.

    Idempotent per ``request_key`` (inside an evolution run the key is ``run:<run>:<version>``). The suites run
    outside any database transaction.
    """
    plans: list[BenchmarkPlan] = []
    with db_factory() as db:
        version, strategy = load_version(db, actor, version_id, "strategy:evolve")
        run: EvolutionRun | None = None
        if evolution_run_id:
            run = load_evolution_run(db, actor, evolution_run_id, "strategy:evolve")
            if run.strategy_id != strategy.id:
                raise ValidationFailed("The version does not belong to the evolution run's strategy")
            if run.status in (RunState.CANCELLED, RunState.FAILED):
                raise Conflict(f"The evolution run is {run.status}", code="evolution_run_closed")
        settings = run_settings(run) if run is not None else {}
        keys = list(
            dict.fromkeys(
                benchmark_keys or settings.get("benchmark_suites") or default_evaluation_suites(strategy.kind)
            )
        )
        _validate_suites(strategy.kind, keys)
        seed_list = [int(s) for s in (seeds or settings.get("evaluation_seeds") or DEFAULT_EVALUATION_SEEDS)]
        if not seed_list or len(set(seed_list)) != len(seed_list) or any(s < 0 or s > 0x7FFFFFFF for s in seed_list):
            raise ValidationFailed("seeds must be distinct integers in [0, 2^31-1]")
        key = request_key or (f"run:{run.id}:{version.id}" if run is not None else f"adhoc:{uuid.uuid4().hex}")
        existing = _find_evaluation(db, version.id, key)
        if existing is not None:
            return _evaluation_result(existing, replayed=True)
        mission: Mission | None = None
        if mission_id:
            mission = get_owned(db, Mission, mission_id, actor, label="Mission")
        elif run is not None and run.mission_id is not None:
            mission = db.get(Mission, run.mission_id)
        parent = db.get(StrategyVersion, version.parent_version_id) if version.parent_version_id else None
        report = guardrail_report(version.definition, version.parameters, strategy.parameter_schema, parent)
        schema_doc = dict(strategy.parameter_schema)
        parameters = dict(version.parameters)
        reference = novelty_reference(db, strategy, version, run)
        novelty_k = engine_config(run).novelty_k if run is not None else 5
        for suite_key in keys:
            for seed in seed_list:
                benchmark = create_benchmark_run(db, actor, suite_key, "strategy_version", str(version.id), seed=seed)
                plan = prepare_benchmark(db, actor, benchmark.id)
                assert plan is not None
                plans.append(plan)
        target_id = version.id
        run_ref = run.id if run is not None else None
        mission_ref = mission.id if mission is not None else None

    outcomes: list[BenchmarkOutcome] = []
    for plan in plans:
        outcomes.append(compute_benchmark(plan))
        if heartbeat is not None:
            heartbeat({"benchmark_run_id": str(plan.run_id), "done": len(outcomes), "total": len(plans)})
    try:
        novelty = DiversityManager(ParameterSchema.from_dict(schema_doc)).novelty(parameters, reference, k=novelty_k)
    except (SchemaError, ValueError):
        novelty = 1.0

    with db_factory() as db:
        existing = _find_evaluation(db, target_id, key)
        if existing is not None:
            return _evaluation_result(existing, replayed=True)
        runs = [record_benchmark_result(db, actor, outcome) for outcome in outcomes]
        objectives, samples, raw = summarize_benchmarks(runs, seed_list, guardrails_ok=report.ok, novelty=novelty)
        version = db.execute(
            select(StrategyVersion).where(StrategyVersion.id == target_id).execution_options(populate_existing=True)
        ).scalar_one()
        evaluation = StrategyEvaluation(
            id=uuid.uuid4(),
            organization_id=version.organization_id,
            strategy_version_id=version.id,
            evolution_run_id=run_ref,
            benchmark_run_id=runs[0].id if runs else None,
            mission_id=mission_ref,
            objectives=objectives,
            raw_metrics={
                "request_key": key,
                "suites": sorted(keys),
                "seeds": seed_list,
                "samples": samples,
                "benchmark": {
                    r.suite_key: {str(x.seed): x.score for x in runs if x.suite_key == r.suite_key} for r in runs
                },
                "guardrails": report.as_dict(),
                "novelty_reference_size": len(reference),
                **raw,
            },
            n_samples=len(seed_list),
            seeds=seed_list,
            evaluator_version=EVALUATOR_VERSION,
            constraints_satisfied=objectives["safety"] >= 1.0,
            evidence=[
                {
                    "benchmark_run_id": str(r.id),
                    "suite": r.suite_key,
                    "suite_version": r.suite_version,
                    "seed": r.seed,
                    "score": r.score,
                    "status": r.status,
                    "content_hash": (r.metrics or {}).get("content_hash"),
                    "comparison": (r.comparison or {}).get("evidence"),
                }
                for r in runs
            ],
        )
        db.add(evaluation)
        db.flush()
        pooled: Sequence[dict[str, Any]] = db.scalars(
            select(StrategyEvaluation.objectives).where(StrategyEvaluation.strategy_version_id == version.id)
        ).all()
        version.fitness = {
            name: round(_mean([float(o[name]) for o in pooled if isinstance(o.get(name), int | float)]), 9)
            for name in objectives
        }
        if version.status == StrategyStatus.CANDIDATE:
            set_version_status(version, StrategyStatus.EXPERIMENTAL)
        db.flush()
        return _evaluation_result(evaluation, replayed=False)


@contextmanager
def _reuse(session: Session) -> Any:
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def run_evaluation_job(
    session: Session, version_id: str, request: dict[str, Any], actor_payload: dict[str, Any]
) -> None:
    """Job entry point for ``POST /strategy-versions/{id}/evaluate`` (inline thread pool or Celery)."""
    from aegis_api.lab.workflows.registry import actor_from_payload

    actor = actor_from_payload(actor_payload)
    session.commit()
    try:
        evaluate_version(
            lambda: _reuse(session),
            actor,
            version_id,
            benchmark_keys=request.get("benchmark_keys"),
            seeds=request.get("seeds"),
            request_key=request.get("request_key"),
        )
    except Exception:
        log.warning("strategy_evaluation_job_failed", strategy_version_id=version_id, exc_info=True)
        raise


def queue_version_evaluation(
    db: Session, actor: Actor, version_id: uuid.UUID | str, data: schemas.EvaluateVersionIn
) -> schemas.EvaluationAcceptedOut:
    """Validate an evaluation request and run it in the background after commit (HTTP 202)."""
    from aegis_api.lab.workflows.registry import actor_to_payload

    actor.require("benchmark:run")
    version, strategy = load_version(db, actor, version_id, "strategy:evolve")
    keys = _validate_suites(strategy.kind, data.benchmark_keys or default_evaluation_suites(strategy.kind))
    seeds = list(data.seeds or DEFAULT_EVALUATION_SEEDS)
    request_key = f"adhoc:{uuid.uuid4().hex}"
    request = {"benchmark_keys": keys, "seeds": seeds, "request_key": request_key}
    db.info.setdefault("after_commit", []).append(
        (run_evaluation_job, actor.organization_id, str(version.id), request, actor_to_payload(actor))
    )
    audit(
        db,
        actor,
        "STRATEGY_EVALUATION_REQUESTED",
        "strategy_version",
        version.id,
        after={"benchmark_keys": keys, "seeds": seeds, "request_key": request_key},
    )
    return schemas.EvaluationAcceptedOut(
        strategy_version_id=str(version.id), status="queued", request_key=request_key, benchmark_keys=keys, seeds=seeds
    )


# ---------------------------------------------------------------------------------------------
# Generations
# ---------------------------------------------------------------------------------------------
def _run_versions(db: Session, run: EvolutionRun, statuses: Sequence[str] | None = None) -> list[StrategyVersion]:
    stmt = select(StrategyVersion).where(StrategyVersion.evolution_run_id == run.id)
    if statuses is not None:
        stmt = stmt.where(StrategyVersion.status.in_(list(statuses)))
    return list(db.scalars(stmt.order_by(StrategyVersion.version)).all())


def _run_fitness(
    db: Session, run: EvolutionRun, versions: Sequence[StrategyVersion], names: Sequence[str]
) -> dict[uuid.UUID, dict[str, float | None] | None]:
    """Mean objectives of each version's evaluations inside this run (``None`` = not evaluated)."""
    rows = db.execute(
        select(StrategyEvaluation.strategy_version_id, StrategyEvaluation.objectives).where(
            StrategyEvaluation.evolution_run_id == run.id,
            StrategyEvaluation.strategy_version_id.in_([v.id for v in versions]),
        )
    ).all()
    grouped: dict[uuid.UUID, list[Mapping[str, Any]]] = {}
    for version_id, objectives in rows:
        grouped.setdefault(version_id, []).append(objectives or {})
    fitness: dict[uuid.UUID, dict[str, float | None] | None] = {}
    for version in versions:
        evaluations = grouped.get(version.id)
        if not evaluations:
            fitness[version.id] = None
            continue
        values: dict[str, float | None] = {}
        for name in names:
            numbers = [float(o[name]) for o in evaluations if isinstance(o.get(name), int | float)]
            values[name] = round(_mean(numbers), 9) if numbers else None
        fitness[version.id] = values
    return fitness


def _persist_children(
    db: Session,
    actor: Actor,
    run: EvolutionRun,
    strategy: Strategy,
    children: Sequence[Candidate],
    *,
    generation: int,
) -> tuple[list[StrategyVersion], list[dict[str, Any]]]:
    """Children → CANDIDATE versions + mutation rows (guardrails re-checked; duplicates skipped)."""
    created: list[StrategyVersion] = []
    rejected: list[dict[str, Any]] = []
    cache: dict[str, StrategyVersion | None] = {}

    def version_by_id(raw: str) -> StrategyVersion | None:
        if raw not in cache:
            try:
                found = db.get(StrategyVersion, uuid.UUID(raw))
            except ValueError:
                found = None
            cache[raw] = found if found is not None and found.strategy_id == strategy.id else None
        return cache[raw]

    for child in children:
        parent = version_by_id(child.parents[0]) if child.parents else None
        second = version_by_id(child.parents[1]) if len(child.parents) > 1 else None
        if parent is None:
            rejected.append({"candidate": child.id, "reason": "unknown_parent"})
            continue
        definition = dict(child.definition) if child.definition is not None else dict(parent.definition)
        if find_by_content(db, strategy.id, version_content_hash(definition, dict(child.params))) is not None:
            rejected.append({"candidate": child.id, "reason": "duplicate_of_existing_version"})
            continue
        try:
            with db.begin_nested():
                version = insert_version(
                    db,
                    strategy,
                    definition=definition,
                    parameters=child.params,
                    parent=parent,
                    created_by="evolution",
                    evolution_run_id=run.id,
                    generation=generation,
                    mutation_history=[m.model_dump(mode="json") for m in child.mutations],
                )
        except ValidationFailed as exc:
            rejected.append({"candidate": child.id, "reason": "guardrail", "details": exc.details})
            continue
        for record in child.mutations:
            first = version_by_id(record.parents[0]) if record.parents else parent
            other = version_by_id(record.parents[1]) if len(record.parents) > 1 else second
            record_mutation(
                db,
                strategy,
                version,
                operator=record.operator,
                diff={**record.diff(), "detail": record.detail},
                parent_version_id=(first or parent).id,
                second_parent_version_id=other.id if other is not None else None,
                seed=record.seed,
                generated_by="engine",
                evolution_run_id=run.id,
            )
        created.append(version)
    if created:
        emit(
            db,
            organization_id=run.organization_id,
            type=EventType.STRATEGY_MUTATED,
            payload={
                "strategy_id": str(strategy.id),
                "evolution_run_id": str(run.id),
                "generation": generation,
                "version_ids": [str(v.id) for v in created[:100]],
                "count": len(created),
                "rejected": len(rejected),
            },
            mission_id=run.mission_id,
            project_id=run.project_id,
            workspace_id=run.workspace_id,
            subject_type="evolution_run",
            subject_id=run.id,
            actor=actor,
        )
    return created, rejected


def _state(run: EvolutionRun, *, done: bool, children: Sequence[str] = (), replayed: bool = False) -> dict[str, Any]:
    generations = (run.summary or {}).get("generations") or []
    last = generations[-1] if generations and isinstance(generations[-1], Mapping) else {}
    return {
        "evolution_run_id": str(run.id),
        "generation": run.current_generation,
        "children_ids": list(children) or [str(c) for c in last.get("children") or []],
        "front_ids": [str(f) for f in last.get("front") or []],
        "done": done,
        "status": run.status,
        "replayed": replayed,
    }


def evolution_step(
    db_factory: DbFactory, actor: Actor, run_id: uuid.UUID | str, *, expected_generation: int | None = None
) -> dict[str, Any]:
    """Advance the run by one generation (generation 0 = initial mutations of the incumbent).

    Idempotent: when none of the latest generation's candidates has been evaluated yet (a retried step), or
    ``expected_generation`` is already reached, the current state is returned without advancing.
    """
    with db_factory() as db:
        run = _lock_run(db, load_evolution_run(db, actor, run_id, "strategy:evolve"))
        config = engine_config(run)
        total = int(config.generations or 0)
        if run.status in TERMINAL_RUN_STATES:
            return _state(run, done=True, replayed=True)
        initialized = bool((run.summary or {}).get("initialized"))
        if initialized and expected_generation is not None and run.current_generation >= expected_generation:
            return _state(run, done=run.current_generation >= total, replayed=True)
        strategy = db.get(Strategy, run.strategy_id)
        assert strategy is not None
        schema = ParameterSchema.from_dict(strategy.parameter_schema)
        engine = EvolutionEngine(config, schema)
        if run.status == RunState.PENDING:
            _transition_run(run, RunState.RUNNING)
        summary = dict(run.summary or {})
        history = list(summary.get("generations") or [])

        if not initialized:
            base = db.get(StrategyVersion, parse_uuid(summary.get("base_version_id"), "Strategy version"))
            if base is None or base.strategy_id != strategy.id:
                raise Conflict("The run's base version is missing", code="evolution_base_missing")
            try:
                drafts = engine.initialize(
                    base.parameters, n=config.population_size, definition=base.definition, base_id=str(base.id)
                )
            except (GuardrailViolation, MutationError) as exc:
                _transition_run(run, RunState.FAILED)
                run.error = f"initialization failed: {str(exc)[:1500]}"
                db.flush()
                return _state(run, done=True)
            created, rejected = _persist_children(db, actor, run, strategy, drafts, generation=0)
            history.append(
                {
                    "generation": 0,
                    "children": [str(v.id) for v in created],
                    "rejected_children": rejected,
                    "front": [],
                    "base_version_id": str(base.id),
                }
            )
            run.summary = {**summary, "initialized": True, "generations": history}
            run.current_generation = 0
            db.flush()
            return _state(run, done=False, children=[str(v.id) for v in created])

        generation = run.current_generation
        if generation >= total:
            return _state(run, done=True, replayed=True)
        live = _run_versions(db, run, LIVE_STATUSES)
        names = engine.fitness.names
        fitness = _run_fitness(db, run, live, names)
        latest = [v for v in live if v.generation == generation]
        if latest and all(fitness[v.id] is None for v in latest):
            state = _state(run, done=False, replayed=True)
            state["awaiting_evaluation"] = [str(v.id) for v in latest]
            return state
        if not live:
            _transition_run(run, RunState.FAILED)
            run.error = "no live candidates remain"
            db.flush()
            return _state(run, done=True)
        population = [
            Candidate(
                id=str(v.id),
                params=dict(v.parameters),
                parents=(str(v.parent_version_id),) if v.parent_version_id else (),
                generation=v.generation or 0,
                fitness=fitness[v.id],
                definition=dict(v.definition),
            )
            for v in live
        ]
        plan = engine.step(population, generation + 1, archive=run.archive or None)
        plan_doc = plan.to_json_dict()
        by_id = {str(v.id): v for v in live}
        for version_id, _current, target in survival_transitions(plan, {k: v.status for k, v in by_id.items()}):
            set_version_status(by_id[version_id], target)
        for version_id, assessment in plan.assessments.items():
            version = by_id.get(version_id)
            if version is None:
                continue
            version.pareto_rank = assessment.rank
            version.crowding = assessment.crowding if math.isfinite(assessment.crowding) else None
            version.fitness = dict(assessment.objectives)
        run.archive = plan_doc["archive"]
        run.current_generation = generation + 1
        done = run.current_generation >= total
        created = []
        rejected = []
        if not done:
            created, rejected = _persist_children(db, actor, run, strategy, plan.children, generation=generation + 1)
        unevaluated = [str(v.id) for v in live if fitness[v.id] is None]
        history.append(
            {
                "generation": generation + 1,
                "survivors": plan.survivors,
                "eliminated": plan.eliminated,
                "front": plan.front,
                "children": [str(v.id) for v in created],
                "rejected_children": rejected + [r.model_dump(mode="json") for r in plan.rejected_children][:50],
                "hypervolume": plan.hypervolume,
                "diversity": plan_doc["diversity"],
                "notes": plan.notes,
                "unevaluated": unevaluated,
            }
        )
        run.summary = {**summary, "generations": history}
        db.flush()
        _event(
            db,
            actor,
            run,
            EventType.EVOLUTION_GENERATION_COMPLETED,
            {
                "evolution_run_id": str(run.id),
                "strategy_id": str(strategy.id),
                "generation": run.current_generation,
                "front": plan.front[:50],
                "survivors": len(plan.survivors),
                "eliminated": len(plan.eliminated),
                "children": len(created),
                "hypervolume": plan.hypervolume,
                "done": done,
            },
        )
        return _state(run, done=done, children=[str(v.id) for v in created])


def finalize_evolution(db_factory: DbFactory, actor: Actor, run_id: uuid.UUID | str) -> dict[str, Any]:
    """Complete the run: best versions = rank-0 front; promotion candidates = eligible front members.

    Nothing is promoted: auto-promotion is a governance action the baseline policy denies, and every promotion
    requires a human (``POST /strategy-versions/{id}/promote``).
    """
    from aegis_api.lab.governance.policies import evaluate_policy
    from aegis_api.lab.strategies.promotion import AUTO_PROMOTION_ACTION, evaluate_gate

    with db_factory() as db:
        run = _lock_run(db, load_evolution_run(db, actor, run_id, "strategy:evolve"))
        if run.status == RunState.COMPLETED:
            summary = run.summary or {}
            return {
                "evolution_run_id": str(run.id),
                "best_version_ids": list(run.best_version_ids or []),
                "promotion_candidates": list(summary.get("promotion_candidate_ids") or []),
                "replayed": True,
            }
        if run.status in (RunState.CANCELLED, RunState.FAILED):
            raise Conflict(f"The evolution run is {run.status}", code="evolution_run_closed")
        total = int(engine_config(run).generations or 0)
        if not (run.summary or {}).get("initialized") or run.current_generation < total:
            raise Conflict(
                f"The run is at generation {run.current_generation} of {total}; finish the generations first",
                code="evolution_run_incomplete",
            )
        strategy = db.get(Strategy, run.strategy_id)
        assert strategy is not None
        live = _run_versions(db, run, (StrategyStatus.SURVIVING, StrategyStatus.EXPERIMENTAL))
        front = sorted(
            (v for v in live if v.pareto_rank == 0),
            key=lambda v: (-(float((v.fitness or {}).get("scientific_performance") or 0.0)), v.version),
        )
        candidates: list[dict[str, Any]] = []
        eligible_ids: list[str] = []
        for version in front:
            gate = evaluate_gate(db, version, strategy)
            candidates.append(
                {
                    "version_id": str(version.id),
                    "eligible": gate.decision.eligible,
                    "reasons": list(gate.decision.reasons)[:10],
                    "incumbent_version_id": str(gate.incumbent.id) if gate.incumbent is not None else None,
                }
            )
            if gate.decision.eligible:
                eligible_ids.append(str(version.id))
        mission = db.get(Mission, run.mission_id) if run.mission_id else None
        decision = evaluate_policy(
            db, actor, AUTO_PROMOTION_ACTION, {"risk_level": "MEDIUM"}, project_id=run.project_id, mission=mission
        )
        run.best_version_ids = [str(v.id) for v in front]
        run.summary = {
            **(run.summary or {}),
            "promotion_candidates": candidates,
            "promotion_candidate_ids": eligible_ids,
            "auto_promotion": {
                "effect": decision.effect,
                "reasons": list(decision.reasons),
                "applied": False,
                "detail": "strategy auto-promotion denied: evolution never promotes; a human must promote "
                "a candidate after reviewing its benchmark evidence",
            },
        }
        _transition_run(run, RunState.COMPLETED)
        db.flush()
        audit(
            db,
            actor,
            "EVOLUTION_COMPLETED",
            "evolution_run",
            run.id,
            after={"best_version_ids": run.best_version_ids, "promotion_candidates": eligible_ids},
        )
        return {
            "evolution_run_id": str(run.id),
            "best_version_ids": list(run.best_version_ids),
            "promotion_candidates": eligible_ids,
            "replayed": False,
        }
