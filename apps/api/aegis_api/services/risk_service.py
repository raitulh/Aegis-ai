"""Risk snapshots and posture aggregation."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.models import Audit, Finding, RiskSnapshot, TestResult
from engines.risk.scoring import posture_from_scores


def write_snapshot(session: Session, audit: Audit, scores: dict[str, float], findings: list[Finding]) -> RiskSnapshot:
    open_by_sev: dict[str, int] = {}
    for f in findings:
        open_by_sev[f.severity] = open_by_sev.get(f.severity, 0) + 1
    test_volume = session.scalar(select(func.count(TestResult.id)).where(TestResult.audit_id == audit.id)) or 0
    snapshot = RiskSnapshot(
        organization_id=audit.organization_id,
        system_id=audit.system_id,
        audit_id=audit.id,
        captured_at=utcnow(),
        scores=scores,
        open_findings=open_by_sev,
        test_volume=int(test_volume),
        posture=posture_from_scores(scores),
    )
    session.add(snapshot)
    return snapshot


def risk_trend(
    session: Session, organization_id: uuid.UUID, *, days: int = 30, system_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    since = utcnow() - timedelta(days=days)
    q = select(RiskSnapshot).where(RiskSnapshot.organization_id == organization_id, RiskSnapshot.captured_at >= since)
    if system_id:
        q = q.where(RiskSnapshot.system_id == system_id)
    snapshots = session.scalars(q.order_by(RiskSnapshot.captured_at)).all()
    return [
        {
            "date": s.captured_at.date().isoformat(),
            "timestamp": s.captured_at.isoformat(),
            "scores": s.scores,
            "posture": s.posture,
            "open_findings": s.open_findings,
            "test_volume": s.test_volume,
        }
        for s in snapshots
    ]


def current_dimensions(session: Session, organization_id: uuid.UUID) -> tuple[dict[str, float], dict[str, float]]:
    snaps = session.scalars(
        select(RiskSnapshot)
        .where(RiskSnapshot.organization_id == organization_id)
        .order_by(RiskSnapshot.captured_at.desc())
        .limit(30)
    ).all()
    if not snaps:
        return {}, {}
    current = _avg([s.scores for s in snaps[: max(1, len(snaps) // 2)]])
    previous = _avg([s.scores for s in snaps[max(1, len(snaps) // 2) :]]) if len(snaps) > 1 else {}
    return current, previous


def _avg(list_of_scores: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, list[float]] = {}
    for scores in list_of_scores:
        for k, v in (scores or {}).items():
            totals.setdefault(k, []).append(float(v))
    return {k: round(sum(v) / len(v), 1) for k, v in totals.items() if v}
