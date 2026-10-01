"""Audit orchestrator — executes an audit end to end and persists all results.

Stages: setup → test_generation → inference → evaluation → evidence → policy_mapping → risk_scoring →
report_generation. Progress and a live event stream are written to ``audit_events`` (served over SSE).

Design choices:
* Target inference runs on a thread pool (providers are blocking I/O); evaluators and persistence run on
  the orchestrator thread. Invocations are plain data, so no ORM object crosses threads.
* A provider becoming unavailable degrades the audit to ``partially_completed`` with the affected
  categories recorded — it is never silently marked as passed.
* Evidence is written as an append-only hash chain for tamper-evidence.

Execution model (crash- and duplicate-safe):
* **Claim.** A worker atomically moves the audit ``queued → running`` and takes a lease
  (``lease_owner`` + ``heartbeat_at``) in its own committed transaction. A duplicate delivery finds nothing
  to claim and exits without side effects.
* **Progress channel.** Events, progress and heartbeats are written through separate short transactions
  (:class:`ProgressChannel`) so SSE clients see them immediately. The main session never touches the
  ``audits`` row before finalisation, so the two connections can never deadlock.
* **Results transaction.** Test cases, results, evidence, findings, assessments and the report are written
  in one transaction and committed together, together with a *conditional* terminal update
  (``WHERE status = 'running' AND lease_owner = me AND NOT cancel_requested``). A cancelled audit or a lost
  lease rolls everything back, so a retried attempt never duplicates results.
* **Cancellation** is cooperative: the runner checks the flag at stage boundaries and during inference.
* **Lost workers** are detected by the maintenance reaper from a stale heartbeat (see ``maintenance``).
"""

from __future__ import annotations

import os
import socket
import uuid
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import rowcount, session_factory
from aegis_api.models import (
    Audit,
    AuditEvent,
    AuditRun,
    Claim,
    Control,
    ControlAssessment,
    Evaluation,
    Evidence,
    EvidenceLink,
    Finding,
    ModelCall,
    Organization,
    TestCase,
    TestResult,
    TestSuite,
)
from aegis_api.models.enums import (
    TERMINAL_AUDIT_STATUSES,
    AuditStage,
    AuditStatus,
    RunStatus,
)
from aegis_api.security.crypto import encrypt_json
from aegis_api.services import context_builder, finding_service
from engines.common.types import ResultStatus
from engines.evaluation.aggregation import ResultRow, group_findings, test_matrix
from engines.evaluation.base import EvaluationContext, EvaluationOutcome, SystemInvocation, TestCaseSpec
from engines.evaluation.registry import EvaluatorRegistry
from engines.evidence.hashing import ChainState
from engines.generation.generator import GenerationConfig, TestGenerator
from engines.providers.base import ProviderUnavailable
from engines.providers.pricing import estimate_cost
from engines.redteam.corpus import ProbeCorpus

log = structlog.get_logger("aegis.orchestrator")
REGISTRY = EvaluatorRegistry()

STAGE_PROGRESS = {
    AuditStage.SETUP: 5,
    AuditStage.TEST_GENERATION: 12,
    AuditStage.INFERENCE: 45,
    AuditStage.EVALUATION: 70,
    AuditStage.EVIDENCE: 82,
    AuditStage.POLICY_MAPPING: 88,
    AuditStage.RISK_SCORING: 94,
    AuditStage.REPORT_GENERATION: 100,
}


@dataclass
class _Tally:
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    provider_errors: dict[str, str] = field(default_factory=dict)

    def cost(self, pricing: dict[str, dict[str, float]]) -> dict[str, Any]:
        total = 0.0
        estimated = False
        inp = out = 0
        for call in self.model_calls:
            est = estimate_cost(
                pricing, call["provider"], call.get("model"), call.get("input_tokens"), call.get("output_tokens")
            )
            if est.usd is not None:
                total += est.usd
            estimated = estimated or est.estimated
            inp += call.get("input_tokens") or 0
            out += call.get("output_tokens") or 0
        return {
            "model_calls": len(self.model_calls),
            "input_tokens": inp,
            "output_tokens": out,
            "estimated_usd": round(total, 6) if self.model_calls else 0.0,
            "estimated": estimated,
            "note": "Cost is only reported when a price is configured for the provider/model." if estimated else "",
        }


class AuditCancelled(Exception):
    """Raised inside the runner when a cancellation request is observed."""


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class ProgressChannel:
    """Append-only audit event log, progress and heartbeat, each written in its own committed transaction.

    Separate from the runner's results transaction so progress is visible to SSE clients immediately and
    survives a rollback of the results (the timeline explains *why* an attempt failed)."""

    def __init__(self, organization_id: uuid.UUID, audit_id: uuid.UUID, lease_owner: str) -> None:
        self.org_id = organization_id
        self.audit_id = audit_id
        self.lease_owner = lease_owner
        self.progress = 0
        self._factory = session_factory()
        with self._session() as s:
            self.seq = int(
                s.scalar(select(func.coalesce(func.max(AuditEvent.seq), 0)).where(AuditEvent.audit_id == audit_id)) or 0
            )

    @contextmanager
    def _session(self) -> Iterator[Session]:
        s = self._factory()
        s.info["org_id"] = self.org_id
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def emit(
        self,
        type_: str,
        message: str,
        *,
        stage: str | None = None,
        level: str = "info",
        data: dict[str, Any] | None = None,
        progress: int | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        if progress is not None:
            self.progress = max(self.progress, min(progress, 100))
        self.seq += 1
        values: dict[str, Any] = {"heartbeat_at": utcnow(), "progress": self.progress}
        if stage:
            values["stage"] = stage
        if fields:
            values.update(fields)
        with self._session() as s:
            s.add(
                AuditEvent(
                    organization_id=self.org_id,
                    audit_id=self.audit_id,
                    seq=self.seq,
                    type=type_,
                    stage=stage,
                    level=level,
                    message=message,
                    progress=self.progress,
                    data=data or {},
                )
            )
            s.execute(
                update(Audit)
                .where(
                    Audit.id == self.audit_id,
                    Audit.status == AuditStatus.RUNNING,
                    Audit.lease_owner == self.lease_owner,
                )
                .values(**values)
            )
        _publish(self.org_id, self.audit_id, self.seq)

    def append_terminal(self, type_: str, message: str, *, level: str, data: dict[str, Any] | None = None) -> None:
        """Event written after the audit left ``running`` (no lease condition applies)."""
        self.seq += 1
        with self._session() as s:
            s.add(
                AuditEvent(
                    organization_id=self.org_id,
                    audit_id=self.audit_id,
                    seq=self.seq,
                    type=type_,
                    stage=None,
                    level=level,
                    message=message,
                    progress=self.progress,
                    data=data or {},
                )
            )
        _publish(self.org_id, self.audit_id, self.seq)

    def cancel_requested(self) -> bool:
        with self._session() as s:
            row = s.execute(
                select(Audit.status, Audit.cancel_requested, Audit.lease_owner).where(Audit.id == self.audit_id)
            ).one_or_none()
        if row is None:
            return True
        return bool(row.cancel_requested) or row.status != AuditStatus.RUNNING or row.lease_owner != self.lease_owner

    def fail(self, run_id: uuid.UUID | None, code: str, message: str) -> None:
        now = utcnow()
        with self._session() as s:
            s.execute(
                update(Audit)
                .where(
                    Audit.id == self.audit_id,
                    Audit.status == AuditStatus.RUNNING,
                    Audit.lease_owner == self.lease_owner,
                )
                .values(status=AuditStatus.FAILED, error_code=code, error_message=message[:2000], completed_at=now)
            )
            if run_id is not None:
                s.execute(
                    update(AuditRun)
                    .where(AuditRun.id == run_id)
                    .values(status=RunStatus.FAILED, finished_at=now, error=message[:2000])
                )
        self.append_terminal("audit.failed", f"Audit failed: {message}", level="error", data={"code": code})

    def finish_run(self, run_id: uuid.UUID | None, status: str, error: str | None = None) -> None:
        if run_id is None:
            return
        with self._session() as s:
            s.execute(
                update(AuditRun).where(AuditRun.id == run_id).values(status=status, finished_at=utcnow(), error=error)
            )


def _publish(organization_id: uuid.UUID, audit_id: uuid.UUID, seq: int) -> None:
    try:
        from aegis_api.realtime import publish_audit_event

        publish_audit_event(organization_id, audit_id, seq)
    except Exception:  # pragma: no cover - realtime fan-out is best-effort; the DB log is the source of truth
        log.debug("audit_event_publish_failed", exc_info=True)


def claim_audit(organization_id: uuid.UUID, audit_id: uuid.UUID, worker: str) -> tuple[int, uuid.UUID] | None:
    """Atomically claim a queued audit. Returns (attempt, audit_run_id) or None when there is nothing to claim."""
    now = utcnow()
    factory = session_factory()
    s = factory()
    s.info["org_id"] = organization_id
    try:
        row = s.execute(
            update(Audit)
            .where(Audit.id == audit_id, Audit.status == AuditStatus.QUEUED, Audit.cancel_requested.is_(False))
            .values(
                status=AuditStatus.RUNNING,
                started_at=func.coalesce(Audit.started_at, now),
                lease_owner=worker,
                heartbeat_at=now,
                attempts=Audit.attempts + 1,
                stage=AuditStage.SETUP,
                progress=0,
                error_code=None,
                error_message=None,
            )
            .returning(Audit.attempts)
        ).first()
        if row is None:
            s.rollback()
            return None
        run = AuditRun(
            organization_id=organization_id,
            audit_id=audit_id,
            attempt=int(row.attempts),
            worker=worker,
            status=RunStatus.RUNNING,
            started_at=now,
        )
        s.add(run)
        s.flush()
        run_id = run.id
        s.commit()
        return int(row.attempts), run_id
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


class AuditRunner:
    def __init__(self, session: Session, audit: Audit, *, worker: str | None = None) -> None:
        self.session = session
        self.audit = audit
        self.audit_id = audit.id
        self.org_id = audit.organization_id
        self.worker = worker or worker_identity()
        self._tally = _Tally()
        self.chain = ChainState()
        self._evidence_seq = 0
        self.channel: ProgressChannel | None = None
        self.run_id: uuid.UUID | None = None
        # Values accumulated during the run and written once, conditionally, at finalisation.
        self.test_count = 0
        self.tests_completed = 0
        self.evidence_count = 0

    # -- events ---------------------------------------------------------------------------------
    def emit(
        self,
        type_: str,
        message: str,
        *,
        stage: str | None = None,
        level: str = "info",
        data: dict[str, Any] | None = None,
        progress: int | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        assert self.channel is not None
        self.channel.emit(type_, message, stage=stage, level=level, data=data, progress=progress, fields=fields)

    def checkpoint(self) -> None:
        assert self.channel is not None
        if self.channel.cancel_requested():
            raise AuditCancelled()

    def _on_model_call(self, task: str, result: Any, error: str | None) -> None:
        if error:
            return
        if result is not None:
            self._tally.model_calls.append(
                {
                    "purpose": "judge",
                    "provider": result.provider,
                    "model": result.model,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "latency_ms": result.latency_ms,
                }
            )

    # -- main -----------------------------------------------------------------------------------
    def run(self) -> Audit:
        claim = claim_audit(self.org_id, self.audit_id, self.worker)
        if claim is None:
            log.info("audit_claim_skipped", audit_id=str(self.audit_id), status=self.audit.status)
            return self.audit
        attempt, self.run_id = claim
        self.session.refresh(self.audit)
        audit = self.audit
        self.channel = ProgressChannel(self.org_id, self.audit_id, self.worker)
        self.emit(
            "audit.started",
            f"Audit '{audit.name}' started" + (f" (attempt {attempt})" if attempt > 1 else ""),
            stage=AuditStage.SETUP,
            level="success",
            progress=STAGE_PROGRESS[AuditStage.SETUP],
            data={"attempt": attempt, "worker": self.worker},
        )
        try:
            system = audit.system
            policy_ids = [uuid.UUID(p) for p in audit.policy_version_ids]
            config = audit.config or {}
            corpus = (
                ProbeCorpus.from_records(config.get("corpus", []), name=config.get("corpus_name", "custom"))
                if config.get("corpus")
                else None
            )
            ctx = context_builder.build_context(
                self.session,
                system,
                policy_ids,
                seed=config.get("seed", 1337),
                judge_preference=config.get("evaluator_preference"),
                use_judge=config.get("use_model_judge", True),
                on_model_call=self._on_model_call,
            )
            self.emit(
                "audit.setup",
                f"Loaded system '{system.name}' with {len(ctx.controls)} control(s)",
                stage=AuditStage.SETUP,
                data={"controls": len(ctx.controls), "judge": ctx.judge is not None},
            )
            target = context_builder.build_target(self.session, system)

            cases = self._generate(ctx, config, corpus)
            if not cases:
                raise ValueError("No test cases were generated for the selected categories")
            self.checkpoint()
            invocations = self._infer(target, cases, config.get("concurrency", 4))
            self.checkpoint()
            rows = self._evaluate(ctx, cases, invocations)
            self.checkpoint()
            self._record_traces(system, cases, invocations)
            self._build_evidence(rows)
            findings, new_findings = self._create_findings(rows)
            self._map_controls(rows, policy_ids)
            self.checkpoint()
            self._finalize(rows, findings, new_findings)
        except AuditCancelled:
            self.session.rollback()
            self.channel.finish_run(self.run_id, RunStatus.CANCELLED)
            self.channel.append_terminal(
                "audit.cancelled", "Audit cancelled; no results were recorded", level="warning"
            )
        except ProviderUnavailable as exc:
            self.session.rollback()
            self.channel.fail(self.run_id, "provider_unavailable", str(exc))
        except Exception as exc:
            log.exception("audit_failed", audit_id=str(self.audit_id))
            self.session.rollback()
            self.channel.fail(self.run_id, "execution_error", f"{type(exc).__name__}: {exc}")
        self.session.expire_all()
        refreshed = self.session.get(Audit, self.audit_id)
        return refreshed or audit

    # -- stages ---------------------------------------------------------------------------------
    def _generate(
        self, ctx: EvaluationContext, config: dict[str, Any], corpus: ProbeCorpus | None
    ) -> list[TestCaseSpec]:
        gen = TestGenerator(
            GenerationConfig(
                intensity=self.audit.intensity,
                seed=config.get("seed", 1337),
                max_cases_per_category=config.get("cases_per_category"),
                repetitions_override=config.get("repetitions"),
                fact_questions=self._fact_questions(),
                corpus=corpus,
                domain=ctx.system.domain,
                tool_policies=ctx.system.tools,
            )
        )
        controls = list(ctx.controls.values())
        cases = gen.generate(self.audit.categories, controls=controls, attributes=config.get("attributes") or None)
        suite = TestSuite(
            organization_id=self.org_id,
            system_id=self.audit.system_id,
            name=f"{self.audit.name} suite",
            kind="generated",
            generator="aegis.generator",
            generator_version="1.2.0",
            seed=config.get("seed", 1337),
        )
        self.session.add(suite)
        self.session.flush()
        self.suite_id = suite.id
        total_invocations = 0
        records: dict[str, TestCase] = {}
        for case in cases:
            tc = TestCase(
                organization_id=self.org_id,
                suite_id=suite.id,
                audit_id=self.audit_id,
                external_key=case.key,
                category=case.category,
                test_type=case.test_type,
                control_ref=case.control_ref,
                name=case.name,
                inputs=[i.model_dump() for i in case.inputs],
                expected_behavior=case.expected_behavior,
                repetitions=case.repetitions,
                source=case.source,
                generator=case.generator,
                generator_version=case.generator_version,
                seed=case.seed,
                params=case.params,
            )
            self.session.add(tc)
            records[case.key] = tc
            total_invocations += len(case.inputs) * case.repetitions
        self.session.flush()
        self._case_db_ids = {key: tc.id for key, tc in records.items()}
        self.test_count = len(cases)
        self.emit(
            "audit.tests_generated",
            f"Generated {len(cases)} test case(s) ({total_invocations} invocations)",
            stage=AuditStage.TEST_GENERATION,
            level="success",
            data={"cases": len(cases), "invocations": total_invocations},
            progress=STAGE_PROGRESS[AuditStage.TEST_GENERATION],
            fields={"test_count": len(cases)},
        )
        for cat in sorted({c.category for c in cases}):
            n = sum(1 for c in cases if c.category == cat)
            self.emit(
                "audit.probe",
                f"{n} {cat.replace('_', ' ')} probe(s) prepared",
                stage=AuditStage.TEST_GENERATION,
                data={"category": cat, "count": n},
            )
        return cases

    def _fact_questions(self) -> list[str]:
        # Curated groundedness questions live in the system configuration.
        return list((self.audit.system.config or {}).get("fact_questions", []))

    def _infer(self, target: Any, cases: list[TestCaseSpec], concurrency: int) -> dict[str, list[SystemInvocation]]:
        self.emit(
            "audit.stage",
            "Running inference against the system under test",
            stage=AuditStage.INFERENCE,
            progress=STAGE_PROGRESS[AuditStage.TEST_GENERATION] + 3,
        )
        jobs: list[tuple[str, Any, int]] = []
        for case in cases:
            for test_input in case.inputs:
                for rep in range(case.repetitions):
                    jobs.append((case.key, test_input, rep))
        results: dict[str, list[SystemInvocation]] = defaultdict(list)
        completed = 0
        unavailable = False

        def invoke(job: tuple[str, Any, int]) -> tuple[str, SystemInvocation]:
            key, test_input, rep = job
            return key, target.invoke(test_input, rep)

        span = STAGE_PROGRESS[AuditStage.INFERENCE] - STAGE_PROGRESS[AuditStage.TEST_GENERATION]
        workers = max(1, min(concurrency, get_settings().inline_job_workers * 4, 12))
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aegis-infer")
        try:
            for key, inv in pool.map(invoke, jobs):
                results[key].append(inv)
                completed += 1
                if inv.error and "unavailable" in inv.error:
                    unavailable = True
                if inv.provider and inv.provider != "demo":
                    self._tally.model_calls.append(
                        {
                            "purpose": "inference",
                            "provider": inv.provider,
                            "model": inv.model,
                            "input_tokens": inv.input_tokens,
                            "output_tokens": inv.output_tokens,
                            "latency_ms": inv.latency_ms,
                        }
                    )
                if completed % max(1, len(jobs) // 12) == 0 or completed == len(jobs):
                    self.tests_completed = completed
                    self.emit(
                        "audit.inference_progress",
                        f"{completed}/{len(jobs)} invocations complete",
                        stage=AuditStage.INFERENCE,
                        data={"completed": completed, "total": len(jobs)},
                        progress=STAGE_PROGRESS[AuditStage.TEST_GENERATION] + int(span * completed / len(jobs)),
                        fields={"tests_completed": completed},
                    )
                    self.checkpoint()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        if unavailable:
            self.emit(
                "audit.warning",
                "The system under test was partly unreachable during inference",
                stage=AuditStage.INFERENCE,
                level="warning",
            )
        return results

    def _evaluate(
        self, ctx: EvaluationContext, cases: list[TestCaseSpec], invocations: dict[str, list[SystemInvocation]]
    ) -> list[ResultRow]:
        self.emit(
            "audit.stage",
            "Evaluating responses",
            stage=AuditStage.EVALUATION,
            progress=STAGE_PROGRESS[AuditStage.INFERENCE] + 2,
        )
        rows: list[ResultRow] = []
        span = STAGE_PROGRESS[AuditStage.EVALUATION] - STAGE_PROGRESS[AuditStage.INFERENCE]
        for i, case in enumerate(cases, start=1):
            evaluator = REGISTRY.for_case(case)
            invs = invocations.get(case.key, [])
            if evaluator is None:
                outcome = EvaluationOutcome(
                    status=ResultStatus.SKIPPED, summary="No evaluator registered for this test type"
                )
            else:
                try:
                    outcome = evaluator.run(ctx, case, invs)
                except Exception as exc:
                    log.exception("evaluator_error", case=case.key)
                    outcome = EvaluationOutcome(
                        status=ResultStatus.ERROR, summary=f"Evaluator error: {exc}", confidence=0.0
                    )
            row = ResultRow(
                case=case,
                outcome=outcome,
                evaluator_key=(evaluator.key if evaluator else "none"),
                evaluator_version=(evaluator.version if evaluator else "0"),
            )
            rows.append(row)
            self._persist_result(row, invs)
            if outcome.status == ResultStatus.FAILED:
                self.emit(
                    "audit.finding_signal",
                    f"⚠ {case.name}: {outcome.summary}",
                    stage=AuditStage.EVALUATION,
                    level="warning",
                    data={"category": case.category, "severity": outcome.severity},
                )
            if i % max(1, len(cases) // 10) == 0 or i == len(cases):
                self.emit(
                    "audit.eval_progress",
                    f"Evaluated {i}/{len(cases)} test(s)",
                    stage=AuditStage.EVALUATION,
                    data={"completed": i, "total": len(cases)},
                    progress=STAGE_PROGRESS[AuditStage.INFERENCE] + int(span * i / len(cases)),
                )
        return rows

    def _persist_result(self, row: ResultRow, invs: list[SystemInvocation]) -> None:
        case, outcome = row.case, row.outcome
        case_id = self._case_db_ids.get(case.key)
        result = TestResult(
            organization_id=self.org_id,
            audit_id=self.audit.id,
            test_case_id=case_id,
            category=case.category,
            test_type=case.test_type,
            control_ref=case.control_ref,
            status=outcome.status,
            score=outcome.score,
            severity=outcome.severity,
            confidence=outcome.confidence,
            summary=outcome.summary,
            observed=outcome.observed,
            evaluator_key=row.evaluator_key,
            evaluator_version=row.evaluator_version,
            latency_ms=int(sum(i.latency_ms or 0 for i in invs) / max(len(invs), 1)),
        )
        self.session.add(result)
        self.session.flush()
        row.result_id = result.id  # type: ignore[attr-defined]
        for judgment in outcome.judgments:
            self.session.add(
                Evaluation(
                    organization_id=self.org_id,
                    audit_id=self.audit.id,
                    test_result_id=result.id,
                    evaluator_key=row.evaluator_key,
                    evaluator_version=row.evaluator_version,
                    evaluator_model=judgment.evaluator_model,
                    prompt_version=judgment.prompt_version,
                    confidence=judgment.confidence,
                    raw_result=judgment.raw,
                    normalized_result=judgment.normalized,
                )
            )
        for claim in outcome.claims:
            self.session.add(
                Claim(
                    organization_id=self.org_id,
                    audit_id=self.audit.id,
                    test_result_id=result.id,
                    text=claim.text,
                    normalized=claim.normalized,
                    status=claim.status,
                    support_confidence=claim.support_confidence,
                    contradiction_confidence=claim.contradiction_confidence,
                    reason=claim.reason,
                    span_start=claim.span_start,
                    span_end=claim.span_end,
                    source_title=claim.source_title,
                    source_url=claim.source_url,
                    source_excerpt=claim.source_excerpt,
                    source_hash=claim.source_hash,
                    retrieved_at=claim.retrieved_at,
                    verification_method=claim.verification_method,
                    evaluator_key=row.evaluator_key,
                    evaluator_version=row.evaluator_version,
                )
            )

    def _record_traces(
        self, system: Any, cases: list[TestCaseSpec], invocations: dict[str, list[SystemInvocation]]
    ) -> None:
        """Persist a sample of observable agent traces (agent systems only)."""
        if system.system_type not in ("agent", "multi_agent"):
            return
        from aegis_api.services import agent_trace_service

        recorded = 0
        for case in cases:
            if recorded >= 12:
                break
            invs = invocations.get(case.key, [])
            sample = next((i for i in invs if i.ok and i.tool_calls), None) or (
                invs[0] if invs and invs[0].ok else None
            )
            if sample is not None and sample.tool_calls:
                agent_trace_service.record_invocation_trace(
                    self.session, system, sample, audit_id=self.audit.id, name=case.name, source="audit"
                )
                recorded += 1

    def _build_evidence(self, rows: list[ResultRow]) -> None:
        self.emit(
            "audit.stage",
            "Collecting evidence",
            stage=AuditStage.EVIDENCE,
            progress=STAGE_PROGRESS[AuditStage.EVIDENCE],
        )
        count = 0
        for row in rows:
            evidence_ids: list[uuid.UUID] = []
            for artifact in row.outcome.artifacts:
                self._evidence_seq += 1
                payload = {
                    "kind": artifact.kind,
                    "title": artifact.title,
                    "content": artifact.content,
                    "seq": self._evidence_seq,
                }
                c_hash, ch_hash, prev = self.chain.append(payload)
                ev = Evidence(
                    organization_id=self.org_id,
                    audit_id=self.audit_id,
                    system_id=self.audit.system_id,
                    seq=self._evidence_seq,
                    kind=artifact.kind,
                    title=artifact.title,
                    content=artifact.content,
                    sensitive=artifact.sensitive,
                    sensitive_ciphertext=encrypt_json(artifact.sensitive_raw) if artifact.sensitive_raw else None,
                    content_hash=c_hash,
                    prev_hash=prev,
                    chain_hash=ch_hash,
                    confidence_level=artifact.confidence_level,
                    confidence_reasons=artifact.confidence_reasons,
                    source_uri=artifact.source_uri,
                )
                self.session.add(ev)
                self.session.flush()
                result_id = getattr(row, "result_id", None)
                if result_id:
                    self.session.add(
                        EvidenceLink(
                            organization_id=self.org_id,
                            evidence_id=ev.id,
                            target_type="test_result",
                            target_id=result_id,
                            relation="supports",
                        )
                    )
                evidence_ids.append(ev.id)
                count += 1
            row.evidence_ids = evidence_ids  # type: ignore[attr-defined]
        self.evidence_count = count
        self.emit(
            "audit.evidence",
            f"Captured {count} evidence artifact(s) in a hash chain",
            stage=AuditStage.EVIDENCE,
            level="success",
            data={"count": count, "head": self.chain.head},
        )

    def _create_findings(self, rows: list[ResultRow]) -> tuple[list[Finding], list[Finding]]:
        self.emit(
            "audit.stage",
            "Scoring risk and creating findings",
            stage=AuditStage.RISK_SCORING,
            progress=STAGE_PROGRESS[AuditStage.POLICY_MAPPING],
        )
        groups = group_findings(rows)
        org = self.session.get(Organization, self.org_id)
        assert org is not None
        findings: list[Finding] = []
        new_findings: list[Finding] = []
        for group in groups:
            evidence_ids: list[uuid.UUID] = []
            for row in group.rows:
                evidence_ids.extend(getattr(row, "evidence_ids", []))
            finding = finding_service.create_from_group(
                self.session,
                self.audit,
                org,
                group,
                evidence_ids,
                self._extra_stats(group),
            )
            findings.append(finding)
            # A finding first detected by this audit is new; others were re-observed (or regressed).
            if finding.audit_id == self.audit_id:
                new_findings.append(finding)
            self.emit(
                "audit.finding_created",
                f"Finding #{finding.number}: {finding.title} [{finding.risk_level.upper()}]",
                stage=AuditStage.RISK_SCORING,
                level="warning",
                data={"finding_id": str(finding.id), "severity": finding.severity, "risk": finding.risk_level},
            )
        return findings, new_findings

    def _map_controls(self, rows: list[ResultRow], policy_ids: list[uuid.UUID]) -> None:
        if not policy_ids:
            return
        by_control: dict[str, list[ResultRow]] = defaultdict(list)
        for row in rows:
            if row.case.control_ref:
                by_control[row.case.control_ref].append(row)
        controls = self.session.scalars(select(Control).where(Control.policy_version_id.in_(policy_ids))).all()
        assessed = 0
        for control in controls:
            control_rows = by_control.get(control.control_id, [])
            if control.automation == "manual":
                status = "manual"
            elif not control_rows:
                status = "not_tested"
            else:
                failures = sum(1 for r in control_rows if r.outcome.failed)
                errors = sum(1 for r in control_rows if r.outcome.status == ResultStatus.ERROR)
                status = "failing" if failures else ("inconclusive" if errors == len(control_rows) else "passing")
            self.session.add(
                ControlAssessment(
                    organization_id=self.org_id,
                    audit_id=self.audit_id,
                    control_id=control.id,
                    status=status,
                    tests_run=len(control_rows),
                    failures=sum(1 for r in control_rows if r.outcome.failed),
                    evidence_count=sum(len(getattr(r, "evidence_ids", [])) for r in control_rows),
                    observed={"test_type": control.test_type},
                )
            )
            assessed += 1
        self.emit(
            "audit.policy_mapping",
            f"Assessed {assessed} policy control(s)",
            stage=AuditStage.POLICY_MAPPING,
            data={"controls": assessed},
            progress=STAGE_PROGRESS[AuditStage.POLICY_MAPPING],
        )

    def _finalize(self, rows: list[ResultRow], findings: list[Finding], new_findings: list[Finding]) -> None:
        from aegis_api.services import report_service, risk_service
        from engines.risk.scoring import dimension_scores_from_results, posture_from_scores

        assert self.channel is not None
        self.emit(
            "audit.stage",
            "Generating report",
            stage=AuditStage.REPORT_GENERATION,
            progress=STAGE_PROGRESS[AuditStage.RISK_SCORING],
        )
        result_dicts = [
            {"category": r.case.category, "status": r.outcome.status, "severity": r.outcome.severity} for r in rows
        ]
        scores = dimension_scores_from_results(result_dicts)
        matrix = test_matrix(rows)
        missing = self._missing_categories(rows)
        summary = {
            "dimensions": scores,
            "posture": posture_from_scores(scores),
            "test_matrix": matrix,
            "findings": {
                "total": len(findings),
                "new": len(new_findings),
                "by_severity": _severity_counts(findings),
                "by_dimension": _dimension_counts(findings),
            },
            "tests": {
                "total": len(rows),
                "failed": sum(1 for r in rows if r.outcome.failed),
                "errors": sum(1 for r in rows if r.outcome.status == ResultStatus.ERROR),
            },
            "high_risk": sum(1 for f in findings if f.risk_level in ("high", "critical")),
        }
        final_status = AuditStatus.PARTIALLY_COMPLETED if missing else AuditStatus.COMPLETED
        completed_at = utcnow()
        values: dict[str, Any] = {
            "status": final_status,
            "summary": summary,
            "cost": self._tally.cost(get_settings().model_pricing),
            "manifest": self._manifest(),
            "missing_categories": missing,
            "completed_at": completed_at,
            "progress": 100,
            "stage": AuditStage.REPORT_GENERATION,
            "test_count": self.test_count,
            "tests_completed": self.tests_completed,
            "findings_count": len(findings),
            "evidence_count": self.evidence_count,
            "evidence_head_hash": self.chain.head,
        }
        for mc in self._tally.model_calls:
            self.session.add(
                ModelCall(
                    organization_id=self.org_id,
                    audit_id=self.audit_id,
                    purpose=mc["purpose"],
                    provider=mc["provider"],
                    model=mc.get("model"),
                    input_tokens=mc.get("input_tokens"),
                    output_tokens=mc.get("output_tokens"),
                    latency_ms=mc.get("latency_ms"),
                    simulated=mc["provider"] == "demo",
                )
            )
        # The report and snapshot read a view of the final audit; the audits row itself is only written by
        # the conditional update below, so no lock is held on it while the results are assembled.
        view = SimpleNamespace(
            id=self.audit_id,
            organization_id=self.org_id,
            system_id=self.audit.system_id,
            system=self.audit.system,
            name=self.audit.name,
            categories=self.audit.categories,
            intensity=self.audit.intensity,
            policy_version_ids=self.audit.policy_version_ids,
            config=self.audit.config,
            started_at=self.audit.started_at,
            **values,
        )
        risk_service.write_snapshot(self.session, view, scores, findings)  # type: ignore[arg-type]
        report_service.generate_report(self.session, view, rows, findings, matrix, scores)  # type: ignore[arg-type]
        finding_service.notify_audit_complete(self.session, view, findings, new_findings=new_findings)
        from aegis_api.services import usage_service

        usage_service.record(
            self.session,
            self.org_id,
            "audit_run",
            quantity=1,
            source_type="audit",
            source_id=self.audit_id,
            metadata={"tests": len(rows), "intensity": self.audit.intensity},
        )
        updated = rowcount(
            self.session.execute(
                update(Audit)
                .where(
                    Audit.id == self.audit_id,
                    Audit.status == AuditStatus.RUNNING,
                    Audit.lease_owner == self.worker,
                    Audit.cancel_requested.is_(False),
                )
                .values(**values)
            )
        )
        if not updated:
            # Cancelled (or lease lost) while finalising: discard every result of this attempt.
            raise AuditCancelled()
        if self.run_id is not None:
            self.session.execute(
                update(AuditRun)
                .where(AuditRun.id == self.run_id)
                .values(status=RunStatus.COMPLETED, finished_at=completed_at)
            )
        self.session.commit()
        level = "warning" if final_status == AuditStatus.PARTIALLY_COMPLETED else "success"
        self.channel.append_terminal(
            "audit.completed",
            f"Audit complete — {len(findings)} finding(s) ({len(new_findings)} new), posture {summary['posture']}",
            level=level,
            data=summary,
        )

    def _extra_stats(self, group: Any) -> dict[str, Any]:
        # Pool counterfactual statistics across the group for the finding detail.
        if group.category == "fairness":
            evaluator = REGISTRY.get("fairness.counterfactual")
            if evaluator and hasattr(evaluator, "aggregate"):
                outcomes = [r.outcome for r in group.rows if r.outcome.observed.get("deltas")]
                if outcomes:
                    return {"counterfactual": evaluator.aggregate(outcomes)}
        return {}

    def _missing_categories(self, rows: list[ResultRow]) -> list[dict[str, Any]]:
        by_cat: dict[str, list[ResultRow]] = defaultdict(list)
        for r in rows:
            by_cat[r.case.category].append(r)
        missing = []
        for cat in self.audit.categories:
            cat_rows = by_cat.get(cat, [])
            if cat_rows and all(r.outcome.status == ResultStatus.ERROR for r in cat_rows):
                reason = next((r.outcome.summary for r in cat_rows if r.outcome.summary), "evaluation error")
                missing.append({"category": cat, "reason": reason})
            elif not cat_rows:
                missing.append(
                    {"category": cat, "reason": "no test cases generated (missing configuration or provider)"}
                )
        return missing

    def _manifest(self) -> dict[str, Any]:
        system = self.audit.system
        return {
            "system_version": system.version,
            "model": system.model_name,
            "model_version": system.model_version,
            "prompt_version": system.prompt_version,
            "policy_version_ids": self.audit.policy_version_ids,
            "evaluator_versions": {e.key: e.version for e in REGISTRY.all()},
            "generator_version": "1.2.0",
            "intensity": self.audit.intensity,
            "seed": (self.audit.config or {}).get("seed", 1337),
            "thresholds": {},
            "timestamp": utcnow().isoformat(),
        }


def _severity_counts(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for f in findings:
        counts[f.severity] += 1
    return dict(counts)


def _dimension_counts(findings: list[Finding]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for f in findings:
        counts[f.dimension] += 1
    return dict(counts)


def run_audit(session: Session, audit_id: uuid.UUID) -> Audit:
    audit = session.get(Audit, audit_id)
    if audit is None:
        raise ValueError(f"Audit {audit_id} not found")
    if audit.status in TERMINAL_AUDIT_STATUSES:
        return audit
    return AuditRunner(session, audit).run()
