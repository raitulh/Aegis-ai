"""Discovery registry.

candidate → verification_pending → verified → human_review → approved → published (contested/rejected at any
review point). Every status change appends an immutable ``discovery_versions`` snapshot.

Rules (enforced by the baseline policy and here):
* only human users approve or publish; agents/workflows never can;
* the claim must be VERIFIED before approval;
* separation of duties — whoever requested the review cannot approve it;
* publication always needs a separate human approval.
Nothing here marks anything as "proven" or "compliant"; discoveries carry confidence and limitations.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Forbidden, InvalidState, PolicyDenied, ValidationFailed
from aegis_api.models.lab import (
    Approval,
    ClaimEvidence,
    Discovery,
    DiscoveryVersion,
    Experiment,
    ExperimentRun,
    ScientificClaim,
    Verification,
)
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import approvals, events, graph
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab.access import accessible_project_ids, get_scoped
from aegis_api.services.lab.common import Actor
from engines.lab.enums import ApprovalStatus, ClaimStatus, DiscoveryStatus, LabEventType, RunKind
from engines.lab.state_machines import DISCOVERY


def _snapshot(db: Session, d: Discovery, *, reason: str | None, actor: Actor) -> None:
    number = 1 + int(
        db.scalar(
            select(func.coalesce(func.max(DiscoveryVersion.version), 0)).where(DiscoveryVersion.discovery_id == d.id)
        )
        or 0
    )
    db.add(
        DiscoveryVersion(
            organization_id=d.organization_id,
            discovery_id=d.id,
            version=number,
            status=d.status,
            snapshot={
                "title": d.title,
                "summary": d.summary,
                "confidence": d.confidence,
                "limitations": d.limitations,
                "evidence_ids": d.evidence_ids,
                "experiment_ids": d.experiment_ids,
                "reproduction_ids": d.reproduction_ids,
                "verifier_ids": d.verifier_ids,
            },
            reason=reason,
            actor=actor.label[:160],
        )
    )


def _move(db: Session, d: Discovery, target: str, *, reason: str | None, actor: Actor) -> None:
    d.status = DISCOVERY.ensure(d.status, target)
    _snapshot(db, d, reason=reason, actor=actor)


def create_from_claim(
    db: Session,
    *,
    organization_id: uuid.UUID,
    claim_id: uuid.UUID,
    actor: Actor,
    workflow_run_id: uuid.UUID | None = None,
    request_review: bool = True,
    on_behalf_of: str | None = None,
) -> tuple[Discovery, Approval | None]:
    """``on_behalf_of`` is the human who launched the automation; separation of duties excludes them (and
    the acting principal) from approving the resulting discovery."""
    claim = db.get(ScientificClaim, claim_id)
    if claim is None or claim.organization_id != organization_id:
        raise ValidationFailed("claim not found")
    existing = db.scalar(select(Discovery).where(Discovery.claim_id == claim.id))
    if existing is not None:
        pending = db.scalar(
            select(Approval).where(
                Approval.resource_type == "discovery",
                Approval.resource_id == str(existing.id),
                Approval.status == ApprovalStatus.PENDING,
            )
        )
        return existing, pending
    experiment = db.get(Experiment, claim.experiment_id) if claim.experiment_id else None
    evidence_ids = [
        str(e) for e in db.scalars(select(ClaimEvidence.evidence_id).where(ClaimEvidence.claim_id == claim.id)).all()
    ]
    repro_ids = (
        [
            str(r)
            for r in db.scalars(
                select(ExperimentRun.id).where(
                    ExperimentRun.experiment_id == experiment.id, ExperimentRun.run_kind == RunKind.REPRODUCTION
                )
            ).all()
        ]
        if experiment
        else []
    )
    verifications = [str(v) for v in db.scalars(select(Verification.id).where(Verification.claim_id == claim.id)).all()]
    limitations = [
        "Result holds for the tested configuration, dataset version and environment only.",
        f"Sample size: {(claim.uncertainty or {}).get('n')} candidate runs.",
    ]
    if claim.status != ClaimStatus.VERIFIED:
        limitations.append(f"Claim status is {claim.status}; not independently verified.")
    discovery = Discovery(
        organization_id=organization_id,
        project_id=claim.project_id,
        mission_id=claim.mission_id,
        claim_id=claim.id,
        title=f"Candidate discovery: {claim.statement[:250]}",
        summary=claim.statement,
        evidence_ids=evidence_ids,
        experiment_ids=[str(experiment.id)] if experiment else [],
        reproduction_ids=repro_ids,
        verifier_ids=verifications,
        strategy_version_id=experiment.strategy_version_id if experiment else None,
        confidence=claim.confidence,
        limitations=limitations,
        status=DiscoveryStatus.CANDIDATE,
        requested_by=(on_behalf_of or actor.id)[:64],
    )
    db.add(discovery)
    db.flush()
    _snapshot(db, discovery, reason="created from claim", actor=actor)
    _move(db, discovery, DiscoveryStatus.VERIFICATION_PENDING, reason="awaiting verification status", actor=actor)
    if claim.status == ClaimStatus.VERIFIED:
        _move(db, discovery, DiscoveryStatus.VERIFIED, reason="claim verified", actor=actor)
    elif claim.status in (ClaimStatus.REJECTED,):
        _move(db, discovery, DiscoveryStatus.REJECTED, reason="claim rejected", actor=actor)
    elif claim.status == ClaimStatus.CONTESTED:
        _move(db, discovery, DiscoveryStatus.CONTESTED, reason="claim contested", actor=actor)
    graph.link_refs(
        db,
        organization_id=organization_id,
        project_id=claim.project_id,
        source=("discovery", str(discovery.id), discovery.title),
        target=("claim", str(claim.id), claim.statement[:200]),
        relation="derived_from",
    )
    events.emit(
        db,
        organization_id=organization_id,
        mission_id=claim.mission_id,
        project_id=claim.project_id,
        event_type=LabEventType.DISCOVERY_CREATED,
        message=f"Candidate discovery registered ({discovery.status}): {claim.statement[:200]}",
        data={"discovery_id": str(discovery.id), "claim_id": str(claim.id), "status": discovery.status},
        actor=actor,
    )
    approval = None
    if request_review and discovery.status == DiscoveryStatus.VERIFIED:
        _move(db, discovery, DiscoveryStatus.HUMAN_REVIEW, reason="human review requested", actor=actor)
        approval = approvals.request(
            db,
            organization_id=organization_id,
            kind="discovery_promotion",
            resource_type="discovery",
            resource_id=str(discovery.id),
            title=f"Review discovery: {claim.statement[:200]}",
            requester=actor,
            details={"claim_id": str(claim.id), "confidence": claim.confidence, "evidence": evidence_ids[:20]},
            project_id=claim.project_id,
            mission_id=claim.mission_id,
            workflow_run_id=workflow_run_id,
        )
    return discovery, approval


def _check_review_policy(db: Session, principal: Principal, d: Discovery, action: str) -> None:
    claim = db.get(ScientificClaim, d.claim_id)
    actor = Actor.of(principal)
    result = lab_policy.evaluate(
        db,
        organization_id=d.organization_id,
        action=action,
        facts=lab_policy.base_facts(
            db,
            d.organization_id,
            actor,
            extra={
                "claim": {"status": claim.status if claim else None},
                "discovery": {"requested_by": d.requested_by, "status": d.status},
            },
        ),
        actor=actor,
        resource_type="discovery",
        resource_id=str(d.id),
    )
    if result.denied:
        raise PolicyDenied("; ".join(result.reasons), details=result.to_dict())


def apply_review(
    db: Session, principal: Principal, discovery_id: uuid.UUID | str, *, approve: bool, reason: str | None
) -> Discovery:
    """Human review decision (called from the approvals flow or the discovery endpoint)."""
    principal.require_human("discovery review")
    principal.require("discovery:approve")
    d = get_scoped(db, principal, Discovery, discovery_id, label="Discovery")
    if d.status != DiscoveryStatus.HUMAN_REVIEW:
        raise InvalidState(f"Discovery is {d.status}, not awaiting human review")
    if d.requested_by and d.requested_by == principal.actor_id:
        raise Forbidden("Separation of duties: the requester cannot approve their own discovery")
    if approve:
        _check_review_policy(db, principal, d, "discovery.approve")
    actor = Actor.of(principal)
    _move(db, d, DiscoveryStatus.APPROVED if approve else DiscoveryStatus.REJECTED, reason=reason, actor=actor)
    d.reviewed_by_id = principal.user_id
    d.reviewed_at = utcnow()
    audit_log.record(
        db,
        organization_id=d.organization_id,
        action="lab.discovery.approved" if approve else "lab.discovery.rejected",
        resource_type="discovery",
        resource_id=d.id,
        principal=principal,
        after={"reason": reason},
    )
    return d


def review(
    db: Session, principal: Principal, discovery_id: uuid.UUID | str, *, approve: bool, reason: str | None
) -> Discovery:
    """Endpoint path: decide the pending review approval (which applies the review and signals workflows)."""
    d = get_scoped(db, principal, Discovery, discovery_id, label="Discovery")
    pending = db.scalar(
        select(Approval).where(
            Approval.resource_type == "discovery",
            Approval.resource_id == str(d.id),
            Approval.kind == "discovery_promotion",
            Approval.status == ApprovalStatus.PENDING,
        )
    )
    if pending is not None:
        approvals.decide(db, principal, pending.id, decision="approve" if approve else "reject", reason=reason)
        return d
    return apply_review(db, principal, d.id, approve=approve, reason=reason)


def contest(db: Session, principal: Principal, discovery_id: uuid.UUID | str, reason: str) -> Discovery:
    principal.require("verification:run")
    d = get_scoped(db, principal, Discovery, discovery_id, label="Discovery")
    _move(db, d, DiscoveryStatus.CONTESTED, reason=reason, actor=Actor.of(principal))
    audit_log.record(
        db,
        organization_id=d.organization_id,
        action="lab.discovery.contested",
        resource_type="discovery",
        resource_id=d.id,
        principal=principal,
        after={"reason": reason},
    )
    return d


def request_publication(db: Session, principal: Principal, discovery_id: uuid.UUID | str) -> Approval:
    principal.require_human("publication request")
    principal.require("discovery:publish")
    d = get_scoped(db, principal, Discovery, discovery_id, label="Discovery")
    if d.status != DiscoveryStatus.APPROVED:
        raise InvalidState("Only approved discoveries can be published")
    _check_review_policy(db, principal, d, "discovery.publish")
    return approvals.request(
        db,
        organization_id=d.organization_id,
        kind="publication",
        resource_type="discovery",
        resource_id=str(d.id),
        title=f"Publish discovery: {d.title[:200]}",
        requester=Actor.of(principal),
        project_id=d.project_id,
        mission_id=d.mission_id,
    )


def apply_publication(
    db: Session, principal: Principal, discovery_id: uuid.UUID | str, *, approve: bool, reason: str | None
) -> Discovery:
    principal.require_human("publication decision")
    principal.require("discovery:publish")
    d = get_scoped(db, principal, Discovery, discovery_id, label="Discovery")
    if approve:
        _move(db, d, DiscoveryStatus.PUBLISHED, reason=reason, actor=Actor.of(principal))
        audit_log.record(
            db,
            organization_id=d.organization_id,
            action="lab.discovery.published",
            resource_type="discovery",
            resource_id=d.id,
            principal=principal,
        )
    return d


def on_approval_decided(db: Session, principal: Principal, approval: Approval) -> None:
    """Hook invoked by ``approvals.decide`` in the deciding human's transaction."""
    if approval.resource_type != "discovery":
        return
    approved = approval.status == ApprovalStatus.APPROVED
    if approval.kind == "discovery_promotion":
        apply_review(db, principal, approval.resource_id, approve=approved, reason=approval.decision_reason)
    elif approval.kind == "publication":
        apply_publication(db, principal, approval.resource_id, approve=approved, reason=approval.decision_reason)


def list_discoveries(
    db: Session, principal: Principal, *, status: str | None = None, mission_id: uuid.UUID | None = None
) -> Select[Discovery]:
    stmt = select(Discovery).where(Discovery.organization_id == principal.organization_id)
    visible = accessible_project_ids(db, principal)
    if visible is not None:
        stmt = stmt.where(Discovery.project_id.in_(visible))
    if status:
        stmt = stmt.where(Discovery.status == status)
    if mission_id:
        stmt = stmt.where(Discovery.mission_id == mission_id)
    return stmt.order_by(Discovery.created_at.desc())


def history(db: Session, d: Discovery) -> list[DiscoveryVersion]:
    return list(
        db.scalars(
            select(DiscoveryVersion).where(DiscoveryVersion.discovery_id == d.id).order_by(DiscoveryVersion.version)
        ).all()
    )


def discovery_dict(d: Discovery) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "title": d.title,
        "summary": d.summary,
        "status": d.status,
        "claim_id": str(d.claim_id),
        "project_id": str(d.project_id),
        "mission_id": str(d.mission_id) if d.mission_id else None,
        "confidence": d.confidence,
        "limitations": d.limitations,
        "evidence_ids": d.evidence_ids,
        "experiment_ids": d.experiment_ids,
        "reproduction_ids": d.reproduction_ids,
        "verifier_ids": d.verifier_ids,
        "is_demo": d.is_demo,
        "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
