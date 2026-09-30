"""FastAPI dependencies: identity resolution, tenant-scoped DB sessions, and permission guards.

Identity is derived from (in order): ``Authorization: Bearer <api key>`` (user or service-account key), a
platform JWT access token, a session cookie / session bearer token, or a Supabase access token. The organization is ALWAYS derived from the authenticated
membership — never trusted from a client-supplied header/body. The requested org (``X-Aegis-Org`` header
or query) is only honoured if the user has an active membership in it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Literal

import structlog
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.errors import Forbidden, Unauthorized
from aegis_api.models import Organization, ServiceAccount, User
from aegis_api.models.enums import MembershipStatus
from aegis_api.ratelimit import Tier, get_limiter
from aegis_api.security.context import Principal, build_permissions
from aegis_api.security.jwt_tokens import decode_access_token, looks_like_platform_jwt
from aegis_api.security.rbac import is_system_role
from aegis_api.services import api_key_service, auth_service, rbac_service, token_service

SESSION_COOKIE = "aegis_session"


def _raw_session() -> Iterator[Session]:
    """Owner-connection session for the identity/auth layer (organization provisioning, session and
    API-key resolution) which must operate before a tenant context exists. Tenant DATA access always
    goes through ``get_db`` on the RLS-enforced application role."""
    session = session_factory(admin=True)()
    try:
        yield session
    finally:
        session.close()


def _extract_credentials(request: Request) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (api_key, session_token, access_jwt, supabase_token)."""
    api_key = session_token = access_jwt = supabase_token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token.startswith(("aeg_live_", "aeg_test_")):
            api_key = token
        elif token.startswith("aegs_"):
            session_token = token
        elif looks_like_platform_jwt(token):
            access_jwt = token
        else:
            supabase_token = token
    if request.headers.get("x-api-key"):
        api_key = request.headers["x-api-key"].strip()
    if not session_token and not access_jwt:
        session_token = request.cookies.get(SESSION_COOKIE)
    return api_key, session_token, access_jwt, supabase_token


def _requested_org(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("x-aegis-org") or request.query_params.get("organization_id")
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def get_current_principal(
    request: Request,
    db: Session = Depends(_raw_session),
) -> Principal:
    settings = get_settings()
    api_key, session_token, access_jwt, supabase_token = _extract_credentials(request)
    request_id = getattr(request.state, "request_id", None)
    identity = "ip:" + (request.client.host if request.client else "unknown")

    principal: Principal | None = None
    if api_key:
        identity = "key:" + api_key[:12]
        record = api_key_service.verify_api_key(db, api_key)
        db.commit()
        if record is None:
            _rate("public", identity)
            raise Unauthorized("Invalid or revoked API key", code="invalid_api_key")
        service_account = None
        if record.service_account_id is not None:
            service_account = db.get(ServiceAccount, record.service_account_id)
            if service_account is None or service_account.disabled_at is not None:
                raise Unauthorized("Service account is disabled", code="invalid_api_key")
        role = service_account.role if service_account else record.role
        custom = None if is_system_role(role) else rbac_service.resolve_permissions(db, record.organization_id, role)
        principal = Principal(
            user_id=(service_account.created_by_id if service_account else record.created_by_id) or uuid.UUID(int=0),
            organization_id=record.organization_id,
            role=role,
            permissions=build_permissions(role, record.scopes, custom),
            auth_method="service_account" if service_account else "api_key",
            api_key_id=record.id,
            service_account_id=service_account.id if service_account else None,
            scopes=record.scopes,
            request_id=request_id,
            project_ids=frozenset(service_account.project_ids) if service_account else frozenset(),
        )
    else:
        user: User | None = None
        family_id: uuid.UUID | None = None
        requested_org = _requested_org(request)
        auth_method = "session"
        if access_jwt:
            claims = decode_access_token(access_jwt)
            if not token_service.family_active(db, claims.family_id):
                raise Unauthorized("Session has been revoked", code="token_revoked")
            user = db.get(User, claims.user_id)
            family_id = claims.family_id
            requested_org = claims.organization_id  # a token is bound to exactly one organization
            auth_method = "jwt"
        elif session_token:
            user = auth_service.resolve_session(db, session_token)
            db.commit()
        elif supabase_token and (settings.effective_supabase_url or settings.supabase_jwt_secret):
            user = auth_service.authenticate_supabase(db, supabase_token)
            db.commit()
            auth_method = "supabase"
        if user is None:
            _rate("public", identity)
            raise Unauthorized("Authentication required")
        identity = f"user:{user.id}"
        membership = auth_service.membership_for(db, user, requested_org)
        if membership is None or membership.status == MembershipStatus.SUSPENDED:
            raise Forbidden("You do not have access to this workspace")
        custom = (
            None
            if is_system_role(membership.role)
            else rbac_service.resolve_permissions(db, membership.organization_id, membership.role)
        )
        principal = Principal(
            user_id=user.id,
            organization_id=membership.organization_id,
            role=membership.role,
            permissions=build_permissions(membership.role, None, custom),
            email=user.email,
            display_name=user.full_name,
            auth_method="guest" if user.is_guest else auth_method,
            token_family_id=family_id,
            is_guest=user.is_guest,
            request_id=request_id,
        )
    _rate("authenticated", identity)
    request.state.principal = principal
    structlog.contextvars.bind_contextvars(
        tenant_id=str(principal.organization_id), user_id=principal.actor_id, auth_method=principal.auth_method
    )
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


def rate_limited(tier: Tier, *, per: Literal["org", "user", "key"] = "org") -> Callable[..., None]:
    """Rate-limit an endpoint by tenant, user or API key. The route path is part of the bucket key so one
    expensive endpoint cannot starve another's budget."""

    def _dep(request: Request, principal: Principal = Depends(get_current_principal)) -> None:
        if per == "user":
            identity = f"user:{principal.actor_id}"
        elif per == "key" and principal.api_key_id is not None:
            identity = f"key:{principal.api_key_id}"
        else:
            identity = f"org:{principal.organization_id}"
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        get_limiter().check(tier, f"{identity}:{request.method}:{path}")

    return _dep
