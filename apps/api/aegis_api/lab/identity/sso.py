"""Enterprise SSO (OpenID Connect, authorization-code + PKCE). Feature flag: ``enterprise_sso``.

Configuration (``/sso/providers``) runs on the tenant session with ``org:manage``. The client secret is
stored encrypted in ``secrets`` and never returned.

Login runs on the identity-layer owner session (no tenant exists before authentication) in SHORT
transactions — never across the IdP HTTP calls:

1. ``start_login``: load the provider → fetch discovery (no transaction) → store the single-use state
   (hashed, 10 minutes) → return the authorization URL (state + nonce + PKCE S256).
2. ``complete_login``: consume the state (atomically) → exchange the code and validate the ID token
   (no transaction) → resolve/provision the account → create a normal session.

Account-linking rules (prevent cross-tenant account takeover by a malicious IdP configuration):

* an identity already linked to this provider (``auth_subject``) signs in;
* an existing account with the same email is linked only if it belongs to no organization other than
  the provider's (an IdP can never take over an account that has access elsewhere);
* otherwise a new account is created only when ``jit_provisioning`` is enabled;
* platform administrators can never sign in through an organization's IdP;
* the asserted email must be verified by the IdP and its domain must be one of ``email_domains``.

``kind="saml"`` providers can be stored (integration point) but ``start`` answers 501
``sso_protocol_not_supported``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import admin_session_scope
from aegis_api.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized, ValidationFailed
from aegis_api.lab.core.access import get_owned
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.audit import AuditAction, audit
from aegis_api.lab.core.features import ensure_feature, feature_enabled
from aegis_api.lab.identity import validators
from aegis_api.lab.identity.schemas import SSOProviderCreate, SSOProviderOut, SSOProviderUpdate
from aegis_api.lab.models import IdentityProvider
from aegis_api.models import AuthToken, Membership, Organization, Secret, User
from aegis_api.models.enums import MembershipStatus
from aegis_api.security import oidc
from aegis_api.security.rbac import can_assign_role, permissions_for_role
from aegis_api.security.tokens import constant_time_equals, keyed_hash, random_token, sha256_hex
from aegis_api.services import auth_service, secrets_service

log = structlog.get_logger("aegis.lab.sso")

FEATURE = "enterprise_sso"
STATE_PURPOSE = "sso_state"
STATE_TTL = timedelta(minutes=10)
STATE_SUBJECT_PREFIX = "sso:"
STATE_COOKIE = "aegis_sso_state"
CALLBACK_PATH = "/api/v1/auth/sso/callback"
STATE_PURGE_BATCH = 500


class SSOProtocolNotSupported(AppError):
    """This identity-provider protocol is not supported yet."""

    status_code = 501
    code = "sso_protocol_not_supported"


def redirect_uri() -> str:
    settings = get_settings()
    base = (settings.oidc_redirect_base_url or settings.api_base_url).rstrip("/")
    return f"{base}{CALLBACK_PATH}"


# --- configuration (tenant session) --------------------------------------------------------------
def provider_out(provider: IdentityProvider) -> SSOProviderOut:
    return SSOProviderOut(
        id=str(provider.id),
        kind=provider.kind,
        name=provider.name,
        issuer=provider.issuer,
        discovery_url=provider.discovery_url,
        client_id=provider.client_id,
        has_client_secret=provider.secret_id is not None,
        email_domains=list(provider.email_domains or []),
        scopes=list(provider.scopes or []),
        jit_provisioning=provider.jit_provisioning,
        default_role=provider.default_role,
        enabled=provider.enabled,
        redirect_uri=redirect_uri(),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _manage(db: Session, actor: Actor) -> None:
    actor.require("org:manage")
    actor.require_human("configuring single sign-on")
    ensure_feature(db, actor.organization_id, FEATURE)


def _default_role(actor: Actor, role: str) -> str:
    role = validators.validate_assignable_role(role)
    if not can_assign_role(actor.role or "", role):
        raise Forbidden("The default role cannot exceed your own role")
    return role


def _store_secret(db: Session, actor: Actor, provider_id: uuid.UUID, value: str) -> uuid.UUID:
    if not value or len(value) > 4096:
        raise ValidationFailed("client_secret must be 1-4096 characters")
    secret = secrets_service.create_secret(
        db,
        organization_id=actor.organization_id,
        name=f"sso-client-secret:{provider_id}",
        value=value,
        kind="sso_client_secret",
        created_by_id=actor.user_id,
    )
    return secret.id


def _validate_oidc_fields(provider: IdentityProvider) -> None:
    if provider.kind != "oidc":
        return
    if not provider.discovery_url or not provider.client_id:
        raise ValidationFailed("OIDC providers require discovery_url and client_id")
    provider.discovery_url = oidc.validate_idp_url(provider.discovery_url)
    if provider.issuer:
        oidc.validate_browser_url(provider.issuer)


def create_provider(db: Session, actor: Actor, data: SSOProviderCreate) -> IdentityProvider:
    _manage(db, actor)
    name = data.name.strip()
    if db.scalar(
        select(IdentityProvider.id).where(
            IdentityProvider.organization_id == actor.organization_id, IdentityProvider.name == name
        )
    ):
        raise Conflict("An identity provider with this name already exists")
    provider = IdentityProvider(
        id=uuid.uuid4(),
        organization_id=actor.organization_id,
        kind=data.kind,
        name=name,
        issuer=data.issuer,
        discovery_url=data.discovery_url,
        client_id=data.client_id,
        email_domains=validators.normalize_hostnames(
            data.email_domains, allow_suffix=False, limit=validators.MAX_EMAIL_DOMAINS
        ),
        scopes=oidc.require_scopes(data.scopes) if data.kind == "oidc" else list(data.scopes),
        jit_provisioning=data.jit_provisioning,
        default_role=_default_role(actor, data.default_role),
        enabled=data.enabled,
        settings={},
    )
    _validate_oidc_fields(provider)
    if data.client_secret is not None:
        provider.secret_id = _store_secret(db, actor, provider.id, data.client_secret.get_secret_value())
    db.add(provider)
    db.flush()
    audit(db, actor, "SSO_PROVIDER_CHANGED", "identity_provider", provider.id, after=_audit_view(provider, "created"))
    return provider


def _audit_view(provider: IdentityProvider, operation: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "name": provider.name,
        "kind": provider.kind,
        "enabled": provider.enabled,
        "email_domains": list(provider.email_domains or []),
        "jit_provisioning": provider.jit_provisioning,
        "default_role": provider.default_role,
        "has_client_secret": provider.secret_id is not None,
    }


def list_providers(db: Session, actor: Actor) -> list[IdentityProvider]:
    actor.require("org:manage")
    return list(
        db.scalars(
            select(IdentityProvider)
            .where(IdentityProvider.organization_id == actor.organization_id)
            .order_by(IdentityProvider.name.asc())
        ).all()
    )


def get_provider(db: Session, actor: Actor, provider_id: uuid.UUID | str) -> IdentityProvider:
    actor.require("org:manage")
    return get_owned(db, IdentityProvider, provider_id, actor, label="Identity provider")


def update_provider(
    db: Session, actor: Actor, provider_id: uuid.UUID | str, data: SSOProviderUpdate
) -> IdentityProvider:
    _manage(db, actor)
    provider = get_owned(db, IdentityProvider, provider_id, actor, label="Identity provider")
    before = _audit_view(provider, "before")
    if data.name is not None:
        provider.name = data.name.strip()
    if data.issuer is not None:
        provider.issuer = data.issuer or None
    if data.discovery_url is not None:
        provider.discovery_url = data.discovery_url
    if data.client_id is not None:
        provider.client_id = data.client_id
    if data.email_domains is not None:
        provider.email_domains = validators.normalize_hostnames(
            data.email_domains, allow_suffix=False, limit=validators.MAX_EMAIL_DOMAINS
        )
    if data.scopes is not None:
        provider.scopes = oidc.require_scopes(data.scopes) if provider.kind == "oidc" else list(data.scopes)
    if data.jit_provisioning is not None:
        provider.jit_provisioning = data.jit_provisioning
    if data.default_role is not None:
        provider.default_role = _default_role(actor, data.default_role)
    if data.enabled is not None:
        provider.enabled = data.enabled
    _validate_oidc_fields(provider)
    if data.client_secret is not None:
        provider.secret_id = _store_secret(db, actor, provider.id, data.client_secret.get_secret_value())
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        raise Conflict("An identity provider with this name already exists") from exc
    audit(
        db,
        actor,
        "SSO_PROVIDER_CHANGED",
        "identity_provider",
        provider.id,
        before=before,
        after=_audit_view(provider, "updated"),
    )
    return provider


def delete_provider(db: Session, actor: Actor, provider_id: uuid.UUID | str) -> None:
    _manage(db, actor)
    provider = get_owned(db, IdentityProvider, provider_id, actor, label="Identity provider")
    secret_id = provider.secret_id
    snapshot = _audit_view(provider, "deleted")
    db.delete(provider)
    db.flush()
    if secret_id is not None:
        secret = db.get(Secret, secret_id)
        if secret is not None and secret.organization_id == actor.organization_id:
            db.delete(secret)
    audit(db, actor, "SSO_PROVIDER_CHANGED", "identity_provider", provider.id, before=snapshot)
    db.flush()


# --- login flow (identity-layer owner session, short transactions) ---------------------------------
@dataclass(frozen=True)
class _ProviderSnapshot:
    id: uuid.UUID
    organization_id: uuid.UUID
    discovery_url: str
    issuer: str | None
    client_id: str
    scopes: list[str]
    email_domains: list[str]
    jit_provisioning: bool
    default_role: str
    client_secret: str | None


@dataclass
class SSOStart:
    authorization_url: str
    state: str
    expires_at: datetime


def _load_for_login(db: Session, provider_id: uuid.UUID, *, with_secret: bool) -> _ProviderSnapshot:
    provider = db.get(IdentityProvider, provider_id)
    if provider is None or not provider.enabled:
        raise NotFound("Identity provider not found")
    if not feature_enabled(db, provider.organization_id, FEATURE):
        raise NotFound("Identity provider not found")
    if provider.kind == "saml":
        raise SSOProtocolNotSupported(
            "SAML providers can be registered but SAML sign-in is not supported yet; use OIDC",
        )
    if not provider.discovery_url or not provider.client_id:
        raise ValidationFailed("The identity provider is not fully configured")
    secret = secrets_service.resolve_optional(db, provider.secret_id, provider.organization_id) if with_secret else None
    return _ProviderSnapshot(
        id=provider.id,
        organization_id=provider.organization_id,
        discovery_url=provider.discovery_url,
        issuer=provider.issuer,
        client_id=provider.client_id,
        scopes=list(provider.scopes or ["openid", "email", "profile"]),
        email_domains=list(provider.email_domains or []),
        jit_provisioning=provider.jit_provisioning,
        default_role=provider.default_role,
        client_secret=secret,
    )


def _discovery(snapshot: _ProviderSnapshot) -> oidc.DiscoveryDocument:
    discovery = oidc.fetch_discovery(snapshot.discovery_url)
    if snapshot.issuer and discovery.issuer != snapshot.issuer:
        raise oidc.OIDCProtocolError("The identity provider issuer does not match the configured issuer")
    return discovery


def _purge_expired_states(db: Session) -> None:
    """Bounded opportunistic cleanup so abandoned sign-in attempts never accumulate."""
    stale = (
        select(AuthToken.id)
        .where(AuthToken.purpose == STATE_PURPOSE, AuthToken.expires_at < utcnow() - STATE_TTL)
        .limit(STATE_PURGE_BATCH)
    )
    db.execute(delete(AuthToken).where(AuthToken.id.in_(stale)))


def start_login(provider_id: uuid.UUID) -> SSOStart:
    with admin_session_scope() as db:
        snapshot = _load_for_login(db, provider_id, with_secret=False)
    discovery = _discovery(snapshot)  # network I/O outside any transaction
    state = random_token(32)
    expires = utcnow() + STATE_TTL
    with admin_session_scope() as db:
        _purge_expired_states(db)
        db.add(
            AuthToken(
                user_id=None,
                email=f"{STATE_SUBJECT_PREFIX}{snapshot.id}",
                purpose=STATE_PURPOSE,
                token_hash=keyed_hash(state),
                expires_at=expires,
            )
        )
    url = oidc.build_authorization_url(
        discovery, client_id=snapshot.client_id, redirect_uri=redirect_uri(), scopes=snapshot.scopes, state=state
    )
    return SSOStart(authorization_url=url, state=state, expires_at=expires)


def _consume_state(db: Session, state: str) -> uuid.UUID:
    record = db.scalar(
        select(AuthToken)
        .where(AuthToken.token_hash == keyed_hash(state), AuthToken.purpose == STATE_PURPOSE)
        .with_for_update()
    )
    if record is None or record.used_at is not None or record.expires_at <= utcnow():
        raise Unauthorized("The sign-in attempt is invalid or has expired; start again", code="sso_state_invalid")
    record.used_at = utcnow()
    try:
        return uuid.UUID(record.email.removeprefix(STATE_SUBJECT_PREFIX))
    except ValueError as exc:
        raise Unauthorized("The sign-in attempt is invalid", code="sso_state_invalid") from exc


def _subject_key(provider_id: uuid.UUID, sub: str) -> str:
    key = f"oidc:{provider_id}:{sub}"
    return key if len(key) <= 128 else f"oidc:{provider_id}:sha256:{sha256_hex(sub)[:64]}"


def _email_domain_allowed(email: str, domains: list[str]) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return bool(domains) and domain in {d.lower() for d in domains}


def _resolve_account(
    db: Session, snapshot: _ProviderSnapshot, claims: dict[str, Any], email: str
) -> tuple[User, Membership, Organization]:
    org = db.get(Organization, snapshot.organization_id)
    if org is None:
        raise NotFound("Identity provider not found")
    if (org.settings or {}).get("suspended"):
        raise Forbidden("This organization has been suspended", code="organization_suspended")
    subject = _subject_key(snapshot.id, str(claims["sub"]))
    user = db.scalar(select(User).where(User.auth_subject == subject))
    if user is None:
        user = db.scalar(select(User).where(func.lower(User.email) == email))
        if user is not None:
            elsewhere = db.scalar(
                select(func.count(Membership.id)).where(
                    Membership.user_id == user.id,
                    Membership.organization_id != snapshot.organization_id,
                    Membership.status != MembershipStatus.SUSPENDED,
                )
            )
            if elsewhere or user.is_guest or user.is_platform_admin:
                raise Forbidden(
                    "An account with this email already exists; sign in with your existing method",
                    code="sso_account_link_forbidden",
                )
            user.auth_subject = subject
            user.email_verified = True
    if user is not None and user.is_platform_admin:
        raise Forbidden("Platform administrators cannot sign in through an organization identity provider")
    name = claims.get("name") if isinstance(claims.get("name"), str) else None
    if user is None:
        if not snapshot.jit_provisioning:
            raise Forbidden(
                "No account exists for this identity; ask an administrator to invite you",
                code="sso_user_not_provisioned",
            )
        user = User(
            email=email,
            full_name=(name or "")[:160] or None,
            auth_provider="sso",
            auth_subject=subject,
            email_verified=True,
        )
        db.add(user)
        db.flush()
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id, Membership.organization_id == snapshot.organization_id)
    )
    if membership is None:
        if not snapshot.jit_provisioning:
            raise Forbidden(
                "Your account is not a member of this organization; ask an administrator to invite you",
                code="sso_user_not_provisioned",
            )
        membership = Membership(
            organization_id=snapshot.organization_id,
            user_id=user.id,
            role=snapshot.default_role,
            status=MembershipStatus.ACTIVE,
        )
        db.add(membership)
    elif membership.status == MembershipStatus.SUSPENDED:
        raise Forbidden("Your membership in this organization is suspended")
    elif membership.status != MembershipStatus.ACTIVE:
        membership.status = MembershipStatus.ACTIVE  # an outstanding invitation is accepted by signing in
    user.default_organization_id = snapshot.organization_id
    user.last_active_at = utcnow()
    membership.last_active_at = utcnow()
    db.flush()
    return user, membership, org


def complete_login(*, code: str, state: str, cookie_state: str | None) -> auth_service.SessionResult:
    """Finish an OIDC sign-in and create a normal server-side session."""
    if not code or not state or len(code) > 4096 or len(state) > 512:
        raise ValidationFailed("code and state are required")
    # Binding the flow to the browser that started it prevents login CSRF / session fixation.
    if not cookie_state or not constant_time_equals(cookie_state, state):
        raise Unauthorized("The sign-in was started in a different browser session", code="sso_state_mismatch")
    with admin_session_scope() as db:
        provider_id = _consume_state(db, state)  # single use even if a later step fails
    with admin_session_scope() as db:
        snapshot = _load_for_login(db, provider_id, with_secret=True)

    # Network I/O outside any transaction.
    discovery = _discovery(snapshot)
    tokens = oidc.exchange_code(
        discovery,
        code=code,
        state=state,
        redirect_uri=redirect_uri(),
        client_id=snapshot.client_id,
        client_secret=snapshot.client_secret,
    )
    claims = oidc.validate_id_token(
        tokens["id_token"], discovery=discovery, client_id=snapshot.client_id, expected_nonce=oidc.nonce_for(state)
    )
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email or len(email) > 320:
        raise Forbidden("The identity provider did not assert an email address", code="sso_email_missing")
    email = email.strip().lower()
    verified = claims.get("email_verified")
    if verified is False or (isinstance(verified, str) and verified.strip().lower() == "false"):
        raise Forbidden("The identity provider has not verified this email address", code="sso_email_unverified")
    if not _email_domain_allowed(email, snapshot.email_domains):
        raise Forbidden("This email domain is not allowed for this identity provider", code="sso_domain_not_allowed")

    try:
        with admin_session_scope() as db:
            user, membership, org = _resolve_account(db, snapshot, claims, email)
            token, expires = auth_service._issue_session(db, user)
            actor = Actor(
                kind="user",
                organization_id=org.id,
                permissions=permissions_for_role(membership.role),
                label=user.email,
                user_id=user.id,
                role=membership.role,
                auth_method="sso",
            )
            audit(
                db,
                actor,
                AuditAction.LOGIN,
                "user",
                user.id,
                after={"method": "sso", "provider_id": snapshot.id, "issuer": discovery.issuer},
            )
            result = auth_service.SessionResult(
                user=user, membership=membership, organization=org, session_token=token, expires_at=expires
            )
    except IntegrityError as exc:  # a concurrent first sign-in for the same identity won the race
        raise Conflict("A concurrent sign-in for this account is in progress; please retry") from exc

    log.info("sso_login", provider_id=str(snapshot.id), organization_id=str(org.id))
    return result
