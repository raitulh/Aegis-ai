"""Remediation application and regression re-testing (measured before/after)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.db.session import rowcount
from aegis_api.errors import NotFound
from aegis_api.models import (
    AISystem,
    Finding,
    FindingEvent,
    RegressionRun,
    RegressionTest,
    Remediation,
    SystemEvent,
    TestCase,
    TestSuite,
)
from aegis_api.models.enums import FindingStatus, RegressionVerdict, RemediationStatus, RunStatus
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, context_builder, system_service
from engines.evaluation.base import EvaluationContext, TestCaseSpec, TestInput
from engines.evaluation.registry import EvaluatorRegistry
from engines.remediation import apply_change, recommend

REGISTRY = EvaluatorRegistry()


def recommend_for_finding(finding: Finding) -> dict[str, Any]:
    rec = recommend(finding.test_type or "", finding.category)
    return {
        "category": rec.category,
        "title": rec.title,
        "description": rec.description,
        "change": rec.change,
        "creates_regression_test": rec.creates_regression_test,
    }


def create_remediation(session: Session, principal: Principal, finding: Finding, data: Any) -> Remediation:
    remediation = Remediation(
        organization_id=finding.organization_id,
        finding_id=finding.id,
        category=data.category,
        title=data.title,
        description=data.description,
        status=RemediationStatus.PROPOSED,
        source="manual",
        change=data.change or {},
        owner_id=uuid.UUID(data.owner_id) if data.owner_id else None,
        due_date=data.due_date,
    )
    session.add(remediation)
    if finding.status == FindingStatus.OPEN:
        finding.status = FindingStatus.ACKNOWLEDGED
    audit_log.record(
        session,
        organization_id=finding.organization_id,
        action="remediation.created",
        resource_type="remediation",
        resource_id=remediation.id,
        principal=principal,
    )
    return remediation


def approve_and_apply(session: Session, principal: Principal, remediation: Remediation) -> tuple[Remediation, AISystem]:
    finding = session.get(Finding, remediation.finding_id)
    if finding is None:
        raise NotFound("Finding not found")
    system = session.get(AISystem, finding.system_id)
    if system is None:
        raise NotFound("System not found")
    remediation.approved_by_id = principal.user_id
    remediation.approved_at = utcnow()
    remediation.status = RemediationStatus.APPLIED
    remediation.applied_at = utcnow()
    if remediation.change:
        before = dict(system.config or {})
        system.config = apply_change(system.config or {}, remediation.change)
        system.version = system_service._bump_version(system.version)
        system_service._snapshot(
            session, system, list(remediation.change.keys()), f"Applied remediation: {remediation.title}", principal
        )
        session.add(
            SystemEvent(
                organization_id=system.organization_id,
                system_id=system.id,
                type="remediation_applied",
                title=remediation.title,
                description=remediation.description,
                data={"change": remediation.change},
                occurred_at=utcnow(),
            )
        )
        audit_log.record(
            session,
            organization_id=system.organization_id,
            action="remediation.applied",
            resource_type="system",
            resource_id=system.id,
            principal=principal,
            before={"config": before},
            after={"config": system.config},
        )
    finding.status = FindingStatus.IN_REMEDIATION
    return remediation, system


def create_regression_test(session: Session, principal: Principal, finding: Finding) -> RegressionTest:
    suite = _suite_for_system(session, finding.system_id, finding.organization_id)
    spec = _spec_from_finding(session, finding)
    test = RegressionTest(
        organization_id=finding.organization_id,
        suite_id=suite.id,
        system_id=finding.system_id,
        finding_id=finding.id,
        name=f"Regression: {finding.title}",
        category=finding.category,
        test_type=finding.test_type or "custom_rule",
        control_ref=finding.control_ref,
        spec=spec,
        baseline={
            "status": "failed",
            "severity": finding.severity,
            "occurrences": finding.occurrences,
            "sample_size": finding.sample_size,
        },
    )
    session.add(test)
    audit_log.record(
        session,
        organization_id=finding.organization_id,
        action="regression.created",
        resource_type="regression_test",
        resource_id=finding.id,
        principal=principal,
    )
    return test


def _suite_for_system(session: Session, system_id: uuid.UUID, org_id: uuid.UUID) -> TestSuite:
    suite = session.scalar(select(TestSuite).where(TestSuite.system_id == system_id, TestSuite.kind == "regression"))
    if suite is None:
        suite = TestSuite(
            organization_id=org_id,
            system_id=system_id,
            name="Regression suite",
            kind="regression",
            generator="aegis.regression",
        )
        session.add(suite)
        session.flush()
    return suite


def _spec_from_finding(session: Session, finding: Finding) -> dict[str, Any]:
    tc = session.get(TestCase, finding.test_case_id) if finding.test_case_id else None
    if tc is None:
        tc = (
            session.scalar(
                select(TestCase).where(
                    TestCase.audit_id == finding.audit_id, TestCase.control_ref == finding.control_ref
                )
            )
            if finding.audit_id
            else None
        )
    if tc:
        return {
            "inputs": tc.inputs,
            "test_type": tc.test_type,
            "category": tc.category,
            "control_ref": tc.control_ref,
            "expected_behavior": tc.expected_behavior,
            "repetitions": max(tc.repetitions, 4),
            "params": tc.params,
        }
    return {
        "inputs": [],
        "test_type": finding.test_type,
        "category": finding.category,
        "control_ref": finding.control_ref,
        "expected_behavior": "",
        "repetitions": 4,
        "params": {},
    }


def run_regression(
    session: Session, principal: Principal, system: AISystem, finding: Finding | None = None, *, inline: bool = False
) -> RegressionRun:
    suite = _suite_for_system(session, system.id, system.organization_id)
    run = RegressionRun(
        organization_id=system.organization_id,
        suite_id=suite.id,
        system_id=system.id,
        status=RunStatus.QUEUED,
        system_version=system.version,
        triggered_by_id=principal.user_id,
    )
    if finding is not None:
        run.results = [{"finding_id": str(finding.id)}]
    session.add(run)
    session.flush()
    if inline:
        execute_run(session, run.id)
    else:
        session.info.setdefault("after_commit", []).append((_job(), run.organization_id, str(run.id)))
    return run


def _job():
    from aegis_api.jobs.jobs import run_regression_job

    return run_regression_job


def execute_run(session: Session, run_id: uuid.UUID) -> RegressionRun:
    run = session.get(RegressionRun, run_id)
    if run is None:
        raise NotFound("Regression run not found")
    system = session.get(AISystem, run.system_id)
    if system is None:
        raise NotFound("System not found")
    if run.status != RunStatus.QUEUED:
        return run  # duplicate delivery / already processed
    claimed = rowcount(
        session.execute(
            update(RegressionRun)
            .where(RegressionRun.id == run.id, RegressionRun.status == RunStatus.QUEUED)
            .values(status=RunStatus.RUNNING, started_at=utcnow(), heartbeat_at=utcnow())
        )
    )
    if not claimed:
        return run
    session.flush()
    session.refresh(run)
    try:
        target = context_builder.build_target(session, system)
        ctx = context_builder.build_context(session, system, [], seed=99, use_judge=False)
        seed_finding_id = run.results[0].get("finding_id") if run.results else None
        tests = _select_tests(session, system, seed_finding_id)
        results: list[dict[str, Any]] = []
        verdicts: list[str] = []
        for test in tests:
            outcome = _run_one(ctx, target, test)
            improved = outcome["after_status"] != "failed" and test.baseline.get("status") == "failed"
            verdict = RegressionVerdict.PASS if outcome["after_status"] != "failed" else RegressionVerdict.FAIL
            verdicts.append(verdict)
            finding = session.get(Finding, test.finding_id) if test.finding_id else None
            if finding is not None:
                _record_retest(session, finding, run, verdict, improved)
            results.append(
                {
                    "regression_test_id": str(test.id),
                    "finding_id": str(test.finding_id) if test.finding_id else None,
                    "name": test.name,
                    "before": test.baseline,
                    "after": outcome,
                    "verdict": verdict,
                    "improved": improved,
                }
            )
        run.results = results
        run.verdict = (
            RegressionVerdict.PASS
            if all(v == RegressionVerdict.PASS for v in verdicts)
            else (
                RegressionVerdict.PARTIAL
                if any(v == RegressionVerdict.PASS for v in verdicts)
                else RegressionVerdict.FAIL
            )
        )
        run.status = RunStatus.COMPLETED
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
        run.verdict = RegressionVerdict.ERROR
    run.completed_at = utcnow()
    return run


def _record_retest(session: Session, finding: Finding, run: RegressionRun, verdict: str, improved: bool) -> None:
    """Retest outcome drives the finding lifecycle: a pass resolves it, a failure keeps (or reopens) it."""
    from aegis_api.services import finding_service

    before = finding.status
    if verdict == RegressionVerdict.PASS and improved:
        finding.status = FindingStatus.RESOLVED
        finding.resolved_at = utcnow()
        event_type = "retest_passed"
    elif verdict == RegressionVerdict.FAIL:
        if finding.status in finding_service.REOPENABLE_STATUSES or finding.status == "retesting":
            finding.status = FindingStatus.OPEN if finding.status != "retesting" else "in_remediation"
            finding.resolved_at = None
        event_type = "retest_failed"
    else:
        event_type = "retest_completed"
    finding.updated_at = utcnow()
    session.add(
        FindingEvent(
            organization_id=finding.organization_id,
            finding_id=finding.id,
            type=event_type,
            from_status=before,
            to_status=finding.status,
            note=f"Regression run {run.id}: {verdict}",
            data={"regression_run_id": str(run.id), "verdict": verdict},
        )
    )
    if verdict == RegressionVerdict.FAIL:
        finding_service.record_occurrence(
            session,
            finding,
            source_type="regression",
            source_id=run.id,
            audit_id=None,
            occurrences=1,
            sample_size=1,
            system_version=run.system_version,
        )


def _select_tests(session: Session, system: AISystem, finding_id: str | None) -> list[RegressionTest]:
    q = select(RegressionTest).where(RegressionTest.system_id == system.id, RegressionTest.active.is_(True))
    if finding_id:
        q = q.where(RegressionTest.finding_id == uuid.UUID(finding_id))
    return list(session.scalars(q).all())


def _run_one(ctx: EvaluationContext, target: Any, test: RegressionTest) -> dict[str, Any]:
    spec = test.spec
    case = TestCaseSpec(
        key=f"reg-{test.id}",
        category=test.category,
        test_type=test.test_type,
        name=test.name,
        inputs=[TestInput(**i) for i in spec.get("inputs", [])] or [TestInput(prompt="Regression check")],
        expected_behavior=spec.get("expected_behavior", ""),
        repetitions=spec.get("repetitions", 4),
        control_ref=test.control_ref,
        params=spec.get("params", {}),
    )
    evaluator = REGISTRY.for_case(case)
    if evaluator is None:
        return {"after_status": "error", "summary": "No evaluator"}
    invs = [target.invoke(ti, rep) for ti in case.inputs for rep in range(case.repetitions)]
    outcome = evaluator.run(ctx, case, invs)
    return {
        "after_status": outcome.status,
        "severity": outcome.severity,
        "summary": outcome.summary,
        "score": outcome.score,
        "observed": {
            k: outcome.observed.get(k)
            for k in ("mean_delta_points", "violations", "leaks_by_surface", "status_counts")
            if k in outcome.observed
        },
    }
