"""Discovery registry with human approval gates and immutable versions.

Lifecycle (``engines.lab.states`` "discovery" machine)::

    CANDIDATE → VERIFICATION_PENDING → VERIFIED → HUMAN_REVIEW → APPROVED → PUBLISHED
                                   ↘ CONTESTED / REJECTED (with a reason)

Rules (spec §32/§97 — configurable approval policy, never relaxable below the platform baseline):

* ``VERIFIED`` requires the claim itself to be ``VERIFIED`` by the verification engine.
* ``HUMAN_REVIEW`` requests an approval (action ``discovery.approve``, required permission ``discovery:approve``)
  after checking the organization's governance policy for human approvers and the project's discovery policy
  (``project.settings["discovery_policy"]``: ``min_confidence``, ``review_expiry_hours``).
* ``APPROVED`` only through :func:`apply_decision` after a signed-in HUMAN holding ``discovery:approve`` decided
  the approval — never the claim or discovery creator, never the requester (separation of duties), never an API
  key, service account, agent or workflow — and only when ``evaluate_policy("discovery.approve")`` does not deny
  that human.
* ``PUBLISHED`` requires ``APPROVED`` plus an approved ``discovery.publish`` approval decided by a human holding
  ``discovery:publish``.
* ``CONTESTED`` / ``REJECTED`` need a reason: a reviewer (``discovery:approve``) may always do it; other actors only
  when the claim's own status is contested/rejected (evidence-driven).
* Seeded demo discoveries (``is_demo``) can never be reviewed, approved or published.
* Every status change writes an immutable ``DiscoveryVersion`` (snapshot + content hash), emits
  ``DISCOVERY_STATUS_CHANGED`` and is audited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.access import effective_permissions, get_owned, load_project, visible_project_ids
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.errors import ApprovalRequired, PolicyDenied
from aegis_api.lab.core.events import EventType, emit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_keyset
from aegis_api.lab.governance import approvals as approvals_service
from aegis_api.lab.governance.policies import evaluate_policy
from aegis_api.lab.models import (
    Approval,
    ClaimEvidence,
    Discovery,
    DiscoveryVersion,
    Experiment,
    Mission,
    Project,
    Reproduction,
    ScientificClaim,
    Verification,
)
from aegis_api.lab.verification.claims import load_claim
from aegis_api.lab.verification.common import canonical_hash, clean_text, jsonable
from aegis_api.lab.verification.schemas import DiscoveryOut, DiscoveryVersionOut
from aegis_api.lab.verification.verification import claim_context
from aegis_api.models import Membership
from aegis_api.security.rbac import permissions_for_role
from engines.lab.states import ApprovalStatus, ClaimStatus, DiscoveryStatus, RunState, assert_transition

log = structlog.get_logger("aegis.lab.discoveries")

D = DiscoveryStatus
APPROVE_ACTION = "discovery.approve"
PUBLISH_ACTION = "discovery.publish"
SUBJECT_TYPE = "discovery"
APPROVE_PERMISSION = "discovery:approve"
PUBLISH_PERMISSION = "discovery:publish"
DECISION_SIGNAL = "discovery_decision"
CREATABLE_CLAIM_STATUSES = frozenset({ClaimStatus.CANDIDATE, ClaimStatus.PARTIALLY_VERIFIED, ClaimStatus.VERIFIED})
GATED_STATUSES = frozenset({D.HUMAN_REVIEW, D.APPROVED, D.PUBLISHED})
#: Captured provenance is refreshed on every status change up to human review; afterwards the snapshot is
#: what the human reviewed and approved.
REFRESH_BEFORE = frozenset({D.CANDIDATE, D.VERIFICATION_PENDING, D.VERIFIED, D.HUMAN_REVIEW, D.CONTESTED})
DEFAULT_REVIEW_EXPIRY_HOURS = 168


# ---------------------------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DiscoveryPolicy:
    """Project/mission discovery policy. Can only make approval STRICTER than the platform rules."""

    min_confidence: float = 0.0
    review_expiry_hours: int = DEFAULT_REVIEW_EXPIRY_HOURS

    @classmethod
    def resolve(cls, project: Project, mission: Mission | None) -> DiscoveryPolicy:
        sources = [(project.settings or {}).get("discovery_policy") or {}]
        if mission is not None:
            sources.append((mission.approval_policy or {}).get("discovery") or {})
        min_confidence = 0.0
        expiry = DEFAULT_REVIEW_EXPIRY_HOURS
        for source in sources:
            if not isinstance(source, dict):
                continue
            value = source.get("min_confidence")
            if isinstance(value, int | float) and not isinstance(value, bool) and 0.0 <= value <= 1.0:
                min_confidence = max(min_confidence, float(value))
            hours = source.get("review_expiry_hours")
            if isinstance(hours, int) and not isinstance(hours, bool) and 1 <= hours <= 24 * 30:
                expiry = min(expiry, hours)
        return cls(min_confidence=min_confidence, review_expiry_hours=expiry)


# ---------------------------------------------------------------------------------------------
# Mappers / loading
# ---------------------------------------------------------------------------------------------
def discovery_out(discovery: Discovery) -> DiscoveryOut:
    return DiscoveryOut.model_validate(discovery)


def discovery_version_out(version: DiscoveryVersion) -> DiscoveryVersionOut:
    return DiscoveryVersionOut.model_validate(version)


def load_discovery(db: Session, actor: Actor, discovery_id: uuid.UUID | str, *permissions: str) -> Discovery:
    discovery = get_owned(db, Discovery, discovery_id, actor, label="Discovery")
    try:
        load_project(db, actor, discovery.project_id, *permissions)
    except NotFound as exc:
        raise NotFound("Discovery not found") from exc
    return discovery


def discovery_versions(db: Session, discovery_id: uuid.UUID) -> list[DiscoveryVersion]:
    return list(
        db.scalars(
            select(DiscoveryVersion)
            .where(DiscoveryVersion.discovery_id == discovery_id)
            .order_by(DiscoveryVersion.version)
        ).all()
    )


def list_discoveries(
    db: Session,
    actor: Actor,
    params: CursorParams,
    *,
    status: str | None = None,
    mission_id: uuid.UUID | str | None = None,
    project_id: uuid.UUID | str | None = None,
    claim_id: uuid.UUID | str | None = None,
) -> CursorPage[DiscoveryOut]:
    stmt = select(Discovery).where(Discovery.organization_id == actor.organization_id)
    if status:
        value = status.upper()
        if value not in D.__members__:
            raise ValidationFailed(f"status must be one of {', '.join(D.__members__)}")
        stmt = stmt.where(Discovery.status == value)
    if project_id:
        stmt = stmt.where(Discovery.project_id == load_project(db, actor, project_id, "discovery:read").id)
    if mission_id:
        stmt = stmt.where(Discovery.mission_id == get_owned(db, Mission, mission_id, actor, label="Mission").id)
    if claim_id:
        stmt = stmt.where(Discovery.claim_id == load_claim(db, actor, claim_id, "discovery:read").id)
    visible = visible_project_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(Discovery.project_id.in_(visible))
    return paginate_keyset(db, stmt, params, time_col=Discovery.created_at, id_col=Discovery.id, mapper=discovery_out)


# ---------------------------------------------------------------------------------------------
# Snapshots / versions
# ---------------------------------------------------------------------------------------------
def _capture(db: Session, discovery: Discovery, claim: ScientificClaim) -> None:
    """Evidence, experiments, reproductions and verifiers behind the discovery (refreshed pre-approval)."""
    evidence_ids = [
        str(eid)
        for eid in db.scalars(
            select(ClaimEvidence.evidence_id)
            .where(ClaimEvidence.claim_id == claim.id, ClaimEvidence.evidence_id.is_not(None))
            .order_by(ClaimEvidence.created_at)
        ).all()
    ]
    experiment_refs = db.scalars(
        select(ClaimEvidence.ref_id).where(ClaimEvidence.claim_id == claim.id, ClaimEvidence.evidence_type == "experiment")
    ).all()
    ctx = claim_context(db, claim)
    experiment_ids: list[str] = []
    for exp_id in [
        ctx.candidate.id if ctx.candidate else None,
        ctx.baseline.id if ctx.baseline else None,
        *experiment_refs,
    ]:
        if exp_id is not None and str(exp_id) not in experiment_ids:
            experiment_ids.append(str(exp_id))
    reproduction_ids: list[str] = []
    if ctx.candidate is not None:
        reproduction_ids = [
            str(rid)
            for rid in db.scalars(
                select(Reproduction.id)
                .where(Reproduction.experiment_id == ctx.candidate.id, Reproduction.status == RunState.COMPLETED)
                .order_by(Reproduction.created_at)
            ).all()
        ]
    verifier_ids = [
        str(vid)
        for vid in db.scalars(
            select(Verification.id)
            .where(Verification.claim_id == claim.id, Verification.status == RunState.COMPLETED)
            .order_by(Verification.created_at)
        ).all()
    ]
    discovery.evidence_ids = evidence_ids
    discovery.experiment_ids = experiment_ids
    discovery.reproduction_ids = reproduction_ids
    discovery.verifier_ids = verifier_ids
    discovery.strategy_version_id = ctx.candidate.strategy_version_id if ctx.candidate else discovery.strategy_version_id
    discovery.confidence = float(claim.confidence or 0.0)


def _snapshot(discovery: Discovery, claim: ScientificClaim) -> dict[str, Any]:
    return jsonable(
        {
            "title": discovery.title,
            "summary": discovery.summary,
            "status": discovery.status,
            "confidence": discovery.confidence,
            "evidence_ids": discovery.evidence_ids,
            "experiment_ids": discovery.experiment_ids,
            "reproduction_ids": discovery.reproduction_ids,
            "verifier_ids": discovery.verifier_ids,
            "strategy_version_id": discovery.strategy_version_id,
            "approval_id": discovery.approval_id,
            "reviewed_by_id": discovery.reviewed_by_id,
            "reviewed_at": discovery.reviewed_at,
            "published_at": discovery.published_at,
            "is_demo": discovery.is_demo,
            "claim": {
                "id": claim.id,
                "status": claim.status,
                "confidence": claim.confidence,
                "statement": claim.statement,
                "criteria_profile": claim.criteria_profile,
            },
        }
    )


def _write_version(db: Session, actor: Actor, discovery: Discovery, claim: ScientificClaim, reason: str | None) -> DiscoveryVersion:
    snapshot = _snapshot(discovery, claim)
    version = DiscoveryVersion(
        id=uuid.uuid4(),
        organization_id=discovery.organization_id,
        discovery_id=discovery.id,
        version=discovery.version,
        status=discovery.status,
        snapshot=snapshot,
        reason=reason,
        content_hash=canonical_hash(
            {
                "discovery_id": discovery.id,
                "version": discovery.version,
                "status": discovery.status,
                "snapshot": snapshot,
                "reason": reason,
            }
        ),
        created_by_id=actor.user_id,
    )
    db.add(version)
    db.flush()
    return version


_AUDIT_ACTIONS = {
    D.APPROVED: AuditAction.DISCOVERY_APPROVED,
    D.PUBLISHED: AuditAction.DISCOVERY_PUBLISHED,
    D.REJECTED: AuditAction.DISCOVERY_REJECTED,
}


def _change_status(
    db: Session,
    actor: Actor,
    discovery: Discovery,
    claim: ScientificClaim,
    target: str,
    reason: str,
    *,
    extra: dict[str, Any] | None = None,
) -> Discovery:
    assert_transition("discovery", discovery.status, target)
    previous = discovery.status
    if target in REFRESH_BEFORE:
        _capture(db, discovery, claim)
    discovery.status = target
    discovery.version = int(discovery.version or 1) + 1
    db.flush()
    version = _write_version(db, actor, discovery, claim, reason)
    payload = {
        "discovery_id": str(discovery.id),
        "claim_id": str(claim.id),
        "from": previous,
        "to": target,
        "version": discovery.version,
        "reason": clean_text(reason, 500),
        "content_hash": version.content_hash,
        **(extra or {}),
    }
    emit(
        db,
        organization_id=discovery.organization_id,
        type=EventType.DISCOVERY_STATUS_CHANGED,
        payload=payload,
        mission_id=discovery.mission_id,
        project_id=discovery.project_id,
        workspace_id=discovery.workspace_id,
        subject_type="discovery",
        subject_id=discovery.id,
        actor=actor,
    )
    audit(
        db,
        actor,
        _AUDIT_ACTIONS.get(target, "DISCOVERY_STATUS_CHANGED"),
        "discovery",
        discovery.id,
        before={"status": previous},
        after={"status": target, "version": discovery.version, "reason": clean_text(reason, 500), **(extra or {})},
    )
    return discovery


# ---------------------------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------------------------
def _is_demo(db: Session, claim: ScientificClaim, project: Project) -> bool:
    if project.is_demo:
        return True
    if claim.mission_id is not None:
        mission = db.get(Mission, claim.mission_id)
        if mission is not None and mission.is_demo:
            return True
    ctx = claim_context(db, claim)
    return any(e is not None and e.is_demo for e in (ctx.candidate, ctx.baseline))


def create_candidate(
    db: Session,
    actor: Actor,
    claim_id: uuid.UUID | str,
    *,
    title: str | None = None,
    summary: str | None = None,
    mission_id: uuid.UUID | str | None = None,
) -> Discovery:
    """Register a discovery candidate for a claim (idempotent per claim while not rejected)."""
    claim = load_claim(db, actor, claim_id, "discovery:create")
    project = load_project(db, actor, claim.project_id, "discovery:create")
    if claim.status not in CREATABLE_CLAIM_STATUSES:
        raise Conflict(
            f"A discovery needs a CANDIDATE, PARTIALLY_VERIFIED or VERIFIED claim (claim is {claim.status})",
            code="claim_not_eligible",
        )
    mission: Mission | None = None
    if mission_id not in (None, ""):
        mission = get_owned(db, Mission, mission_id, actor, label="Mission")
        if mission.project_id != claim.project_id:
            raise ValidationFailed("mission_id does not belong to the claim's project")
    elif claim.mission_id is not None:
        mission = db.get(Mission, claim.mission_id)
    advisory_xact_lock(db, f"discovery-claim:{claim.id}")
    existing = db.scalar(
        select(Discovery)
        .where(Discovery.claim_id == claim.id, Discovery.status != D.REJECTED)
        .order_by(Discovery.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    clean_title = clean_text(title or claim.statement, 300)
    if len(clean_title) < 3:
        raise ValidationFailed("title must be at least 3 characters")
    discovery = Discovery(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        mission_id=mission.id if mission is not None else None,
        claim_id=claim.id,
        title=clean_title,
        summary=(summary or "").strip() or None,
        evidence_ids=[],
        experiment_ids=[],
        reproduction_ids=[],
        verifier_ids=[],
        confidence=0.0,
        status=D.CANDIDATE,
        version=1,
        is_demo=_is_demo(db, claim, project),
        created_by_id=actor.user_id,
    )
    _capture(db, discovery, claim)
    db.add(discovery)
    db.flush()
    _write_version(db, actor, discovery, claim, "discovery candidate registered")
    emit(
        db,
        organization_id=discovery.organization_id,
        type=EventType.DISCOVERY_CREATED,
        payload={
            "discovery_id": str(discovery.id),
            "claim_id": str(claim.id),
            "title": discovery.title,
            "status": discovery.status,
            "confidence": discovery.confidence,
            "is_demo": discovery.is_demo,
        },
        mission_id=discovery.mission_id,
        project_id=discovery.project_id,
        workspace_id=discovery.workspace_id,
        subject_type="discovery",
        subject_id=discovery.id,
        actor=actor,
    )
    audit(
        db,
        actor,
        AuditAction.DISCOVERY_CREATED,
        "discovery",
        discovery.id,
        after={"claim_id": str(claim.id), "title": discovery.title, "claim_status": claim.status},
    )
    return discovery


# ---------------------------------------------------------------------------------------------
# Separation of duties / human gate helpers
# ---------------------------------------------------------------------------------------------
def _conflicted_user_ids(db: Session, discovery: Discovery, claim: ScientificClaim) -> set[uuid.UUID]:
    """Users who may never approve or publish this discovery (its creators)."""
    ids = {uid for uid in (discovery.created_by_id, claim.created_by_id) if uid is not None}
    ctx = claim_context(db, claim)
    if ctx.candidate is not None and ctx.candidate.created_by_id is not None:
        ids.add(ctx.candidate.created_by_id)
    return ids


def _require_demo_safe(discovery: Discovery, target: str) -> None:
    if discovery.is_demo and target in GATED_STATUSES:
        raise Forbidden(
            "Demo discoveries can never be submitted for review, approved or published",
            code="demo_discovery_not_approvable",
        )


def _require_human_reviewer(db: Session, actor: Actor, discovery: Discovery, claim: ScientificClaim, permission: str) -> None:
    actor.require_human("approving or publishing a discovery")
    project = load_project(db, actor, discovery.project_id)
    if permission not in effective_permissions(db, actor, project):
        raise Forbidden(f"Approving this discovery requires the '{permission}' permission")
    if actor.user_id in _conflicted_user_ids(db, discovery, claim):
        raise Forbidden(
            "Separation of duties: the creator of a discovery or of its claim cannot approve or publish it",
            code="separation_of_duties",
        )


def _decider_actor(db: Session, discovery: Discovery, approval: Approval) -> Actor:
    """The human who decided ``approval`` as a lab actor (used for policy evaluation only)."""
    if approval.decided_by_id is None:
        raise Forbidden("The approval was not decided by a signed-in human", code="approval_not_human")
    membership = db.scalar(
        select(Membership).where(
            Membership.organization_id == discovery.organization_id, Membership.user_id == approval.decided_by_id
        )
    )
    if membership is None or membership.status != "active":
        raise Forbidden("The reviewer who decided the approval is no longer an active member", code="approval_not_human")
    return Actor(
        kind="user",
        organization_id=discovery.organization_id,
        permissions=permissions_for_role(membership.role),
        label=f"user:{approval.decided_by_id}",
        user_id=approval.decided_by_id,
        role=membership.role,
        auth_method="session",
    )


def _validate_decider(
    db: Session, discovery: Discovery, claim: ScientificClaim, approval: Approval, permission: str, action: str
) -> Actor:
    """Separation of duties + permission + governance policy for the human who decided the approval."""
    decider = _decider_actor(db, discovery, approval)
    if decider.user_id in _conflicted_user_ids(db, discovery, claim) or (
        approval.requested_by_id is not None and approval.requested_by_id == decider.user_id
    ):
        raise Forbidden(
            "Separation of duties: the approval was decided by the discovery's creator or requester",
            code="separation_of_duties",
        )
    project = db.get(Project, discovery.project_id)
    if project is None or permission not in effective_permissions(db, decider, project):
        raise Forbidden(f"The reviewer does not hold the '{permission}' permission")
    decision = evaluate_policy(
        db,
        decider,
        action,
        {"risk_level": approval.risk_level, "x_confidence": float(claim.confidence or 0.0)},
        project_id=discovery.project_id,
    )
    if decision.denied:
        raise PolicyDenied(
            "Governance policy does not allow this reviewer to approve the discovery",
            details={"reasons": list(decision.reasons), "matched_rules": list(decision.matched_rules)},
        )
    if decision.approver_permission and decision.approver_permission not in effective_permissions(db, decider, project):
        raise Forbidden(f"Governance policy requires an approver holding '{decision.approver_permission}'")
    return decider


def _latest_approval(db: Session, discovery: Discovery, action: str, *statuses: str) -> Approval | None:
    stmt = select(Approval).where(
        Approval.organization_id == discovery.organization_id,
        Approval.action == action,
        Approval.subject_type == SUBJECT_TYPE,
        Approval.subject_id == str(discovery.id),
    )
    if statuses:
        stmt = stmt.where(Approval.status.in_(statuses))
    return db.scalar(stmt.order_by(Approval.created_at.desc()).limit(1))


# ---------------------------------------------------------------------------------------------
# Human review request
# ---------------------------------------------------------------------------------------------
def request_human_review(
    db: Session,
    actor: Actor,
    discovery_id: uuid.UUID | str,
    *,
    workflow_run_id: uuid.UUID | str | None = None,
    note: str | None = None,
) -> tuple[Discovery, Approval]:
    """VERIFIED → HUMAN_REVIEW with a ``discovery.approve`` approval request (idempotent)."""
    discovery = load_discovery(db, actor, discovery_id, "discovery:create")
    advisory_xact_lock(db, f"discovery:{discovery.id}")
    db.refresh(discovery)
    claim = db.get(ScientificClaim, discovery.claim_id)
    if claim is None:  # pragma: no cover - FK RESTRICT
        raise NotFound("Discovery not found")
    _require_demo_safe(discovery, D.HUMAN_REVIEW)
    if discovery.status == D.HUMAN_REVIEW:
        pending = _latest_approval(db, discovery, APPROVE_ACTION, ApprovalStatus.PENDING)
        if pending is not None and (pending.expires_at is None or pending.expires_at > utcnow()):
            return discovery, pending
    elif discovery.status != D.VERIFIED:
        assert_transition("discovery", discovery.status, D.HUMAN_REVIEW)
    if claim.status != ClaimStatus.VERIFIED:
        raise Conflict(
            f"Human review requires a VERIFIED claim (claim is {claim.status})", code="claim_not_verified"
        )
    project = db.get(Project, discovery.project_id)
    assert project is not None
    mission = db.get(Mission, discovery.mission_id) if discovery.mission_id else None
    policy = DiscoveryPolicy.resolve(project, mission)
    if float(claim.confidence or 0.0) < policy.min_confidence:
        raise Conflict(
            f"The claim's confidence {claim.confidence:.2f} is below the project's minimum {policy.min_confidence:.2f}",
            code="confidence_below_policy",
        )
    # What would governance require of a HUMAN approver? (dry run: the requester may be a workflow)
    decision = evaluate_policy(
        db,
        actor,
        APPROVE_ACTION,
        {"is_human": True, "actor_kind": "user", "risk_level": mission.risk_level if mission else "MEDIUM"},
        project_id=discovery.project_id,
        dry_run=True,
    )
    if decision.denied:
        raise PolicyDenied(
            "Governance policy does not allow discoveries in this project to be approved",
            details={"reasons": list(decision.reasons)},
        )
    required_permission = APPROVE_PERMISSION
    excluded = sorted(str(uid) for uid in _conflicted_user_ids(db, discovery, claim))
    reproductions = db.scalars(
        select(Reproduction.verdict).where(Reproduction.id.in_(sorted(uuid.UUID(r) for r in discovery.reproduction_ids)))
    ).all() if discovery.reproduction_ids else []
    approval = approvals_service.request_approval(
        db,
        actor,
        action=APPROVE_ACTION,
        subject_type=SUBJECT_TYPE,
        subject_id=discovery.id,
        title=f"Approve discovery: {discovery.title}",
        payload={
            "discovery_id": str(discovery.id),
            "claim_id": str(claim.id),
            "claim_statement": clean_text(claim.statement, 1000),
            "claim_status": claim.status,
            "confidence": claim.confidence,
            "evidence_count": len(discovery.evidence_ids or []),
            "reproduction_verdicts": list(reproductions),
            "verification_ids": list(discovery.verifier_ids or []),
            "excluded_decider_ids": excluded,
            "policy": {"min_confidence": policy.min_confidence, "approver_permission": decision.approver_permission},
            "note": clean_text(note, 2000) if note else None,
        },
        risk_level=mission.risk_level if mission is not None else "MEDIUM",
        decision=decision,
        project_id=discovery.project_id,
        mission_id=discovery.mission_id,
        workflow_run_id=workflow_run_id,
        signal_name=DECISION_SIGNAL,
        required_permission=required_permission,
        expires_in_hours=policy.review_expiry_hours,
    )
    discovery.approval_id = approval.id
    if discovery.status != D.HUMAN_REVIEW:
        _change_status(
            db,
            actor,
            discovery,
            claim,
            D.HUMAN_REVIEW,
            note or "submitted for human review",
            extra={"approval_id": str(approval.id)},
        )
    db.flush()
    return discovery, approval


# ---------------------------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------------------------
def apply_decision(
    db: Session,
    actor: Actor,
    discovery_id: uuid.UUID | str,
    *,
    approved: bool,
    reason: str | None = None,
    approval: Approval | None = None,
) -> Discovery:
    """Apply the HUMAN decision recorded on the ``discovery.approve`` approval (idempotent).

    The approval row is the source of truth: ``approved=True`` requires an APPROVED approval decided by a human
    reviewer who is neither the creator nor the requester, holds ``discovery:approve`` and is not denied by
    governance policy. Expired/cancelled approvals leave the discovery in HUMAN_REVIEW.
    """
    discovery = load_discovery(db, actor, discovery_id, "discovery:read")
    advisory_xact_lock(db, f"discovery:{discovery.id}")
    db.refresh(discovery)
    claim = db.get(ScientificClaim, discovery.claim_id)
    if claim is None:  # pragma: no cover - FK RESTRICT
        raise NotFound("Discovery not found")
    if approved and discovery.status in (D.APPROVED, D.PUBLISHED):
        return discovery
    if not approved and discovery.status == D.REJECTED:
        return discovery
    _require_demo_safe(discovery, D.APPROVED if approved else D.REJECTED)
    if discovery.status != D.HUMAN_REVIEW:
        raise Conflict(
            f"Only discoveries in HUMAN_REVIEW can be decided (discovery is {discovery.status})",
            code="invalid_state_transition",
        )
    decided = approval
    if decided is None or decided.action != APPROVE_ACTION or decided.subject_id != str(discovery.id):
        decided = _latest_approval(
            db, discovery, APPROVE_ACTION, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED
        ) or _latest_approval(db, discovery, APPROVE_ACTION)
    if decided is None:
        raise ApprovalRequired("The discovery has no review request; request a human review first")
    if approved:
        if decided.status != ApprovalStatus.APPROVED:
            raise ApprovalRequired(
                "The discovery has not been approved by a human reviewer", approval_id=str(decided.id)
            )
        decider = _validate_decider(db, discovery, claim, decided, APPROVE_PERMISSION, APPROVE_ACTION)
        if claim.status != ClaimStatus.VERIFIED:
            raise Conflict(
                f"The claim is no longer VERIFIED (it is {claim.status}); the discovery cannot be approved",
                code="claim_not_verified",
            )
        discovery.reviewed_at = decided.decided_at or utcnow()
        discovery.reviewed_by_id = decider.user_id
        discovery.approval_id = decided.id
        return _change_status(
            db,
            actor,
            discovery,
            claim,
            D.APPROVED,
            reason or decided.decision_reason or "approved by a human reviewer",
            extra={"approval_id": str(decided.id), "reviewed_by_id": str(decider.user_id)},
        )
    if decided.status != ApprovalStatus.REJECTED:
        # Expired, cancelled or still pending: no human rejected it, so the discovery stays under review.
        log.info("discovery_decision_not_rejected", discovery_id=str(discovery.id), approval_status=decided.status)
        return discovery
    if decided.decided_by_id is None:
        raise Forbidden("The rejection was not decided by a signed-in human", code="approval_not_human")
    discovery.reviewed_at = decided.decided_at or utcnow()
    discovery.reviewed_by_id = decided.decided_by_id
    return _change_status(
        db,
        actor,
        discovery,
        claim,
        D.REJECTED,
        reason or decided.decision_reason or "rejected by a human reviewer",
        extra={"approval_id": str(decided.id), "reviewed_by_id": str(decided.decided_by_id)},
    )


def _publish(db: Session, actor: Actor, discovery: Discovery, claim: ScientificClaim, approval: Approval, reason: str) -> Discovery:
    _require_demo_safe(discovery, D.PUBLISHED)
    if discovery.status == D.PUBLISHED:
        return discovery
    if discovery.status != D.APPROVED:
        raise Conflict("Only APPROVED discoveries can be published", code="invalid_state_transition")
    if approval.status != ApprovalStatus.APPROVED:
        raise ApprovalRequired("Publishing requires an approved publication request", approval_id=str(approval.id))
    _validate_decider(db, discovery, claim, approval, PUBLISH_PERMISSION, PUBLISH_ACTION)
    if claim.status != ClaimStatus.VERIFIED:
        raise Conflict(f"The claim is no longer VERIFIED (it is {claim.status})", code="claim_not_verified")
    discovery.published_at = utcnow()
    return _change_status(
        db, actor, discovery, claim, D.PUBLISHED, reason, extra={"publish_approval_id": str(approval.id)}
    )


def request_publication(db: Session, actor: Actor, discovery: Discovery, claim: ScientificClaim, reason: str) -> Approval:
    decision = evaluate_policy(
        db, actor, PUBLISH_ACTION, {"is_human": True, "actor_kind": "user"}, project_id=discovery.project_id, dry_run=True
    )
    if decision.denied:
        raise PolicyDenied(
            "Governance policy does not allow publishing discoveries in this project",
            details={"reasons": list(decision.reasons)},
        )
    return approvals_service.request_approval(
        db,
        actor,
        action=PUBLISH_ACTION,
        subject_type=SUBJECT_TYPE,
        subject_id=discovery.id,
        title=f"Publish discovery: {discovery.title}",
        payload={
            "discovery_id": str(discovery.id),
            "claim_id": str(claim.id),
            "reason": clean_text(reason, 2000),
            "excluded_decider_ids": sorted(str(uid) for uid in _conflicted_user_ids(db, discovery, claim)),
        },
        risk_level="HIGH",
        decision=decision,
        project_id=discovery.project_id,
        mission_id=discovery.mission_id,
        signal_name=DECISION_SIGNAL,
        required_permission=PUBLISH_PERMISSION,
    )


# ---------------------------------------------------------------------------------------------
# Transition (API / workflow entry point)
# ---------------------------------------------------------------------------------------------
def transition(
    db: Session, actor: Actor, discovery_id: uuid.UUID | str, status: str, reason: str
) -> Discovery:
    """Move a discovery through its lifecycle under the approval policy (see the module docstring)."""
    target = (status or "").upper()
    if target not in D.__members__:
        raise ValidationFailed(f"status must be one of {', '.join(D.__members__)}")
    clean_reason = (reason or "").strip()
    if not 3 <= len(clean_reason) <= 2000:
        raise ValidationFailed("reason must be 3-2000 characters")
    discovery = load_discovery(db, actor, discovery_id, "discovery:read")
    claim = db.get(ScientificClaim, discovery.claim_id)
    if claim is None:  # pragma: no cover - FK RESTRICT
        raise NotFound("Discovery not found")
    _require_demo_safe(discovery, target)

    if target == D.HUMAN_REVIEW:
        load_project(db, actor, discovery.project_id, "discovery:create")
        return request_human_review(db, actor, discovery.id, note=clean_reason)[0]

    if target == D.APPROVED:
        _require_human_reviewer(db, actor, discovery, claim, APPROVE_PERMISSION)
        if discovery.status in (D.APPROVED, D.PUBLISHED):
            return discovery
        assert_transition("discovery", discovery.status, D.APPROVED)
        pending = _latest_approval(db, discovery, APPROVE_ACTION, ApprovalStatus.PENDING)
        if pending is not None:
            approvals_service.decide_approval(db, actor, pending.id, approve=True, reason=clean_reason)
            db.refresh(discovery)
            if discovery.status == D.APPROVED:
                return discovery
            return apply_decision(db, actor, discovery.id, approved=True, reason=clean_reason, approval=pending)
        decided = _latest_approval(db, discovery, APPROVE_ACTION, ApprovalStatus.APPROVED)
        if decided is not None:
            return apply_decision(db, actor, discovery.id, approved=True, reason=clean_reason, approval=decided)
        _, approval = request_human_review(db, actor, discovery.id, note=clean_reason)
        raise ApprovalRequired("A human review request is pending for this discovery", approval_id=str(approval.id))

    if target == D.PUBLISHED:
        _require_human_reviewer(db, actor, discovery, claim, PUBLISH_PERMISSION)
        if discovery.status == D.PUBLISHED:
            return discovery
        assert_transition("discovery", discovery.status, D.PUBLISHED)
        decided = _latest_approval(db, discovery, PUBLISH_ACTION, ApprovalStatus.APPROVED)
        if decided is not None:
            return _publish(db, actor, discovery, claim, decided, clean_reason)
        pending = _latest_approval(db, discovery, PUBLISH_ACTION, ApprovalStatus.PENDING)
        approval = pending if pending is not None else request_publication(db, actor, discovery, claim, clean_reason)
        raise ApprovalRequired(
            "Publishing requires an approval decided by another human reviewer", approval_id=str(approval.id)
        )

    advisory_xact_lock(db, f"discovery:{discovery.id}")
    db.refresh(discovery)
    if discovery.status == target:
        return discovery
    if target in (D.REJECTED, D.CONTESTED):
        evidence_driven = (target == D.REJECTED and claim.status == ClaimStatus.REJECTED) or (
            target == D.CONTESTED and claim.status in (ClaimStatus.CONTESTED, ClaimStatus.REJECTED)
        )
        if evidence_driven:
            load_project(db, actor, discovery.project_id, "discovery:create")
        else:
            _require_human_reviewer(db, actor, discovery, claim, APPROVE_PERMISSION)
        return _change_status(db, actor, discovery, claim, target, clean_reason)

    # CANDIDATE / VERIFICATION_PENDING / VERIFIED
    load_project(db, actor, discovery.project_id, "discovery:create")
    if target == D.VERIFIED and claim.status != ClaimStatus.VERIFIED:
        raise Conflict(
            f"A discovery can only be VERIFIED when its claim is VERIFIED (claim is {claim.status})",
            code="claim_not_verified",
        )
    if target == D.VERIFICATION_PENDING:
        verification = db.scalar(
            select(Verification.id)
            .where(Verification.claim_id == claim.id)
            .order_by(Verification.created_at.desc())
            .limit(1)
        )
        extra = {"verification_id": str(verification)} if verification else None
        return _change_status(db, actor, discovery, claim, target, clean_reason, extra=extra)
    return _change_status(db, actor, discovery, claim, target, clean_reason)


# ---------------------------------------------------------------------------------------------
# Approval hook (wired by governance.approvals through HOOK_MODULES)
# ---------------------------------------------------------------------------------------------
def on_approval_decided(db: Session, actor: Actor, approval: Approval) -> None:
    """React to a HUMAN decision on ``discovery.approve`` / ``discovery.publish`` approvals."""
    if approval.subject_type != SUBJECT_TYPE or approval.action not in (APPROVE_ACTION, PUBLISH_ACTION):
        return
    if approval.status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        return
    discovery = db.get(Discovery, uuid.UUID(approval.subject_id))
    if discovery is None or discovery.organization_id != approval.organization_id:
        return
    claim = db.get(ScientificClaim, discovery.claim_id)
    if claim is None:
        return
    reason = approval.decision_reason or ("approved" if approval.status == ApprovalStatus.APPROVED else "rejected")
    if approval.action == APPROVE_ACTION:
        if discovery.status != D.HUMAN_REVIEW:
            return
        try:
            apply_decision(
                db, actor, discovery.id, approved=approval.status == ApprovalStatus.APPROVED, reason=reason, approval=approval
            )
        except Forbidden as exc:
            if getattr(exc, "code", None) != "separation_of_duties":
                raise
            # A conflicted human decided the approval: it cannot count. Ask for a fresh, independent review.
            log.warning("discovery_approval_conflicted", discovery_id=str(discovery.id), approval_id=str(approval.id))
            fresh = approvals_service.request_approval(
                db,
                Actor.system(discovery.organization_id, "system:discovery-review"),
                action=APPROVE_ACTION,
                subject_type=SUBJECT_TYPE,
                subject_id=discovery.id,
                title=f"Approve discovery: {discovery.title}",
                payload={
                    "discovery_id": str(discovery.id),
                    "claim_id": str(claim.id),
                    "replaces_approval_id": str(approval.id),
                    "excluded_decider_ids": sorted(str(uid) for uid in _conflicted_user_ids(db, discovery, claim)),
                },
                project_id=discovery.project_id,
                mission_id=discovery.mission_id,
                signal_name=DECISION_SIGNAL,
                required_permission=APPROVE_PERMISSION,
            )
            discovery.approval_id = fresh.id
            audit(
                db,
                actor,
                "DISCOVERY_APPROVAL_INVALIDATED",
                "discovery",
                discovery.id,
                after={"approval_id": str(approval.id), "replacement_approval_id": str(fresh.id)},
            )
            db.flush()
        return
    if approval.status == ApprovalStatus.APPROVED and discovery.status == D.APPROVED:
        _publish(db, actor, discovery, claim, approval, reason)


approvals_service.register_approval_hook("discovery.", on_approval_decided)


def discovery_counts(db: Session, organization_id: uuid.UUID, mission_id: uuid.UUID) -> dict[str, int]:
    """Discoveries of a mission by status (for mission summaries)."""
    rows = db.execute(
        select(Discovery.status, func.count(Discovery.id))
        .where(Discovery.organization_id == organization_id, Discovery.mission_id == mission_id)
        .group_by(Discovery.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


__all__ = [
    "APPROVE_ACTION",
    "PUBLISH_ACTION",
    "DiscoveryPolicy",
    "Experiment",
    "apply_decision",
    "create_candidate",
    "on_approval_decided",
    "request_human_review",
    "transition",
]
