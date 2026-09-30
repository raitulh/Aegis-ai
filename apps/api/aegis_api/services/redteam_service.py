"""Adaptive red-team runs against an imported corpus (no built-in attack payloads)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound
from aegis_api.models import Finding, Organization, RedTeamProbe, RedTeamRun
from aegis_api.models.enums import RunStatus, Severity
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, context_builder
from engines.common.types import CATEGORY_DIMENSION, ProbeResult
from engines.redteam.corpus import CorpusError, ProbeCorpus
from engines.redteam.engine import RedTeamConfig, RedTeamEngine


def create_run(session: Session, principal: Principal, data: Any) -> RedTeamRun:
    from aegis_api.services.system_service import get_system

    system = get_system(session, uuid.UUID(data.system_id), principal.organization_id)
    try:
        corpus = ProbeCorpus.from_records(data.corpus, name=data.corpus_name) if data.corpus else ProbeCorpus()
    except CorpusError as exc:
        from aegis_api.errors import ValidationFailed

        raise ValidationFailed(f"Invalid probe corpus: {exc}") from exc
    run = RedTeamRun(
        organization_id=principal.organization_id,
        system_id=system.id,
        name=data.name or f"Red team — {system.name}",
        status=RunStatus.QUEUED,
        config={
            "corpus": data.corpus,
            "corpus_name": data.corpus_name,
            "max_probes": data.max_probes,
            "max_depth": data.max_depth,
            "empty": corpus.is_empty,
        },
        created_by_id=principal.fk_user_id,
    )
    session.add(run)
    session.flush()
    audit_log.record(
        session,
        organization_id=principal.organization_id,
        action="redteam.created",
        resource_type="redteam_run",
        resource_id=run.id,
        principal=principal,
    )
    session.info.setdefault("after_commit", []).append((_job(), run.organization_id, str(run.id)))
    return run


def _job():
    from aegis_api.jobs.jobs import run_redteam_job

    return run_redteam_job


def execute_run(session: Session, run_id: uuid.UUID) -> RedTeamRun:
    run = session.get(RedTeamRun, run_id)
    if run is None:
        raise NotFound("Red team run not found")
    from aegis_api.services.system_service import get_system

    system = get_system(session, run.system_id, run.organization_id)
    run.status = RunStatus.RUNNING
    run.started_at = utcnow()
    session.flush()
    try:
        target = context_builder.build_target(session, system)
        profile = context_builder.build_system_profile(system)
        corpus = (
            ProbeCorpus.from_records(run.config.get("corpus", []), name=run.config.get("corpus_name", "custom"))
            if run.config.get("corpus")
            else ProbeCorpus()
        )
        engine = RedTeamEngine(
            target=target.invoke,
            config=RedTeamConfig(
                max_depth=run.config.get("max_depth", 0), max_probes=run.config.get("max_probes", 100)
            ),
            canary=profile.canary,
            allowed_domains=profile.guardrails.get("allowed_email_domains", []),
            domain=profile.domain,
        )
        report = engine.run(corpus)
        org = session.get(Organization, run.organization_id)
        assert org is not None
        for node in report.nodes:
            probe = RedTeamProbe(
                organization_id=run.organization_id,
                run_id=run.id,
                parent_id=uuid.UUID(node.parent_id) if node.parent_id else None,
                root_id=uuid.UUID(node.root_id),
                probe_key=node.probe_key,
                depth=node.depth,
                category=node.category,
                technique=node.technique,
                payload=node.payload,
                expected_behavior=node.expected_behavior,
                observed_behavior=node.observed_behavior,
                result=node.result,
                severity=node.severity if node.result == ProbeResult.BYPASSED else Severity.INFO,
                confidence=node.confidence,
                detection=node.detection,
            )
            # Map engine node ids to DB rows for parent linkage.
            probe.id = uuid.UUID(node.id)
            session.add(probe)
        session.flush()
        _create_findings(session, run, system, org, report)
        run.summary = report.summary()
        run.status = RunStatus.COMPLETED
    except Exception as exc:
        run.status = RunStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
    run.completed_at = utcnow()
    return run


def _create_findings(session: Session, run: RedTeamRun, system: Any, org: Organization, report: Any) -> None:
    from aegis_api.services import webhook_service

    bypasses = report.bypassed
    if not bypasses:
        return
    by_category: dict[str, list[Any]] = {}
    for node in bypasses:
        by_category.setdefault(node.category, []).append(node)
    for category, nodes in by_category.items():
        worst = max(nodes, key=lambda n: n.confidence)
        from engines.common.types import Category

        cat = (
            Category.PROMPT_INJECTION
            if "inject" in category
            else Category.JAILBREAK
            if category == "jailbreak"
            else Category.TOOL_ABUSE
            if "tool" in category or "exfil" in category
            else Category.PROMPT_INJECTION
        )
        finding = Finding(
            organization_id=run.organization_id,
            number=_num(org),
            title=f"Red-team bypass: {category.replace('_', ' ')}",
            category=cat,
            dimension=CATEGORY_DIMENSION.get(cat, "security"),
            severity=worst.severity,
            status="open",
            description=f"{len(nodes)} probe(s) in category '{category}' bypassed the system's guardrails. Example: {worst.observed_behavior}",
            system_id=system.id,
            system_version=system.version,
            test_type="prompt_injection",
            evaluator_key="redteam.engine",
            evaluator_version="1.0.0",
            confidence=worst.confidence,
            risk_level="high" if worst.severity in ("high", "critical") else "medium",
            risk_score=70.0 if worst.severity in ("high", "critical") else 45.0,
            risk_reasons=[f"{len(nodes)} successful bypass(es)", f"technique: {worst.technique}", "security dimension"],
            occurrences=len(nodes),
            sample_size=sum(1 for n in report.nodes if n.category == category),
            fingerprint=f"redteam:{category}",
            details={"corpus": report.corpus_name, "probes": [n.to_public() for n in nodes[:10]]},
            is_demo=system.is_demo,
        )
        session.add(finding)
        session.flush()
        for node in nodes:
            probe = session.get(RedTeamProbe, uuid.UUID(node.id))
            if probe:
                probe.finding_id = finding.id
        webhook_service.enqueue_event(
            session, run.organization_id, "finding.created", {"finding_id": str(finding.id), "source": "redteam"}
        )


def _num(org: Organization) -> int:
    org.finding_seq = (org.finding_seq or 1000) + 1
    return org.finding_seq
