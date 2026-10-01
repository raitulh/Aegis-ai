"""Request-scoped dependencies: database session and the authenticated actor."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import Forbidden, Unauthenticated
from app.core.logging import user_id_var
from app.core.security import hash_token
from app.core.time import utcnow
from app.models.enums import MembershipStatus, OrgRole, PlatformRole, UserStatus
from app.models.org import OrgMembership
from app.models.user import AuthSession, PlatformRoleAssignment, User

SESSION_COOKIE = "db_session"
CSRF_COOKIE = "db_csrf"
CSRF_HEADER = "x-csrf-token"


@dataclass
class Actor:
    """The caller of a request. Authorization helpers in app.core.permissions take an Actor."""

    db: Session
    user: User | None = None
    session_id: uuid.UUID | None = None
    auth_via: str | None = None  # "cookie" | "bearer"
    platform_roles: set[str] = field(default_factory=set)
    _memberships: dict[uuid.UUID, OrgMembership] | None = None
    _staff_cache: dict[uuid.UUID, set[str]] = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def id(self) -> uuid.UUID | None:
        return self.user.id if self.user else None

    @property
    def is_active(self) -> bool:
        return self.user is not None and self.user.status == UserStatus.active

    @property
    def is_admin(self) -> bool:
        return PlatformRole.platform_admin in self.platform_roles

    @property
    def is_moderator(self) -> bool:
        return self.is_admin or PlatformRole.moderator in self.platform_roles

    def memberships(self) -> dict[uuid.UUID, OrgMembership]:
        if self._memberships is None:
            if not self.user:
                self._memberships = {}
            else:
                rows = self.db.scalars(
                    select(OrgMembership).where(
                        OrgMembership.user_id == self.user.id, OrgMembership.status == MembershipStatus.active
                    )
                ).all()
                self._memberships = {m.org_id: m for m in rows}
        return self._memberships

    def member_org_ids(self) -> list[uuid.UUID]:
        return list(self.memberships().keys())

    def org_role(self, org_id: uuid.UUID | None) -> str | None:
        if org_id is None:
            return None
        m = self.memberships().get(org_id)
        return m.role if m else None

    def is_org_member(self, org_id: uuid.UUID | None) -> bool:
        return self.org_role(org_id) is not None

    def is_org_admin(self, org_id: uuid.UUID | None) -> bool:
        return self.is_admin or self.org_role(org_id) in (OrgRole.owner, OrgRole.admin)

    def is_org_manager(self, org_id: uuid.UUID | None) -> bool:
        return self.is_admin or self.org_role(org_id) in (OrgRole.owner, OrgRole.admin, OrgRole.manager)

    def invalidate_memberships(self) -> None:
        self._memberships = None


def _extract_token(request: Request) -> tuple[str | None, str | None]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None, "bearer"
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie, "cookie"
    return None, None


def get_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    token, via = _extract_token(request)
    actor = Actor(db=db)
    if not token:
        return actor
    now = utcnow()
    session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    if session is None:
        return actor
    user = db.get(User, session.user_id)
    if user is None or user.status == UserStatus.deleted:
        return actor
    actor.user = user
    actor.session_id = session.id
    actor.auth_via = via
    actor.platform_roles = set(db.scalars(select(PlatformRoleAssignment.role).where(PlatformRoleAssignment.user_id == user.id)))
    if now - session.last_seen_at > timedelta(minutes=5):
        db.execute(update(AuthSession).where(AuthSession.id == session.id).values(last_seen_at=now))
        db.commit()
    request.state.actor_id = str(user.id)
    user_id_var.set(str(user.id))
    return actor


def require_user(actor: Actor = Depends(get_actor)) -> Actor:
    if not actor.is_authenticated:
        raise Unauthenticated()
    if not actor.is_active:
        raise Forbidden("Your account is suspended. Contact support if you believe this is a mistake.",
                        code="account_suspended")
    return actor


def require_verified_user(actor: Actor = Depends(require_user)) -> Actor:
    assert actor.user is not None
    if actor.user.email_verified_at is None:
        raise Forbidden("Please verify your email address first.", code="email_not_verified")
    return actor


def require_moderator(actor: Actor = Depends(require_user)) -> Actor:
    if not actor.is_moderator:
        raise Forbidden()
    return actor


def require_admin(actor: Actor = Depends(require_user)) -> Actor:
    if not actor.is_admin:
        raise Forbidden()
    return actor
