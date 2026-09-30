"""Authentication and account provisioning.

Two authentication backends are supported and can coexist:
  * **local** — self-hosted email/password with Argon2id and opaque, revocable server-side sessions
    (so no paid services are required for development or self-hosting);
  * **supabase** — Supabase Auth JWTs are verified and mapped to a local user by ``auth_subject``.

Both converge on the same ``users`` / ``memberships`` model. Guest demo sandboxes are ordinary users
flagged ``is_guest`` with a short-lived organization.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.errors import Conflict, Forbidden, Unauthorized, ValidationFailed
from aegis_api.models import AuthSession, Membership, Organization, User
from aegis_api.models.enums import MembershipStatus, Role
from aegis_api.security.passwords import hash_password, password_problems, verify_password
from aegis_api.security.supabase_jwt import verify_supabase_token
from aegis_api.security.tokens import keyed_hash, new_session_token

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class SessionResult:
    user: User
    membership: Membership
    organization: Organization
    session_token: str | None = None
    expires_at: datetime | None = None


def slugify(value: str, fallback: str = "workspace") -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or fallback


def unique_org_slug(session: Session, base: str) -> str:
    """A free slug: the plain slug if available, else the slug with a random suffix (O(1) queries, and
    concurrent sign-ups with the same organization name do not race onto the same sequential suffix)."""
    slug = slugify(base)[:70]
    if not session.scalar(select(Organization.id).where(Organization.slug == slug)):
        return slug
    for _ in range(5):
        candidate = f"{slug}-{secrets.token_hex(3)}"
        if not session.scalar(select(Organization.id).where(Organization.slug == candidate)):
            return candidate
    return f"{slug}-{secrets.token_hex(8)}"


def _provision_org(
    session: Session,
    *,
    name: str,
    user: User,
    is_demo: bool = False,
    is_sandbox: bool = False,
    ttl_hours: int | None = None,
) -> tuple[Organization, Membership]:
    org: Organization | None = None
    for attempt in range(3):
        candidate = Organization(
            name=name,
            slug=unique_org_slug(session, name),
            is_demo=is_demo,
            is_sandbox=is_sandbox,
            expires_at=(utcnow() + timedelta(hours=ttl_hours)) if ttl_hours else None,
            onboarding={"steps": {}, "completed": False},
        )
        try:
            # A concurrent sign-up may take the same slug between the check and the insert.
            with session.begin_nested():
                session.add(candidate)
                session.flush()
        except IntegrityError:
            if attempt == 2:
                raise
            continue
        org = candidate
        break
    assert org is not None
    membership = Membership(organization_id=org.id, user_id=user.id, role=Role.OWNER, status=MembershipStatus.ACTIVE)
    session.add(membership)
    user.default_organization_id = org.id
    session.flush()
    return org, membership


def signup(
    session: Session, *, email: str, password: str, full_name: str | None, org_name: str | None = None
) -> SessionResult:
    if not get_settings().local_auth_enabled:
        raise ValidationFailed("Local authentication is disabled on this deployment")
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValidationFailed("A valid email address is required")
    problems = password_problems(password)
    if problems:
        raise ValidationFailed("Password does not meet requirements: " + "; ".join(problems))
    if session.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise Conflict("An account with this email already exists")
    user = User(
        email=email,
        full_name=full_name,
        auth_provider="local",
        auth_subject=f"local:{uuid.uuid4()}",
        password_hash=hash_password(password),
        email_verified=False,
    )
    session.add(user)
    session.flush()
    org, membership = _provision_org(
        session, name=org_name or (full_name or email.split("@")[0]) + "'s Workspace", user=user
    )
    token, expires = _issue_session(session, user)
    return SessionResult(user=user, membership=membership, organization=org, session_token=token, expires_at=expires)


def login(session: Session, *, email: str, password: str) -> SessionResult:
    if not get_settings().local_auth_enabled:
        raise Unauthorized("Password authentication is disabled on this deployment", code="local_auth_disabled")
    email = email.strip().lower()
    user = session.scalar(select(User).where(func.lower(User.email) == email))
    # verify_password runs a dummy hash when there is no stored hash, keeping timing uniform.
    password_ok = verify_password(user.password_hash if user else None, password)
    if user is None or user.auth_provider != "local" or not password_ok:
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    membership = _primary_membership(session, user)
    if membership is None:
        raise Unauthorized("This account has no active workspace")
    org = session.get(Organization, membership.organization_id)
    assert org is not None
    if not user.is_platform_admin:
        if (org.settings or {}).get("suspended"):
            raise Forbidden("This organization has been suspended", code="organization_suspended")
        if _sso_enforced(session, org.id):
            raise Forbidden("This organization requires single sign-on", code="sso_required")
    user.last_active_at = utcnow()
    token, expires = _issue_session(session, user)
    return SessionResult(user=user, membership=membership, organization=org, session_token=token, expires_at=expires)


def create_guest_sandbox(session: Session, *, template_org_name: str = "Demo Sandbox") -> SessionResult:
    settings = get_settings()
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"guest-{suffix}@sandbox.aegis.local",
        full_name="Guest",
        auth_provider="guest",
        auth_subject=f"guest:{uuid.uuid4()}",
        is_guest=True,
        email_verified=False,
    )
    session.add(user)
    session.flush()
    org, membership = _provision_org(
        session,
        name=f"{template_org_name} {suffix}",
        user=user,
        is_sandbox=True,
        ttl_hours=settings.guest_session_ttl_hours,
    )
    membership.role = Role.ADMIN  # guests can explore but not manage billing/ownership transfer
    token, expires = _issue_session(session, user, guest=True)
    return SessionResult(user=user, membership=membership, organization=org, session_token=token, expires_at=expires)


def authenticate_supabase(session: Session, access_token: str) -> User:
    identity = verify_supabase_token(access_token)
    subject = f"supabase:{identity.subject}"
    user = session.scalar(select(User).where(User.auth_subject == subject))
    if user is None and identity.email:
        user = session.scalar(select(User).where(func.lower(User.email) == identity.email.lower()))
    if user is None:
        user = User(
            email=identity.email or f"{identity.subject}@supabase.local",
            full_name=identity.full_name,
            auth_provider="supabase",
            auth_subject=subject,
            email_verified=True,
        )
        session.add(user)
        session.flush()
        _provision_org(
            session, name=(identity.full_name or (identity.email or "New").split("@")[0]) + "'s Workspace", user=user
        )
    else:
        user.auth_subject = subject
        user.last_active_at = utcnow()
    return user


def _issue_session(session: Session, user: User, guest: bool = False) -> tuple[str, datetime]:
    settings = get_settings()
    token = new_session_token()
    ttl = settings.guest_session_ttl_hours if guest else settings.session_ttl_hours
    expires = utcnow() + timedelta(hours=ttl)
    session.add(
        AuthSession(
            user_id=user.id, token_hash=keyed_hash(token), expires_at=expires, is_guest=guest, last_used_at=utcnow()
        )
    )
    return token, expires


def resolve_session(session: Session, token: str) -> User | None:
    record = session.scalar(select(AuthSession).where(AuthSession.token_hash == keyed_hash(token)))
    if record is None or record.revoked_at is not None or record.expires_at < utcnow():
        return None
    record.last_used_at = utcnow()
    return session.get(User, record.user_id)


def revoke_session(session: Session, token: str) -> None:
    record = session.scalar(select(AuthSession).where(AuthSession.token_hash == keyed_hash(token)))
    if record and record.revoked_at is None:
        record.revoked_at = utcnow()


def _sso_enforced(session: Session, organization_id: uuid.UUID) -> bool:
    from aegis_api.lab.models import OrganizationSettings

    return bool(
        session.scalar(
            select(OrganizationSettings.sso_enforced).where(OrganizationSettings.organization_id == organization_id)
        )
    )


def _primary_membership(session: Session, user: User) -> Membership | None:
    if user.default_organization_id:
        m = session.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == user.default_organization_id,
                Membership.status != MembershipStatus.SUSPENDED,
            )
        )
        if m:
            return m
    return session.scalar(
        select(Membership)
        .where(Membership.user_id == user.id, Membership.status == MembershipStatus.ACTIVE)
        .order_by(Membership.created_at)
    )


def membership_for(session: Session, user: User, organization_id: uuid.UUID | None) -> Membership | None:
    if organization_id is not None:
        return session.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == organization_id,
                Membership.status != MembershipStatus.SUSPENDED,
            )
        )
    return _primary_membership(session, user)
