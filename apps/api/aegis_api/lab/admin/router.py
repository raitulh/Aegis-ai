"""HTTP API for platform administration (``/api/v1/admin``; platform admins only).

Access: the principal must be a signed-in human whose user has ``is_platform_admin`` (never grantable
through the API). Everyone else — including organization owners — receives 404 so the surface is not
revealed. These endpoints use the owner connection (RLS bypass) by design; see ``lab.admin.service``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from aegis_api.db.session import session_factory
from aegis_api.deps import get_current_principal
from aegis_api.errors import NotFound
from aegis_api.lab.admin import service
from aegis_api.lab.admin.schemas import (
    AdminComputeJobOut,
    AdminFeatureFlagOut,
    AdminFeatureFlagsBulkUpdate,
    AdminFeatureFlagUpdate,
    AdminJobOut,
    AdminOrganizationOut,
    AdminUsageOut,
    ModelProvidersOut,
    SuspendRequest,
    SystemInfoOut,
    UnsuspendRequest,
)
from aegis_api.lab.core.actor import HUMAN_AUTH_METHODS, Actor
from aegis_api.lab.core.pagination import CursorPage, CursorParams, cursor_params
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.context import Principal

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])

HIDDEN: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication required"},
    404: {"description": "Not found (also returned to callers who are not platform administrators)"},
}


def require_platform_admin(principal: Principal = Depends(get_current_principal)) -> Actor:
    """Allow only signed-in platform administrators; everyone else gets 404 (the API is not revealed)."""
    if not principal.is_platform_admin or principal.auth_method not in HUMAN_AUTH_METHODS:
        raise NotFound("Not found")
    actor = Actor.from_principal(principal)
    return actor


def platform_db(_: Actor = Depends(require_platform_admin)) -> Iterator[Session]:
    """Owner-connection session for platform operators (bypasses RLS — the audited exception)."""
    session = session_factory(admin=True)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.get(
    "/organizations",
    summary="List all organizations",
    description="Every organization with member count, plan and suspension state.",
    response_model=Page[AdminOrganizationOut],
    status_code=status.HTTP_200_OK,
    responses=HIDDEN,
)
def list_organizations(
    params: PageParams = Depends(),
    q: str | None = Query(None, max_length=200, description="Filter by name or slug"),
    suspended: bool | None = Query(None, description="Filter by suspension state"),
    _: Actor = Depends(require_platform_admin),
    db: Session = Depends(platform_db),
) -> Page[AdminOrganizationOut]:
    return service.list_organizations(db, params, q=q, suspended=suspended)


@router.post(
    "/organizations/{organization_id}/suspend",
    summary="Suspend an organization",
    description="Every principal of the organization is rejected with 403 `organization_suspended` until it is "
    "unsuspended. Recorded as ADMIN_ACTION in the organization's audit log.",
    response_model=AdminOrganizationOut,
    status_code=status.HTTP_200_OK,
    responses={**HIDDEN, 409: {"description": "Already suspended"}},
)
def suspend_organization(
    organization_id: uuid.UUID,
    body: SuspendRequest,
    admin: Actor = Depends(require_platform_admin),
    db: Session = Depends(platform_db),
) -> AdminOrganizationOut:
    org = service.suspend_organization(db, admin, organization_id, reason=body.reason)
    return service.organization_out(db, org)


@router.post(
    "/organizations/{organization_id}/unsuspend",
    summary="Lift an organization suspension",
    description="Restores access for the organization's principals. Recorded as ADMIN_ACTION in its audit log.",
    response_model=AdminOrganizationOut,
    status_code=status.HTTP_200_OK,
    responses={**HIDDEN, 409: {"description": "Not suspended"}},
)
def unsuspend_organization(
    organization_id: uuid.UUID,
    body: UnsuspendRequest | None = None,
    admin: Actor = Depends(require_platform_admin),
    db: Session = Depends(platform_db),
) -> AdminOrganizationOut:
    org = service.unsuspend_organization(db, admin, organization_id, reason=body.reason if body else None)
    return service.organization_out(db, org)


@router.get(
    "/jobs",
    summary="List workflow runs across organizations",
    description="Cursor-paginated (newest first); filter by status, kind and `org_id`.",
    response_model=CursorPage[AdminJobOut],
    status_code=status.HTTP_200_OK,
    responses={**HIDDEN, 422: {"description": "Unknown status"}},
)
def list_jobs(
    params: CursorParams = Depends(cursor_params),
    job_status: str | None = Query(None, alias="status", description="Workflow status"),
    kind: str | None = Query(None, max_length=48, description="Workflow kind, e.g. MissionWorkflow"),
    organization_id: uuid.UUID | None = Query(
        None,
        alias="org_id",
        description="Filter by organization (`organization_id` is reserved for selecting the caller's org)",
    ),
    _: Actor = Depends(require_platform_admin),
    db: Session = Depends(platform_db),
) -> CursorPage[AdminJobOut]:
    return service.list_workflow_runs(db, params, status=job_status, kind=kind, organization_id=organization_id)


@router.get(
    "/compute-jobs",
    summary="List compute jobs across organizations",
    description="Cursor-paginated (newest first); filter by status and `org_id`.",
    response_model=CursorPage[AdminComputeJobOut],
    status_code=status.HTTP_200_OK,
    responses={**HIDDEN, 422: {"description": "Unknown status"}},
)
def list_compute_jobs(
    params: CursorParams = Depends(cursor_params),
    job_status: str | None = Query(None, alias="status", description="Execution status"),
    organization_id: uuid.UUID | None = Query(
        None,
        alias="org_id",
        description="Filter by organization (`organization_id` is reserved for selecting the caller's org)",
    ),
    _: Actor = Depends(require_platform_admin),
    db: Session = Depends(platform_db),
) -> CursorPage[AdminComputeJobOut]:
    return service.list_compute_jobs(db, params, status=job_status, organization_id=organization_id)


@router.get(
    "/usage",
    summary="Cross-organization usage totals",
    description="LLM and compute usage/cost totals for a date range (default: last 30 days; max 366 days).",
    response_model=AdminUsageOut,
    status_code=status.HTTP_200_OK,
    responses={**HIDDEN, 422: {"description": "Invalid date range"}},
)
def usage(
    start: datetime | None = Query(None, description="Inclusive start (ISO-8601 with timezone)"),
    end: datetime | None = Query(None, description="Exclusive end (ISO-8601 with timezone)"),
    _: Actor = Depends(require_platform_admin),
    db: Session = Depends(platform_db),
) -> AdminUsageOut:
    return service.usage_summary(db, start=start, end=end)


@router.get(
    "/feature-flags",
    summary="List platform-wide feature flags",
    description="Platform defaults, platform-wide overrides and how many organizations override each flag.",
    response_model=list[AdminFeatureFlagOut],
    status_code=status.HTTP_200_OK,
    responses=HIDDEN,
)
def list_feature_flags(
    _: Actor = Depends(require_platform_admin), db: Session = Depends(platform_db)
) -> list[AdminFeatureFlagOut]:
    return service.list_global_flags(db)


@router.put(
    "/feature-flags",
    summary="Set platform-wide feature flags (bulk)",
    description='`{"flags": {"<key>": true|false|null}}` — null removes the platform-wide override.',
    response_model=list[AdminFeatureFlagOut],
    status_code=status.HTTP_200_OK,
    responses={**HIDDEN, 422: {"description": "Validation error"}},
)
def set_feature_flags(
    body: AdminFeatureFlagsBulkUpdate,
    admin: Actor = Depends(require_platform_admin),
    db: Session = Depends(platform_db),
) -> list[AdminFeatureFlagOut]:
    for key, enabled in sorted(body.flags.items()):
        service.set_global_flag(db, admin, key, enabled)
    return service.list_global_flags(db)


@router.put(
    "/feature-flags/{key}",
    summary="Set one platform-wide feature flag",
    description="`enabled: null` removes the platform-wide override. Recorded as ADMIN_ACTION.",
    response_model=AdminFeatureFlagOut,
    status_code=status.HTTP_200_OK,
    responses=HIDDEN,
)
def set_feature_flag(
    key: str,
    body: AdminFeatureFlagUpdate,
    admin: Actor = Depends(require_platform_admin),
    db: Session = Depends(platform_db),
) -> AdminFeatureFlagOut:
    return service.set_global_flag(db, admin, key, body.enabled)


@router.get(
    "/model-providers",
    summary="Configured model providers",
    description="Which providers have credentials/base URLs configured (booleans only; keys are never exposed).",
    response_model=ModelProvidersOut,
    status_code=status.HTTP_200_OK,
    responses=HIDDEN,
)
def model_providers(_: Actor = Depends(require_platform_admin)) -> ModelProvidersOut:
    return service.model_providers()


@router.get(
    "/system",
    summary="Platform system information",
    description="Versions, schema revision, workflow engine, execution backend, event bus and environment.",
    response_model=SystemInfoOut,
    status_code=status.HTTP_200_OK,
    responses=HIDDEN,
)
def system(_: Actor = Depends(require_platform_admin), db: Session = Depends(platform_db)) -> SystemInfoOut:
    return service.system_info(db)
