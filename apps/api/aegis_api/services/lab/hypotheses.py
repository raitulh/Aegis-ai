"""Hypothesis engine (application side).

Generated hypotheses must be falsifiable with a measurable prediction. Citations the model gives are checked
against the project's recorded research sources: unknown source ids are dropped and flagged (a model cannot
cite a paper the platform never retrieved). Selection is deterministic — ranked from the critic's structured
scores plus the proposer's feasibility — and the reason is stored; scores remain *model assessments*.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from aegis_api.db.session import session_scope
from aegis_api.errors import ValidationFailed
from aegis_api.models.lab import Hypothesis, HypothesisEvidence, ResearchSource
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import events, graph
from aegis_api.services.lab.access import accessible_project_ids, get_project, get_scoped
from aegis_api.services.lab.common import Actor
from engines.lab.agents.schemas import HypothesisCritiques, HypothesisSet
from engines.lab.enums import HypothesisStatus, LabEventType
from engines.lab.state_machines import HYPOTHESIS

SELECTION_WEIGHTS = {"critique": 0.6, "feasibility": 0.25, "confidence": 0.15}


def _known_sources(db: Session, project_id: uuid.UUID, ids: list[str]) -> set[str]:
    uuids = []
    for i in ids:
        try:
            uuids.append(uuid.UUID(str(i)))
        except ValueError:
            continue
    if not uuids:
        return set()
    return {
        str(r)
        for r in db.scalars(
            select(ResearchSource.id).where(ResearchSource.project_id == project_id, ResearchSource.id.in_(uuids))
        ).all()
    }


def persist_generated(
    organization_id: uuid.UUID,
    *,
    project_id: uuid.UUID,
    mission_id: uuid.UUID | None,
    agent_run_id: uuid.UUID,
    output: dict[str, Any],
    actor: Actor,
) -> list[str]:
    proposals = HypothesisSet.model_validate(output).hypotheses
    ids: list[str] = []
    with session_scope(organization_id) as db:
        for p in proposals:
            cited = [*p.supporting_source_ids, *p.contradicting_source_ids]
            known = _known_sources(db, project_id, cited)
            unknown = sorted(set(map(str, cited)) - known)
            h = Hypothesis(
                organization_id=organization_id,
                project_id=project_id,
                mission_id=mission_id,
                statement=p.statement,
                rationale=p.rationale,
                expected_outcome=p.expected_outcome,
                measurable_prediction=p.measurable_prediction.model_dump(),
                assumptions=p.assumptions,
                novelty_notes=p.novelty_notes,
                feasibility=p.feasibility,
                estimated_cost_usd=p.estimated_cost_usd,
                confidence=p.confidence,
                parameters=p.parameters,
                status=HypothesisStatus.GENERATED,
                generated_by_run_id=agent_run_id,
                critique={"unverified_citations": unknown} if unknown else {},
            )
            db.add(h)
            db.flush()
            for sid in p.supporting_source_ids:
                if str(sid) in known:
                    db.add(_evidence(h, "research_source", str(sid), "supports"))
            for sid in p.contradicting_source_ids:
                if str(sid) in known:
                    db.add(_evidence(h, "research_source", str(sid), "contradicts"))
            ids.append(str(h.id))
            if mission_id:
                events.emit(
                    db,
                    organization_id=organization_id,
                    mission_id=mission_id,
                    project_id=project_id,
                    event_type=LabEventType.HYPOTHESIS_CREATED,
                    message=f"Hypothesis proposed: {p.statement[:200]}",
                    data={"hypothesis_id": str(h.id), "unverified_citations": len(unknown)},
                    actor=actor,
                )
                graph.link_refs(
                    db,
                    organization_id=organization_id,
                    project_id=project_id,
                    source=("hypothesis", str(h.id), p.statement[:200]),
                    target=("mission", str(mission_id), "mission"),
                    relation="part_of",
                )
    return ids


def _evidence(
    h: Hypothesis, source_type: str, source_id: str, relation: str, note: str | None = None
) -> HypothesisEvidence:
    return HypothesisEvidence(
        organization_id=h.organization_id,
        hypothesis_id=h.id,
        source_type=source_type,
        source_id=source_id,
        relation=relation,
        note=note,
    )


def apply_critiques(organization_id: uuid.UUID, hypothesis_ids: list[str], output: dict[str, Any]) -> dict[str, Any]:
    critiques = HypothesisCritiques.model_validate(output).critiques
    applied = 0
    with session_scope(organization_id) as db:
        for c in critiques:
            if c.hypothesis_index >= len(hypothesis_ids):
                continue
            h = db.get(Hypothesis, uuid.UUID(hypothesis_ids[c.hypothesis_index]))
            if h is None or h.status != HypothesisStatus.GENERATED:
                continue
            h.critique = {**(h.critique or {}), **c.model_dump()}
            h.critique_score = c.score
            if not c.falsifiable or c.recommendation == "reject":
                h.status = HYPOTHESIS.ensure(h.status, HypothesisStatus.REJECTED)
                h.selection_reason = "rejected by critique: " + (
                    "not falsifiable" if not c.falsifiable else "; ".join(c.issues[:3])
                )
            else:
                h.status = HYPOTHESIS.ensure(h.status, HypothesisStatus.CRITIQUED)
            applied += 1
    return {"critiqued": applied}


def rank(hypotheses: list[Hypothesis]) -> list[tuple[Hypothesis, float]]:
    scored = []
    for h in hypotheses:
        score = (
            SELECTION_WEIGHTS["critique"] * float(h.critique_score or 0.0)
            + SELECTION_WEIGHTS["feasibility"] * float(h.feasibility or 0.0)
            + SELECTION_WEIGHTS["confidence"] * float(h.confidence or 0.0)
        )
        scored.append((h, round(score, 6)))
    return sorted(scored, key=lambda x: (-x[1], str(x[0].id)))


def select_top(
    organization_id: uuid.UUID, *, hypothesis_ids: list[str], count: int, actor: Actor, mission_id: uuid.UUID | None
) -> list[str]:
    with session_scope(organization_id) as db:
        rows = [
            h
            for h in db.scalars(
                select(Hypothesis).where(Hypothesis.id.in_([uuid.UUID(i) for i in hypothesis_ids]))
            ).all()
            if h.status == HypothesisStatus.CRITIQUED
        ]
        ranked = rank(rows)
        chosen = ranked[: max(1, count)]
        for h, score in chosen:
            h.status = HYPOTHESIS.ensure(h.status, HypothesisStatus.SELECTED)
            h.selection_reason = (
                f"ranked {ranked.index((h, score)) + 1}/{len(ranked)} by weighted score {score} "
                f"(critique×{SELECTION_WEIGHTS['critique']} + feasibility×{SELECTION_WEIGHTS['feasibility']} + "
                f"confidence×{SELECTION_WEIGHTS['confidence']}; model self-assessments)"
            )
            if mission_id:
                events.emit(
                    db,
                    organization_id=organization_id,
                    mission_id=mission_id,
                    project_id=h.project_id,
                    event_type=LabEventType.HYPOTHESIS_SELECTED,
                    message=f"Hypothesis selected: {h.statement[:200]}",
                    data={"hypothesis_id": str(h.id), "score": score},
                    actor=actor,
                )
        return [str(h.id) for h, _ in chosen]


def mark(organization_id: uuid.UUID, hypothesis_id: uuid.UUID, status: str, summary: str | None = None) -> None:
    with session_scope(organization_id) as db:
        h = db.get(Hypothesis, hypothesis_id)
        if h is None:
            return
        h.status = HYPOTHESIS.ensure(h.status, status)
        if summary:
            h.outcome_summary = summary[:4000]


# --- API operations ----------------------------------------------------------------------------------------


def create(db: Session, principal: Principal, data: dict[str, Any]) -> Hypothesis:
    principal.require("hypothesis:write")
    project = get_project(db, principal, data["project_id"])
    prediction = data.get("measurable_prediction") or {}
    if not prediction.get("metric") or prediction.get("direction") not in ("increase", "decrease", "no_change"):
        raise ValidationFailed("measurable_prediction needs 'metric' and 'direction' (increase|decrease|no_change)")
    h = Hypothesis(
        organization_id=principal.organization_id,
        project_id=project.id,
        mission_id=data.get("mission_id"),
        statement=data["statement"],
        rationale=data.get("rationale"),
        expected_outcome=data.get("expected_outcome"),
        measurable_prediction=prediction,
        assumptions=list(data.get("assumptions") or []),
        novelty_notes=data.get("novelty_notes"),
        feasibility=data.get("feasibility"),
        confidence=data.get("confidence"),
        parameters=data.get("parameters") or {},
        status=HypothesisStatus.GENERATED,
        parent_hypothesis_id=data.get("parent_hypothesis_id"),
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(h)
    db.flush()
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="lab.hypothesis.created",
        resource_type="hypothesis",
        resource_id=h.id,
        principal=principal,
    )
    return h


def transition(
    db: Session, principal: Principal, hypothesis_id: uuid.UUID | str, status: str, reason: str | None
) -> Hypothesis:
    principal.require("hypothesis:write")
    h = get_scoped(db, principal, Hypothesis, hypothesis_id, label="Hypothesis")
    before = h.status
    h.status = HYPOTHESIS.ensure(h.status, status)
    if reason:
        h.selection_reason = reason
    audit_log.record(
        db,
        organization_id=h.organization_id,
        action="lab.hypothesis.transition",
        resource_type="hypothesis",
        resource_id=h.id,
        principal=principal,
        before={"status": before},
        after={"status": h.status, "reason": reason},
    )
    return h


def add_evidence(
    db: Session,
    principal: Principal,
    hypothesis_id: uuid.UUID | str,
    *,
    source_type: str,
    source_id: str,
    relation: str,
    note: str | None,
) -> HypothesisEvidence:
    principal.require("hypothesis:write")
    if relation not in ("supports", "contradicts"):
        raise ValidationFailed("relation must be supports or contradicts")
    h = get_scoped(db, principal, Hypothesis, hypothesis_id, label="Hypothesis")
    row = _evidence(h, source_type[:32], source_id[:64], relation, note)
    db.add(row)
    return row


def list_hypotheses(
    db: Session,
    principal: Principal,
    *,
    project_id: uuid.UUID | None = None,
    mission_id: uuid.UUID | None = None,
    status: str | None = None,
) -> Select[Hypothesis]:
    stmt = select(Hypothesis).where(Hypothesis.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Hypothesis.project_id.in_(visible))
    if project_id:
        stmt = stmt.where(Hypothesis.project_id == project_id)
    if mission_id:
        stmt = stmt.where(Hypothesis.mission_id == mission_id)
    if status:
        stmt = stmt.where(Hypothesis.status == status)
    return stmt.order_by(Hypothesis.created_at.desc())


def evidence_for(db: Session, h: Hypothesis) -> list[HypothesisEvidence]:
    return list(db.scalars(select(HypothesisEvidence).where(HypothesisEvidence.hypothesis_id == h.id)).all())
