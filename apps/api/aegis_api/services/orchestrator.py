"""Audit orchestrator — executes an audit end to end and persists all results.

Stages: setup → test_generation → inference → evaluation → evidence → policy_mapping → risk_scoring →
report_generation. Progress and a live event stream are written to ``audit_events`` (served over SSE).

Design choices:
* Target inference runs on a thread pool (providers are blocking I/O); evaluators and persistence run on
  the orchestrator thread. Invocations are plain data, so no ORM object crosses threads.
* A provider becoming unavailable degrades the audit to ``partially_completed`` with the affected
  categories recorded — it is never silently marked as passed.
* Evidence is written as an append-only hash chain for tamper-evidence.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
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


class AuditRunner:
    def __init__(self, session: Session, audit: Audit) -> None:
        self.session = session
        self.audit = audit
        self.org_id = audit.organization_id
        self._seq = 0
        self._tally = _Tally()
        self.chain = ChainState()
        self._evidence_seq = 0

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
    ) -> None:
        self._seq += 1
        if progress is not None:
            self.audit.progress = progress
        event = AuditEvent(
            organization_id=self.org_id,
            audit_id=self.audit.id,
            seq=self._seq,
            type=type_,
            stage=stage,
            level=level,
            message=message,
            progress=self.audit.progress,
            data=data or {},
        )
        self.session.add(event)
        self.session.flush()

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
        audit = self.audit
        run = AuditRun(
            organization_id=self.org_id, audit_id=audit.id, attempt=1, status=RunStatus.RUNNING, started_at=utcnow()
        )
        self.session.add(run)
        audit.status = AuditStatus.RUNNING
        audit.started_at = utcnow()
        audit.stage = AuditStage.SETUP
        self.emit(
            "audit.started",
            f"Audit '{audit.name}' started",
            stage=AuditStage.SETUP,
            level="success",
            progress=STAGE_PROGRESS[AuditStage.SETUP],
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
            invocations = self._infer(target, cases, config.get("concurrency", 4))
            rows = self._evaluate(ctx, cases, invocations)
            self._record_traces(system, cases, invocations)
            self._build_evidence(rows)
            findings = self._create_findings(rows)
            self._map_controls(rows, policy_ids)
            self._finalize(rows, findings, run)
        except ProviderUnavailable as exc:
            self._fail(run, "provider_unavailable", str(exc))
        except Exception as exc:
            log.exception("audit_failed", audit_id=str(audit.id))
            self._fail(run, "execution_error", f"{type(exc).__name__}: {exc}")
        return audit

    # -- stages ---------------------------------------------------------------------------------
    def _generate(
        self, ctx: EvaluationContext, config: dict[str, Any], corpus: ProbeCorpus | None
    ) -> list[TestCaseSpec]:
        self.audit.stage = AuditStage.TEST_GENERATION
        fact_qs = self._fact_questions()
        gen = TestGenerator(
            GenerationConfig(
                intensity=self.audit.intensity,
                seed=config.get("seed", 1337),
                max_cases_per_category=config.get("cases_per_category"),
                repetitions_override=config.get("repetitions"),
                fact_questions=fact_qs,
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
        for case in cases:
            tc = TestCase(
                organization_id=self.org_id,
                suite_id=suite.id,
                audit_id=self.audit.id,
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
            case.params["_db_id"] = None  # placeholder; set after flush
            total_invocations += len(case.inputs) * case.repetitions
        self.session.flush()
        self._case_db_ids = {
            c.external_key: c.id
            for c in self.session.scalars(select(TestCase).where(TestCase.audit_id == self.audit.id)).all()
        }
        self.audit.test_count = len(cases)
        self.emit(
            "audit.tests_generated",
            f"Generated {len(cases)} test case(s) ({total_invocations} invocations)",
            stage=AuditStage.TEST_GENERATION,
            level="success",
            data={"cases": len(cases), "invocations": total_invocations},
            progress=STAGE_PROGRESS[AuditStage.TEST_GENERATION],
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
        from aegis_api.models import KnowledgeDocument

        docs = self.session.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.organization_id == self.org_id,
                (KnowledgeDocument.system_id == self.audit.system_id) | (KnowledgeDocument.system_id.is_(None)),
            )
        ).all()
        questions: list[str] = []
        for doc in docs:
            questions.extend((doc.version and []) or [])
        # System config may carry curated fact questions for groundedness.
        cfg_qs = (self.audit.system.config or {}).get("fact_questions", [])
        return list(cfg_qs) + questions

    def _infer(self, target: Any, cases: list[TestCaseSpec], concurrency: int) -> dict[str, list[SystemInvocation]]:
        self.audit.stage = AuditStage.INFERENCE
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
        with ThreadPoolExecutor(max_workers=workers) as pool:
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
                    self.audit.tests_completed = completed
                    self.emit(
                        "audit.inference_progress",
                        f"{completed}/{len(jobs)} invocations complete",
                        stage=AuditStage.INFERENCE,
                        data={"completed": completed, "total": len(jobs)},
                        progress=STAGE_PROGRESS[AuditStage.TEST_GENERATION] + int(span * completed / len(jobs)),
                    )
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
        self.audit.stage = AuditStage.EVALUATION
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
        self.audit.stage = AuditStage.EVIDENCE
        self.emit(
            "audit.stage",
            "Collecting evidence",
            stage=AuditStage.EVIDENCE,
            progress=STAGE_PROGRESS[AuditStage.EVIDENCE],
        )
        count = 0
        for row in rows:
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
                    audit_id=self.audit.id,
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
                row.setdefault_evidence = getattr(row, "evidence_ids", [])  # type: ignore[attr-defined]
                if not hasattr(row, "evidence_ids"):
                    row.evidence_ids = []  # type: ignore[attr-defined]
                row.evidence_ids.append(ev.id)  # type: ignore[attr-defined]
                count += 1
        self.audit.evidence_count = count
        self.audit.evidence_head_hash = self.chain.head
        self.emit(
            "audit.evidence",
            f"Captured {count} evidence artifact(s) in a hash chain",
            stage=AuditStage.EVIDENCE,
            level="success",
            data={"count": count, "head": self.chain.head},
        )

    def _create_findings(self, rows: list[ResultRow]) -> list[Finding]:
        self.audit.stage = AuditStage.RISK_SCORING
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
            self.emit(
                "audit.finding_created",
                f"Finding #{finding.number}: {finding.title} [{finding.risk_level.upper()}]",
                stage=AuditStage.RISK_SCORING,
                level="warning",
                data={"finding_id": str(finding.id), "severity": finding.severity, "risk": finding.risk_level},
            )
        self.audit.findings_count = len(findings)
        return findings

    def _extra_stats(self, group: Any) -> dict[str, Any]:
        # Pool counterfactual statistics across the group for the finding detail.
        if group.category == "fairness":
            evaluator = REGISTRY.get("fairness.counterfactual")
            if evaluator and hasattr(evaluator, "aggregate"):
                outcomes = [r.outcome for r in group.rows if r.outcome.observed.get("deltas")]
                if outcomes:
                    return {"counterfactual": evaluator.aggregate(outcomes)}
        return {}

    def _map_controls(self, rows: list[ResultRow], policy_ids: list[uuid.UUID]) -> None:
        self.audit.stage = AuditStage.POLICY_MAPPING
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
                    audit_id=self.audit.id,
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

    def _finalize(self, rows: list[ResultRow], findings: list[Finding], run: AuditRun) -> None:
        from aegis_api.services import report_service, risk_service

        self.audit.stage = AuditStage.REPORT_GENERATION
        result_dicts = [
            {"category": r.case.category, "status": r.outcome.status, "severity": r.outcome.severity} for r in rows
        ]
        from engines.risk.scoring import dimension_scores_from_results, posture_from_scores

        scores = dimension_scores_from_results(result_dicts)
        matrix = test_matrix(rows)
        errored_categories = self._missing_categories(rows)
        summary = {
            "dimensions": scores,
            "posture": posture_from_scores(scores),
            "test_matrix": matrix,
            "findings": {
                "total": len(findings),
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
        self.audit.summary = summary
        self.audit.cost = self._tally.cost(get_settings().model_pricing)
        self.audit.manifest = self._manifest()
        for mc in self._tally.model_calls:
            self.session.add(
                ModelCall(
                    organization_id=self.org_id,
                    audit_id=self.audit.id,
                    purpose=mc["purpose"],
                    provider=mc["provider"],
                    model=mc.get("model"),
                    input_tokens=mc.get("input_tokens"),
                    output_tokens=mc.get("output_tokens"),
                    latency_ms=mc.get("latency_ms"),
                    simulated=mc["provider"] == "demo",
                )
            )
        risk_service.write_snapshot(self.session, self.audit, scores, findings)
        report_service.generate_report(self.session, self.audit, rows, findings, matrix, scores)
        if errored_categories:
            self.audit.status = AuditStatus.PARTIALLY_COMPLETED
            self.audit.missing_categories = errored_categories
        else:
            self.audit.status = AuditStatus.COMPLETED
        self.audit.completed_at = utcnow()
        self.audit.progress = 100
        run.status = RunStatus.COMPLETED
        run.finished_at = utcnow()
        level = "warning" if self.audit.status == AuditStatus.PARTIALLY_COMPLETED else "success"
        self.emit(
            "audit.completed",
            f"Audit complete — {len(findings)} finding(s), posture {summary['posture']}",
            stage=AuditStage.REPORT_GENERATION,
            level=level,
            data=summary,
            progress=100,
        )
        finding_service.notify_audit_complete(self.session, self.audit, findings)

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

    def _fail(self, run: AuditRun, code: str, message: str) -> None:
        self.audit.status = AuditStatus.FAILED
        self.audit.error_code = code
        self.audit.error_message = message
        self.audit.completed_at = utcnow()
        run.status = RunStatus.FAILED
        run.finished_at = utcnow()
        run.error = message
        self.emit("audit.failed", f"Audit failed: {message}", level="error", data={"code": code})


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
