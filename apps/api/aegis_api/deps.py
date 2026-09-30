"""FastAPI dependencies: identity resolution, tenant-scoped DB sessions, and permission guards.

Identity is derived from (in order): an API key (``Authorization: Bearer aeg_live_…`` / ``X-API-Key``),
a lab JWT access token (``Authorization: Bearer <jwt>`` whose issuer is ``JWT_ISSUER``), a session cookie
or session bearer token, or a Supabase access token. The organization is ALWAYS derived from the
authenticated membership / credential — never trusted from a client-supplied header/body. The requested
org (``X-Aegis-Org`` header or query) is only honoured for session users with an active membership in it;
a JWT is bound to the organization in its ``org`` claim.

API keys bound to a service account authenticate as that account (its current role, scopes and project
restriction; human-only permissions stripped). Every principal of a suspended organization is rejected
with 403 ``organization_suspended`` (platform administrators excepted).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.errors import Forbidden, Unauthorized
from aegis_api.models import ApiKey, Membership, Organization, User
from aegis_api.models.enums import MembershipStatus
from aegis_api.ratelimit import get_limiter
from aegis_api.security import jwt_tokens
from aegis_api.security.context import Principal, build_permissions
from aegis_api.security.rbac import apply_scopes, permissions_for_role
from aegis_api.services import api_key_service, auth_service

SESSION_COOKIE = "aegis_session"
NIL_USER_ID = uuid.UUID(int=0)
_LAST_USED_RESOLUTION = timedelta(seconds=60)


def _raw_session() -> Iterator[Session]:
    """Owner-connection session for the identity/auth layer (organization provisioning, session and
    API-key resolution) which must operate before a tenant context exists. Tenant DATA access always
    goes through ``get_db`` on the RLS-enforced application role."""
    session = session_factory(admin=True)()
    try:
        yield session
    finally:
        session.close()


def _extract_credentials(request: Request) -> tuple[str | None, str | None, str | None]:
    """Return (api_key, session_token, bearer_token). ``bearer_token`` is a JWT (lab or Supabase)."""
    api_key = session_token = bearer_token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token.startswith(("aeg_live_", "aeg_test_")):
            api_key = token
        elif token.startswith("aegs_"):
            session_token = token
        else:
            bearer_token = token
    if request.headers.get("x-api-key"):
        api_key = request.headers["x-api-key"].strip()
    if not session_token:
        session_token = request.cookies.get(SESSION_COOKIE)
    return api_key, session_token, bearer_token


def _requested_org(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("x-aegis-org") or request.query_params.get("organization_id")
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _api_key_principal(db: Session, record: ApiKey, request_id: str | None) -> Principal:
    if record.service_account_id is None:
        return Principal(
            user_id=record.created_by_id or NIL_USER_ID,
            organization_id=record.organization_id,
            role=record.role,
            permissions=build_permissions(record.role, record.scopes),
            auth_method="api_key",
            api_key_id=record.id,
            scopes=record.scopes,
            request_id=request_id,
        )
    from aegis_api.lab.models import ServiceAccount

    account = db.get(ServiceAccount, record.service_account_id)
    if account is None or account.organization_id != record.organization_id or account.disabled_at is not None:
        raise Unauthorized("The service account for this API key is disabled", code="service_account_disabled")
    now = utcnow()
    if account.last_used_at is None or now - account.last_used_at > _LAST_USED_RESOLUTION:
        account.last_used_at = now
    # The account's CURRENT role/scopes apply, narrowed by what the key was issued with — narrowing an
    # account takes effect immediately and widening it never silently upgrades existing keys.
    scopes = [s for s in (account.scopes or []) if s in (record.scopes or [])]
    permissions = apply_scopes(permissions_for_role(account.role), scopes) & apply_scopes(
        permissions_for_role(record.role), scopes
    )
    return Principal(
        user_id=NIL_USER_ID,  # a service account is not a person; never inherit its creator's identity
        organization_id=record.organization_id,
        role=account.role,
        permissions=permissions,
        display_name=account.name,
        auth_method="service_account",
        api_key_id=record.id,
        scopes=scopes,
        service_account_id=account.id,
        project_ids=[str(p) for p in (account.project_ids or [])],
        request_id=request_id,
    )


def _jwt_principal(db: Session, token: str, request_id: str | None) -> Principal:
    from aegis_api.lab.identity.tokens import family_is_active

    claims = jwt_tokens.decode_access_token(token)
    user = db.get(User, claims.user_id)
    if user is None:
        raise Unauthorized("Invalid access token", code="invalid_token")
    if claims.session_id is not None and not family_is_active(db, claims.session_id, user.id):
        raise Unauthorized("This sign-in has been revoked", code="session_revoked")
    membership = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.organization_id == claims.organization_id,
            Membership.status == MembershipStatus.ACTIVE,
        )
    )
    if membership is None:
        raise Forbidden("You do not have access to this organization")
    # Permissions follow the CURRENT membership role (a role change applies before the token expires).
    return Principal(
        user_id=user.id,
        organization_id=membership.organization_id,
        role=membership.role,
        permissions=build_permissions(membership.role, None),
        email=user.email,
        display_name=user.full_name,
        auth_method="jwt",
        is_guest=user.is_guest,
        request_id=request_id,
        is_platform_admin=user.is_platform_admin,
    )


def _enforce_organization_active(db: Session, principal: Principal) -> None:
    if principal.is_platform_admin:
        return
    org = db.get(Organization, principal.organization_id)
    if org is None:
        raise Forbidden("Workspace not found")
    if (org.settings or {}).get("suspended"):
        raise Forbidden("This organization has been suspended", code="organization_suspended")


def get_current_principal(
    request: Request,
    db: Session = Depends(_raw_session),
) -> Principal:
    """Resolve the caller. The identity (owner-connection) session is committed and CLOSED before the endpoint
    runs, so no admin-pool connection is held for the rest of the request (e.g. during SSE streams). The
    session object stays usable: endpoints that also depend on ``_raw_session`` transparently get a new
    connection/transaction on first use."""
    try:
        principal = _resolve_principal(request, db)
        db.commit()
        return principal
    finally:
        db.close()


def _resolve_principal(request: Request, db: Session) -> Principal:
    settings = get_settings()
    api_key, session_token, bearer_token = _extract_credentials(request)
    request_id = getattr(request.state, "request_id", None)
    identity = "ip:" + (request.client.host if request.client else "unknown")

    principal: Principal | None = None
    if api_key:
        identity = "key:" + api_key[:12]
        record = api_key_service.verify_api_key(db, api_key)
        if record is None:
            db.commit()
            _rate("public", identity)
            raise Unauthorized("Invalid or revoked API key", code="invalid_api_key")
        try:
            principal = _api_key_principal(db, record, request_id)
        except Unauthorized:
            db.rollback()
            _rate("public", identity)
            raise
        db.commit()
    elif bearer_token and jwt_tokens.looks_like_access_token(bearer_token):
        try:
            principal = _jwt_principal(db, bearer_token, request_id)
        except Unauthorized:
            _rate("public", identity)
            raise
        identity = f"user:{principal.user_id}"
    else:
        user: User | None = None
        if session_token:
            user = auth_service.resolve_session(db, session_token)
            db.commit()
        elif bearer_token and (settings.effective_supabase_url or settings.supabase_jwt_secret):
            user = auth_service.authenticate_supabase(db, bearer_token)
            db.commit()
        if user is None:
            _rate("public", identity)
            raise Unauthorized("Authentication required")
        identity = f"user:{user.id}"
        membership = auth_service.membership_for(db, user, _requested_org(request))
        if membership is None or membership.status == MembershipStatus.SUSPENDED:
            raise Forbidden("You do not have access to this workspace")
        principal = Principal(
            user_id=user.id,
            organization_id=membership.organization_id,
            role=membership.role,
            permissions=build_permissions(membership.role, None),
            email=user.email,
            display_name=user.full_name,
            auth_method="guest" if user.is_guest else ("supabase" if user.auth_provider == "supabase" else "session"),
            is_guest=user.is_guest,
            request_id=request_id,
            is_platform_admin=user.is_platform_admin,
        )
    _enforce_organization_active(db, principal)
    _rate("authenticated", identity)
    request.state.principal = principal
    return principal


def _rate(tier: str, identity: str) -> None:
    get_limiter().check(tier, identity)  # type: ignore[arg-type]


def get_db(
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> Iterator[Session]:
    """A DB session bound to the principal's organization so RLS policies apply."""
    session = session_factory()()
    session.begin()
    set_tenant(session, principal.organization_id, principal.user_id)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require(*permissions: str):
    def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        principal.require(*permissions)
        return principal

    return _dep


def get_organization(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Organization:
    org = db.get(Organization, principal.organization_id)
    if org is None:
        raise Forbidden("Workspace not found")
    return org


def principal_identity(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return f"org:{principal.organization_id}"
    return "ip:" + (request.client.host if request.client else "unknown")
