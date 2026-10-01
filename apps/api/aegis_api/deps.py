"""FastAPI dependencies: identity resolution, tenant-scoped DB sessions, and permission guards.

Identity is derived from (in order): ``Authorization: Bearer <api key>``, a session cookie, a session
bearer token, or a Supabase access token. The organization is ALWAYS derived from the authenticated
membership — never trusted from a client-supplied header/body. The requested org (``X-Aegis-Org`` header
or query) is only honoured if the user has an active membership in it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.session import session_factory, set_tenant
from aegis_api.errors import Forbidden, Unauthorized
from aegis_api.models import Organization, User
from aegis_api.models.enums import MembershipStatus
from aegis_api.ratelimit import client_ip, get_limiter
from aegis_api.security.context import Principal, build_permissions
from aegis_api.services import api_key_service, auth_service

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


def _extract_credentials(request: Request) -> tuple[str | None, str | None, str | None]:
    """Return (api_key, session_token, supabase_token)."""
    api_key = session_token = supabase_token = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token.startswith(("aeg_live_", "aeg_test_")):
            api_key = token
        elif token.startswith("aegs_"):
            session_token = token
        else:
            supabase_token = token
    if request.headers.get("x-api-key"):
        api_key = request.headers["x-api-key"].strip()
    if not session_token:
        session_token = request.cookies.get(SESSION_COOKIE)
    return api_key, session_token, supabase_token


def _requested_org(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("x-aegis-org") or request.query_params.get("organization_id")
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def get_current_principal(request: Request) -> Principal:
    """Resolve the caller. Uses a short-lived owner-connection session that is closed before the endpoint
    runs, so long-lived responses (SSE) never pin an identity-pool connection."""
    cached = getattr(request.state, "principal", None)
    if isinstance(cached, Principal):
        return cached
    session = session_factory(admin=True)()
    try:
        principal = _resolve_principal(request, session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    request.state.principal = principal
    return principal


def _resolve_principal(request: Request, db: Session) -> Principal:
    settings = get_settings()
    api_key, session_token, supabase_token = _extract_credentials(request)
    request_id = getattr(request.state, "request_id", None)
    identity = "ip:" + client_ip(request)

    principal: Principal | None = None
    if api_key:
        identity = "key:" + api_key[:12]
        record = api_key_service.verify_api_key(db, api_key)
        if record is None:
            _rate("public", identity)
            raise Unauthorized("Invalid or revoked API key", code="invalid_api_key")
        principal = Principal(
            user_id=record.created_by_id or uuid.UUID(int=0),
            organization_id=record.organization_id,
            role=record.role,
            permissions=build_permissions(record.role, record.scopes),
            auth_method="api_key",
            api_key_id=record.id,
            scopes=record.scopes,
            request_id=request_id,
        )
    else:
        user: User | None = None
        if session_token:
            user = auth_service.resolve_session(db, session_token)
        elif supabase_token and (settings.effective_supabase_url or settings.supabase_jwt_secret):
            user = auth_service.authenticate_supabase(db, supabase_token)
        if user is None:
            _rate("public", identity)
            raise Unauthorized("Authentication required")
        identity = f"user:{user.id}"
        membership = auth_service.membership_for(db, user, _requested_org(request))
        if membership is None or membership.status != MembershipStatus.ACTIVE:
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
        )
    _rate("authenticated", identity)
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
    return "ip:" + client_ip(request)
