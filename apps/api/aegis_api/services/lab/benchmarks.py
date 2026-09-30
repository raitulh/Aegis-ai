"""Benchmark runs: versioned suites scored deterministically, for platform components, agents and strategies.

* ``platform`` suites exercise deterministic components (failure classifier, verifier, manifest checks).
* ``strategy`` suites run the evolution engine on analytic problems (hypervolume).
* ``agent`` suites run the real agent runtime (model calls are metered like any other) on fixed cases.
Runs can be compared case-by-case with a paired test (``compare_runs``) to detect regressions.
"""

from __future__ import annotations

import json
import statistics
import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import NotFound, ValidationFailed
from aegis_api.models.lab import BenchmarkResult, BenchmarkRun
from aegis_api.security.context import Principal
from aegis_api.services.lab.common import Actor, sha256_json
from engines.lab.benchmarks.suites import (
    BenchmarkCase,
    compare_runs,
    platform_evolution_subject,
    platform_failure_subject,
    platform_repro_subject,
    platform_verification_subject,
    suites,
)
from engines.lab.prompts.registry import UntrustedBlock

PLATFORM_SUBJECTS = {
    "failure_analysis_bench": platform_failure_subject,
    "reproducibility_bench": platform_repro_subject,
    "claim_verification_bench": platform_verification_subject,
    "strategy_evolution_bench": platform_evolution_subject,
}


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "key": s.key,
            "version": s.version,
            "description": s.description,
            "subject_kind": s.subject_kind,
            "subject_role": s.subject_role,
            "cases": len(s.cases),
            "pass_threshold": s.pass_threshold,
        }
        for s in suites().values()
    ]


def create_run(db: Session, principal: Principal, *, suite_key: str, subject_ref: str | None) -> BenchmarkRun:
    principal.require("benchmark:run")
    suite = suites().get(suite_key)
    if suite is None:
        raise ValidationFailed(f"unknown suite '{suite_key}'")
    run = BenchmarkRun(
        organization_id=principal.organization_id,
        suite_key=suite.key,
        suite_version=suite.version,
        subject_kind=suite.subject_kind,
        subject_ref=(subject_ref or suite.subject_role or "platform")[:120],
        status="running",
        n_cases=len(suite.cases),
        comparison={},
        started_at=utcnow(),
    )
    db.add(run)
    db.flush()
    return run


def _agent_task_inputs(role: str, case: BenchmarkCase) -> tuple[dict[str, Any], str, list[UntrustedBlock]]:
    inp = case.input
    if role == "literature":
        blocks = [
            UntrustedBlock(content=str(t), source="benchmark", source_id=k)
            for k, t in (inp.get("sources") or {}).items()
        ]
        return {"queries": inp.get("question", "")}, str(inp.get("question", "")), blocks
    if role == "hypothesis":
        blocks = [
            UntrustedBlock(content=f"Reference source {s}", source="benchmark", source_id=s)
            for s in inp.get("source_ids", [])
        ]
        return (
            {
                "count": 3,
                "domain": inp.get("domain", "general"),
                "primary_metric": "primary",
                "parameter_space": "{}",
                "strategy_guidance": "none",
            },
            str(inp.get("objective", "")),
            blocks,
        )
    if role == "experiment_designer":
        return (
            {
                "hypothesis": inp.get("hypothesis", ""),
                "spec_contract": "ExperimentSpec (objective, baseline, metrics, success_criteria, statistical_plan, seeds, "
                "environment, resources, harness, parameters)",
                "environments": "python-stdlib",
                "harnesses": ", ".join(inp.get("harnesses", [])),
                "datasets": "none",
                "resource_limits": "cpu<=4, memory_mb<=8192, timeout_seconds<=3600, network none",
            },
            str(inp.get("hypothesis", "")),
            [],
        )
    if role == "coding":
        from aegis_api.services.lab.experiments import IO_CONTRACT

        return (
            {"spec": json.dumps(inp.get("spec", {})), "io_contract": json.dumps(IO_CONTRACT), "previous_error": "none"},
            "Implement the experiment candidate.",
            [],
        )
    raise ValidationFailed(f"no benchmark adapter for role '{role}'")


def execute(
    organization_id: uuid.UUID, run_id: uuid.UUID, *, actor: Actor, project_id: uuid.UUID | None
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        run = db.get(BenchmarkRun, run_id)
        if run is None:
            raise NotFound("Benchmark run not found")
        done = {r.case_id for r in db.scalars(select(BenchmarkResult).where(BenchmarkResult.run_id == run.id)).all()}
        suite_key, subject_kind, subject_ref = run.suite_key, run.subject_kind, run.subject_ref
    suite = suites()[suite_key]
    for case in suite.cases:
        if case.id in done:
            continue  # resumable after a crash
        try:
            if subject_kind in ("platform", "strategy"):
                output: Any = PLATFORM_SUBJECTS[suite_key](case)
            else:
                from aegis_api.services.lab.agents import AgentRunFailed, AgentTask, get_runtime

                variables, instructions, blocks = _agent_task_inputs(subject_ref, case)
                try:
                    result = get_runtime().run(
                        AgentTask(
                            organization_id=organization_id,
                            role=subject_ref,
                            variables=variables,
                            mission_instructions=instructions,
                            actor=actor,
                            project_id=project_id,
                            purpose=f"benchmark:{suite_key}:{case.id}",
                            research_data=blocks,
                        )
                    )
                    output = result.output
                except AgentRunFailed as exc:
                    output = {"error": str(exc)}
            score = suite.score(case, output)
        except Exception as exc:  # a crashing subject scores zero for the case; the run continues
            from engines.lab.benchmarks.suites import CaseScore

            output = {"error": f"{type(exc).__name__}: {exc}"[:500]}
            score = CaseScore(case.id, 0.0, False, {"error": output["error"]})
        with session_scope(organization_id) as db:
            db.add(
                BenchmarkResult(
                    organization_id=organization_id,
                    run_id=run_id,
                    case_id=case.id,
                    score=score.score,
                    passed=score.passed,
                    details=score.details,
                    output_sha256=sha256_json(output),
                )
            )
    return finalize(organization_id, run_id)


def finalize(organization_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        run = db.get(BenchmarkRun, run_id)
        assert run is not None
        results = db.scalars(select(BenchmarkResult).where(BenchmarkResult.run_id == run.id)).all()
        scores = {r.case_id: r.score for r in results}
        run.mean_score = round(statistics.fmean(scores.values()), 6) if scores else None
        run.pass_rate = round(sum(1 for r in results if r.passed) / len(results), 6) if results else None
        previous = db.scalar(
            select(BenchmarkRun)
            .where(
                BenchmarkRun.organization_id == organization_id,
                BenchmarkRun.suite_key == run.suite_key,
                BenchmarkRun.subject_ref == run.subject_ref,
                BenchmarkRun.status == "completed",
                BenchmarkRun.id != run.id,
            )
            .order_by(BenchmarkRun.completed_at.desc())
        )
        if previous is not None:
            prev_scores = {
                r.case_id: r.score
                for r in db.scalars(select(BenchmarkResult).where(BenchmarkResult.run_id == previous.id)).all()
            }
            run.comparison = {
                "previous_run_id": str(previous.id),
                **compare_runs(run.suite_key, prev_scores, scores).to_dict(),
            }
        run.status = "completed"
        run.completed_at = utcnow()
        return {
            "run_id": str(run.id),
            "mean_score": run.mean_score,
            "pass_rate": run.pass_rate,
            "comparison": run.comparison,
        }


def list_runs(db: Session, principal: Principal, *, suite_key: str | None = None) -> Select[BenchmarkRun]:
    stmt = select(BenchmarkRun).where(BenchmarkRun.organization_id == principal.organization_id)
    if suite_key:
        stmt = stmt.where(BenchmarkRun.suite_key == suite_key)
    return stmt.order_by(BenchmarkRun.created_at.desc())


def results(db: Session, run: BenchmarkRun) -> list[BenchmarkResult]:
    return list(
        db.scalars(
            select(BenchmarkResult).where(BenchmarkResult.run_id == run.id).order_by(BenchmarkResult.case_id)
        ).all()
    )
