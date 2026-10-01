"""Runtime Agent Guard: ingest normalized agent telemetry, evaluate runtime policies, decide, prove.

Pipeline (per event)::

    adapter (SDK / MCP / OTel / LangGraph / CrewAI)
      → normalize (schema aegis.runtime.v1, redaction, deterministic signals)
      → applicable published policies (workspace / environment / system assignments)
      → decision (allow | flag | require_approval | block)
      → mode: observe  — record only, never interferes
              audit    — record + violations become findings (with evidence)
              enforce  — the decision is returned to the agent; approvals are queued for a person
      → evidence (violations are appended to the system's runtime hash chain) → webhooks / alerts

Enforcement never bypasses authentication: only callers holding ``runtime:decide`` receive decisions, and
approvals can only be granted by ``approvals:decide`` holders in the same workspace.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.models import (
    AISystem,
    Evidence,
    EvidenceLink,
    Finding,
    FindingEvent,
    FindingOccurrence,
    RuntimeApproval,
    RuntimeEvent,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log, entitlements, finding_service, runtime_policy_service, usage_service
from engines.common.text import stable_hash
from engines.evidence.hashing import chain_hash, content_hash
from engines.runtime.policy import evaluate
from engines.runtime.schema import RuntimeEventIn, normalize

log = structlog.get_logger("aegis.runtime")
MODES = ("observe", "audit", "enforce")
APPROVAL_TTL = timedelta(hours=1)
SEVERITY_RISK = {"critical": "critical", "high": "high", "medium": "medium", "low": "low", "info": "low"}


def _system(session: Session, organization_id: uuid.UUID, system_id: str, cache: dict[str, AISystem]) -> AISystem:
    if system_id in cache:
        return cache[system_id]
    try:
        sid = uuid.UUID(system_id)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid system_id '{system_id}'") from exc
    system = session.get(AISystem, sid)
    if system is None or system.organization_id != organization_id or system.deleted_at is not None:
        raise NotFound(f"AI system {system_id} not found")
    cache[system_id] = system
    return system


def effective_mode(session: Session, system: AISystem) -> tuple[str, str | None]:
    mode = system.runtime_mode if system.runtime_mode in MODES else get_settings().runtime_default_mode
    if mode == "enforce":
        try:
            entitlements.require_feature(session, system.organization_id, "runtime_enforcement")
        except Exception:
            return "audit", "Enforcement is not included in the current plan; evaluated in audit mode."
    return mode, None


def process(
    session: Session,
    principal: Principal,
    events: list[RuntimeEventIn],
    *,
    synchronous: bool = False,
) -> list[dict[str, Any]]:
    """Ingest events and return one decision record per event (in order). Duplicate ``event_id``s return
    the originally recorded decision without re-processing (idempotent ingestion)."""
    org_id = principal.organization_id
    systems: dict[str, AISystem] = {}
    policy_cache: dict[uuid.UUID, list[dict[str, Any]]] = {}
    results: list[dict[str, Any]] = []
    accepted = 0
    for raw in events:
        system = _system(session, org_id, raw.system_id, systems)
        normalized = normalize(raw, default_classification=system.data_classification)
        if not normalized["environment"]:
            normalized["environment"] = system.environment
        existing = session.scalar(
            select(RuntimeEvent).where(
                RuntimeEvent.organization_id == org_id, RuntimeEvent.event_id == normalized["event_id"]
            )
        )
        if existing is not None:
            results.append(_result(existing, duplicate=True))
            continue
        if system.id not in policy_cache:
            policy_cache[system.id] = runtime_policy_service.applicable_policies(session, system)
        verdict = evaluate(normalized, policy_cache[system.id])
        mode, mode_note = effective_mode(session, system)
        decision = verdict["decision"]
        effective = decision if mode == "enforce" else "allow"
        reason = (verdict["reason"] + (f" ({mode_note})" if mode_note else ""))[:2000]
        inserted = session.execute(
            insert(RuntimeEvent)
            .values(
                id=uuid.uuid4(),
                organization_id=org_id,
                system_id=system.id,
                mode=mode,
                decision=decision,
                effective_decision=effective,
                decision_reason=reason,
                policy_matches=verdict["matches"],
                risk_level=verdict["risk_level"],
                **normalized,
            )
            .on_conflict_do_nothing(constraint="uq_runtime_events_org_event")
            .returning(RuntimeEvent.id)
        ).scalar()
        if inserted is None:  # concurrent duplicate committed first
            existing = session.scalar(
                select(RuntimeEvent).where(
                    RuntimeEvent.organization_id == org_id, RuntimeEvent.event_id == normalized["event_id"]
                )
            )
            if existing is not None:
                results.append(_result(existing, duplicate=True))
            continue
        accepted += 1
        stored = session.get(RuntimeEvent, inserted)
        assert stored is not None
        if decision != "allow" and mode in ("audit", "enforce"):
            _record_violation(session, system, stored, verdict)
        if effective == "require_approval":
            approval = RuntimeApproval(
                organization_id=org_id,
                system_id=system.id,
                runtime_event_id=stored.id,
                status="pending",
                summary=f"{stored.event_type}{' · ' + stored.tool_name if stored.tool_name else ''}: {verdict['reason']}"[
                    :500
                ],
                rule_ref=", ".join(
                    f"{m['policy_key']}/{m['rule_id']}" for m in verdict["matches"] if m["action"] == "require_approval"
                )[:200],
                request={
                    "event_type": stored.event_type,
                    "tool": stored.tool_name,
                    "agent": stored.agent_name,
                    "payload": stored.payload,
                },
                expires_at=utcnow() + APPROVAL_TTL,
            )
            session.add(approval)
            session.flush()
            stored.approval_id = approval.id
        _metrics(stored)
        results.append(_result(stored))
    if accepted:
        usage_service.record(
            session,
            org_id,
            "runtime_event",
            quantity=accepted,
            source_type="runtime_batch",
            source_id=uuid.uuid4(),
            metadata={"synchronous": synchronous},
        )
    return results


def _metrics(event: RuntimeEvent) -> None:
    from aegis_api.observability import metrics

    try:
        metrics.RUNTIME_EVENTS.labels(event.event_type).inc()
        metrics.RUNTIME_DECISIONS.labels(event.decision, event.mode).inc()
        if event.decision != "allow":
            metrics.POLICY_VIOLATIONS.labels(event.decision).inc()
    except Exception:  # pragma: no cover - metrics are best-effort
        log.debug("runtime_metrics_failed", exc_info=True)


def _result(event: RuntimeEvent, duplicate: bool = False) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "event_id": event.event_id,
        "system_id": str(event.system_id),
        "event_type": event.event_type,
        "mode": event.mode,
        "decision": event.decision,
        "effective_decision": event.effective_decision,
        "allowed": event.effective_decision in ("allow", "flag"),
        "reason": event.decision_reason,
        "matches": event.policy_matches,
        "risk_level": event.risk_level,
        "approval_id": str(event.approval_id) if event.approval_id else None,
        "finding_id": str(event.finding_id) if event.finding_id else None,
        "evidence_id": str(event.evidence_id) if event.evidence_id else None,
        "duplicate": duplicate,
    }


def _record_violation(session: Session, system: AISystem, event: RuntimeEvent, verdict: dict[str, Any]) -> None:
    """Violations in audit/enforce mode become evidence (system runtime chain) and findings (deduplicated per
    rule and system; one occurrence row per day with a running count)."""
    deciding = [m for m in verdict["matches"] if m["action"] == verdict["decision"]] or verdict["matches"]
    primary = deciding[0]
    evidence = _append_runtime_evidence(session, system, event, verdict)
    event.evidence_id = evidence.id
    # Fingerprints are fixed-width; the readable policy/rule key is kept in the finding details.
    fingerprint = "runtime:" + stable_hash(f"{primary['policy_key']}:{primary['rule_id']}", 56)
    finding = finding_service.find_existing(session, system.organization_id, system.id, fingerprint)
    severity = primary.get("severity", "medium")
    if finding is None:
        finding = Finding(
            organization_id=system.organization_id,
            number=finding_service.allocate_finding_number(system.organization_id),
            title=f"Runtime policy violation: {primary['message']}"[:300],
            category="agent_action"
            if event.event_type in ("tool.call", "mcp.tool.call", "file.write", "file.read", "database.query")
            else "policy",
            dimension="governance",
            severity=severity,
            status="open",
            description=(
                f"Runtime Guard ({event.mode} mode) decided '{verdict['decision']}' for a {event.event_type} event"
                f"{' from agent ' + event.agent_name if event.agent_name else ''}. Policy {primary['policy_key']} "
                f"v{primary['version']}, rule {primary['rule_id']}."
            ),
            system_id=system.id,
            system_version=system.version,
            control_ref=None,
            test_type="runtime_policy",
            evaluator_key="runtime.guard",
            evaluator_version="1.0.0",
            confidence=1.0,
            impact=f"{SEVERITY_RISK.get(severity, 'medium').title()} risk observed in production telemetry.",
            risk_level=SEVERITY_RISK.get(severity, "medium"),
            risk_score={"critical": 90.0, "high": 72.0, "medium": 50.0, "low": 25.0}.get(severity, 25.0),
            risk_reasons=[
                "deterministic runtime policy match",
                f"decision: {verdict['decision']}",
                f"environment: {event.environment or system.environment}",
            ],
            risk_factors=[],
            occurrences=1,
            sample_size=1,
            fingerprint=fingerprint,
            source="runtime",
            details={
                "policy_key": primary["policy_key"],
                "rule_id": primary["rule_id"],
                "first_event_id": event.event_id,
            },
            last_seen_at=utcnow(),
            sla_due_at=finding_service.sla_due(severity),
            is_demo=system.is_demo,
        )
        session.add(finding)
        session.flush()
        session.add(
            FindingEvent(
                organization_id=system.organization_id,
                finding_id=finding.id,
                type="created",
                to_status="open",
                note=f"Created by Runtime Guard from event {event.event_id}",
                data={"runtime_event_id": str(event.id)},
            )
        )
        from aegis_api.services import webhook_service

        webhook_service.enqueue_event(
            session,
            system.organization_id,
            "finding.created",
            {"finding_id": str(finding.id), "number": finding.number, "source": "runtime", "severity": severity},
        )
    else:
        if finding.status in finding_service.REOPENABLE_STATUSES:
            session.add(
                FindingEvent(
                    organization_id=system.organization_id,
                    finding_id=finding.id,
                    type="regressed",
                    from_status=finding.status,
                    to_status="open",
                    note=f"Runtime violation observed again (event {event.event_id})",
                )
            )
            finding.status = "open"
            finding.resolved_at = None
        finding.occurrences = (finding.occurrences or 0) + 1
        finding.sample_size = (finding.sample_size or 0) + 1
        finding.last_seen_at = utcnow()
    event.finding_id = finding.id
    session.add(
        EvidenceLink(
            organization_id=system.organization_id,
            evidence_id=evidence.id,
            target_type="finding",
            target_id=finding.id,
            relation="supports",
        )
    )
    day_source = uuid.uuid5(finding.id, utcnow().date().isoformat())
    occurrence = session.scalar(
        select(FindingOccurrence).where(
            FindingOccurrence.finding_id == finding.id,
            FindingOccurrence.source_type == "runtime",
            FindingOccurrence.source_id == day_source,
        )
    )
    if occurrence is None:
        finding_service.record_occurrence(
            session,
            finding,
            source_type="runtime",
            source_id=day_source,
            audit_id=None,
            occurrences=1,
            sample_size=1,
            system_version=system.version,
        )
    else:
        occurrence.occurrences += 1
        occurrence.sample_size += 1
    from aegis_api.services import webhook_service

    webhook_service.enqueue_event(
        session,
        system.organization_id,
        "policy.violation",
        {
            "runtime_event_id": str(event.id),
            "event_id": event.event_id,
            "system_id": str(system.id),
            "decision": verdict["decision"],
            "mode": event.mode,
            "policy": primary["policy_key"],
            "rule": primary["rule_id"],
            "finding_id": str(finding.id),
        },
    )


def _append_runtime_evidence(
    session: Session, system: AISystem, event: RuntimeEvent, verdict: dict[str, Any]
) -> Evidence:
    """Append to the system's runtime evidence chain. A transaction-scoped advisory lock per system
    serialises appends, so concurrent ingestion cannot fork the chain."""
    session.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"runtime-chain:{system.id}"})
    last = session.scalar(
        select(Evidence)
        .where(Evidence.system_id == system.id, Evidence.audit_id.is_(None), Evidence.kind == "runtime_decision")
        .order_by(Evidence.seq.desc())
        .limit(1)
    )
    seq = (last.seq if last else 0) + 1
    prev = last.chain_hash if last else None
    title = f"Runtime decision: {verdict['decision']} {event.event_type}"[:300]
    content = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "agent": event.agent_name,
        "tool": event.tool_name,
        "environment": event.environment,
        "mode": event.mode,
        "decision": verdict["decision"],
        "effective_decision": event.effective_decision,
        "reason": verdict["reason"],
        "matches": verdict["matches"],
        "payload": event.payload,
        "signals": {k: v for k, v in (event.signals or {}).items() if k != "path"},
    }
    c_hash = content_hash({"kind": "runtime_decision", "title": title, "content": content, "seq": seq})
    evidence = Evidence(
        organization_id=system.organization_id,
        audit_id=None,
        system_id=system.id,
        seq=seq,
        kind="runtime_decision",
        title=title,
        content=content,
        sensitive=False,
        content_hash=c_hash,
        prev_hash=prev,
        chain_hash=chain_hash(prev, c_hash),
        confidence_level="high",
        confidence_reasons=["deterministic policy evaluation of recorded telemetry"],
        source_uri=f"aegis://runtime/{event.event_id}",
    )
    session.add(evidence)
    session.flush()
    return evidence


def verify_runtime_chain(session: Session, system: AISystem) -> dict[str, Any]:
    from engines.evidence import package

    rows = session.scalars(
        select(Evidence)
        .where(Evidence.system_id == system.id, Evidence.audit_id.is_(None), Evidence.kind == "runtime_decision")
        .order_by(Evidence.seq)
    ).all()
    artifacts = [
        {
            "seq": r.seq,
            "kind": r.kind,
            "title": r.title,
            "content": r.content,
            "content_hash": r.content_hash,
            "prev_hash": r.prev_hash,
            "chain_hash": r.chain_hash,
            "purged": r.purged_at is not None,
        }
        for r in rows
    ]
    report = package.verify_artifacts(artifacts)
    return {"system_id": str(system.id), **report.as_dict(), "status": report.status if rows else "EMPTY"}


# --- approvals -----------------------------------------------------------------------------------
def get_approval(session: Session, approval_id: uuid.UUID, organization_id: uuid.UUID) -> RuntimeApproval:
    approval = session.get(RuntimeApproval, approval_id)
    if approval is None or approval.organization_id != organization_id:
        raise NotFound("Approval not found")
    if approval.status == "pending" and approval.expires_at < utcnow():
        approval.status = "expired"
    return approval


def decide_approval(
    session: Session, principal: Principal, approval: RuntimeApproval, approve: bool, note: str | None
) -> RuntimeApproval:
    if approval.status != "pending":
        raise Conflict(f"Approval is already {approval.status}")
    approval.status = "approved" if approve else "denied"
    approval.decided_by_id = principal.user_id if principal.auth_method != "api_key" else None
    approval.decided_by_label = principal.actor_label
    approval.decided_at = utcnow()
    approval.decision_note = note
    approval.updated_at = utcnow()
    event = session.get(RuntimeEvent, approval.runtime_event_id)
    audit_log.record(
        session,
        organization_id=approval.organization_id,
        action="runtime.approval_granted" if approve else "runtime.approval_denied",
        resource_type="runtime_approval",
        resource_id=approval.id,
        principal=principal,
        after={"event_id": event.event_id if event else None, "note": note},
    )
    from aegis_api.services import webhook_service

    webhook_service.enqueue_event(
        session,
        approval.organization_id,
        "runtime.approval_decided",
        {"approval_id": str(approval.id), "status": approval.status},
    )
    return approval


def set_mode(session: Session, principal: Principal, system: AISystem, mode: str) -> AISystem:
    if mode not in MODES:
        raise ValidationFailed(f"mode must be one of {MODES}")
    if mode == "enforce":
        entitlements.require_feature(session, system.organization_id, "runtime_enforcement")
    before = system.runtime_mode
    system.runtime_mode = mode
    system.updated_at = utcnow()
    audit_log.record(
        session,
        organization_id=system.organization_id,
        action="runtime.mode_changed",
        resource_type="ai_system",
        resource_id=system.id,
        principal=principal,
        before={"mode": before},
        after={"mode": mode},
    )
    return system


# --- read models ---------------------------------------------------------------------------------
def overview(
    session: Session, organization_id: uuid.UUID, *, hours: int = 24, system_id: uuid.UUID | None = None
) -> dict[str, Any]:
    since = utcnow() - timedelta(hours=hours)
    base = [RuntimeEvent.organization_id == organization_id, RuntimeEvent.occurred_at >= since]
    if system_id:
        base.append(RuntimeEvent.system_id == system_id)
    total = session.scalar(select(func.count(RuntimeEvent.id)).where(*base)) or 0
    decisions = dict(
        session.execute(select(RuntimeEvent.decision, func.count()).where(*base).group_by(RuntimeEvent.decision)).all()
    )
    types = dict(
        session.execute(
            select(RuntimeEvent.event_type, func.count()).where(*base).group_by(RuntimeEvent.event_type)
        ).all()
    )
    bucket = "hour" if hours <= 72 else "day"
    timeline = [
        {"t": t.isoformat(), "events": int(n), "violations": int(v)}
        for t, n, v in session.execute(
            select(
                func.date_trunc(bucket, RuntimeEvent.occurred_at).label("t"),
                func.count(),
                func.count().filter(RuntimeEvent.decision != "allow"),
            )
            .where(*base)
            .group_by("t")
            .order_by("t")
        ).all()
    ]
    agents = [
        {"agent": a or "(unnamed)", "events": int(n), "violations": int(v)}
        for a, n, v in session.execute(
            select(
                RuntimeEvent.agent_name,
                func.count(),
                func.count().filter(RuntimeEvent.decision != "allow"),
            )
            .where(*base)
            .group_by(RuntimeEvent.agent_name)
            .order_by(func.count().desc())
            .limit(10)
        ).all()
    ]
    pending = (
        session.scalar(
            select(func.count(RuntimeApproval.id)).where(
                RuntimeApproval.organization_id == organization_id,
                RuntimeApproval.status == "pending",
                RuntimeApproval.expires_at > utcnow(),
            )
        )
        or 0
    )
    systems = session.execute(
        select(AISystem.id, AISystem.name, AISystem.runtime_mode).where(
            AISystem.organization_id == organization_id, AISystem.deleted_at.is_(None)
        )
    ).all()
    return {
        "window_hours": hours,
        "events": int(total),
        "decisions": {k: int(decisions.get(k, 0)) for k in ("allow", "flag", "require_approval", "block")},
        "event_types": {k: int(v) for k, v in types.items()},
        "timeline": timeline,
        "agents": agents,
        "pending_approvals": int(pending),
        "systems": [{"id": str(i), "name": n, "mode": m} for i, n, m in systems],
    }
