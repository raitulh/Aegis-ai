"""Platform-admin (cross-tenant operator) use cases.

SECURITY: this is the one explicit exception to "tenant data goes through the RLS-scoped session". The
functions here run on the owner connection (bypasses RLS) and are reachable only by users flagged
``users.is_platform_admin`` (granted out-of-band, never through the API). Every mutating action is
recorded as ``ADMIN_ACTION`` in the affected organization's audit log (or the admin's own organization
for platform-wide changes) with the admin's identity. Read endpoints expose operational metadata only —
never secrets, prompts, documents or model outputs.
"""

from __future__ import annotations

import platform
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from importlib import metadata
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, NotFound, ValidationFailed
from aegis_api.lab.admin.schemas import (
    AdminComputeJobOut,
    AdminFeatureFlagOut,
    AdminJobOut,
    AdminOrganizationOut,
    AdminUsageOut,
    ModelProvidersOut,
    OrganizationUsage,
    SystemInfoOut,
    UsageTotals,
)
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.locks import advisory_xact_lock
from aegis_api.lab.core.pagination import CursorPage, CursorParams, paginate_keyset
from aegis_api.lab.models import ComputeJob, ComputeUsage, ModelUsage, Subscription, WorkflowRun
from aegis_api.models import FeatureFlag, Membership, Organization
from aegis_api.models.enums import MembershipStatus
from aegis_api.schemas.common import Page, PageParams
from engines.lab.states import ExecutionStatus, WorkflowStatus

ACTIVE_SUBSCRIPTION_STATUSES = ("active", "trialing", "past_due")
MAX_USAGE_RANGE = timedelta(days=366)
TOP_ORGANIZATIONS = 50
LIBRARIES = ("fastapi", "pydantic", "sqlalchemy", "psycopg", "temporalio", "httpx", "PyJWT")


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.000001"))


# --- organizations ---------------------------------------------------------------------------------
def _suspended_expr() -> Any:
    return func.coalesce(Organization.settings["suspended"].as_boolean(), False)


def list_organizations(
    db: Session, params: PageParams, *, q: str | None = None, suspended: bool | None = None
) -> Page[AdminOrganizationOut]:
    members = (
        select(Membership.organization_id, func.count(Membership.id).label("members"))
        .where(Membership.status == MembershipStatus.ACTIVE)
        .group_by(Membership.organization_id)
        .subquery()
    )
    stmt = select(Organization, func.coalesce(members.c.members, 0)).outerjoin(
        members, members.c.organization_id == Organization.id
    )
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:200] + "%"
        stmt = stmt.where(
            or_(Organization.name.ilike(pattern, escape="\\"), Organization.slug.ilike(pattern, escape="\\"))
        )
    if suspended is not None:
        stmt = stmt.where(_suspended_expr() == suspended)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(
        stmt.order_by(Organization.created_at.desc(), Organization.id.desc())
        .limit(params.page_size)
        .offset(params.offset)
    ).all()
    org_ids = [org.id for org, _ in rows]
    plans: dict[uuid.UUID, str] = {}
    if org_ids:
        plans = dict(
            db.execute(
                select(Subscription.organization_id, Subscription.plan_key).where(
                    Subscription.organization_id.in_(org_ids),
                    Subscription.status.in_(ACTIVE_SUBSCRIPTION_STATUSES),
                )
            )
            .tuples()
            .all()
        )
    items = [
        AdminOrganizationOut(
            id=str(org.id),
            name=org.name,
            slug=org.slug,
            plan=org.plan,
            subscription_plan=plans.get(org.id),
            member_count=int(count),
            suspended=bool((org.settings or {}).get("suspended")),
            suspended_reason=(org.settings or {}).get("suspended_reason"),
            suspended_at=(org.settings or {}).get("suspended_at"),
            is_demo=org.is_demo,
            is_sandbox=org.is_sandbox,
            created_at=org.created_at,
        )
        for org, count in rows
    ]
    return Page.build(items, int(total), params)


def _organization(db: Session, organization_id: uuid.UUID) -> Organization:
    org = db.scalar(select(Organization).where(Organization.id == organization_id).with_for_update())
    if org is None:
        raise NotFound("Organization not found")
    return org


def suspend_organization(db: Session, admin: Actor, organization_id: uuid.UUID, *, reason: str) -> Organization:
    """Block every principal of the organization (403 ``organization_suspended``) until unsuspended."""
    org = _organization(db, organization_id)
    current = dict(org.settings or {})
    if current.get("suspended"):
        raise Conflict("The organization is already suspended", code="already_suspended")
    current.update(
        {
            "suspended": True,
            "suspended_reason": reason.strip(),
            "suspended_at": utcnow().isoformat(),
            "suspended_by": str(admin.user_id) if admin.user_id else admin.label,
        }
    )
    org.settings = current
    audit(
        db,
        admin,
        AuditAction.ADMIN_ACTION,
        "organization",
        org.id,
        before={"suspended": False},
        after={"operation": "organization.suspend", "reason": reason.strip(), "platform_admin": admin.label},
        organization_id=org.id,
    )
    db.flush()
    return org


def unsuspend_organization(
    db: Session, admin: Actor, organization_id: uuid.UUID, *, reason: str | None = None
) -> Organization:
    org = _organization(db, organization_id)
    current = dict(org.settings or {})
    if not current.get("suspended"):
        raise Conflict("The organization is not suspended", code="not_suspended")
    previous_reason = current.get("suspended_reason")
    for key in ("suspended", "suspended_reason", "suspended_at", "suspended_by"):
        current.pop(key, None)
    org.settings = current
    audit(
        db,
        admin,
        AuditAction.ADMIN_ACTION,
        "organization",
        org.id,
        before={"suspended": True, "reason": previous_reason},
        after={"operation": "organization.unsuspend", "reason": reason, "platform_admin": admin.label},
        organization_id=org.id,
    )
    db.flush()
    return org


def organization_out(db: Session, org: Organization) -> AdminOrganizationOut:
    members = db.scalar(
        select(func.count(Membership.id)).where(
            Membership.organization_id == org.id, Membership.status == MembershipStatus.ACTIVE
        )
    )
    plan = db.scalar(
        select(Subscription.plan_key).where(
            Subscription.organization_id == org.id, Subscription.status.in_(ACTIVE_SUBSCRIPTION_STATUSES)
        )
    )
    settings = org.settings or {}
    return AdminOrganizationOut(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        plan=org.plan,
        subscription_plan=plan,
        member_count=int(members or 0),
        suspended=bool(settings.get("suspended")),
        suspended_reason=settings.get("suspended_reason"),
        suspended_at=settings.get("suspended_at"),
        is_demo=org.is_demo,
        is_sandbox=org.is_sandbox,
        created_at=org.created_at,
    )


# --- jobs ------------------------------------------------------------------------------------------
def _check_enum(value: str | None, allowed: type[Any], label: str) -> str | None:
    if value is None:
        return None
    options = {str(v) for v in allowed}
    if value not in options:
        raise ValidationFailed(f"Unknown {label} '{value}'. Allowed: {', '.join(sorted(options))}")
    return value


def list_workflow_runs(
    db: Session,
    params: CursorParams,
    *,
    status: str | None = None,
    kind: str | None = None,
    organization_id: uuid.UUID | None = None,
) -> CursorPage[AdminJobOut]:
    stmt = select(WorkflowRun)
    if (checked := _check_enum(status, WorkflowStatus, "status")) is not None:
        stmt = stmt.where(WorkflowRun.status == checked)
    if kind:
        stmt = stmt.where(WorkflowRun.kind == kind)
    if organization_id is not None:
        stmt = stmt.where(WorkflowRun.organization_id == organization_id)
    return paginate_keyset(
        db, stmt, params, time_col=WorkflowRun.created_at, id_col=WorkflowRun.id, mapper=AdminJobOut.model_validate
    )


def list_compute_jobs(
    db: Session, params: CursorParams, *, status: str | None = None, organization_id: uuid.UUID | None = None
) -> CursorPage[AdminComputeJobOut]:
    stmt = select(ComputeJob)
    if (checked := _check_enum(status, ExecutionStatus, "status")) is not None:
        stmt = stmt.where(ComputeJob.status == checked)
    if organization_id is not None:
        stmt = stmt.where(ComputeJob.organization_id == organization_id)
    return paginate_keyset(
        db,
        stmt,
        params,
        time_col=ComputeJob.created_at,
        id_col=ComputeJob.id,
        mapper=AdminComputeJobOut.model_validate,
    )


# --- usage -----------------------------------------------------------------------------------------
def usage_summary(db: Session, *, start: datetime | None, end: datetime | None) -> AdminUsageOut:
    end = end or utcnow()
    start = start or (end - timedelta(days=30))
    if start.tzinfo is None or end.tzinfo is None:
        raise ValidationFailed("start and end must include a timezone offset")
    if start >= end:
        raise ValidationFailed("start must be before end")
    if end - start > MAX_USAGE_RANGE:
        raise ValidationFailed("The date range may span at most 366 days")

    llm = db.execute(
        select(
            func.coalesce(func.sum(ModelUsage.cost_usd), 0),
            func.count(ModelUsage.id),
            func.count(ModelUsage.id).filter(ModelUsage.success.is_(False)),
            func.coalesce(func.sum(ModelUsage.input_tokens), 0),
            func.coalesce(func.sum(ModelUsage.output_tokens), 0),
        ).where(ModelUsage.created_at >= start, ModelUsage.created_at < end)
    ).one()
    compute = db.execute(
        select(
            func.coalesce(func.sum(ComputeUsage.cost_usd), 0),
            func.count(ComputeUsage.id),
            func.coalesce(func.sum(ComputeUsage.cpu_seconds), 0.0),
            func.coalesce(func.sum(ComputeUsage.gpu_seconds), 0.0),
            func.coalesce(func.sum(ComputeUsage.wall_seconds), 0.0),
        ).where(ComputeUsage.created_at >= start, ComputeUsage.created_at < end)
    ).one()
    totals = UsageTotals(
        llm_cost_usd=_money(llm[0]),
        llm_calls=int(llm[1]),
        llm_failed_calls=int(llm[2]),
        input_tokens=int(llm[3]),
        output_tokens=int(llm[4]),
        compute_cost_usd=_money(compute[0]),
        compute_jobs=int(compute[1]),
        cpu_seconds=float(compute[2]),
        gpu_seconds=float(compute[3]),
        wall_seconds=float(compute[4]),
        total_cost_usd=_money(llm[0]) + _money(compute[0]),
    )

    llm_by_org = {
        org: (cost, calls)
        for org, cost, calls in db.execute(
            select(ModelUsage.organization_id, func.sum(ModelUsage.cost_usd), func.count(ModelUsage.id))
            .where(ModelUsage.created_at >= start, ModelUsage.created_at < end)
            .group_by(ModelUsage.organization_id)
        ).all()
    }
    compute_by_org = {
        org: (cost, jobs)
        for org, cost, jobs in db.execute(
            select(ComputeUsage.organization_id, func.sum(ComputeUsage.cost_usd), func.count(ComputeUsage.id))
            .where(ComputeUsage.created_at >= start, ComputeUsage.created_at < end)
            .group_by(ComputeUsage.organization_id)
        ).all()
    }
    org_ids = set(llm_by_org) | set(compute_by_org)
    names: dict[uuid.UUID, str] = {}
    if org_ids:
        names = dict(
            db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))).tuples().all()
        )
    rows = []
    for org_id in org_ids:
        llm_cost, calls = llm_by_org.get(org_id, (0, 0))
        compute_cost, jobs = compute_by_org.get(org_id, (0, 0))
        rows.append(
            OrganizationUsage(
                organization_id=str(org_id),
                organization_name=names.get(org_id),
                llm_cost_usd=_money(llm_cost),
                compute_cost_usd=_money(compute_cost),
                total_cost_usd=_money(llm_cost) + _money(compute_cost),
                llm_calls=int(calls),
                compute_jobs=int(jobs),
            )
        )
    rows.sort(key=lambda r: (-r.total_cost_usd, r.organization_id))
    return AdminUsageOut(start=start, end=end, totals=totals, by_organization=rows[:TOP_ORGANIZATIONS])


# --- global feature flags --------------------------------------------------------------------------
def list_global_flags(db: Session) -> list[AdminFeatureFlagOut]:
    defaults = get_settings().feature_defaults()
    global_rows = dict(
        db.execute(select(FeatureFlag.key, FeatureFlag.enabled).where(FeatureFlag.organization_id.is_(None)))
        .tuples()
        .all()
    )
    org_counts = dict(
        db.execute(
            select(FeatureFlag.key, func.count(FeatureFlag.id))
            .where(FeatureFlag.organization_id.is_not(None))
            .group_by(FeatureFlag.key)
        )
        .tuples()
        .all()
    )
    out = []
    for key, default in sorted(defaults.items()):
        override = global_rows.get(key)
        out.append(
            AdminFeatureFlagOut(
                key=key,
                default=default,
                global_override=override,
                effective_default=default if override is None else bool(override),
                organization_overrides=int(org_counts.get(key, 0)),
            )
        )
    return out


def set_global_flag(db: Session, admin: Actor, key: str, enabled: bool | None) -> AdminFeatureFlagOut:
    """Set (or with ``None`` remove) the platform-wide override for a feature flag."""
    defaults = get_settings().feature_defaults()
    if key not in defaults:
        raise NotFound(f"Unknown feature flag '{key}'")
    advisory_xact_lock(db, f"global-feature-flag:{key}")  # (org NULL, key) is not unique-constrained
    rows = list(
        db.scalars(select(FeatureFlag).where(FeatureFlag.organization_id.is_(None), FeatureFlag.key == key)).all()
    )
    before = rows[0].enabled if rows else None
    if enabled is None:
        for row in rows:
            db.delete(row)
    elif rows:
        rows[0].enabled = enabled
        for duplicate in rows[1:]:
            db.delete(duplicate)
    else:
        db.add(FeatureFlag(organization_id=None, key=key, enabled=enabled))
    audit(
        db,
        admin,
        AuditAction.ADMIN_ACTION,
        "feature_flag",
        key,
        before={"global_override": before},
        after={"operation": "feature_flag.global", "global_override": enabled, "platform_admin": admin.label},
    )
    db.flush()
    return next(flag for flag in list_global_flags(db) if flag.key == key)


# --- configuration introspection -------------------------------------------------------------------
def model_providers() -> ModelProvidersOut:
    """Which providers are configured — booleans only; credentials are never exposed."""
    s = get_settings()
    return ModelProvidersOut(
        default_provider=s.llm_default_provider,
        embedding_provider=s.embedding_provider,
        providers={
            "gemini": bool(s.gemini_api_key),
            "openai": bool(s.openai_api_key),
            "anthropic": bool(s.anthropic_api_key),
            "ollama": bool(s.ollama_base_url),
        },
    )


def _library_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in LIBRARIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions


def system_info(db: Session) -> SystemInfoOut:
    s = get_settings()
    database_version: str | None
    schema_revision: str | None
    try:
        database_version = db.scalar(text("show server_version"))
    except SQLAlchemyError:
        db.rollback()
        database_version = None
    try:
        schema_revision = db.scalar(text("select version_num from alembic_version limit 1"))
    except SQLAlchemyError:
        db.rollback()
        schema_revision = None
    return SystemInfoOut(
        app_name=s.app_name,
        app_version=s.app_version,
        environment=s.environment,
        python_version=platform.python_version(),
        database_version=database_version,
        schema_revision=schema_revision,
        library_versions=_library_versions(),
        workflow_engine=s.effective_workflow_engine,
        execution_backend=s.execution_backend,
        event_bus=s.effective_event_bus,
        job_backend=s.effective_job_backend,
        object_storage_backend=s.object_storage_backend,
        malware_scanner=s.malware_scanner,
        redis_configured=bool(s.redis_url),
        temporal_configured=bool(s.temporal_address),
        otel_configured=bool(s.otel_exporter_otlp_endpoint),
        dev_secrets=s.uses_dev_secrets,
        features=s.feature_defaults(),
    )
