"""Evolution engine (application side): Population → Evaluate → Rank → Select → Mutate → Candidates → Evaluate
→ Archive → Promotion gate.

The pure ``EvolutionEngine`` ranks *measured* individuals and proposes candidates; this service persists the
generation (survivors, mutations with lineage, guardrail rejections) and evaluates candidates in one of two
modes:

* ``experiment`` — the candidate's parameters are substituted into a template experiment (same code bundle,
  same harness, same baseline) and executed in the sandbox; fitness comes from harness-measured metrics.
* ``benchmark`` — analytic objective functions (StrategyEvolutionBench); pure platform code, no generated code.

Every candidate is checked against its parent for privilege escalation; escalations are rejected and logged.
"""

from __future__ import annotations

import math
import statistics
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models.lab import (
    ComputeUsage,
    EvolutionRun,
    Experiment,
    ExperimentRun,
    ExperimentVersion,
    Mission,
    Strategy,
    StrategyMutation,
    StrategyVersion,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import events
from aegis_api.services.lab import experiments as experiment_service
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab import strategies as strategy_service
from aegis_api.services.lab.access import get_scoped, scoped_ref
from aegis_api.services.lab.common import Actor
from engines.lab.benchmarks.functions import FUNCTIONS
from engines.lab.enums import LabEventType, RunKind, StrategyStatus
from engines.lab.evolution.engine import EvolutionConfig, EvolutionEngine
from engines.lab.evolution.genome import StrategyDefinition
from engines.lab.evolution.population import Individual
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.state_machines import STRATEGY

LIVE_STATUSES = (
    StrategyStatus.CANDIDATE,
    StrategyStatus.EXPERIMENTAL,
    StrategyStatus.SURVIVING,
    StrategyStatus.PROMOTED,
)


def create_run(
    db: Session,
    principal: Principal,
    *,
    strategy_id: uuid.UUID | str,
    config: dict[str, Any],
    max_generations: int,
    mode: str,
    experiment_template_id: uuid.UUID | None,
    benchmark: dict[str, Any] | None,
    mission_id: uuid.UUID | None = None,
) -> EvolutionRun:
    principal.require("strategy:evolve")
    strategy = get_scoped(db, principal, Strategy, strategy_id, label="Strategy")
    if mode not in ("experiment", "benchmark"):
        raise ValidationFailed("mode must be 'experiment' or 'benchmark'")
    if mode == "experiment":
        if experiment_template_id is None:
            raise ValidationFailed("experiment mode needs experiment_template_id")
        template = get_scoped(db, principal, Experiment, experiment_template_id, label="Experiment")
        tv = db.get(ExperimentVersion, template.current_version_id)
        if tv is None or tv.code_artifact_id is None or not (tv.validation or {}).get("valid"):
            raise ValidationFailed("template experiment needs a valid spec and attached code")
    if mode == "benchmark" and (benchmark or {}).get("function") not in FUNCTIONS:
        raise ValidationFailed(f"benchmark.function must be one of {sorted(FUNCTIONS)}")
    try:
        cfg = EvolutionConfig.model_validate({**config, "max_generations": max_generations})
    except Exception as exc:
        raise ValidationFailed(f"invalid evolution config: {exc}") from exc
    if strategy.project_id is None:
        raise ValidationFailed("evolution runs need a project-scoped strategy")
    mission = scoped_ref(db, principal, Mission, mission_id, project_id=strategy.project_id, label="Mission")
    run = EvolutionRun(
        organization_id=principal.organization_id,
        project_id=strategy.project_id,
        mission_id=mission.id if mission else None,
        strategy_id=strategy.id,
        config={
            "engine": cfg.model_dump(mode="json"),
            "mode": mode,
            "experiment_template_id": str(experiment_template_id) if experiment_template_id else None,
            "benchmark": benchmark or {},
        },
        status="running",
        max_generations=max_generations,
        started_at=utcnow(),
    )
    db.add(run)
    db.flush()
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.evolution.started",
        resource_type="evolution_run",
        resource_id=run.id,
        principal=principal,
        after={"strategy_id": str(strategy.id), "mode": mode, "max_generations": max_generations},
    )
    return run


def start(db: Session, principal: Principal, **kwargs: Any) -> EvolutionRun:
    """Create an evolution run and start its EvolutionWorkflow on behalf of ``principal``."""
    from aegis_api.workflows import client as workflow_client

    run = create_run(db, principal, **kwargs)
    wf = workflow_client.start(
        db,
        organization_id=principal.organization_id,
        workflow="evolution",
        business_key=f"evolution:{run.id}",
        payload={"evolution_run_id": str(run.id)},
        principal=principal,
    )
    run.workflow_run_id = wf.id
    return run


def _load(db: Session, organization_id: uuid.UUID, evolution_run_id: uuid.UUID) -> tuple[EvolutionRun, Strategy]:
    run = db.get(EvolutionRun, evolution_run_id)
    if run is None or run.organization_id != organization_id:
        raise NotFound("Evolution run not found")
    strategy = db.get(Strategy, run.strategy_id)
    assert strategy is not None
    return run, strategy


def pending_candidates(organization_id: uuid.UUID, evolution_run_id: uuid.UUID) -> list[str]:
    with session_scope(organization_id) as db:
        _run, strategy = _load(db, organization_id, evolution_run_id)
        rows = db.scalars(
            select(StrategyVersion).where(
                StrategyVersion.strategy_id == strategy.id,
                StrategyVersion.status.in_(LIVE_STATUSES),
                StrategyVersion.evaluations == 0,
            )
        ).all()
        return [str(v.id) for v in sorted(rows, key=lambda v: v.version)]


def _individuals(db: Session, strategy: Strategy) -> list[Individual]:
    out = []
    for v in db.scalars(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy.id, StrategyVersion.status.in_(LIVE_STATUSES)
        )
    ).all():
        if v.evaluations <= 0:
            continue
        out.append(
            Individual(
                id=str(v.id),
                definition=StrategyDefinition.model_validate(v.definition),
                generation=v.generation,
                parent_ids=[str(v.parent_version_id)] if v.parent_version_id else [],
                metrics={k: float(x) for k, x in (v.metrics or {}).items() if isinstance(x, int | float)},
                evaluations=v.evaluations,
            )
        )
    return out


def generation_step(organization_id: uuid.UUID, evolution_run_id: uuid.UUID, *, actor: Actor) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        run, strategy = _load(db, organization_id, evolution_run_id)
        cfg = EvolutionConfig.model_validate(run.config["engine"])
        individuals = _individuals(db, strategy)
        if len(individuals) < 2:
            return {"status": "insufficient_population", "evaluated": len(individuals)}
        generation = run.generation + 1
        engine = EvolutionEngine(cfg)
        seen = {
            str(h)
            for h in db.scalars(
                select(StrategyVersion.parameter_hash).where(StrategyVersion.strategy_id == strategy.id)
            ).all()
        }
        outcome = engine.step(individuals, generation, seen_hashes=seen)
        survivors = set(outcome.survivors)
        by_id = {
            str(v.id): v
            for v in db.scalars(select(StrategyVersion).where(StrategyVersion.strategy_id == strategy.id)).all()
        }
        for ind in individuals:
            v = by_id[ind.id]
            if v.id == strategy.promoted_version_id:
                continue
            if ind.id in survivors:
                for target in (StrategyStatus.EXPERIMENTAL, StrategyStatus.SURVIVING):
                    if STRATEGY.can(v.status, target):
                        v.status = target
            elif STRATEGY.can(v.status, StrategyStatus.RETIRED):
                v.status = StrategyStatus.RETIRED
                v.retired_at = utcnow()
        created: list[str] = []
        for proposal in outcome.candidates:
            parent = by_id.get(proposal.parent_ids[0]) if proposal.parent_ids else None
            problems = strategy_service.escalation_problems(parent, proposal.definition)
            decision = lab_policy.evaluate(
                db,
                organization_id=organization_id,
                action="strategy.mutate",
                facts=lab_policy.base_facts(
                    db, organization_id, actor, extra={"strategy": {"governance_escalation": bool(problems)}}
                ),
                actor=actor,
                resource_type="strategy",
                resource_id=str(strategy.id),
            )
            if decision.denied or problems:
                db.add(
                    StrategyMutation(
                        organization_id=organization_id,
                        parent_version_id=parent.id if parent else next(iter(by_id.values())).id,
                        evolution_run_id=run.id,
                        generation=generation,
                        operator=proposal.record.operator[:32],
                        changes=[c.__dict__ for c in proposal.record.changes],
                        seed=proposal.record.seed,
                        source=proposal.record.source,
                        rejected=True,
                        rejection_reason="; ".join(problems + decision.reasons)[:2000],
                    )
                )
                continue
            child = strategy_service._add_version(
                db,
                strategy,
                proposal.definition,
                parent=parent,
                origin="engine" if proposal.record.source == "engine" else "agent_proposal",
                generation=generation,
                evolution_run_id=run.id,
            )
            strategy_service.link_to_graph(db, child, parent, strategy.project_id)
            db.add(
                StrategyMutation(
                    organization_id=organization_id,
                    parent_version_id=parent.id if parent else child.id,
                    second_parent_version_id=uuid.UUID(proposal.parent_ids[1])
                    if len(proposal.parent_ids) > 1
                    else None,
                    child_version_id=child.id,
                    evolution_run_id=run.id,
                    generation=generation,
                    operator=proposal.record.operator[:32],
                    changes=[c.__dict__ for c in proposal.record.changes],
                    seed=proposal.record.seed,
                    source=proposal.record.source,
                )
            )
            created.append(str(child.id))
        for rejected in outcome.rejected_candidates:
            db.add(
                StrategyMutation(
                    organization_id=organization_id,
                    parent_version_id=uuid.UUID(str(rejected["parent"]))
                    if rejected.get("parent")
                    else next(iter(by_id.values())).id,
                    evolution_run_id=run.id,
                    generation=generation,
                    operator=str(rejected.get("operator", "mutation"))[:32],
                    changes=[],
                    source="engine",
                    rejected=True,
                    rejection_reason=str(rejected.get("reason", "rejected by engine"))[:2000],
                )
            )
        summary = outcome.summary()
        run.generation = generation
        run.generations = [*(run.generations or []), {**summary, "created": created}]
        run.archive_version_ids = sorted(set(engine.archive.members))
        if run.mission_id or strategy.project_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=run.mission_id,
                project_id=strategy.project_id,
                event_type=LabEventType.STRATEGY_MUTATED,
                message=f"Generation {generation}: {len(created)} candidate(s), {len(survivors)} survivor(s), "
                f"diversity {summary['diversity']}",
                data={"evolution_run_id": str(run.id), "generation": generation, "created": created},
                actor=actor,
            )
        return {"status": "ok", "generation": generation, "created": created, "summary": summary}


# --- candidate evaluation ------------------------------------------------------------------------------------


def prepare_candidate_experiment(
    organization_id: uuid.UUID, evolution_run_id: uuid.UUID, version_id: uuid.UUID, *, actor: Actor
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        run, _strategy = _load(db, organization_id, evolution_run_id)
        version = db.get(StrategyVersion, version_id)
        template = db.get(Experiment, uuid.UUID(run.config["experiment_template_id"]))
        if version is None or template is None:
            raise NotFound("candidate or template not found")
        existing = db.scalar(
            select(Experiment.id).where(
                Experiment.strategy_version_id == version.id,
                Experiment.mission_id == run.mission_id,
                Experiment.title.like(f"[evolution {run.id}]%"),
            )
        )
        if existing:
            return {"experiment_id": str(existing)}
        tv = db.get(ExperimentVersion, template.current_version_id)
        assert tv is not None
        spec = dict(tv.spec)
        spec["parameters"] = {**(spec.get("parameters") or {}), **(version.definition.get("parameters") or {})}
        spec_obj, validation = experiment_service.validate_spec(db, organization_id, spec)
        experiment = Experiment(
            organization_id=organization_id,
            project_id=template.project_id,
            mission_id=run.mission_id,
            hypothesis_id=template.hypothesis_id,
            strategy_version_id=version.id,
            title=f"[evolution {run.id}] g{version.generation} v{version.version}: {template.title}"[:300],
            status="draft",
        )
        db.add(experiment)
        db.flush()
        experiment_service._add_version(
            db,
            experiment,
            spec_obj.model_dump(mode="json") if spec_obj else spec,
            validation,
            code_artifact_id=tv.code_artifact_id,
            code_sha256=tv.code_sha256,
            generated_by_run_id=tv.generated_by_run_id,
            change_note=f"evolution candidate {version.id} (parameters from strategy v{version.version})",
        )
        return {"experiment_id": str(experiment.id), "valid": bool(validation.get("valid"))}


def record_candidate_result(
    organization_id: uuid.UUID, version_id: uuid.UUID, experiment_id: uuid.UUID, *, reproduced: bool | None = None
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        version = db.get(StrategyVersion, version_id)
        experiment = db.get(Experiment, experiment_id)
        if version is None or experiment is None:
            raise NotFound("candidate or experiment not found")
        ev = db.get(ExperimentVersion, experiment.current_version_id)
        assert ev is not None
        spec = ExperimentSpec.model_validate(ev.spec)
        primary = spec.primary_metric
        runs = list(
            db.scalars(
                select(ExperimentRun).where(
                    ExperimentRun.experiment_version_id == ev.id,
                    ExperimentRun.run_kind.in_([RunKind.CANDIDATE, RunKind.REPRODUCTION]),
                )
            ).all()
        )
        done = [r for r in runs if r.status == "completed" and primary and primary.name in (r.metrics or {})]
        failed = len(runs) - len(done)
        samples = [float(r.metrics[primary.name]) for r in done] if primary else []
        sign = 1.0 if (primary is None or primary.direction == "maximize") else -1.0
        costs = list(db.scalars(select(ComputeUsage.cost_usd).where(ComputeUsage.experiment_id == experiment.id)).all())
        metrics: dict[str, float | None] = {
            "primary": sign * statistics.fmean(samples) if samples else None,
            "primary_std": statistics.pstdev(samples) if len(samples) > 1 else 0.0 if samples else None,
            "compute_seconds": sum(float((r.resources or {}).get("runtime_seconds") or 0.0) for r in runs) or None,
            "cost_usd": sum(float(c) for c in costs if c is not None) if costs and None not in costs else None,
            "failure_rate": failed / len(runs) if runs else None,
        }
        row = strategy_service.record_evaluation(
            db,
            version=version,
            source="experiment",
            metrics=metrics,
            primary_samples=[sign * s for s in samples],
            mission_id=experiment.mission_id,
            experiment_id=experiment.id,
            reproduced=reproduced,
        )
        return {"strategy_evaluation_id": str(row.id), "metrics": metrics, "samples": len(samples)}


def evaluate_benchmark(
    organization_id: uuid.UUID, evolution_run_id: uuid.UUID, version_id: uuid.UUID
) -> dict[str, Any]:
    """Analytic objective evaluation (platform code; no untrusted code executes)."""
    with session_scope(organization_id) as db:
        run, _ = _load(db, organization_id, evolution_run_id)
        version = db.get(StrategyVersion, version_id)
        if version is None:
            raise NotFound("candidate not found")
        bench = run.config.get("benchmark") or {}
        fn = FUNCTIONS[bench["function"]]
        params = version.definition.get("parameters") or {}
        keys = sorted(k for k in params if k.startswith("x"))
        x = [float(params[k]) for k in keys]
        value = fn(x)
        oriented = -value  # benchmark functions are minimized
        if not math.isfinite(oriented):
            oriented = -1e12
        metrics: dict[str, float | None] = {"primary": oriented, "primary_std": 0.0, "compute_seconds": 0.0}
        row = strategy_service.record_evaluation(
            db, version=version, source="benchmark", metrics=metrics, primary_samples=[oriented], reproduced=True
        )
        return {"strategy_evaluation_id": str(row.id), "objective_value": value}


def best_candidate(organization_id: uuid.UUID, evolution_run_id: uuid.UUID) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        _run, strategy = _load(db, organization_id, evolution_run_id)
        live = list(
            db.scalars(
                select(StrategyVersion).where(
                    StrategyVersion.strategy_id == strategy.id,
                    StrategyVersion.status.in_(LIVE_STATUSES),
                    StrategyVersion.evaluations > 0,
                )
            ).all()
        )
        feasible = [v for v in live if (v.fitness or {}).get("feasible", True)]
        if not feasible:
            return {"version_id": None}
        best = max(feasible, key=lambda v: (float((v.metrics or {}).get("primary", float("-inf"))), -v.version))
        return {
            "version_id": str(best.id),
            "primary": (best.metrics or {}).get("primary"),
            "is_active": best.id == strategy.promoted_version_id,
            "strategy_id": str(strategy.id),
        }


def finish(organization_id: uuid.UUID, evolution_run_id: uuid.UUID, status: str) -> None:
    with session_scope(organization_id) as db:
        run, _ = _load(db, organization_id, evolution_run_id)
        run.status = status
        run.completed_at = utcnow()


def run_dict(r: EvolutionRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "strategy_id": str(r.strategy_id),
        "project_id": str(r.project_id),
        "mission_id": str(r.mission_id) if r.mission_id else None,
        "status": r.status,
        "generation": r.generation,
        "max_generations": r.max_generations,
        "config": r.config,
        "generations": r.generations,
        "archive_version_ids": r.archive_version_ids,
        "workflow_run_id": str(r.workflow_run_id) if r.workflow_run_id else None,
    }
