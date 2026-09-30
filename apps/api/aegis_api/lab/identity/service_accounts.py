"""Service accounts (non-human identities for enterprise automation) and API key rotation.

A service account authenticates with API keys bound to it (``api_keys.service_account_id``). Its effective
permissions are resolved at request time in ``aegis_api.deps`` from the account's *current* role, scopes and
project restriction (narrowed further by the key's own role/scopes), so disabling or narrowing an account
takes effect immediately. Human-only permissions are never granted to it (``rbac.apply_scopes``).

``api_keys`` is identity-layer data without RLS: every query here filters by the actor's organization.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, NotFound
from aegis_api.lab.core.access import get_owned
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.pagination import paginate
from aegis_api.lab.identity import validators
from aegis_api.lab.identity.schemas import (
    ServiceAccountCreate,
    ServiceAccountKeyCreate,
    ServiceAccountOut,
    ServiceAccountUpdate,
)
from aegis_api.lab.models import Project, ServiceAccount
from aegis_api.models import ApiKey
from aegis_api.schemas.common import Page, PageParams
from aegis_api.security.rbac import ALL_SCOPES, can_assign_role
from aegis_api.security.tokens import API_KEY_TEST_PREFIX
from aegis_api.services import api_key_service
from aegis_api.services.api_key_service import IssuedKey


@dataclass
class RotatedKey:
    issued: IssuedKey
    previous: ApiKey


def _manage(actor: Actor) -> None:
    actor.require("service_account:manage")
    actor.require_human("managing service accounts")


def _check_role(actor: Actor, role: str) -> str:
    role = validators.validate_assignable_role(role)
    if not can_assign_role(actor.role or "", role):
        raise Forbidden("You cannot assign a role above your own")
    return role


def _scopes(scopes: list[str]) -> list[str]:
    ordered = [s for s in ALL_SCOPES if s in set(scopes)]
    return ordered or ["read"]


def _project_ids(db: Session, actor: Actor, project_ids: list[uuid.UUID]) -> list[str]:
    result: list[str] = []
    for project_id in project_ids:
        project = get_owned(db, Project, project_id, actor, label="Project")
        if str(project.id) not in result:
            result.append(str(project.id))
    return result


def create_service_account(db: Session, actor: Actor, data: ServiceAccountCreate) -> ServiceAccount:
    _manage(actor)
    name = data.name.strip()
    if db.scalar(
        select(ServiceAccount.id).where(
            ServiceAccount.organization_id == actor.organization_id, ServiceAccount.name == name
        )
    ):
        raise Conflict("A service account with this name already exists")
    account = ServiceAccount(
        organization_id=actor.organization_id,
        name=name,
        description=data.description,
        role=_check_role(actor, data.role),
        scopes=_scopes(list(data.scopes)),
        project_ids=_project_ids(db, actor, data.project_ids),
        created_by_id=actor.user_id,
    )
    db.add(account)
    db.flush()
    audit(
        db,
        actor,
        AuditAction.SERVICE_ACCOUNT_CREATED,
        "service_account",
        account.id,
        after={"name": name, "role": account.role, "scopes": account.scopes, "project_ids": account.project_ids},
    )
    return account


def list_service_accounts(db: Session, actor: Actor, params: PageParams, *, include_disabled: bool = True) -> Page:
    _manage(actor)
    stmt = select(ServiceAccount).where(ServiceAccount.organization_id == actor.organization_id)
    if not include_disabled:
        stmt = stmt.where(ServiceAccount.disabled_at.is_(None))
    return paginate(
        db, stmt.order_by(ServiceAccount.name.asc(), ServiceAccount.id.asc()), params, ServiceAccountOut.model_validate
    )


def get_service_account(db: Session, actor: Actor, account_id: uuid.UUID | str) -> ServiceAccount:
    _manage(actor)
    return get_owned(db, ServiceAccount, account_id, actor, label="Service account")


def update_service_account(
    db: Session, actor: Actor, account_id: uuid.UUID | str, data: ServiceAccountUpdate
) -> ServiceAccount:
    account = get_service_account(db, actor, account_id)
    if account.disabled_at is not None:
        raise Conflict("The service account is disabled", code="service_account_disabled")
    # Managing an account whose role is above your own would let you mint credentials for it.
    _check_role(actor, account.role)
    before = {"role": account.role, "scopes": list(account.scopes), "project_ids": list(account.project_ids)}
    changes = data.model_dump(exclude_unset=True, mode="json")
    if "description" in changes:
        account.description = data.description
    if data.role is not None:
        account.role = _check_role(actor, data.role)
    if data.scopes is not None:
        account.scopes = _scopes(list(data.scopes))
    if data.project_ids is not None:
        account.project_ids = _project_ids(db, actor, data.project_ids)
    audit(
        db,
        actor,
        "SERVICE_ACCOUNT_UPDATED",
        "service_account",
        account.id,
        before=before,
        after={"role": account.role, "scopes": account.scopes, "project_ids": account.project_ids},
    )
    db.flush()
    return account


def disable_service_account(db: Session, actor: Actor, account_id: uuid.UUID | str) -> ServiceAccount:
    """Disable an account: every key bound to it stops authenticating immediately (401)."""
    account = get_service_account(db, actor, account_id)
    if account.disabled_at is not None:
        raise Conflict("The service account is already disabled", code="service_account_disabled")
    _check_role(actor, account.role)
    account.disabled_at = utcnow()
    active_keys = db.scalar(
        select(func.count(ApiKey.id)).where(
            ApiKey.organization_id == actor.organization_id,
            ApiKey.service_account_id == account.id,
            ApiKey.revoked_at.is_(None),
        )
    )
    audit(
        db,
        actor,
        AuditAction.SERVICE_ACCOUNT_DISABLED,
        "service_account",
        account.id,
        after={"disabled_at": account.disabled_at, "keys_blocked": int(active_keys or 0)},
    )
    db.flush()
    return account


def issue_service_account_key(
    db: Session, actor: Actor, account_id: uuid.UUID | str, data: ServiceAccountKeyCreate
) -> IssuedKey:
    """Issue an API key bound to the account (role/scopes copied from it). The plaintext is returned once."""
    account = get_service_account(db, actor, account_id)
    if account.disabled_at is not None:
        raise Conflict("The service account is disabled", code="service_account_disabled")
    _check_role(actor, account.role)
    issued = api_key_service.create_api_key(
        db,
        organization_id=actor.organization_id,
        name=(data.name or f"{account.name} key")[:120],
        role=account.role,
        scopes=list(account.scopes),
        created_by_id=actor.user_id,
        expires_in_days=data.expires_in_days,
    )
    issued.api_key.service_account_id = account.id
    db.flush()
    audit(
        db,
        actor,
        "SERVICE_ACCOUNT_KEY_ISSUED",
        "service_account",
        account.id,
        after={
            "api_key_id": issued.api_key.id,
            "prefix": issued.api_key.prefix,
            "expires_at": issued.api_key.expires_at,
        },
    )
    return issued


def list_service_account_keys(db: Session, actor: Actor, account_id: uuid.UUID | str) -> list[ApiKey]:
    account = get_service_account(db, actor, account_id)
    return list(
        db.scalars(
            select(ApiKey)
            .where(ApiKey.organization_id == actor.organization_id, ApiKey.service_account_id == account.id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        ).all()
    )


def rotate_api_key(db: Session, actor: Actor, key_id: uuid.UUID | str, *, grace_seconds: int) -> RotatedKey:
    """Replace a key with a new one (same name/role/scopes/service account).

    The previous key is revoked immediately (``grace_seconds == 0``) or keeps working until
    ``now + grace_seconds`` (never extending an earlier expiry). Keys of other organizations → 404.
    """
    actor.require("api_keys:manage")
    actor.require_human("rotating API keys")
    try:
        key_uuid = key_id if isinstance(key_id, uuid.UUID) else uuid.UUID(str(key_id))
    except ValueError as exc:
        raise NotFound("API key not found") from exc
    previous = db.scalar(
        select(ApiKey).where(ApiKey.id == key_uuid, ApiKey.organization_id == actor.organization_id).with_for_update()
    )
    if previous is None:
        raise NotFound("API key not found")
    now = utcnow()
    if previous.revoked_at is not None:
        raise Conflict("The API key has been revoked and cannot be rotated", code="api_key_revoked")
    if previous.expires_at is not None and previous.expires_at <= now:
        raise Conflict("The API key has expired and cannot be rotated", code="api_key_expired")
    if not can_assign_role(actor.role or "", previous.role):
        raise Forbidden("You cannot rotate a key whose role is above your own")
    if previous.service_account_id is not None:
        account = get_owned(db, ServiceAccount, previous.service_account_id, actor, label="Service account")
        if account.disabled_at is not None:
            raise Conflict("The key's service account is disabled", code="service_account_disabled")
    new_expiry: datetime | None = None
    if previous.expires_at is not None:
        # Keep the original lifetime for the replacement key.
        new_expiry = now + max(previous.expires_at - previous.created_at, timedelta(seconds=60))
    issued = api_key_service.create_api_key(
        db,
        organization_id=actor.organization_id,
        name=previous.name,
        role=previous.role,
        scopes=list(previous.scopes),
        created_by_id=previous.created_by_id,
        test=previous.prefix.startswith(API_KEY_TEST_PREFIX),
    )
    issued.api_key.service_account_id = previous.service_account_id
    issued.api_key.rotated_from_id = previous.id
    issued.api_key.expires_at = new_expiry
    if grace_seconds <= 0:
        previous.revoked_at = now
    else:
        grace_end = now + timedelta(seconds=grace_seconds)
        previous.expires_at = min(previous.expires_at, grace_end) if previous.expires_at else grace_end
    db.flush()
    audit(
        db,
        actor,
        AuditAction.API_KEY_ROTATED,
        "api_key",
        previous.id,
        before={"api_key_id": previous.id, "prefix": previous.prefix},
        after={
            "new_api_key_id": issued.api_key.id,
            "new_prefix": issued.api_key.prefix,
            "grace_seconds": grace_seconds,
            "previous_expires_at": previous.expires_at,
            "previous_revoked": previous.revoked_at is not None,
        },
    )
    return RotatedKey(issued=issued, previous=previous)
