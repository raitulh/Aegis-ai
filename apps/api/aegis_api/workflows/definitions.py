"""The lab's durable workflows (engine-neutral; see ``workflows.api`` for the determinism rules).

MissionWorkflow · ResearchWorkflow · HypothesisWorkflow · ExperimentWorkflow · ExperimentBatchWorkflow ·
EvaluationWorkflow · VerificationWorkflow · EvolutionWorkflow · DiscoveryWorkflow · ReportWorkflow ·
DatasetProcessingWorkflow · ArtifactProcessingWorkflow (+ BenchmarkWorkflow).

All side effects run in activities; workflows only sequence them, wait for humans (approval signals with
timeouts and a polling fallback), react to failures and honour pause/cancel at checkpoints.
"""

from __future__ import annotations

from typing import Any

from aegis_api.workflows.api import (
    NO_RETRY,
    ActivityFailure,
    RetryPolicy,
    WorkflowCancelled,
    WorkflowContext,
    WorkflowDefinition,
)

APPROVAL_POLL_SECONDS = 6 * 3600
DEEP_RESEARCH_POLL_SECONDS = 20
DEEP_RESEARCH_MAX_POLLS = 540  # ~3 hours
EXECUTION_RETRY = RetryPolicy(
    max_attempts=3, initial_interval_seconds=5, non_retryable=("approval_required", "policy_denied")
)
AGENT_RETRY = RetryPolicy(max_attempts=2, initial_interval_seconds=5)


async def wait_for_approval(ctx: WorkflowContext, approval_id: str, signal: str, *, key: str) -> dict[str, Any]:
    """Wait for a human decision; if a signal is ever lost, the periodic status poll still observes it."""
    polls = 0
    while True:
        payload = await ctx.wait_signal(signal, key=f"{key}:wait:{polls}", timeout_seconds=APPROVAL_POLL_SECONDS)
        if payload is not None:
            return payload
        status = await ctx.activity("approval.status", {"approval_id": approval_id}, key=f"{key}:poll:{polls}")
        if status.get("status") != "pending":
            return status
        polls += 1


async def checkpoint(ctx: WorkflowContext, mission_id: str, *, key: str) -> dict[str, Any]:
    """Pause/cancel point: paused missions wait for ``mission.resume``; cancelled missions stop."""
    state = await ctx.activity("mission.checkpoint", {"mission_id": mission_id}, key=f"cp:{key}")
    resumes = 0
    while state.get("status") == "paused":
        await ctx.wait_signal("mission.resume", key=f"cp:{key}:resume:{resumes}")
        resumes += 1
        state = await ctx.activity("mission.checkpoint", {"mission_id": mission_id}, key=f"cp:{key}:{resumes}")
    if state.get("status") in ("cancelled", "archived") or state.get("cancel_requested"):
        raise WorkflowCancelled()
    return state


async def gate(
    ctx: WorkflowContext, mission_id: str, action: str, *, key: str, details: dict[str, Any] | None = None
) -> bool:
    """Autonomy/policy gate for a mission action; waits for a human when required. True = proceed."""
    decision = await ctx.activity(
        "mission.gate", {"mission_id": mission_id, "action": action, "details": details or {}}, key=f"gate:{key}"
    )
    if decision["decision"] == "allow":
        return True
    if decision["decision"] == "deny":
        return False
    outcome = await wait_for_approval(ctx, decision["approval_id"], decision["signal"], key=f"gate:{key}")
    return bool(outcome.get("approved"))


# --- MissionWorkflow -----------------------------------------------------------------------------------------


async def mission_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    mission_id = ctx.input["mission_id"]
    mission = await ctx.activity("mission.start", {"mission_id": mission_id}, key="start")
    summary: dict[str, Any] = {"cycles": [], "discovery_workflows": []}
    try:
        await ctx.activity("mission.phase", {"mission_id": mission_id, "phase": "planning"}, key="phase:planning")
        await ctx.activity("agent.quest", {"mission_id": mission_id}, key="quest", retry=AGENT_RETRY)
        plan = await ctx.activity("agent.plan", {"mission_id": mission_id}, key="plan", retry=AGENT_RETRY)
        if not await gate(ctx, mission_id, "plan.execute", key="plan", details={"plan": plan.get("summary")}):
            await ctx.activity(
                "mission.finish",
                {"mission_id": mission_id, "status": "failed", "reason": "plan was not approved"},
                key="finish:plan-rejected",
            )
            return {"status": "plan_rejected"}
        max_cycles = int(mission.get("max_cycles", 1))
        for cycle in range(1, max_cycles + 1):
            await checkpoint(ctx, mission_id, key=f"c{cycle}:start")
            if cycle > 1 and not await gate(ctx, mission_id, "mission.next_cycle", key=f"c{cycle}"):
                break
            cycle_result: dict[str, Any] = {"cycle": cycle}
            await ctx.activity("mission.cycle", {"mission_id": mission_id, "cycle": cycle}, key=f"c{cycle}:cycle")
            source_ids: list[str] = []
            if not mission.get("skip_research"):
                await ctx.activity(
                    "mission.phase", {"mission_id": mission_id, "phase": "literature"}, key=f"c{cycle}:phase:lit"
                )
                task = await ctx.activity(
                    "mission.create_research",
                    {"mission_id": mission_id, "cycle": cycle, "queries": plan.get("search_queries", [])},
                    key=f"c{cycle}:research-task",
                )
                try:
                    research = await ctx.child(
                        "research", {"research_task_id": task["research_task_id"]}, key=f"c{cycle}:research"
                    )
                    source_ids = list(research.get("source_ids", []))
                except ActivityFailure as exc:
                    await ctx.activity(
                        "failure.analyze",
                        {
                            "mission_id": mission_id,
                            "stage": "research",
                            "signal": {"stage": "research", "error_message": exc.message, "provider_error": exc.code},
                        },
                        key=f"c{cycle}:research-failure",
                    )
            await checkpoint(ctx, mission_id, key=f"c{cycle}:hyp")
            await ctx.activity(
                "mission.phase", {"mission_id": mission_id, "phase": "hypothesis_generation"}, key=f"c{cycle}:phase:hyp"
            )
            hyp = await ctx.child(
                "hypothesis",
                {"mission_id": mission_id, "cycle": cycle, "source_ids": source_ids},
                key=f"c{cycle}:hypothesis",
            )
            cycle_result["hypotheses"] = hyp.get("selected", [])
            experiments: list[dict[str, Any]] = []
            for index, hypothesis_id in enumerate(hyp.get("selected", [])):
                await checkpoint(ctx, mission_id, key=f"c{cycle}:h{index}")
                if not await gate(ctx, mission_id, "experiment.design", key=f"c{cycle}:h{index}:design"):
                    continue
                await ctx.activity(
                    "mission.phase",
                    {"mission_id": mission_id, "phase": "experiment_design"},
                    key=f"c{cycle}:h{index}:phase:design",
                )
                design = await ctx.activity(
                    "experiment.prepare",
                    {"mission_id": mission_id, "hypothesis_id": hypothesis_id},
                    key=f"c{cycle}:h{index}:prepare",
                    retry=AGENT_RETRY,
                    timeout_seconds=1800,
                )
                if not design.get("ready"):
                    await ctx.activity(
                        "failure.analyze",
                        {
                            "mission_id": mission_id,
                            "stage": "design",
                            "experiment_id": design.get("experiment_id"),
                            "signal": {"stage": "design", "validation_codes": design.get("issues", [])},
                        },
                        key=f"c{cycle}:h{index}:design-failure",
                    )
                    continue
                await ctx.activity(
                    "mission.phase",
                    {"mission_id": mission_id, "phase": "execution"},
                    key=f"c{cycle}:h{index}:phase:exec",
                )
                batch = await ctx.child(
                    "experiment_batch",
                    {"experiment_id": design["experiment_id"], "mission_id": mission_id},
                    key=f"c{cycle}:h{index}:batch",
                )
                experiments.append(batch)
                if batch.get("status") != "evaluated":
                    continue
                await ctx.activity(
                    "mission.phase",
                    {"mission_id": mission_id, "phase": "verification"},
                    key=f"c{cycle}:h{index}:phase:verify",
                )
                for claim_id in batch.get("claim_ids", [])[:2]:
                    if not await gate(ctx, mission_id, "reproduction.run", key=f"verify:{claim_id}"):
                        continue
                    ver = await ctx.activity(
                        "verification.create", {"claim_id": claim_id}, key=f"verify:{claim_id}:create"
                    )
                    verdict = await ctx.child(
                        "verification", {"verification_id": ver["verification_id"]}, key=f"verify:{claim_id}"
                    )
                    if verdict.get("status") == "verified":
                        started = await ctx.activity(
                            "discovery.start", {"claim_id": claim_id}, key=f"discovery:{claim_id}"
                        )
                        summary["discovery_workflows"].append(started)
                evolve = bool(mission.get("evolution") and mission.get("strategy_id"))
                if evolve and await gate(ctx, mission_id, "evolution.mutate", key=f"c{cycle}:h{index}:evolve"):
                    await ctx.activity(
                        "mission.phase",
                        {"mission_id": mission_id, "phase": "evolution"},
                        key=f"c{cycle}:h{index}:phase:evolve",
                    )
                    evo = await ctx.activity(
                        "evolution.create",
                        {"mission_id": mission_id, "experiment_id": design["experiment_id"]},
                        key=f"c{cycle}:h{index}:evolution",
                    )
                    await ctx.child(
                        "evolution", {"evolution_run_id": evo["evolution_run_id"]}, key=f"c{cycle}:h{index}:evolve"
                    )
            cycle_result["experiments"] = experiments
            summary["cycles"].append(cycle_result)
        await checkpoint(ctx, mission_id, key="report")
        await ctx.activity("mission.phase", {"mission_id": mission_id, "phase": "reporting"}, key="phase:report")
        report = await ctx.child("report", {"mission_id": mission_id}, key="report")
        summary["report"] = report
        await ctx.activity(
            "mission.finish", {"mission_id": mission_id, "status": "completed", "summary": summary}, key="finish"
        )
        return summary
    except WorkflowCancelled:
        await ctx.activity("mission.finish", {"mission_id": mission_id, "status": "cancelled"}, key="finish:cancelled")
        raise
    except ActivityFailure as exc:
        await ctx.activity(
            "mission.finish", {"mission_id": mission_id, "status": "failed", "reason": str(exc)}, key="finish:failed"
        )
        raise


# --- ResearchWorkflow ---------------------------------------------------------------------------------------


async def research_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    task_id = ctx.input["research_task_id"]
    state = await ctx.activity("research.state", {"research_task_id": task_id}, key="state")
    try:
        await ctx.activity(
            "research.transition", {"research_task_id": task_id, "status": "planning"}, key="to-planning"
        )
        plan = await ctx.activity("research.plan", {"research_task_id": task_id}, key="plan", retry=AGENT_RETRY)
        if state.get("require_plan_approval") and plan.get("approval_id"):
            outcome = await wait_for_approval(ctx, plan["approval_id"], plan["signal"], key="plan-approval")
            if not outcome.get("approved"):
                await ctx.activity(
                    "research.transition",
                    {"research_task_id": task_id, "status": "cancelled", "error": "research plan rejected"},
                    key="to-cancelled",
                )
                return {"status": "cancelled", "source_ids": []}
        await ctx.activity(
            "research.transition", {"research_task_id": task_id, "status": "approved"}, key="to-approved"
        )
        await ctx.activity("research.transition", {"research_task_id": task_id, "status": "running"}, key="to-running")
        if state["mode"] == "deep_research":
            await ctx.activity("research.start_deep", {"research_task_id": task_id}, key="start-deep")
            for poll in range(DEEP_RESEARCH_MAX_POLLS):
                status = await ctx.activity("research.poll_deep", {"research_task_id": task_id}, key=f"poll:{poll}")
                if status["status"] in ("completed", "failed", "cancelled"):
                    break
                await ctx.sleep(DEEP_RESEARCH_POLL_SECONDS, key=f"sleep:{poll}")
            else:
                await ctx.activity("research.cancel_deep", {"research_task_id": task_id}, key="cancel-timeout")
                raise ActivityFailure("research.poll_deep", "Deep Research exceeded its time budget", code="timeout")
            done = await ctx.activity("research.complete_deep", {"research_task_id": task_id}, key="complete-deep")
            return {"status": done["status"], "source_ids": done.get("source_ids", [])}
        found = await ctx.activity(
            "research.search", {"research_task_id": task_id, "queries": plan.get("queries", [])}, key="search"
        )
        review = await ctx.activity(
            "research.literature_review",
            {"research_task_id": task_id, "source_ids": found["source_ids"]},
            key="review",
            retry=AGENT_RETRY,
        )
        return {"status": "completed", "source_ids": found["source_ids"], "findings": len(review.get("findings", []))}
    except WorkflowCancelled:
        await ctx.activity("research.cancel_deep", {"research_task_id": task_id}, key="cancel")
        await ctx.activity(
            "research.transition", {"research_task_id": task_id, "status": "cancelled"}, key="to-cancelled-2"
        )
        raise
    except ActivityFailure as exc:
        await ctx.activity(
            "research.transition", {"research_task_id": task_id, "status": "failed", "error": str(exc)}, key="to-failed"
        )
        raise


# --- HypothesisWorkflow -------------------------------------------------------------------------------------


async def hypothesis_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    mission_id = ctx.input["mission_id"]
    generated = await ctx.activity(
        "hypothesis.generate",
        {"mission_id": mission_id, "source_ids": ctx.input.get("source_ids", [])},
        key="generate",
        retry=AGENT_RETRY,
    )
    ids = generated["hypothesis_ids"]
    await ctx.activity(
        "mission.phase", {"mission_id": mission_id, "phase": "hypothesis_critique"}, key="phase:critique"
    )
    await ctx.activity(
        "hypothesis.critique", {"mission_id": mission_id, "hypothesis_ids": ids}, key="critique", retry=AGENT_RETRY
    )
    if not await gate(ctx, mission_id, "hypothesis.select", key="select", details={"hypothesis_ids": ids}):
        return {"generated": ids, "selected": []}
    selected = await ctx.activity("hypothesis.select", {"mission_id": mission_id, "hypothesis_ids": ids}, key="select")
    return {"generated": ids, "selected": selected["selected"]}


# --- ExperimentWorkflow (one run) --------------------------------------------------------------------------


async def experiment_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    run_id = ctx.input["run_id"]
    if ctx.input.get("gate"):
        pre = await ctx.activity(
            "experiment.preflight",
            {"experiment_id": ctx.input["experiment_id"], "runs": 1, "action": "reproduction.run"},
            key="preflight",
        )
        if pre["decision"] == "deny":
            return {"status": "denied", "reasons": pre.get("reasons", [])}
        if pre["decision"] == "approval_required":
            outcome = await wait_for_approval(ctx, pre["approval_id"], pre["signal"], key="approval")
            if not outcome.get("approved"):
                return {"status": "not_approved"}
    result = await ctx.activity(
        "execution.run",
        {"run_id": run_id},
        key="run",
        retry=EXECUTION_RETRY,
        timeout_seconds=7200,
        heartbeat_seconds=None,
    )
    if result["status"] == "failed" and result.get("signal"):
        analysis = await ctx.activity(
            "failure.analyze",
            {
                "experiment_run_id": run_id,
                "experiment_id": result["experiment_id"],
                "stage": "execution",
                "signal": result["signal"],
            },
            key="analyze",
        )
        result["failure"] = analysis
    return result


# --- ExperimentBatchWorkflow --------------------------------------------------------------------------------


async def experiment_batch_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    experiment_id = ctx.input["experiment_id"]
    queued = await ctx.activity("experiment.queue", {"experiment_id": experiment_id}, key="queue")
    plan = await ctx.activity("experiment.plan_runs", {"experiment_id": experiment_id}, key="plan")
    pre = await ctx.activity(
        "experiment.preflight", {"experiment_id": experiment_id, "runs": len(plan["run_ids"])}, key="preflight"
    )
    if pre["decision"] == "deny":
        await ctx.activity(
            "experiment.set_status",
            {"experiment_id": experiment_id, "status": "failed", "error": "; ".join(pre.get("reasons", []))},
            key="denied",
        )
        return {"status": "denied", "reasons": pre.get("reasons", [])}
    if pre["decision"] == "approval_required":
        outcome = await wait_for_approval(ctx, pre["approval_id"], pre["signal"], key="execution-approval")
        if not outcome.get("approved"):
            await ctx.activity(
                "experiment.set_status",
                {"experiment_id": experiment_id, "status": "failed", "error": "execution not approved"},
                key="not-approved",
            )
            return {"status": "not_approved"}
    await ctx.activity("experiment.set_status", {"experiment_id": experiment_id, "status": "running"}, key="running")
    results = await ctx.gather(
        *[
            ctx.child("experiment", {"run_id": run_id, "experiment_id": experiment_id}, key=f"run:{run_id}")
            for run_id in plan["run_ids"]
        ]
    )
    failed = [r for r in results if r.get("status") != "completed"]
    recovered = False
    if failed:
        recovery = await ctx.activity(
            "experiment.recover",
            {"experiment_id": experiment_id, "failures": [r.get("failure") for r in failed if r.get("failure")]},
            key="recover",
            retry=AGENT_RETRY,
        )
        if recovery.get("retry"):
            plan2 = await ctx.activity("experiment.plan_runs", {"experiment_id": experiment_id}, key="plan-retry")
            retried = await ctx.gather(
                *[
                    ctx.child("experiment", {"run_id": run_id, "experiment_id": experiment_id}, key=f"retry:{run_id}")
                    for run_id in plan2["run_ids"]
                    if run_id not in plan["run_ids"]
                ]
            )
            failed = [r for r in retried if r.get("status") != "completed"]
            recovered = not failed
    completed = await ctx.activity("experiment.completion", {"experiment_id": experiment_id}, key="completion")
    if not completed.get("evaluable"):
        await ctx.activity(
            "experiment.set_status",
            {
                "experiment_id": experiment_id,
                "status": "failed",
                "error": completed.get("reason", "insufficient completed runs"),
            },
            key="failed",
        )
        return {"status": "failed", "experiment_id": experiment_id, "reason": completed.get("reason"), "queued": queued}
    evaluation = await ctx.child("evaluation", {"experiment_id": experiment_id}, key="evaluation")
    return {"status": "evaluated", "experiment_id": experiment_id, "recovered": recovered, **evaluation}


# --- EvaluationWorkflow -------------------------------------------------------------------------------------


async def evaluation_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    experiment_id = ctx.input["experiment_id"]
    evaluation = await ctx.activity("evaluation.evaluate", {"experiment_id": experiment_id}, key="evaluate")
    claims = await ctx.activity(
        "evaluation.claims", {"experiment_id": experiment_id, "evaluation": evaluation}, key="claims"
    )
    interpretation = await ctx.activity(
        "evaluation.interpret",
        {"experiment_id": experiment_id, "rows": evaluation.get("statistical_rows", [])},
        key="interpret",
        retry=AGENT_RETRY,
    )
    review = await ctx.activity("evaluation.review", {"experiment_id": experiment_id}, key="review")
    return {
        "outcome": evaluation["outcome"],
        "claim_ids": claims["claim_ids"],
        "review": review.get("overall"),
        "interpretation_run_id": interpretation.get("agent_run_id"),
    }


# --- VerificationWorkflow -----------------------------------------------------------------------------------


async def verification_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    verification_id = ctx.input["verification_id"]
    try:
        info = await ctx.activity("verification.begin", {"verification_id": verification_id}, key="begin")
        repro_ids: list[str] = []
        if info["min_reproductions"] > 0:
            repro_ids = (
                await ctx.activity(
                    "verification.plan_reproductions",
                    {"verification_id": verification_id, "count": info["min_reproductions"] + 1},
                    key="plan",
                )
            )["run_ids"]
            pre = await ctx.activity(
                "experiment.preflight",
                {"experiment_id": info["experiment_id"], "runs": len(repro_ids), "action": "reproduction.run"},
                key="preflight",
            )
            if pre["decision"] == "approval_required":
                outcome = await wait_for_approval(ctx, pre["approval_id"], pre["signal"], key="repro-approval")
                if not outcome.get("approved"):
                    repro_ids = []
            elif pre["decision"] == "deny":
                repro_ids = []
            if repro_ids:
                await ctx.gather(
                    *[
                        ctx.child("experiment", {"run_id": r, "experiment_id": info["experiment_id"]}, key=f"repro:{r}")
                        for r in repro_ids
                    ]
                )
        checks = await ctx.activity(
            "verification.checks", {"verification_id": verification_id, "reproduction_run_ids": repro_ids}, key="checks"
        )
        await ctx.activity(
            "verification.assess",
            {"verification_id": verification_id, "checks": checks["checks"]},
            key="assess",
            retry=AGENT_RETRY,
        )
        decision = await ctx.activity(
            "verification.decide",
            {
                "verification_id": verification_id,
                "reproductions_passed": checks["reproductions_passed"],
                "reproductions_failed": checks["reproductions_failed"],
            },
            key="decide",
        )
        return {"status": decision["status"], "claim_id": decision.get("claim_id"), "decision": decision["decision"]}
    except ActivityFailure as exc:
        await ctx.activity("verification.fail", {"verification_id": verification_id, "error": str(exc)}, key="fail")
        raise


# --- EvolutionWorkflow --------------------------------------------------------------------------------------


async def evolution_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    evo_id = ctx.input["evolution_run_id"]
    info = await ctx.activity("evolution.info", {"evolution_run_id": evo_id}, key="info")

    async def evaluate_pending(tag: str) -> None:
        pending = await ctx.activity("evolution.pending", {"evolution_run_id": evo_id}, key=f"pending:{tag}")
        for vid in pending["version_ids"]:
            if info["mode"] == "benchmark":
                await ctx.activity(
                    "evolution.benchmark", {"evolution_run_id": evo_id, "version_id": vid}, key=f"bench:{vid}"
                )
                continue
            prepared = await ctx.activity(
                "evolution.prepare", {"evolution_run_id": evo_id, "version_id": vid}, key=f"prepare:{vid}"
            )
            batch = await ctx.child(
                "experiment_batch", {"experiment_id": prepared["experiment_id"]}, key=f"batch:{vid}"
            )
            await ctx.activity(
                "evolution.record",
                {"version_id": vid, "experiment_id": prepared["experiment_id"], "status": batch.get("status")},
                key=f"record:{vid}",
            )

    try:
        await evaluate_pending("g0")
        for generation in range(1, int(info["max_generations"]) + 1):
            step = await ctx.activity("evolution.step", {"evolution_run_id": evo_id}, key=f"step:{generation}")
            if step["status"] != "ok":
                break
            await evaluate_pending(f"g{generation}")
        best = await ctx.activity("evolution.best", {"evolution_run_id": evo_id}, key="best")
        promotion: dict[str, Any] = {"promoted": False}
        if best.get("version_id") and not best.get("is_active"):
            promotion = await ctx.activity(
                "evolution.promote", {"evolution_run_id": evo_id, "version_id": best["version_id"]}, key="promote"
            )
            if promotion.get("approval_id"):
                outcome = await wait_for_approval(ctx, promotion["approval_id"], promotion["signal"], key="promotion")
                if outcome.get("approved"):
                    promotion = await ctx.activity(
                        "evolution.promote",
                        {
                            "evolution_run_id": evo_id,
                            "version_id": best["version_id"],
                            "approval_id": promotion["approval_id"],
                        },
                        key="promote-approved",
                    )
        await ctx.activity("evolution.finish", {"evolution_run_id": evo_id, "status": "completed"}, key="finish")
        return {"best": best, "promotion": promotion}
    except ActivityFailure:
        await ctx.activity("evolution.finish", {"evolution_run_id": evo_id, "status": "failed"}, key="finish:failed")
        raise
    except WorkflowCancelled:
        await ctx.activity(
            "evolution.finish", {"evolution_run_id": evo_id, "status": "cancelled"}, key="finish:cancelled"
        )
        raise


# --- DiscoveryWorkflow --------------------------------------------------------------------------------------


async def discovery_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    created = await ctx.activity("discovery.create", {"claim_id": ctx.input["claim_id"]}, key="create")
    if not created.get("approval_id"):
        return {"discovery_id": created["discovery_id"], "status": created["status"]}
    outcome = await wait_for_approval(ctx, created["approval_id"], created["signal"], key="review")
    final = await ctx.activity("discovery.outcome", {"discovery_id": created["discovery_id"]}, key="outcome")
    return {
        "discovery_id": created["discovery_id"],
        "status": final["status"],
        "approved": bool(outcome.get("approved")),
    }


# --- ReportWorkflow -----------------------------------------------------------------------------------------


async def report_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    mission_id = ctx.input["mission_id"]
    facts = await ctx.activity("report.facts", {"mission_id": mission_id}, key="facts")
    narrative = await ctx.activity(
        "report.narrative", {"mission_id": mission_id, "facts": facts}, key="narrative", retry=NO_RETRY
    )
    return await ctx.activity(
        "report.assemble",
        {
            "mission_id": mission_id,
            "facts": facts,
            "narrative": narrative.get("output"),
            "narrative_run_id": narrative.get("agent_run_id"),
        },
        key="assemble",
    )


# --- DatasetProcessingWorkflow / ArtifactProcessingWorkflow / BenchmarkWorkflow ----------------------------


async def dataset_processing_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    profile = await ctx.activity("dataset.profile", {"files": ctx.input["files"]}, key="profile", timeout_seconds=1800)
    return await ctx.activity("dataset.register", {**ctx.input, "profile": profile}, key="register")


async def artifact_processing_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    version_id = ctx.input.get("document_version_id")
    if not version_id and ctx.input.get("document_id"):
        fetched = await ctx.activity("artifact.fetch_url", {"document_id": ctx.input["document_id"]}, key="fetch")
        version_id = fetched.get("document_version_id")
    if not version_id:
        return {"status": "failed", "reason": "nothing to process"}
    return await ctx.activity(
        "artifact.process_document", {"document_version_id": version_id}, key="process", timeout_seconds=3600
    )


async def benchmark_workflow(ctx: WorkflowContext) -> dict[str, Any]:
    return await ctx.activity(
        "benchmark.execute",
        {"benchmark_run_id": ctx.input["benchmark_run_id"], "project_id": ctx.input.get("project_id")},
        key="execute",
        timeout_seconds=7200,
    )


WORKFLOWS: dict[str, WorkflowDefinition] = {
    d.name: d
    for d in (
        WorkflowDefinition("mission", mission_workflow, "End-to-end mission lifecycle", 30 * 24 * 3600),
        WorkflowDefinition("research", research_workflow, "Deep Research or literature pipeline", 24 * 3600),
        WorkflowDefinition(
            "hypothesis", hypothesis_workflow, "Generate, critique and select hypotheses", 7 * 24 * 3600
        ),
        WorkflowDefinition("experiment", experiment_workflow, "Execute and measure one run", 24 * 3600),
        WorkflowDefinition(
            "experiment_batch", experiment_batch_workflow, "All runs of an experiment + evaluation", 7 * 24 * 3600
        ),
        WorkflowDefinition("evaluation", evaluation_workflow, "Evaluators, claims, statistics and review", 24 * 3600),
        WorkflowDefinition(
            "verification", verification_workflow, "Reproductions and independent checks", 7 * 24 * 3600
        ),
        WorkflowDefinition("evolution", evolution_workflow, "Strategy evolution with gated promotion", 14 * 24 * 3600),
        WorkflowDefinition("discovery", discovery_workflow, "Discovery registration and human review", 30 * 24 * 3600),
        WorkflowDefinition("report", report_workflow, "Evidence-cited research report", 24 * 3600),
        WorkflowDefinition(
            "dataset_processing", dataset_processing_workflow, "Profile and register a dataset version", 24 * 3600
        ),
        WorkflowDefinition(
            "artifact_processing", artifact_processing_workflow, "Fetch/scan/parse/chunk/embed documents", 24 * 3600
        ),
        WorkflowDefinition("benchmark", benchmark_workflow, "Run a benchmark suite", 24 * 3600),
    )
}
