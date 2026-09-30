"""Execution service: runs experiment code in the sandbox and measures it with a platform-owned harness.

Per run (an idempotent activity):
1. *preflight* (short tx): static code checks, policy (network/secrets/GPU/cost/budget), autonomy gate.
2. stage inputs: code bundle (checksum-verified), dataset splits visible to candidates (never ``harness_only``),
   ``input/config.json`` with the seed and parameters.
3. candidate container: no network, read-only rootfs, non-root, resource limits, timeout (backend-measured
   runtime/memory/exit status).
4. harness container: the registered harness reads the candidate's outputs and the hidden split and writes
   the metrics. Candidate code never measures itself when a harness exists; self-reported metrics are
   flagged as such.
5. persist (short tx): logs/outputs as checksummed artifacts, metrics with their source, compute usage and
   cost, the reproducibility manifest, an evidence record, and live events.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import select

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import ApprovalRequired, NotFound, PolicyDenied
from aegis_api.infrastructure.execution import registry
from aegis_api.infrastructure.execution.base import ExecutionError, ExecutionRequest, ExecutionResult, ResourceSpec
from aegis_api.infrastructure.observability import metrics as prom
from aegis_api.models import Project
from aegis_api.models.lab import (
    Approval,
    ComputeUsage,
    ExecutionJob,
    Experiment,
    ExperimentMetric,
    ExperimentRun,
    ExperimentVersion,
    Mission,
)
from aegis_api.services.lab import approvals, artifacts, datasets, environments, events, evidence, usage
from aegis_api.services.lab import experiments as experiment_service
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab.common import Actor
from engines.lab import harnesses
from engines.lab.autonomy import LabAction, requires_human
from engines.lab.budgets import Spend
from engines.lab.costs import compute_cost, estimate_compute_seconds_cost
from engines.lab.enums import ApprovalStatus, ExecutionStatus, LabEventType, RunKind
from engines.lab.evaluation.base import EvaluationContext
from engines.lab.evaluation.evaluators import DEFAULT_REGISTRY
from engines.lab.experiments.spec import ExperimentSpec
from engines.lab.reproducibility import (
    CodeSnapshot,
    EnvironmentProvenance,
    HardwareProvenance,
    ModelProvenance,
    OutputRecord,
    ReproducibilityManifest,
)
from engines.lab.state_machines import EXECUTION

log = structlog.get_logger("aegis.lab.execution")

CANDIDATE_ROLES = frozenset({"train", "validation", "test"})
MAX_OUTPUT_FILES = 50
HARNESS_TIMEOUT_SECONDS = 300
_ERROR_LINE = re.compile(r"^(\w+(?:Error|Exception|Exit|Interrupt))(?::\s*(.*))?$")


def _estimate(spec: ExperimentSpec, runs: int) -> Spend:
    pricing = get_settings().compute_pricing
    est = estimate_compute_seconds_cost(pricing, spec.resources.model_dump(), spec.resources.timeout_seconds)
    cost = (est.usd or 0.0) * runs
    return Spend(
        total_cost=cost,
        compute_cost=cost,
        compute_seconds=float(spec.resources.timeout_seconds * runs),
        experiment_count=runs,
    )


def static_check(code_files: dict[str, bytes], spec: ExperimentSpec) -> dict[str, Any]:
    result = DEFAULT_REGISTRY.get("code_quality").evaluate(spec, code_files, EvaluationContext())
    return {"passed": bool(result.passed), "warnings": result.warnings, "evidence": result.evidence}


def preflight(
    organization_id: uuid.UUID,
    *,
    experiment_id: uuid.UUID,
    run_count: int,
    actor: Actor,
    workflow_run_id: uuid.UUID | None,
    action: str = "experiment.execute",
) -> dict[str, Any]:
    """Gate a batch of runs: policy + budget + autonomy. Returns allow | approval_required (with an approval
    bound to the calling workflow) | deny."""
    with session_scope(organization_id) as db:
        experiment = db.get(Experiment, experiment_id)
        if experiment is None:
            raise NotFound("Experiment not found")
        version = db.get(ExperimentVersion, experiment.current_version_id)
        assert version is not None
        spec = ExperimentSpec.model_validate(version.spec)
        mission = db.get(Mission, experiment.mission_id) if experiment.mission_id else None
        project = db.get(Project, experiment.project_id)
        estimate = _estimate(spec, run_count)
        budget = usage.combined_check(db, organization_id, mission=mission, project=project, estimate=estimate)
        result = lab_policy.evaluate(
            db,
            organization_id=organization_id,
            action=action,
            facts=lab_policy.base_facts(
                db,
                organization_id,
                actor,
                mission=mission,
                extra={
                    "execution": {
                        "network": spec.resources.network,
                        "secrets_requested": bool(spec.resources.secrets),
                        "gpu_count": spec.resources.gpu_count,
                        "runs": run_count,
                    },
                    "estimated_cost": estimate.total_cost,
                    "budget": budget,
                    "target": {"environment": "sandbox"},
                },
            ),
            actor=actor,
            resource_type="experiment_version",
            resource_id=str(version.id),
        )
        if result.denied:
            return {"decision": "deny", "reasons": result.reasons, "policy": result.to_dict()}
        autonomy_gate = mission is not None and requires_human(
            mission.autonomy_level,
            LabAction.REPRODUCTION_RUN if action == "reproduction.run" else LabAction.EXPERIMENT_EXECUTE,
        )
        needs_approval = result.needs_approval or autonomy_gate
        if not needs_approval:
            return {"decision": "allow", "estimate": estimate.total_cost, "budget": budget}
        prior = db.scalar(
            select(Approval).where(
                Approval.organization_id == organization_id,
                Approval.resource_type == "experiment_version",
                Approval.resource_id == str(version.id),
                Approval.status == ApprovalStatus.APPROVED,
            )
        )
        if prior is not None:
            return {"decision": "allow", "approval_id": str(prior.id), "estimate": estimate.total_cost}
        kinds = result.approval_kinds or ["experiment_execution"]
        approval = approvals.request(
            db,
            organization_id=organization_id,
            kind=kinds[0] if result.needs_approval else "experiment_execution",
            resource_type="experiment_version",
            resource_id=str(version.id),
            title=f"Execute experiment '{experiment.title}' ({run_count} sandboxed runs)",
            requester=actor,
            details={
                "experiment_id": str(experiment.id),
                "runs": run_count,
                "estimated_cost_usd": estimate.total_cost if estimate.total_cost else None,
                "resources": spec.resources.model_dump(),
                "autonomy_gate": autonomy_gate,
            },
            policy_result=result,
            project_id=experiment.project_id,
            mission_id=experiment.mission_id,
            workflow_run_id=workflow_run_id,
        )
        return {
            "decision": "approval_required",
            "approval_id": str(approval.id),
            "signal": approval.signal_name,
            "reasons": result.reasons or ["mission autonomy level requires human approval for execution"],
        }


def _approved(db: Any, organization_id: uuid.UUID, version_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(Approval.id).where(
                Approval.organization_id == organization_id,
                Approval.resource_type == "experiment_version",
                Approval.resource_id == str(version_id),
                Approval.status == ApprovalStatus.APPROVED,
            )
        )
        is not None
    )


def _job(
    db: Any,
    run: ExperimentRun,
    *,
    kind: str,
    image: str,
    command: list[str],
    spec: ExperimentSpec,
    env_id: uuid.UUID | None,
    timeout: int,
    key: str,
    approval_id: uuid.UUID | None,
) -> ExecutionJob:
    existing = db.scalar(
        select(ExecutionJob).where(
            ExecutionJob.organization_id == run.organization_id, ExecutionJob.idempotency_key == key
        )
    )
    if existing is not None:
        return existing
    job = ExecutionJob(
        organization_id=run.organization_id,
        project_id=run.project_id,
        mission_id=run.mission_id,
        experiment_id=run.experiment_id,
        experiment_run_id=run.id,
        kind=kind,
        backend=get_settings().execution_backend,
        image=image,
        command=command,
        environment_id=env_id,
        resource_request=spec.resources.model_dump() if kind == "candidate" else {"cpu": 1, "memory_mb": 512},
        timeout_seconds=timeout,
        network_policy={"mode": "none"},
        filesystem_policy={"root": "read-only", "workspace": "/workspace (ephemeral volume)", "tmp": "tmpfs noexec"},
        secrets_policy={"injected": []},
        status=ExecutionStatus.QUEUED,
        idempotency_key=key[:200],
        attempt=run.attempt,
        approval_id=approval_id,
    )
    db.add(job)
    db.flush()
    return job


def _set_job(organization_id: uuid.UUID, job_id: uuid.UUID, status: str, **fields: Any) -> None:
    with session_scope(organization_id) as db:
        job = db.get(ExecutionJob, job_id)
        assert job is not None
        job.status = EXECUTION.ensure(job.status, status)
        for k, v in fields.items():
            setattr(job, k, v)


def _error_from_stderr(stderr: str) -> tuple[str | None, str | None]:
    for line in reversed(stderr.strip().splitlines()[-30:]):
        m = _ERROR_LINE.match(line.strip())
        if m:
            return m.group(1), (m.group(2) or "")[:500]
    return None, None


def execute_run(organization_id: uuid.UUID, run_id: uuid.UUID, *, actor: Actor) -> dict[str, Any]:
    settings = get_settings()
    # --- 1. load + gate (short tx) -------------------------------------------------------------------------
    with session_scope(organization_id) as db:
        run = db.get(ExperimentRun, run_id)
        if run is None:
            raise NotFound("Experiment run not found")
        if run.status in ("completed", "failed", "cancelled"):
            return summarize(run)
        version = db.get(ExperimentVersion, run.experiment_version_id)
        experiment = db.get(Experiment, run.experiment_id)
        assert version is not None and experiment is not None
        spec = ExperimentSpec.model_validate(version.spec)
        mission = db.get(Mission, run.mission_id) if run.mission_id else None
        if mission is not None and (mission.cancel_requested or mission.status == "cancelled"):
            run.status = "cancelled"
            return summarize(run)
        env = environments.resolve_for_spec(db, organization_id, spec.environment)
        action = "reproduction.run" if run.run_kind == RunKind.REPRODUCTION else "experiment.execute"
        gate = lab_policy.evaluate(
            db,
            organization_id=organization_id,
            action=action,
            facts=lab_policy.base_facts(
                db,
                organization_id,
                actor,
                mission=mission,
                extra={
                    "execution": {
                        "network": spec.resources.network,
                        "secrets_requested": bool(spec.resources.secrets),
                        "gpu_count": spec.resources.gpu_count,
                    },
                    "estimated_cost": 0.0,
                    "target": {"environment": "sandbox"},
                },
            ),
            actor=actor,
            resource_type="experiment_run",
            resource_id=str(run.id),
        )
        if gate.denied:
            raise PolicyDenied("; ".join(gate.reasons), details=gate.to_dict())
        autonomy_gate = mission is not None and requires_human(
            mission.autonomy_level,
            LabAction.REPRODUCTION_RUN if action == "reproduction.run" else LabAction.EXPERIMENT_EXECUTE,
        )
        if (gate.needs_approval or autonomy_gate) and not _approved(db, organization_id, version.id):
            raise ApprovalRequired("Execution requires an approved request for this experiment version")
        first_start = run.started_at is None
        run.attempt = run.attempt if first_start else run.attempt + 1
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        timeout = min(spec.resources.timeout_seconds, settings.execution_max_timeout_seconds)
        job = _job(
            db,
            run,
            kind="candidate",
            image=env.image,
            command=[],
            spec=spec,
            env_id=env.id if env.organization_id else None,
            timeout=timeout,
            key=f"{run.id}:candidate:{run.attempt}",
            approval_id=None,
        )
        if first_start and run.mission_id:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=run.mission_id,
                project_id=run.project_id,
                event_type=LabEventType.EXPERIMENT_STARTED,
                message=f"Run started: {experiment.title} [{run.run_kind}/{run.variant} seed={run.seed}]",
                data={
                    "experiment_id": str(experiment.id),
                    "run_id": str(run.id),
                    "run_kind": run.run_kind,
                    "seed": run.seed,
                },
                actor=actor,
            )
        ctx: dict[str, Any] = {
            "run_id": run.id,
            "run_kind": run.run_kind,
            "variant": run.variant,
            "seed": run.seed,
            "parameters": dict(run.parameters or {}),
            "project_id": run.project_id,
            "mission_id": run.mission_id,
            "experiment_id": experiment.id,
            "experiment_version": version.version,
            "version_id": version.id,
            "spec_sha256": version.spec_sha256,
            "code_artifact_id": version.code_artifact_id,
            "code_sha256": version.code_sha256,
            "generated_by_run_id": version.generated_by_run_id,
            "job_id": job.id,
            "image": env.image,
            "image_digest": env.image_digest,
            "env_id": str(env.id),
            "packages": dict(env.packages or {}),
            "lockfile": env.lockfile_sha256,
            "title": experiment.title,
        }
    # --- 2. stage inputs (no tx) ------------------------------------------------------------------------
    code_files, entrypoint = experiment_service.load_code(organization_id, version)
    check = static_check(code_files, spec)
    if not check["passed"]:
        _set_job(organization_id, ctx["job_id"], ExecutionStatus.FAILED, error="static code checks failed")
        return _finalize_failure(
            organization_id,
            run_id,
            ctx,
            actor,
            reason="Generated code failed static safety checks (forbidden imports/calls)",
            signal={
                "stage": "static_check",
                "policy_denied": True,
                "error_type": "StaticCheckFailed",
                "error_message": json.dumps(check["evidence"])[:1000],
            },
        )
    data_files: dict[str, bytes] = {}
    dataset_info: dict[str, Any] = {}
    if spec.dataset and spec.dataset.dataset_version_id:
        data_files, dataset_info = datasets.load_files(
            organization_id,
            uuid.UUID(spec.dataset.dataset_version_id),
            roles=CANDIDATE_ROLES,
            max_total_bytes=settings.execution_max_output_bytes * 4,
        )
    config = {
        "seed": ctx["seed"],
        "parameters": ctx["parameters"],
        "run_kind": ctx["run_kind"],
        "variant": ctx["variant"],
    }
    files = {
        **code_files,
        **{f"data/{p}": b for p, b in data_files.items()},
        "input/config.json": json.dumps(config).encode(),
    }
    request = ExecutionRequest(
        job_id=str(ctx["job_id"]),
        organization_id=str(organization_id),
        image=ctx["image"],
        command=entrypoint,
        files=files,
        env={"AEGIS_SEED": str(ctx["seed"]), "PYTHONHASHSEED": str(ctx["seed"])},
        resources=ResourceSpec(
            cpu=min(spec.resources.cpu, settings.execution_max_cpu),
            memory_mb=min(spec.resources.memory_mb, settings.execution_max_memory_mb),
            disk_mb=spec.resources.disk_mb,
            pids=settings.execution_pids_limit,
            gpu_count=spec.resources.gpu_count,
            gpu_type=spec.resources.gpu_type,
        ),
        timeout_seconds=min(spec.resources.timeout_seconds, settings.execution_max_timeout_seconds),
        network="none",
        max_output_bytes=settings.execution_max_output_bytes,
        max_log_bytes=settings.execution_max_log_bytes,
        labels={"org": str(organization_id), "run": str(run_id), "kind": "candidate"},
    )
    backend = registry.get_backend()
    _set_job(organization_id, ctx["job_id"], ExecutionStatus.PROVISIONING, command=entrypoint, backend=backend.name)
    _set_job(organization_id, ctx["job_id"], ExecutionStatus.RUNNING, started_at=utcnow(), heartbeat_at=utcnow())
    started = time.perf_counter()
    try:
        result = backend.run(request)
    except ExecutionError as exc:
        _set_job(organization_id, ctx["job_id"], ExecutionStatus.FAILED, error=str(exc)[:2000], completed_at=utcnow())
        raise
    prom.EXPERIMENT_DURATION.labels(
        backend=backend.name, kind="candidate", status="ok" if result.succeeded else "failed"
    ).observe(time.perf_counter() - started)
    # --- 3. store logs + outputs ------------------------------------------------------------------------
    logs = f"=== stdout ===\n{result.stdout}\n=== stderr ===\n{result.stderr}"
    logs_id, _, _logs_sha = artifacts.store(
        organization_id,
        data=logs.encode(),
        name=f"logs-{run_id}.txt",
        kind="logs",
        content_type="text/plain",
        project_id=ctx["project_id"],
        mission_id=ctx["mission_id"],
        experiment_run_id=run_id,
        execution_job_id=ctx["job_id"],
        created_by="execution-backend",
        metadata={"truncated": result.logs_truncated},
    )
    outputs: list[OutputRecord] = []
    for name, data in sorted(result.outputs.items())[:MAX_OUTPUT_FILES]:
        aid, _, sha = artifacts.store(
            organization_id,
            data=data,
            name=name,
            kind="run_output",
            content_type="application/json"
            if name.endswith(".json")
            else "text/plain"
            if name.endswith((".csv", ".txt", ".md"))
            else "application/octet-stream",
            project_id=ctx["project_id"],
            mission_id=ctx["mission_id"],
            experiment_run_id=run_id,
            execution_job_id=ctx["job_id"],
            retention_class="evidence",
            created_by="execution-backend",
        )
        outputs.append(OutputRecord(name=name, artifact_id=str(aid), sha256=sha, size_bytes=len(data)))
    _record_compute(organization_id, ctx, result, spec, backend.name)
    job_status = (
        ExecutionStatus.TIMED_OUT
        if result.timed_out
        else ExecutionStatus.SUCCEEDED
        if result.succeeded
        else ExecutionStatus.FAILED
    )
    _set_job(
        organization_id,
        ctx["job_id"],
        job_status,
        exit_code=result.exit_code,
        measured=result.measured(),
        artifact_ids=[o.artifact_id for o in outputs if o.artifact_id],
        logs_artifact_id=logs_id,
        image_digest=result.image_digest,
        backend_ref=result.backend_ref,
        completed_at=utcnow(),
    )
    ctx["image_digest"] = ctx["image_digest"] or result.image_digest
    ctx["logs_artifact_id"] = str(logs_id)
    if not result.succeeded:
        err_type, err_msg = _error_from_stderr(result.stderr)
        return _finalize_failure(
            organization_id,
            run_id,
            ctx,
            actor,
            reason="timed out"
            if result.timed_out
            else "out of memory"
            if result.oom_killed
            else f"exit code {result.exit_code}",
            signal={
                "stage": "execution",
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "oom_killed": result.oom_killed,
                "error_type": err_type,
                "error_message": err_msg,
                "stderr_tail": result.stderr[-4000:],
                "stdout_tail": result.stdout[-2000:],
            },
            measured=result.measured(),
            outputs=outputs,
            spec=spec,
            dataset_info=dataset_info,
        )
    # --- 4. harness ---------------------------------------------------------------------------------------
    metrics_values: dict[str, float] = {}
    self_reported = True
    harness_record: dict[str, Any] = {}
    if spec.harness is not None:
        info = harnesses.get(spec.harness.key)
        hidden: dict[str, bytes] = {}
        if info.requires_data and spec.dataset and spec.dataset.dataset_version_id:
            hidden, _ = datasets.load_files(
                organization_id,
                uuid.UUID(spec.dataset.dataset_version_id),
                roles=frozenset({"harness_only"}),
                max_total_bytes=settings.execution_max_output_bytes * 4,
            )
        with session_scope(organization_id) as db:
            run = db.get(ExperimentRun, run_id)
            assert run is not None
            hjob = _job(
                db,
                run,
                kind="harness",
                image=settings.execution_default_image,
                command=["python", "harness/harness.py"],
                spec=spec,
                env_id=None,
                timeout=HARNESS_TIMEOUT_SECONDS,
                key=f"{run.id}:harness:{run.attempt}",
                approval_id=None,
            )
            run.harness_job_id = hjob.id
            hjob_id = hjob.id
        hfiles = {
            **{f"harness/{k}": v for k, v in info.files().items()},
            "harness/config.json": json.dumps(spec.harness.config).encode(),
            **{f"candidate/{k}": v for k, v in result.outputs.items()},
            **{f"data/{p}": b for p, b in hidden.items()},
        }
        hrequest = ExecutionRequest(
            job_id=str(hjob_id),
            organization_id=str(organization_id),
            image=settings.execution_default_image,
            command=["python", "harness/harness.py"],
            files=hfiles,
            env={"AEGIS_HARNESS_ROOT": "/workspace"},
            resources=ResourceSpec(cpu=1.0, memory_mb=512, pids=64),
            timeout_seconds=HARNESS_TIMEOUT_SECONDS,
            max_output_bytes=1024 * 1024,
            labels={"org": str(organization_id), "run": str(run_id), "kind": "harness"},
        )
        _set_job(organization_id, hjob_id, ExecutionStatus.PROVISIONING)
        _set_job(organization_id, hjob_id, ExecutionStatus.RUNNING, started_at=utcnow())
        try:
            hres = backend.run(hrequest)
        except ExecutionError as exc:
            _set_job(organization_id, hjob_id, ExecutionStatus.FAILED, error=str(exc)[:2000], completed_at=utcnow())
            raise
        _set_job(
            organization_id,
            hjob_id,
            ExecutionStatus.SUCCEEDED if hres.succeeded else ExecutionStatus.FAILED,
            exit_code=hres.exit_code,
            measured=hres.measured(),
            image_digest=hres.image_digest,
            completed_at=utcnow(),
        )
        raw = hres.outputs.get("metrics.json")
        parsed: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = {}
        harness_record = {
            "key": info.key,
            "version": info.version,
            "sha256": info.sha256,
            "config": spec.harness.config,
            "job_id": str(hjob_id),
            "ok": bool(parsed.get("ok")),
            "image_digest": hres.image_digest,
        }
        if not hres.succeeded or not parsed.get("ok"):
            return _finalize_failure(
                organization_id,
                run_id,
                ctx,
                actor,
                reason=f"evaluation harness failed: {parsed.get('error') or hres.stderr[-300:]}",
                signal={
                    "stage": "evaluation",
                    "exit_code": hres.exit_code,
                    "error_type": "HarnessError",
                    "error_message": str(parsed.get("error") or "harness did not produce metrics")[:500],
                    "stderr_tail": hres.stderr[-2000:],
                },
                measured=result.measured(),
                outputs=outputs,
                spec=spec,
                dataset_info=dataset_info,
                harness=harness_record,
            )
        metrics_values = {
            str(k)[:120]: float(v) for k, v in (parsed.get("metrics") or {}).items() if isinstance(v, int | float)
        }
        self_reported = bool(parsed.get("self_reported", info.self_reported))
    else:
        raw = result.outputs.get("metrics.json")
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        metrics_values = {
            str(k)[:120]: float(v)
            for k, v in ((body.get("metrics") if isinstance(body, dict) else None) or body or {}).items()
            if isinstance(v, int | float)
        }
    return _finalize_success(
        organization_id,
        run_id,
        ctx,
        actor,
        metrics_values=metrics_values,
        self_reported=self_reported,
        measured=result.measured(),
        outputs=outputs,
        spec=spec,
        dataset_info=dataset_info,
        harness=harness_record,
    )


def _record_compute(
    organization_id: uuid.UUID, ctx: dict[str, Any], result: ExecutionResult, spec: ExperimentSpec, backend: str
) -> None:
    pricing = get_settings().compute_pricing
    estimate = compute_cost(
        pricing,
        seconds=result.runtime_seconds,
        cpu=spec.resources.cpu,
        memory_mb=spec.resources.memory_mb,
        gpu_type=spec.resources.gpu_type,
        gpu_count=spec.resources.gpu_count,
    )
    if spec.resources.gpu_count:
        prom.GPU_SECONDS.labels(gpu_type=spec.resources.gpu_type or "default").inc(
            result.runtime_seconds * spec.resources.gpu_count
        )
    with session_scope(organization_id) as db:
        db.add(
            ComputeUsage(
                organization_id=organization_id,
                project_id=ctx["project_id"],
                mission_id=ctx["mission_id"],
                experiment_id=ctx["experiment_id"],
                experiment_run_id=ctx["run_id"],
                execution_job_id=ctx["job_id"],
                backend=backend,
                cpu=spec.resources.cpu,
                memory_mb=spec.resources.memory_mb,
                gpu_type=spec.resources.gpu_type,
                gpu_count=spec.resources.gpu_count,
                runtime_seconds=round(result.runtime_seconds, 3),
                cpu_seconds=result.cpu_seconds,
                gpu_seconds=round(result.runtime_seconds * spec.resources.gpu_count, 3),
                cost_usd=estimate.usd,
                cost_basis=estimate.basis,
            )
        )


def _manifest(
    ctx: dict[str, Any],
    run_id: uuid.UUID,
    *,
    spec: ExperimentSpec,
    measured: dict[str, Any],
    outputs: list[OutputRecord],
    dataset_info: dict[str, Any],
    harness: dict[str, Any],
    backend: str,
    generated_by: ModelProvenance | None,
) -> ReproducibilityManifest:
    return ReproducibilityManifest(
        experiment_id=str(ctx["experiment_id"]),
        experiment_version=int(ctx["experiment_version"]),
        run_id=str(run_id),
        run_kind=str(ctx["run_kind"]),
        code=CodeSnapshot(
            artifact_id=str(ctx["code_artifact_id"]) if ctx["code_artifact_id"] else None,
            sha256=ctx["code_sha256"],
            entrypoint=list(spec.code.entrypoint),
        ),
        generated_by=generated_by,
        dataset_version_id=dataset_info.get("id"),
        dataset_checksum=dataset_info.get("checksum"),
        environment=EnvironmentProvenance(
            environment_id=ctx["env_id"],
            image=ctx["image"],
            image_digest=ctx["image_digest"],
            lockfile_sha256=ctx["lockfile"],
            packages=ctx["packages"],
            env_var_names=["AEGIS_SEED", "PYTHONHASHSEED", "AEGIS_JOB_ID"],
        ),
        seeds=[int(ctx["seed"])],
        parameters=ctx["parameters"],
        hardware=HardwareProvenance(
            backend=backend,
            runtime=spec.resources.runtime,
            cpu=spec.resources.cpu,
            memory_mb=spec.resources.memory_mb,
            gpu_type=spec.resources.gpu_type,
            gpu_count=spec.resources.gpu_count,
        ),
        resources=measured,
        command=list(spec.code.entrypoint),
        network_policy="none",
        logs_artifact_id=ctx.get("logs_artifact_id"),
        outputs=outputs,
        harness=harness,
        evaluators=[
            {"key": k, "version": DEFAULT_REGISTRY.get(k).version} for k in ("metric", "benchmark", "statistical")
        ],
        spec_sha256=ctx["spec_sha256"],
    )


def _generated_by(db: Any, agent_run_id: uuid.UUID | None) -> ModelProvenance | None:
    if agent_run_id is None:
        return None
    from aegis_api.models.lab import AgentRun

    ar = db.get(AgentRun, agent_run_id)
    if ar is None:
        return None
    return ModelProvenance(
        provider=ar.provider,
        model=ar.model,
        model_revision=ar.model_revision,
        prompt_ref=ar.prompt_ref,
        prompt_sha256=ar.prompt_hash,
        config=ar.model_params or {},
        agent_run_id=str(ar.id),
    )


def _finalize_success(
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    ctx: dict[str, Any],
    actor: Actor,
    *,
    metrics_values: dict[str, float],
    self_reported: bool,
    measured: dict[str, Any],
    outputs: list[OutputRecord],
    spec: ExperimentSpec,
    dataset_info: dict[str, Any],
    harness: dict[str, Any],
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        run = db.get(ExperimentRun, run_id)
        assert run is not None
        manifest = _manifest(
            ctx,
            run_id,
            spec=spec,
            measured=measured,
            outputs=outputs,
            dataset_info=dataset_info,
            harness=harness,
            backend=str(measured.get("backend")),
            generated_by=_generated_by(db, ctx["generated_by_run_id"]),
        )
        source = "self_reported" if self_reported else "harness"
        for name, value in metrics_values.items():
            declared = spec.metric(name)
            db.add(
                ExperimentMetric(
                    organization_id=organization_id,
                    experiment_id=ctx["experiment_id"],
                    run_id=run_id,
                    name=name,
                    value=value,
                    source=source,
                    unit=declared.unit if declared else None,
                )
            )
        for name in ("runtime_seconds", "peak_memory_mb", "cpu_seconds"):
            measured_value = measured.get(name)
            if isinstance(measured_value, int | float):
                db.add(
                    ExperimentMetric(
                        organization_id=organization_id,
                        experiment_id=ctx["experiment_id"],
                        run_id=run_id,
                        name=f"resource.{name}",
                        value=float(measured_value),
                        source="backend",
                    )
                )
        run.metrics = metrics_values
        run.self_reported = self_reported
        run.resources = measured
        run.manifest = manifest.model_dump(mode="json")
        run.manifest_sha256 = manifest.digest()
        run.status = "completed"
        run.error = None
        run.completed_at = utcnow()
        record = evidence.seal(
            db,
            organization_id=organization_id,
            mission_id=ctx["mission_id"],
            project_id=ctx["project_id"],
            kind="experiment_run",
            title=f"Run {run.run_kind}/{run.variant} seed={run.seed}: {ctx['title']}"[:300],
            content={
                "run_id": str(run_id),
                "experiment_id": str(ctx["experiment_id"]),
                "run_kind": run.run_kind,
                "seed": run.seed,
                "metrics": metrics_values,
                "metric_source": source,
                "measured": measured,
                "manifest_sha256": run.manifest_sha256,
                "outputs": [o.model_dump() for o in outputs],
                "harness": harness,
            },
            confidence=0.4 if self_reported else 0.9,
            reasons=["metrics self-reported by experiment code"] if self_reported else ["measured by platform harness"],
        )
        if ctx["mission_id"]:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=ctx["mission_id"],
                project_id=ctx["project_id"],
                event_type=LabEventType.EXPERIMENT_COMPLETED,
                message=f"Run completed [{run.run_kind}/{run.variant} seed={run.seed}]: "
                + ", ".join(f"{k}={v:.4g}" for k, v in list(metrics_values.items())[:4]),
                data={
                    "experiment_id": str(ctx["experiment_id"]),
                    "run_id": str(run_id),
                    "metrics": metrics_values,
                    "self_reported": self_reported,
                    "evidence_id": str(record.id),
                },
                actor=actor,
                webhook=False,
            )
        return {**summarize(run), "evidence_id": str(record.id)}


def _finalize_failure(
    organization_id: uuid.UUID,
    run_id: uuid.UUID,
    ctx: dict[str, Any],
    actor: Actor,
    *,
    reason: str,
    signal: dict[str, Any],
    measured: dict[str, Any] | None = None,
    outputs: list[OutputRecord] | None = None,
    spec: ExperimentSpec | None = None,
    dataset_info: dict[str, Any] | None = None,
    harness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with session_scope(organization_id) as db:
        run = db.get(ExperimentRun, run_id)
        assert run is not None
        run.status = "failed"
        run.error = reason[:4000]
        run.resources = measured or {}
        run.completed_at = utcnow()
        if spec is not None:
            manifest = _manifest(
                ctx,
                run_id,
                spec=spec,
                measured=measured or {},
                outputs=outputs or [],
                dataset_info=dataset_info or {},
                harness=harness or {},
                backend=str((measured or {}).get("backend")),
                generated_by=_generated_by(db, ctx["generated_by_run_id"]),
            )
            run.manifest = manifest.model_dump(mode="json")
            run.manifest_sha256 = manifest.digest()
        record = evidence.seal(
            db,
            organization_id=organization_id,
            mission_id=ctx["mission_id"],
            project_id=ctx["project_id"],
            kind="experiment_failure",
            title=f"Run failed [{run.run_kind}/{run.variant} seed={run.seed}]: {reason}"[:300],
            content={
                "run_id": str(run_id),
                "reason": reason,
                "signal": {k: v for k, v in signal.items() if k != "stdout_tail"},
            },
            confidence=0.9,
        )
        if ctx["mission_id"]:
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=ctx["mission_id"],
                project_id=ctx["project_id"],
                event_type=LabEventType.EXPERIMENT_LOG,
                message=(signal.get("stderr_tail") or reason)[-1500:],
                data={"run_id": str(run_id), "stream": "stderr"},
                level="warning",
                actor=actor,
            )
            events.emit(
                db,
                organization_id=organization_id,
                mission_id=ctx["mission_id"],
                project_id=ctx["project_id"],
                event_type=LabEventType.EXPERIMENT_FAILED,
                message=f"Run failed [{run.run_kind}/{run.variant} seed={run.seed}]: {reason}",
                data={"experiment_id": str(ctx["experiment_id"]), "run_id": str(run_id), "reason": reason},
                level="error",
                actor=actor,
            )
        return {**summarize(run), "signal": signal, "evidence_id": str(record.id)}


def summarize(run: ExperimentRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "experiment_id": str(run.experiment_id),
        "run_kind": run.run_kind,
        "variant": run.variant,
        "seed": run.seed,
        "status": run.status,
        "metrics": run.metrics or {},
        "self_reported": run.self_reported,
        "error": run.error,
        "manifest_sha256": run.manifest_sha256,
    }


def cancel_jobs(organization_id: uuid.UUID, experiment_run_id: uuid.UUID) -> int:
    backend = registry.get_backend()
    cancelled = 0
    with session_scope(organization_id) as db:
        jobs = db.scalars(
            select(ExecutionJob).where(
                ExecutionJob.experiment_run_id == experiment_run_id,
                ExecutionJob.status.in_(
                    [ExecutionStatus.QUEUED, ExecutionStatus.PROVISIONING, ExecutionStatus.RUNNING]
                ),
            )
        ).all()
        for job in jobs:
            job.cancel_requested = True
            if backend.cancel(str(job.id)):
                cancelled += 1
            job.status = EXECUTION.ensure(job.status, ExecutionStatus.CANCELLED)
            job.completed_at = utcnow()
    return cancelled
