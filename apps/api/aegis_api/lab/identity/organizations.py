"""Organizations: membership listing, creation, profile, typed lab settings and feature flags.

Everything here runs on the caller's RLS-scoped session except :func:`create_organization`, which must
insert a *new* tenant (no tenant context can exist for it yet) and therefore runs on the identity-layer
owner session — the same exception ``auth_service.signup`` relies on (see docs/security.md).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.errors import ConcurrentModification
from aegis_api.lab.core.features import feature_enabled
from aegis_api.lab.core.org_settings import DEFAULT_RETENTION, get_org_settings
from aegis_api.lab.identity import validators
from aegis_api.lab.identity.schemas import (
    FeatureFlagOut,
    OrganizationOut,
    OrganizationSummaryOut,
    OrgSettingsOut,
    OrgSettingsUpdate,
)
from aegis_api.lab.models import IdentityProvider, OrganizationSettings
from aegis_api.models import FeatureFlag, Membership, Organization, User
from aegis_api.models.enums import MembershipStatus
from aegis_api.ratelimit import get_limiter
from aegis_api.services import auth_service
from engines.lab.states import autonomy_rank

MAX_RETENTION_DAYS = 36500
SLUG_ATTEMPTS = 5


def is_suspended(org: Organization) -> bool:
    return bool((org.settings or {}).get("suspended"))


# --- listing & creation --------------------------------------------------------------------------------
def list_my_organizations(db: Session, actor: Actor) -> list[OrganizationSummaryOut]:
    """Organizations the caller belongs to. Credentials (API keys, service accounts) see only their own."""
    if actor.kind != "user" or actor.user_id is None:
        org = db.get(Organization, actor.organization_id)
        if org is None:
            raise NotFound("Organization not found")
        return [organization_summary(org, actor.role or "", MembershipStatus.ACTIVE, current=True)]
    rows = db.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == actor.user_id, Membership.status != MembershipStatus.SUSPENDED)
        .order_by(Organization.name, Organization.id)
    ).all()
    return [
        organization_summary(org, membership.role, membership.status, current=org.id == actor.organization_id)
        for membership, org in rows
    ]


def organization_summary(org: Organization, role: str, status: str, *, current: bool) -> OrganizationSummaryOut:
    return OrganizationSummaryOut(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        plan=org.plan,
        role=role,
        membership_status=status,
        is_current=current,
        suspended=is_suspended(org),
        is_demo=org.is_demo,
        is_sandbox=org.is_sandbox,
        created_at=org.created_at,
    )


def create_organization(admin_db: Session, actor: Actor, name: str) -> tuple[Organization, Membership]:
    """Create a new organization owned by the calling user (identity-layer owner session)."""
    actor.require_human("creating an organization")
    user = admin_db.get(User, actor.user_id) if actor.user_id else None
    if user is None:
        raise Forbidden("Only signed-in users can create organizations")
    if user.is_guest:
        raise Forbidden("Guest accounts cannot create organizations")
    # Abuse control: organization creation shares the public-tier budget per user.
    if not name.strip():
        raise ValidationFailed("Organization name must not be blank")
    get_limiter().check("public", f"org-create:{user.id}")
    previous_default = user.default_organization_id
    org, membership = _provision_with_retry(admin_db, name.strip(), user)
    # Creating an organization must not silently change which organization the user lands in.
    user.default_organization_id = previous_default or org.id
    get_org_settings(admin_db, org.id)
    audit(
        admin_db,
        actor,
        "ORGANIZATION_CREATED",
        "organization",
        org.id,
        after={"name": org.name, "slug": org.slug, "owner_user_id": user.id},
        organization_id=org.id,
    )
    admin_db.flush()
    return org, membership


def _provision_with_retry(admin_db: Session, name: str, user: User) -> tuple[Organization, Membership]:
    """Slug allocation is check-then-insert; retry in a savepoint when a concurrent insert takes the slug."""
    for _ in range(SLUG_ATTEMPTS):
        try:
            with admin_db.begin_nested():
                return auth_service._provision_org(admin_db, name=name, user=user)
        except IntegrityError:
            continue
    raise Conflict("Could not allocate a unique organization slug; please retry")


# --- current organization ------------------------------------------------------------------------------
def _current_org(db: Session, actor: Actor) -> Organization:
    org = db.get(Organization, actor.organization_id)
    if org is None:
        raise NotFound("Organization not found")
    return org


def get_current_organization(db: Session, actor: Actor) -> OrganizationOut:
    actor.require("org:read")
    org = _current_org(db, actor)
    members = db.scalar(
        select(func.count(Membership.id)).where(
            Membership.organization_id == org.id, Membership.status == MembershipStatus.ACTIVE
        )
    )
    return OrganizationOut(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        plan=org.plan,
        is_demo=org.is_demo,
        is_sandbox=org.is_sandbox,
        suspended=is_suspended(org),
        member_count=int(members or 0),
        role=actor.role or "",
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


def update_current_organization(db: Session, actor: Actor, *, name: str) -> OrganizationOut:
    actor.require("org:manage")
    org = _current_org(db, actor)
    before = {"name": org.name}
    org.name = name.strip()
    if not org.name:
        raise ValidationFailed("Organization name must not be blank")
    audit(db, actor, "ORGANIZATION_UPDATED", "organization", org.id, before=before, after={"name": org.name})
    db.flush()
    return get_current_organization(db, actor)


# --- settings ------------------------------------------------------------------------------------------
SELF_APPROVAL_KEY = "allow_self_approval"


def _allow_self_approval(org: Organization) -> bool:
    return bool((org.settings or {}).get(SELF_APPROVAL_KEY, False))


def settings_out(db: Session, row: OrganizationSettings) -> OrgSettingsOut:
    """Typed lab settings plus the governance flags stored on the organization itself."""
    org = db.get(Organization, row.organization_id)
    out = OrgSettingsOut.model_validate(row)
    return out.model_copy(update={"allow_self_approval": _allow_self_approval(org) if org else False})


def get_organization_settings(db: Session, actor: Actor) -> OrganizationSettings:
    actor.require("org:read")
    return get_org_settings(db, actor.organization_id)


def _snapshot(row: OrganizationSettings, org: Organization) -> dict[str, Any]:
    return {
        "allow_self_approval": _allow_self_approval(org),
        "max_autonomy_level": row.max_autonomy_level,
        "default_autonomy_level": row.default_autonomy_level,
        "retention": dict(row.retention or {}),
        "egress_allowlist": list(row.egress_allowlist or []),
        "execution_policy": dict(row.execution_policy or {}),
        "data_processing": dict(row.data_processing or {}),
        "sso_enforced": row.sso_enforced,
    }


def update_organization_settings(db: Session, actor: Actor, data: OrgSettingsUpdate) -> OrganizationSettings:
    """Validate and apply a partial settings update (quotas are governance-owned and not editable here)."""
    actor.require("org:manage")
    actor.require_human("changing organization settings")
    settings = get_settings()
    row = get_org_settings(db, actor.organization_id)
    if data.lock_version is not None and data.lock_version != row.lock_version:
        raise ConcurrentModification("Organization settings were changed by someone else; reload and retry")
    org = _current_org(db, actor)
    before = _snapshot(row, org)

    max_level = str(data.max_autonomy_level or row.max_autonomy_level)
    default_level = str(data.default_autonomy_level or row.default_autonomy_level)
    if autonomy_rank(default_level) > autonomy_rank(max_level):
        raise ValidationFailed("default_autonomy_level must not exceed max_autonomy_level")
    row.max_autonomy_level = max_level
    row.default_autonomy_level = default_level

    if data.retention is not None:
        retention = dict(row.retention or {})
        for key, days in data.retention.items():
            if key not in DEFAULT_RETENTION:
                raise ValidationFailed(f"Unknown retention class '{key}'. Allowed: {', '.join(DEFAULT_RETENTION)}")
            if days is not None and not 0 <= days <= MAX_RETENTION_DAYS:
                raise ValidationFailed(f"Retention for '{key}' must be between 0 and {MAX_RETENTION_DAYS} days")
            retention[key] = days
        row.retention = retention

    if data.egress_allowlist is not None:
        row.egress_allowlist = validators.normalize_hostnames(
            data.egress_allowlist, allow_suffix=True, limit=validators.MAX_EGRESS_HOSTS
        )

    if data.execution_policy is not None:
        policy = dict(row.execution_policy or {})
        incoming = data.execution_policy
        if incoming.allowed_images is not None:
            policy["allowed_images"] = validators.normalize_allowed_images(
                incoming.allowed_images, settings.execution_allowed_image_list
            )
        if incoming.default_backend is not None:
            if incoming.default_backend != settings.execution_backend:
                raise ValidationFailed(
                    f"Execution backend '{incoming.default_backend}' is not available on this platform"
                )
            policy["default_backend"] = incoming.default_backend
        if incoming.gpu_enabled is not None:
            if incoming.gpu_enabled and not settings.execution_gpu_enabled:
                raise ValidationFailed("GPU execution is disabled on this platform")
            policy["gpu_enabled"] = incoming.gpu_enabled
        if incoming.max_timeout_seconds is not None:
            if incoming.max_timeout_seconds > settings.execution_max_timeout_seconds:
                raise ValidationFailed(
                    f"max_timeout_seconds must not exceed the platform cap ({settings.execution_max_timeout_seconds}s)"
                )
            policy["max_timeout_seconds"] = incoming.max_timeout_seconds
        row.execution_policy = policy

    if data.data_processing is not None:
        processing = dict(row.data_processing or {})
        if data.data_processing.allow_external_llm is not None:
            processing["allow_external_llm"] = data.data_processing.allow_external_llm
        if data.data_processing.allowed_providers is not None:
            processing["allowed_providers"] = sorted(set(data.data_processing.allowed_providers))
        row.data_processing = processing

    if data.sso_enforced is not None:
        if data.sso_enforced and not row.sso_enforced:
            _require_usable_sso(db, actor)
        row.sso_enforced = data.sso_enforced

    if data.allow_self_approval is not None:
        org.settings = {**(org.settings or {}), SELF_APPROVAL_KEY: data.allow_self_approval}

    after = _snapshot(row, org)
    if after != before:
        changed = {k: v for k, v in after.items() if before.get(k) != v}
        audit(
            db,
            actor,
            "ORG_SETTINGS_CHANGED",
            "organization_settings",
            row.id,
            before={k: before[k] for k in changed},
            after=changed,
        )
    db.flush()
    return row


def _require_usable_sso(db: Session, actor: Actor) -> None:
    if not feature_enabled(db, actor.organization_id, "enterprise_sso"):
        raise ValidationFailed("Enable the 'enterprise_sso' feature before enforcing single sign-on")
    enabled = db.scalar(
        select(func.count(IdentityProvider.id)).where(
            IdentityProvider.organization_id == actor.organization_id,
            IdentityProvider.enabled.is_(True),
            IdentityProvider.kind == "oidc",
        )
    )
    if not enabled:
        raise ValidationFailed("Configure and enable an OIDC identity provider before enforcing single sign-on")


# --- feature flags -------------------------------------------------------------------------------------
def _flag_rows(db: Session, actor: Actor) -> dict[tuple[bool, str], bool]:
    rows = db.execute(
        select(FeatureFlag.organization_id, FeatureFlag.key, FeatureFlag.enabled).where(
            or_(FeatureFlag.organization_id == actor.organization_id, FeatureFlag.organization_id.is_(None))
        )
    ).all()
    return {(org is not None, key): bool(enabled) for org, key, enabled in rows}


def _flag_out(key: str, default: bool, rows: dict[tuple[bool, str], bool]) -> FeatureFlagOut:
    if (True, key) in rows:
        return FeatureFlagOut(key=key, enabled=rows[(True, key)], default=default, source="organization")
    if (False, key) in rows:
        return FeatureFlagOut(key=key, enabled=rows[(False, key)], default=default, source="global")
    return FeatureFlagOut(key=key, enabled=default, default=default, source="default")


def list_features(db: Session, actor: Actor) -> list[FeatureFlagOut]:
    actor.require("org:read")
    rows = _flag_rows(db, actor)
    return [_flag_out(key, default, rows) for key, default in sorted(get_settings().feature_defaults().items())]


def set_feature(db: Session, actor: Actor, key: str, *, enabled: bool) -> FeatureFlagOut:
    actor.require("org:manage")
    actor.require_human("changing feature flags")
    defaults = get_settings().feature_defaults()
    if key not in defaults:
        raise NotFound(f"Unknown feature flag '{key}'")
    before = _flag_out(key, defaults[key], _flag_rows(db, actor))
    now = utcnow()
    db.execute(
        insert(FeatureFlag)
        .values(organization_id=actor.organization_id, key=key, enabled=enabled, created_at=now, updated_at=now)
        .on_conflict_do_update(constraint="uq_feature_flags_org_key", set_={"enabled": enabled, "updated_at": now})
    )
    after = _flag_out(key, defaults[key], _flag_rows(db, actor))
    audit(
        db,
        actor,
        AuditAction.FEATURE_FLAG_CHANGED,
        "feature_flag",
        key,
        before={"enabled": before.enabled, "source": before.source},
        after={"enabled": after.enabled, "source": after.source},
    )
    return after
