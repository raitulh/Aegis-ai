"""Continuous production monitoring: sampled evaluation, alerts and overview aggregation."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import NotFound
from aegis_api.models import AISystem, Alert, Monitor, MonitoringEvent, SystemEvent
from aegis_api.models.enums import AlertStatus
from aegis_api.services import risk_service
from engines.common.text import stable_unit, truncate
from engines.privacy.detectors import DEFAULT_DETECTOR, redact
from engines.safety.evaluator import classify_response


def ingest_event(session: Session, organization_id: uuid.UUID, data: Any) -> MonitoringEvent:
    system = session.get(AISystem, uuid.UUID(data.system_id))
    if system is None or system.organization_id != organization_id:
        raise NotFound("System not found")
    monitor = session.scalar(select(Monitor).where(Monitor.system_id == system.id, Monitor.enabled.is_(True)))
    rate = monitor.sampling_rate if monitor else 1.0
    external_id = data.request_id or uuid.uuid4().hex
    sampled = stable_unit(external_id) < rate
    event = MonitoringEvent(
        organization_id=organization_id,
        system_id=system.id,
        monitor_id=monitor.id if monitor else None,
        external_request_id=external_id,
        received_at=utcnow(),
        sampled=sampled,
        model_version=data.model_version,
        latency_ms=data.latency_ms,
        input_redacted=truncate(redact(data.input), 500),
        output_redacted=truncate(redact(data.output), 500),
    )
    if sampled:
        _evaluate(event, data.output)
    session.add(event)
    session.flush()
    if sampled:
        _check_alerts(session, system, monitor, event)
    return event


def _evaluate(event: MonitoringEvent, output: str) -> None:
    pii = [m for m in DEFAULT_DETECTOR.detect(output) if m.category in ("contact", "financial", "identifier", "secret")]
    label, _ = classify_response(output)
    event.evaluated = True
    event.pii_incident = bool(pii)
    event.safety_alert = label == "compliance"
    event.results = {"pii_types": sorted({m.type for m in pii}), "safety_label": label}


def _check_alerts(session: Session, system: AISystem, monitor: Monitor | None, event: MonitoringEvent) -> None:
    if event.pii_incident:
        _raise_alert(
            session,
            system,
            monitor,
            severity="critical",
            event_type="pii_leakage",
            title=f"PII detected in {system.name} output",
            metric="pii_incidents",
            observed=1,
            threshold=0,
            control_ref="PRIV-001",
        )
    if event.safety_alert:
        _raise_alert(
            session,
            system,
            monitor,
            severity="high",
            event_type="unsafe_output",
            title=f"Unsafe output from {system.name}",
            metric="safety_alerts",
            observed=1,
            threshold=0,
        )


def _raise_alert(
    session: Session,
    system: AISystem,
    monitor: Monitor | None,
    *,
    severity: str,
    event_type: str,
    title: str,
    metric: str,
    observed: float,
    threshold: float,
    control_ref: str | None = None,
) -> None:
    window_start = utcnow() - timedelta(hours=1)
    recent = session.scalar(
        select(Alert.id).where(
            Alert.system_id == system.id,
            Alert.event_type == event_type,
            Alert.status == AlertStatus.OPEN,
            Alert.triggered_at >= window_start,
        )
    )
    if recent:
        return
    session.add(
        Alert(
            organization_id=system.organization_id,
            system_id=system.id,
            monitor_id=monitor.id if monitor else None,
            severity=severity,
            title=title,
            event_type=event_type,
            control_ref=control_ref,
            metric=metric,
            observed=observed,
            threshold=threshold,
            evidence_count=1,
            action="Investigate the affected requests",
            triggered_at=utcnow(),
        )
    )
    from aegis_api.services import webhook_service

    webhook_service.enqueue_event(
        session,
        system.organization_id,
        "monitoring.alert",
        {"system_id": str(system.id), "event_type": event_type, "severity": severity},
    )


def create_monitor(session: Session, organization_id: uuid.UUID, data: Any) -> Monitor:
    system = session.get(AISystem, uuid.UUID(data.system_id))
    if system is None or system.organization_id != organization_id:
        raise NotFound("System not found")
    monitor = Monitor(
        organization_id=organization_id,
        system_id=system.id,
        name=data.name,
        sampling_rate=data.sampling_rate,
        evaluators=data.evaluators,
        alert_conditions=data.alert_conditions,
    )
    session.add(monitor)
    return monitor


def overview(
    session: Session, organization_id: uuid.UUID, *, days: int = 7, system_id: uuid.UUID | None = None
) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    base = select(MonitoringEvent).where(
        MonitoringEvent.organization_id == organization_id, MonitoringEvent.received_at >= since
    )
    if system_id:
        base = base.where(MonitoringEvent.system_id == system_id)
    events = session.scalars(base).all()
    alerts = session.scalars(
        select(Alert)
        .where(Alert.organization_id == organization_id, Alert.triggered_at >= since)
        .order_by(Alert.triggered_at.desc())
        .limit(20)
    ).all()
    sys_events = session.scalars(
        select(SystemEvent)
        .where(SystemEvent.organization_id == organization_id, SystemEvent.occurred_at >= since)
        .order_by(SystemEvent.occurred_at.desc())
        .limit(20)
    ).all()
    return {
        "window_days": days,
        "requests": len(events),
        "evaluated": sum(1 for e in events if e.evaluated),
        "policy_violations": sum(1 for e in events if e.policy_violation),
        "hallucination_alerts": sum(1 for e in events if e.hallucination_alert),
        "safety_alerts": sum(1 for e in events if e.safety_alert),
        "pii_incidents": sum(1 for e in events if e.pii_incident),
        "risk_trend": risk_service.risk_trend(session, organization_id, days=days, system_id=system_id),
        "model_events": [
            {"date": e.occurred_at.date().isoformat(), "type": e.type, "title": e.title} for e in sys_events
        ],
        "recent_alerts": alerts,
    }
