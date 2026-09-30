"""Identity & access management: permission catalogue, custom roles, service accounts, API key rotation,
enterprise SSO connections, organization quotas and feature flags."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.deps import get_db, require
from aegis_api.errors import Conflict, Forbidden, NotFound, ValidationFailed
from aegis_api.models import (
    ApiKey,
    Permission,
    RoleDefinition,
    RolePermission,
    ServiceAccount,
    SSOConnection,
)
from aegis_api.schemas.auth import ApiKeyOut, ApiKeyRotated
from aegis_api.schemas.common import Message
from aegis_api.schemas.identity import (
    FeatureFlagUpdate,
    PermissionOut,
    QuotaOut,
    QuotaUpdate,
    RoleCreate,
    RoleOut,
    ServiceAccountCreate,
    ServiceAccountKeyCreate,
    ServiceAccountKeyCreated,
    ServiceAccountOut,
    ServiceAccountUpdate,
    SSOConnectionCreate,
    SSOConnectionOut,
)
from aegis_api.security.context import Principal
from aegis_api.security.rbac import (
    ALL_SCOPES,
    ROLE_PERMISSIONS,
    can_assign_role,
    is_system_role,
    validate_custom_permissions,
)
from aegis_api.services import api_key_service, audit_log, feature_service, quota_service, rbac_service, secrets_service
from engines.lab.autonomy import validate_autonomy_change

router = APIRouter(prefix="/api/v1", tags=["Identity"])


# --- permissions & roles ------------------------------------------------------------------------
@router.get("/permissions", response_model=list[PermissionOut], summary="List the permission catalogue")
def list_permissions(
    principal: Principal = Depends(require("team:read")), db: Session = Depends(get_db)
) -> list[PermissionOut]:
    return [PermissionOut.model_validate(p) for p in db.scalars(select(Permission).order_by(Permission.key)).all()]


def _role_out(db: Session, role: RoleDefinition) -> RoleOut:
    perms = sorted(db.scalars(select(RolePermission.permission_key).where(RolePermission.role_id == role.id)).all())
    if role.is_system:
        perms = sorted(ROLE_PERMISSIONS.get(role.key, frozenset()))
    return RoleOut(
        id=str(role.id),
        key=role.key,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=perms,
    )


@router.get("/roles", response_model=list[RoleOut], summary="List system and custom roles")
def list_roles(principal: Principal = Depends(require("team:read")), db: Session = Depends(get_db)) -> list[RoleOut]:
    roles = db.scalars(
        select(RoleDefinition)
        .where(
            (RoleDefinition.organization_id.is_(None)) | (RoleDefinition.organization_id == principal.organization_id)
        )
        .order_by(RoleDefinition.is_system.desc(), RoleDefinition.key)
    ).all()
    return [_role_out(db, r) for r in roles]


@router.post(
    "/roles", response_model=RoleOut, status_code=201, summary="Create a custom role (no privilege escalation)"
)
def create_role(
    body: RoleCreate, principal: Principal = Depends(require("team:manage")), db: Session = Depends(get_db)
) -> RoleOut:
    principal.require_human("role.create")
    if is_system_role(body.key):
        raise Conflict("A system role with this key exists")
    problems = validate_custom_permissions(set(body.permissions), principal.permissions)
    if problems:
        raise Forbidden("; ".join(problems), code="privilege_escalation")
    if db.scalar(
        select(RoleDefinition.id).where(
            RoleDefinition.organization_id == principal.organization_id, RoleDefinition.key == body.key
        )
    ):
        raise Conflict("A role with this key already exists")
    role = RoleDefinition(
        organization_id=principal.organization_id,
        key=body.key,
        name=body.name,
        description=body.description,
        is_system=False,
    )
    db.add(role)
    db.flush()
    for perm in sorted(set(body.permissions)):
        db.add(RolePermission(role_id=role.id, permission_key=perm))
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="role.created",
        resource_type="role",
        resource_id=role.id,
        principal=principal,
        after={"key": role.key, "permissions": sorted(body.permissions)},
    )
    db.flush()
    return _role_out(db, role)


@router.delete("/roles/{role_id}", response_model=Message, summary="Delete a custom role")
def delete_role(
    role_id: uuid.UUID, principal: Principal = Depends(require("team:manage")), db: Session = Depends(get_db)
) -> Message:
    role = db.get(RoleDefinition, role_id)
    if role is None or role.organization_id != principal.organization_id:
        raise NotFound("Role not found")
    from aegis_api.models import Membership

    if db.scalar(
        select(Membership.id)
        .where(Membership.organization_id == principal.organization_id, Membership.role == role.key)
        .limit(1)
    ):
        raise Conflict("Role is assigned to members; reassign them first")
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="role.deleted",
        resource_type="role",
        resource_id=role.id,
        principal=principal,
    )
    db.delete(role)
    return Message(message="Role deleted")


# --- service accounts -----------------------------------------------------------------------------
def _assignable(db: Session, principal: Principal, role: str) -> None:
    target = None if is_system_role(role) else rbac_service.resolve_permissions(db, principal.organization_id, role)
    if not is_system_role(role) and not target:
        raise ValidationFailed(f"Unknown role '{role}'")
    if not can_assign_role(principal.role, role, target_permissions=target, actor_permissions=principal.permissions):
        raise Forbidden("You cannot assign a role above your own", code="privilege_escalation")


@router.get("/service-accounts", response_model=list[ServiceAccountOut], summary="List service accounts")
def list_service_accounts(
    principal: Principal = Depends(require("service_account:manage")), db: Session = Depends(get_db)
) -> list[ServiceAccountOut]:
    rows = db.scalars(
        select(ServiceAccount)
        .where(ServiceAccount.organization_id == principal.organization_id)
        .order_by(ServiceAccount.created_at)
    ).all()
    return [ServiceAccountOut.model_validate(r) for r in rows]


@router.post(
    "/service-accounts", response_model=ServiceAccountOut, status_code=201, summary="Create a scoped service account"
)
def create_service_account(
    body: ServiceAccountCreate,
    principal: Principal = Depends(require("service_account:manage")),
    db: Session = Depends(get_db),
) -> ServiceAccountOut:
    principal.require_human("service_account.create")
    _assignable(db, principal, body.role)
    bad = [s for s in body.scopes if s not in ALL_SCOPES]
    if bad:
        raise ValidationFailed(f"Unknown scopes: {', '.join(bad)}")
    for pid in body.project_ids:
        try:
            uuid.UUID(pid)
        except ValueError as exc:
            raise ValidationFailed("project_ids must be UUIDs") from exc
    if db.scalar(
        select(ServiceAccount.id).where(
            ServiceAccount.organization_id == principal.organization_id, ServiceAccount.name == body.name
        )
    ):
        raise Conflict("A service account with this name exists")
    sa = ServiceAccount(
        organization_id=principal.organization_id,
        name=body.name,
        description=body.description,
        role=body.role,
        scopes=body.scopes,
        project_ids=body.project_ids,
        created_by_id=principal.user_id,
    )
    db.add(sa)
    db.flush()
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="service_account.created",
        resource_type="service_account",
        resource_id=sa.id,
        principal=principal,
        after={"role": sa.role, "scopes": sa.scopes},
    )
    return ServiceAccountOut.model_validate(sa)


def _service_account(db: Session, principal: Principal, sa_id: uuid.UUID) -> ServiceAccount:
    sa = db.get(ServiceAccount, sa_id)
    if sa is None or sa.organization_id != principal.organization_id:
        raise NotFound("Service account not found")
    return sa


@router.patch(
    "/service-accounts/{sa_id}", response_model=ServiceAccountOut, summary="Update or disable a service account"
)
def update_service_account(
    sa_id: uuid.UUID,
    body: ServiceAccountUpdate,
    principal: Principal = Depends(require("service_account:manage")),
    db: Session = Depends(get_db),
) -> ServiceAccountOut:
    sa = _service_account(db, principal, sa_id)
    if body.description is not None:
        sa.description = body.description
    if body.project_ids is not None:
        sa.project_ids = body.project_ids
    if body.disabled is not None:
        sa.disabled_at = utcnow() if body.disabled else None
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="service_account.updated",
        resource_type="service_account",
        resource_id=sa.id,
        principal=principal,
        after=body.model_dump(exclude_none=True),
    )
    return ServiceAccountOut.model_validate(sa)


@router.post(
    "/service-accounts/{sa_id}/keys",
    response_model=ServiceAccountKeyCreated,
    status_code=201,
    summary="Issue an API key for a service account",
)
def create_service_account_key(
    sa_id: uuid.UUID,
    body: ServiceAccountKeyCreate,
    principal: Principal = Depends(require("service_account:manage")),
    db: Session = Depends(get_db),
) -> ServiceAccountKeyCreated:
    principal.require_human("service_account.key_create")
    sa = _service_account(db, principal, sa_id)
    if sa.disabled_at is not None:
        raise Conflict("Service account is disabled")
    scopes = body.scopes if body.scopes is not None else list(sa.scopes)
    if not set(scopes) <= set(sa.scopes):
        raise Forbidden("Key scopes cannot exceed the service account's scopes", code="privilege_escalation")
    issued = api_key_service.create_api_key(
        db,
        organization_id=principal.organization_id,
        name=body.name,
        role=sa.role,
        scopes=scopes,
        created_by_id=principal.user_id,
        expires_in_days=body.expires_in_days,
        service_account_id=sa.id,
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="api_key.created",
        resource_type="api_key",
        resource_id=issued.api_key.id,
        principal=principal,
        after={"service_account_id": str(sa.id)},
    )
    return ServiceAccountKeyCreated(api_key=ApiKeyOut.model_validate(issued.api_key), plaintext=issued.plaintext)


@router.post(
    "/api-keys/{key_id}/rotate", response_model=ApiKeyRotated, summary="Rotate an API key (new secret, old key revoked)"
)
def rotate_api_key(
    key_id: uuid.UUID, principal: Principal = Depends(require("api_keys:manage")), db: Session = Depends(get_db)
) -> ApiKeyRotated:
    record = db.get(ApiKey, key_id)
    if record is None or record.organization_id != principal.organization_id:
        raise NotFound("API key not found")
    issued = api_key_service.rotate_api_key(
        db,
        key_id,
        principal.organization_id,
        rotated_by_id=principal.user_id if principal.actor_type == "user" else None,
    )
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="api_key.rotated",
        resource_type="api_key",
        resource_id=key_id,
        principal=principal,
        after={"new_key_id": str(issued.api_key.id)},
    )
    return ApiKeyRotated(
        api_key=ApiKeyOut.model_validate(issued.api_key), plaintext=issued.plaintext, revoked_key_id=str(key_id)
    )


# --- SSO connections -----------------------------------------------------------------------------
def _sso_out(conn: SSOConnection) -> SSOConnectionOut:
    out = SSOConnectionOut.model_validate(conn)
    out.has_client_secret = conn.client_secret_id is not None
    return out


@router.get("/sso/connections", response_model=list[SSOConnectionOut], summary="List SSO connections")
def list_sso(
    principal: Principal = Depends(require("sso:manage")), db: Session = Depends(get_db)
) -> list[SSOConnectionOut]:
    rows = db.scalars(select(SSOConnection).where(SSOConnection.organization_id == principal.organization_id)).all()
    return [_sso_out(r) for r in rows]


@router.post(
    "/sso/connections", response_model=SSOConnectionOut, status_code=201, summary="Register an OIDC connection"
)
def create_sso(
    body: SSOConnectionCreate, principal: Principal = Depends(require("sso:manage")), db: Session = Depends(get_db)
) -> SSOConnectionOut:
    principal.require_human("sso.configure")
    _assignable(db, principal, body.default_role)
    secret_id = None
    if body.client_secret:
        secret_id = secrets_service.create_secret(
            db,
            organization_id=principal.organization_id,
            name=f"sso:{body.issuer}",
            value=body.client_secret,
            kind="oidc_client_secret",
            created_by_id=principal.user_id,
        ).id
    conn = SSOConnection(
        organization_id=principal.organization_id,
        protocol="oidc",
        issuer=body.issuer,
        client_id=body.client_id,
        client_secret_id=secret_id,
        email_domains=[d.lower() for d in body.email_domains],
        default_role=body.default_role,
        enabled=body.enabled,
    )
    db.add(conn)
    db.flush()
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="sso.connection_created",
        resource_type="sso_connection",
        resource_id=conn.id,
        principal=principal,
        after={"issuer": conn.issuer},
    )
    return _sso_out(conn)


# --- quotas & feature flags ------------------------------------------------------------------------
@router.get("/organization/quotas", response_model=QuotaOut, summary="Organization quotas and autonomy ceiling")
def get_quotas(principal: Principal = Depends(require("org:read")), db: Session = Depends(get_db)) -> QuotaOut:
    return QuotaOut.model_validate(quota_service.get_quota(db, principal.organization_id))


@router.put("/organization/quotas", response_model=QuotaOut, summary="Update organization quotas")
def update_quotas(
    body: QuotaUpdate, principal: Principal = Depends(require("org:manage")), db: Session = Depends(get_db)
) -> QuotaOut:
    principal.require_human("quota.update")
    quota = quota_service.get_quota(db, principal.organization_id)
    before = QuotaOut.model_validate(quota).model_dump()
    data = body.model_dump(exclude_none=True)
    if "max_autonomy_level" in data:
        from aegis_api.config import get_settings

        data["max_autonomy_level"] = validate_autonomy_change(
            principal.actor_type,
            quota.max_autonomy_level,
            data["max_autonomy_level"],
            max_allowed=get_settings().lab_platform_max_autonomy,
        )
    if "retention" in data:
        unknown = set(data["retention"]) - set(quota_service.RETENTION_CLASSES)
        if unknown:
            raise ValidationFailed(f"Unknown retention classes: {', '.join(sorted(unknown))}")
    for key, value in data.items():
        setattr(quota, key, value)
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="quota.updated",
        resource_type="organization_quota",
        resource_id=quota.id,
        principal=principal,
        before=before,
        after=data,
    )
    return QuotaOut.model_validate(quota)


@router.get("/feature-flags", response_model=dict[str, bool], summary="Effective feature flags for this workspace")
def get_flags(principal: Principal = Depends(require("org:read")), db: Session = Depends(get_db)) -> dict[str, bool]:
    return feature_service.flags(db, principal.organization_id)


@router.put(
    "/feature-flags/{key}", response_model=dict[str, bool], summary="Override a feature flag for this workspace"
)
def set_flag(
    key: str,
    body: FeatureFlagUpdate,
    principal: Principal = Depends(require("org:manage")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    principal.require_human("feature_flag.update")
    from aegis_api.config import get_settings

    if key not in get_settings().feature_defaults():
        raise NotFound("Unknown feature flag")
    feature_service.set_flag(db, principal.organization_id, key, body.enabled)
    audit_log.record(
        db,
        organization_id=principal.organization_id,
        action="feature_flag.updated",
        resource_type="feature_flag",
        resource_id=None,
        principal=principal,
        after={key: body.enabled},
    )
    return feature_service.flags(db, principal.organization_id)
