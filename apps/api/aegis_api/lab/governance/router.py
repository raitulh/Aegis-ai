"""HTTP API for governance: policies, dry-run evaluation, approvals, budgets, quotas and the audit log."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from aegis_api.deps import get_db
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import require_actor
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.lab.governance import approvals, audit_log, budgets, policies, quotas
from aegis_api.lab.governance.schemas import (
    ApprovalCancelIn,
    ApprovalDecisionIn,
    ApprovalOut,
    AuditEntryOut,
    BaselineOut,
    BudgetStateOut,
    DecisionOut,
    EvaluateRequest,
    PolicyCreate,
    PolicyDetailOut,
    PolicyOut,
    PolicyStatusUpdate,
    PolicyVersionCreate,
    PolicyVersionOut,
    QuotasOut,
    QuotasUpdate,
)
from aegis_api.schemas.common import Page, PageParams

router = APIRouter(prefix="/api/v1", tags=["Governance"])

E401: dict[int | str, dict[str, Any]] = {401: {"description": "Authentication required"}}
E403: dict[int | str, dict[str, Any]] = {
    403: {"description": "Missing permission, or the action requires a signed-in human"}
}
E404: dict[int | str, dict[str, Any]] = {404: {"description": "Not found (or belongs to another organization)"}}
E409: dict[int | str, dict[str, Any]] = {
    409: {"description": "Conflict (invalid state transition, expired, duplicate)"}
}
E422: dict[int | str, dict[str, Any]] = {422: {"description": "Validation error"}}

PolicyId = Path(description="Governance policy id")
ApprovalId = Path(description="Approval id")


def page_params(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(25, ge=1, le=200, description="Items per page"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


# ---------------------------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------------------------
@router.get(
    "/governance/policies",
    summary="List governance policies",
    description="Organization and project policies (with their current version number).",
    response_model=Page[PolicyOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E422},
)
def list_policies(
    policy_status: str | None = Query(None, alias="status", pattern="^(active|disabled)$"),
    project_id: uuid.UUID | None = Query(None, description="Only policies scoped to this project"),
    params: PageParams = Depends(page_params),
    actor: Actor = Depends(require_actor("policy:read")),
    db: Session = Depends(get_db),
) -> Page[PolicyOut]:
    return policies.list_policies(db, actor, params, status=policy_status, project_id=project_id)


@router.post(
    "/governance/policies",
    summary="Create a governance policy",
    description=(
        "Creates an organization (or project-scoped) policy with immutable version 1. Rules are validated "
        "strictly (see `GET /governance/baseline` for the DSL); organization rules can only add restrictions "
        "on top of the platform baseline. Requires `admin:policy` and a signed-in human."
    ),
    response_model=PolicyDetailOut,
    status_code=status.HTTP_201_CREATED,
    responses={**E401, **E403, **E404, 409: {"description": "Policy key already exists"}, **E422},
)
def create_policy(
    body: PolicyCreate,
    actor: Actor = Depends(require_actor("admin:policy")),
    db: Session = Depends(get_db),
) -> PolicyDetailOut:
    policy = policies.create_policy(db, actor, body)
    return policies.get_policy_detail(db, actor, policy.id)


@router.get(
    "/governance/policies/{policy_id}",
    summary="Get a governance policy",
    description="The policy with the rules of its current version.",
    response_model=PolicyDetailOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404},
)
def get_policy(
    policy_id: uuid.UUID = PolicyId,
    actor: Actor = Depends(require_actor("policy:read")),
    db: Session = Depends(get_db),
) -> PolicyDetailOut:
    return policies.get_policy_detail(db, actor, policy_id)


@router.patch(
    "/governance/policies/{policy_id}",
    summary="Enable or disable a governance policy",
    description="Only the status is mutable; rules change through new immutable versions.",
    response_model=PolicyDetailOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
)
def update_policy(
    body: PolicyStatusUpdate,
    policy_id: uuid.UUID = PolicyId,
    actor: Actor = Depends(require_actor("admin:policy")),
    db: Session = Depends(get_db),
) -> PolicyDetailOut:
    policies.set_policy_status(db, actor, policy_id, body.status)
    return policies.get_policy_detail(db, actor, policy_id)


@router.get(
    "/governance/policies/{policy_id}/versions",
    summary="List policy versions",
    description="Immutable version history, newest first.",
    response_model=Page[PolicyVersionOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404},
)
def list_policy_versions(
    policy_id: uuid.UUID = PolicyId,
    params: PageParams = Depends(page_params),
    actor: Actor = Depends(require_actor("policy:read")),
    db: Session = Depends(get_db),
) -> Page[PolicyVersionOut]:
    return policies.list_policy_versions(db, actor, policy_id, params)


@router.post(
    "/governance/policies/{policy_id}/versions",
    summary="Create a new policy version",
    description="Validates the rules, stores them as a new immutable version (with a content hash) and makes it current.",
    response_model=PolicyVersionOut,
    status_code=status.HTTP_201_CREATED,
    responses={**E401, **E403, **E404, **E422},
)
def create_policy_version(
    body: PolicyVersionCreate,
    policy_id: uuid.UUID = PolicyId,
    actor: Actor = Depends(require_actor("admin:policy")),
    db: Session = Depends(get_db),
) -> PolicyVersionOut:
    version = policies.create_policy_version(db, actor, policy_id, body)
    return policies.version_out(version)


@router.get(
    "/governance/baseline",
    summary="Platform baseline policy",
    description=(
        "The immutable, fail-safe platform baseline (always evaluated first), the default effect per action, "
        "the action catalog and the context keys usable in rule conditions."
    ),
    response_model=BaselineOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403},
)
def baseline(_: Actor = Depends(require_actor("policy:read"))) -> BaselineOut:
    return BaselineOut(**policies.baseline_view())


@router.post(
    "/governance/evaluate",
    summary="Dry-run a policy decision",
    description=(
        "Evaluates an action against the baseline and the organization's active policies without side "
        "effects. The supplied context may override derived facts (actor kind, autonomy…) to simulate "
        "scenarios; the response includes the evaluated context."
    ),
    response_model=DecisionOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
)
def evaluate(
    body: EvaluateRequest,
    actor: Actor = Depends(require_actor("policy:read")),
    db: Session = Depends(get_db),
) -> DecisionOut:
    decision, context = policies.dry_run_policy(
        db, actor, body.action, body.context, project_id=body.project_id, mission_id=body.mission_id
    )
    return DecisionOut(**decision.to_dict(), context=context)


# ---------------------------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------------------------
@router.get(
    "/approvals",
    summary="List approval requests",
    description="Approval requests of the organization (restricted projects are only visible to their members).",
    response_model=Page[ApprovalOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E422},
)
def list_approvals(
    approval_status: str | None = Query(
        None, alias="status", description="PENDING|APPROVED|REJECTED|EXPIRED|CANCELLED"
    ),
    mission_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None, max_length=64),
    params: PageParams = Depends(page_params),
    actor: Actor = Depends(require_actor("approval:read")),
    db: Session = Depends(get_db),
) -> Page[ApprovalOut]:
    return approvals.list_approvals(
        db, actor, params, status=approval_status, mission_id=mission_id, project_id=project_id, action=action
    )


@router.get(
    "/approvals/{approval_id}",
    summary="Get an approval request",
    response_model=ApprovalOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404},
)
def get_approval(
    approval_id: uuid.UUID = ApprovalId,
    actor: Actor = Depends(require_actor("approval:read")),
    db: Session = Depends(get_db),
) -> ApprovalOut:
    """An approval request with its policy decision and current status."""
    return approvals.approval_out(approvals.get_approval(db, actor, approval_id))


@router.post(
    "/approvals/{approval_id}/decision",
    summary="Approve or reject a request",
    description=(
        "Signed-in humans only (API keys, service accounts and agents are refused) holding the request's "
        "required permission in its project. Requesters cannot decide their own requests (separation of "
        "duties). Expired requests become EXPIRED and return 409."
    ),
    response_model=ApprovalOut,
    status_code=status.HTTP_200_OK,
    responses={
        **E401,
        403: {"description": "Not a human, missing permission, or separation of duties (`separation_of_duties`)"},
        **E404,
        409: {"description": "Not pending (`invalid_state_transition`) or expired (`approval_expired`)"},
        **E422,
    },
)
def decide(
    body: ApprovalDecisionIn,
    approval_id: uuid.UUID = ApprovalId,
    actor: Actor = Depends(require_actor("approval:read")),
    db: Session = Depends(get_db),
) -> ApprovalOut:
    approval = approvals.decide_approval(db, actor, approval_id, approve=body.approve, reason=body.reason)
    return approvals.approval_out(approval)


@router.post(
    "/approvals/{approval_id}/cancel",
    summary="Cancel an approval request",
    description="The requester (or an organization owner/admin) withdraws a pending request.",
    response_model=ApprovalOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E409, **E422},
)
def cancel(
    body: ApprovalCancelIn | None = None,
    approval_id: uuid.UUID = ApprovalId,
    actor: Actor = Depends(require_actor("approval:read")),
    db: Session = Depends(get_db),
) -> ApprovalOut:
    approval = approvals.cancel_approval(db, actor, approval_id, reason=body.reason if body else None)
    return approvals.approval_out(approval)


# ---------------------------------------------------------------------------------------------
# Budgets & quotas
# ---------------------------------------------------------------------------------------------
@router.get(
    "/budgets/missions/{mission_id}",
    summary="Mission budget state",
    description=(
        "Limits, spend, remaining and utilization per dimension (LLM, compute, tools, total, experiments, "
        "research tasks), warning (≥80%) / exceeded (≥100%) status, time budget and the project ceiling."
    ),
    response_model=BudgetStateOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E404, **E422},
)
def mission_budget(
    mission_id: uuid.UUID = Path(description="Mission id"),
    actor: Actor = Depends(require_actor("mission:read")),
    db: Session = Depends(get_db),
) -> BudgetStateOut:
    return budgets.budget_view(db, actor, mission_id)


@router.get(
    "/quotas",
    summary="Organization quotas",
    description=(
        "Effective limit (the lowest of organization settings, plan and subscription override), current "
        "usage and remaining headroom for every quota."
    ),
    response_model=QuotasOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403},
)
def get_quotas(
    actor: Actor = Depends(require_actor("usage:read")),
    db: Session = Depends(get_db),
) -> QuotasOut:
    return quotas.quotas_view(db, actor)


@router.put(
    "/quotas",
    summary="Update organization quotas",
    description="Sets organization quota limits (non-negative; null restores the default). Requires `org:manage` and a human.",
    response_model=QuotasOut,
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E422},
)
def put_quotas(
    body: QuotasUpdate,
    actor: Actor = Depends(require_actor("org:manage")),
    db: Session = Depends(get_db),
) -> QuotasOut:
    return quotas.update_quotas(db, actor, body.quotas)


# ---------------------------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------------------------
@router.get(
    "/audit",
    summary="Lab audit log",
    description="The organization's append-only audit log, newest first, with cursor pagination.",
    response_model=CursorPage[AuditEntryOut],
    status_code=status.HTTP_200_OK,
    responses={**E401, **E403, **E422},
)
def list_audit(
    action: str | None = Query(None, max_length=80),
    resource_type: str | None = Query(None, max_length=48),
    resource_id: str | None = Query(None, max_length=64),
    actor_type: str | None = Query(None, max_length=16),
    user_id: str | None = Query(None, max_length=36),
    since: datetime | None = Query(None, description="Inclusive lower bound (ISO-8601)"),
    until: datetime | None = Query(None, description="Exclusive upper bound (ISO-8601)"),
    params: CursorParams = Depends(cursor_params),
    actor: Actor = Depends(require_actor("audit:read")),
    db: Session = Depends(get_db),
) -> CursorPage[AuditEntryOut]:
    return audit_log.list_audit_entries(
        db,
        actor,
        params,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_type=actor_type,
        user_id=user_id,
        since=since,
        until=until,
    )
