"""Organizations: universities, clubs, communities, sponsors and companies.

Membership trust model
----------------------
* A membership is *verified* (``verified_at`` set) only through: institutional email on a domain the
  platform has verified for the organization, an invite created by an org admin, or explicit approval by
  an org admin. Self-declared affiliation on a profile is never shown as verified.
* ``email_domains`` can only be changed by platform admins, and domain verification only works for
  organizations whose own verification status is ``verified`` — otherwise anyone could create
  "University X" with ``gmail.com`` and self-verify.
* Admin analytics are aggregate. Rosters show handles/names/roles only — never personal emails.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.ids import slugify
from app.core.markdown import render_markdown
from app.core.pagination import PageParams
from app.core.schemas import org_mini, user_mini
from app.core.security import hash_identifier, hash_token, new_token
from app.core.slugs import unique_slug
from app.core.time import utcnow
from app.core.validators import validate_external_url
from app.email.sender import queue_email
from app.models.competition import Competition, CompetitionParticipant, CompetitionSponsor
from app.models.enums import (
    CompetitionVisibility,
    Lifecycle,
    MembershipStatus,
    NotificationKind,
    OrgRole,
    OrgType,
    OrgVerification,
    UserStatus,
    VerificationMethod,
)
from app.models.learning import Course, CourseEnrollment
from app.models.org import Department, Organization, OrgInvite, OrgMembership, OrgSubscription, Plan
from app.models.project import Project
from app.models.submission import CompetitionResult, Submission
from app.models.user import AuthToken, User
from app.modules.notifications.service import notify
from app.modules.search.indexer import index_org
from app.storage import media_url

ORG_EMAIL_PURPOSE = "org_email"
ORG_EMAIL_TTL = timedelta(hours=24)
ROLE_RANK = {OrgRole.member: 0, OrgRole.manager: 1, OrgRole.admin: 2, OrgRole.owner: 3}


# ----------------------------------------------------------------------------- loading


def get_org(db: Session, slug: str) -> Organization:
    org = db.scalar(select(Organization).where(Organization.slug == slug.lower()))
    if org is None:
        raise NotFound("Organization not found.")
    return org


def load_admin(actor: Actor, slug: str) -> Organization:
    org = get_org(actor.db, slug)
    if not actor.is_org_admin(org.id):
        raise Forbidden("Only administrators of this organization can do that.")
    return org


def load_manager(actor: Actor, slug: str) -> Organization:
    org = get_org(actor.db, slug)
    if not actor.is_org_manager(org.id):
        raise Forbidden("Only managers of this organization can do that.")
    return org


def _actor_rank(actor: Actor, org_id: uuid.UUID) -> int:
    if actor.is_admin:
        return ROLE_RANK[OrgRole.owner]
    role = actor.org_role(org_id)
    return ROLE_RANK.get(OrgRole(role), -1) if role else -1


# ----------------------------------------------------------------------------- listing & detail


def card(db: Session, org: Organization, member_counts: dict[uuid.UUID, int] | None = None) -> dict[str, Any]:
    return {
        "id": org.id, "slug": org.slug, "name": org.name, "type": org.type, "tagline": org.tagline,
        "logo_url": media_url(org.logo_key), "accent_color": org.accent_color, "country": org.country, "city": org.city,
        "verification_status": org.verification_status, "member_count": (member_counts or {}).get(org.id, 0),
        "is_demo": org.is_demo,
    }


def list_orgs(actor: Actor, params: PageParams, *, q: str | None, type_: str | None, verified: bool | None) -> tuple[list[dict[str, Any]], int]:
    db = actor.db
    stmt = select(Organization)
    if type_:
        stmt = stmt.where(Organization.type == type_)
    if verified is not None:
        stmt = stmt.where((Organization.verification_status == OrgVerification.verified) if verified
                          else (Organization.verification_status != OrgVerification.verified))
    if q:
        like = f"%{q.strip()[:80]}%"
        stmt = stmt.where(or_(Organization.name.ilike(like), Organization.tagline.ilike(like), Organization.city.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    verified_first = case((Organization.verification_status == OrgVerification.verified, 0), else_=1)
    orgs = db.scalars(stmt.order_by(verified_first, func.lower(Organization.name))
                      .limit(params.page_size).offset(params.offset)).all()
    counts = _member_counts(db, [o.id for o in orgs])
    return [card(db, o, counts) for o in orgs], total


def _member_counts(db: Session, org_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not org_ids:
        return {}
    return dict(db.execute(select(OrgMembership.org_id, func.count()).where(
        OrgMembership.org_id.in_(org_ids), OrgMembership.status == MembershipStatus.active).group_by(OrgMembership.org_id)).all())


def viewer_membership(actor: Actor, org: Organization) -> OrgMembership | None:
    if not actor.is_authenticated:
        return None
    return actor.db.scalar(select(OrgMembership).where(OrgMembership.org_id == org.id, OrgMembership.user_id == actor.id))


def detail(actor: Actor, org: Organization) -> dict[str, Any]:
    db = actor.db
    member = actor.is_org_member(org.id)
    comp_vis = [CompetitionVisibility.public] + ([CompetitionVisibility.university] if member else [])
    comps = db.scalars(select(Competition).where(Competition.host_org_id == org.id, Competition.visibility.in_(comp_vis),
                                                 Competition.lifecycle != Lifecycle.draft)
                       .order_by(Competition.starts_at.desc().nulls_last()).limit(12)).all()
    sponsored = db.execute(select(Competition, CompetitionSponsor.tier).join(CompetitionSponsor, CompetitionSponsor.competition_id == Competition.id)
                           .where(CompetitionSponsor.org_id == org.id, Competition.visibility == CompetitionVisibility.public,
                                  Competition.lifecycle != Lifecycle.draft).limit(12)).all()
    courses = db.scalars(select(Course).where(Course.org_id == org.id, Course.status == "published",
                                              or_(Course.visibility == "public", member)).limit(12)).all()
    projects = db.scalars(select(Project).where(Project.org_id == org.id, Project.visibility == "public",
                                                Project.taken_down.is_(False), Project.status != "draft").limit(12)).all()
    counts = _member_counts(db, [org.id])
    m = viewer_membership(actor, org)
    depts = db.scalars(select(Department).where(Department.org_id == org.id).order_by(Department.name)).all()
    from app.modules.competitions import state

    return {
        **card(db, org, counts),
        "description_html": org.description_html or "", "description_md": org.description_md if actor.is_org_admin(org.id) else None,
        "website_url": org.website_url, "allow_membership_requests": org.allow_membership_requests,
        "domain_verification_available": bool(org.email_domains) and org.verification_status == OrgVerification.verified,
        "email_domains": list(org.email_domains or []) if org.verification_status == OrgVerification.verified else [],
        "departments": [{"id": d.id, "slug": d.slug, "name": d.name} for d in depts],
        "competitions": [{"slug": c.slug, "title": c.title, "status": state.effective_status(c), "visibility": c.visibility,
                          "cover_style": c.cover_style, "ends_at": c.ends_at, "participant_count": c.participant_count} for c in comps],
        "sponsored_competitions": [{"slug": c.slug, "title": c.title, "tier": tier} for c, tier in sponsored],
        "courses": [{"slug": c.slug, "title": c.title, "difficulty": c.difficulty} for c in courses],
        "projects": [{"slug": p.slug, "title": p.title, "summary": p.summary} for p in projects],
        "viewer": {
            "membership_status": m.status if m else None, "role": m.role if m and m.status == MembershipStatus.active else None,
            "verified": bool(m and m.verified_at and m.status == MembershipStatus.active),
            "can_manage": actor.is_org_admin(org.id), "can_manage_content": actor.is_org_manager(org.id),
        },
        "plan_key": org.plan_key if actor.is_org_admin(org.id) else None,
    }


# ----------------------------------------------------------------------------- create / update


def create_org(actor: Actor, data: dict[str, Any]) -> Organization:
    db = actor.db
    org_type = data.get("type")
    if org_type not in {t.value for t in OrgType}:
        raise ValidationFailed(details={"fields": {"type": "Choose an organization type."}})
    name = (data.get("name") or "").strip()
    if len(name) < 3:
        raise ValidationFailed(details={"fields": {"name": "Enter at least 3 characters."}})
    owned = db.scalar(select(func.count()).select_from(OrgMembership).where(
        OrgMembership.user_id == actor.id, OrgMembership.role == OrgRole.owner)) or 0
    if owned >= 10 and not actor.is_admin:
        raise Conflict("You already own the maximum number of organizations.", code="org_limit")
    org = Organization(slug=unique_slug(db, Organization, name, requested=data.get("slug")), name=name[:160], type=org_type,
                       created_by=actor.id, verification_status=OrgVerification.unverified)
    _apply(actor, org, data)
    db.add(org)
    db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=actor.id, role=OrgRole.owner, status=MembershipStatus.active,
                         verification_method=VerificationMethod.admin_review, verified_at=utcnow()))
    db.add(OrgSubscription(org_id=org.id, plan_key=_default_plan(db), status="active", provider="manual"))
    record_audit(db, actor.id, "org.create", target_type="organization", target_id=org.id, org_id=org.id, meta={"type": org_type})
    index_org(db, org)
    db.commit()
    actor.invalidate_memberships()
    return org


def _default_plan(db: Session) -> str:
    plan = db.get(Plan, "free")
    if plan is None:
        db.add(Plan(key="free", name="Free", description="Core features for student communities.", price_cents_monthly=0,
                    entitlements={"max_active_competitions": 3, "private_competitions": False, "custom_certificates": False,
                                  "analytics_export": False}, sort_order=0))
        db.flush()
    return "free"


def _apply(actor: Actor, org: Organization, data: dict[str, Any]) -> None:
    for key in ("tagline", "city"):
        if key in data:
            setattr(org, key, (data[key] or "").strip()[:200] or None)
    if "name" in data and data["name"]:
        org.name = data["name"].strip()[:160]
    if "country" in data:
        org.country = (data["country"] or "").upper()[:2] or None
    if "website_url" in data:
        org.website_url = validate_external_url(data["website_url"], "website_url")
    if "description_md" in data:
        org.description_md = data["description_md"] or ""
        org.description_html = render_markdown(org.description_md)
    if "accent_color" in data:
        color = data["accent_color"]
        if color and not (len(color) == 7 and color.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in color[1:])):
            raise ValidationFailed(details={"fields": {"accent_color": "Use a hex color like #7C5CFF."}})
        org.accent_color = color or None
    if "allow_membership_requests" in data and data["allow_membership_requests"] is not None:
        org.allow_membership_requests = bool(data["allow_membership_requests"])
    if "email_domains" in data and data["email_domains"] is not None:
        if not actor.is_admin:
            raise Forbidden("Only platform administrators can change verified email domains.")
        domains = sorted({d.strip().lower().lstrip("@") for d in data["email_domains"] if d and "." in d})
        public_providers = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me", "protonmail.com"}
        if public_providers & set(domains):
            raise ValidationFailed(details={"fields": {"email_domains": "Public email providers cannot verify membership."}})
        org.email_domains = domains[:20]


def update_org(actor: Actor, org: Organization, data: dict[str, Any]) -> Organization:
    _apply(actor, org, data)
    record_audit(actor.db, actor.id, "org.update", target_type="organization", target_id=org.id, org_id=org.id,
                 meta={"fields": sorted(data.keys())})
    index_org(actor.db, org)
    actor.db.commit()
    return org


def set_logo(actor: Actor, org: Organization, key: str) -> Organization:
    org.logo_key = key
    record_audit(actor.db, actor.id, "org.logo", target_type="organization", target_id=org.id, org_id=org.id)
    actor.db.commit()
    return org


def request_verification(actor: Actor, org: Organization, note: str) -> Organization:
    if org.verification_status == OrgVerification.verified:
        raise Conflict("This organization is already verified.", code="already_verified")
    org.verification_status = OrgVerification.pending
    record_audit(actor.db, actor.id, "org.verification_requested", target_type="organization", target_id=org.id, org_id=org.id,
                 reason=note[:1000])
    actor.db.commit()
    return org


def set_verification(actor: Actor, org: Organization, status: str, reason: str) -> Organization:
    if not actor.is_admin:
        raise Forbidden()
    if status not in {s.value for s in OrgVerification}:
        raise ValidationFailed("Unknown verification status.")
    org.verification_status = status
    record_audit(actor.db, actor.id, "org.verification_set", target_type="organization", target_id=org.id, org_id=org.id,
                 reason=reason, meta={"status": status})
    index_org(actor.db, org)
    actor.db.commit()
    return org


# ----------------------------------------------------------------------------- departments


def create_department(actor: Actor, org: Organization, name: str, description: str | None) -> Department:
    db = actor.db
    base = slugify(name, 60)
    slug = base
    n = 2
    while db.scalar(select(Department.id).where(Department.org_id == org.id, Department.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    dept = Department(org_id=org.id, slug=slug, name=name.strip()[:160], description=(description or "")[:2000] or None)
    db.add(dept)
    record_audit(db, actor.id, "org.department_create", target_type="organization", target_id=org.id, org_id=org.id, meta={"name": name})
    db.commit()
    return dept


def update_department(actor: Actor, org: Organization, dept_id: uuid.UUID, name: str, description: str | None) -> Department:
    dept = actor.db.get(Department, dept_id)
    if dept is None or dept.org_id != org.id:
        raise NotFound()
    dept.name = name.strip()[:160]
    dept.description = (description or "")[:2000] or None
    actor.db.commit()
    return dept


def delete_department(actor: Actor, org: Organization, dept_id: uuid.UUID) -> None:
    dept = actor.db.get(Department, dept_id)
    if dept is None or dept.org_id != org.id:
        raise NotFound()
    actor.db.delete(dept)
    record_audit(actor.db, actor.id, "org.department_delete", target_type="organization", target_id=org.id, org_id=org.id,
                 meta={"name": dept.name})
    actor.db.commit()


# ----------------------------------------------------------------------------- membership


def _existing(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> OrgMembership | None:
    return db.scalar(select(OrgMembership).where(OrgMembership.org_id == org_id, OrgMembership.user_id == user_id))


def _admin_ids(db: Session, org_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(OrgMembership.user_id).where(
        OrgMembership.org_id == org_id, OrgMembership.status == MembershipStatus.active,
        OrgMembership.role.in_([OrgRole.owner, OrgRole.admin]))))


def request_membership(actor: Actor, org: Organization, note: str | None, department_id: uuid.UUID | None) -> OrgMembership:
    db = actor.db
    if not org.allow_membership_requests:
        raise Conflict("This organization is not accepting membership requests. Ask an administrator for an invite.",
                       code="requests_closed")
    m = _existing(db, org.id, actor.id)  # type: ignore[arg-type]
    if m and m.status == MembershipStatus.active:
        raise Conflict("You are already a member.", code="already_member")
    if m and m.status == MembershipStatus.pending:
        return m
    if department_id and (d := db.get(Department, department_id)) is not None and d.org_id != org.id:
        raise ValidationFailed(details={"fields": {"department_id": "Unknown department."}})
    if m is None:
        m = OrgMembership(org_id=org.id, user_id=actor.id, role=OrgRole.member)
        db.add(m)
    m.status = MembershipStatus.pending
    m.verification_method = VerificationMethod.none
    m.request_note = (note or "")[:500] or None
    m.department_id = department_id
    db.flush()
    for admin_id in _admin_ids(db, org.id):
        notify(db, admin_id, NotificationKind.org_membership, f"New membership request for {org.name}",
               body=f"@{actor.user.handle} asked to join.", link=f"/orgs/{org.slug}/admin/members?status=pending",  # type: ignore[union-attr]
               dedupe_key=f"orgreq:{m.id}:{admin_id}", group_key=f"orgreq:{org.id}")
    record_audit(db, actor.id, "org.membership_request", target_type="org_membership", target_id=m.id, org_id=org.id)
    db.commit()
    return m


def start_domain_verification(actor: Actor, org: Organization, email: str) -> None:
    db = actor.db
    email = email.strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if org.verification_status != OrgVerification.verified or not org.email_domains:
        raise Conflict("Email verification is not available for this organization.", code="domain_verification_unavailable")
    if not any(domain == d or domain.endswith("." + d) for d in org.email_domains):
        raise ValidationFailed(details={"fields": {"email": f"Use an address ending in {', '.join('@' + d for d in org.email_domains)}."}})
    email_hash = hash_identifier(f"orgmail:{org.id}:{email}")
    taken = db.scalar(select(AuthToken.id).where(AuthToken.purpose == ORG_EMAIL_PURPOSE, AuthToken.used_at.is_not(None),
                                                 AuthToken.payload["email_hash"].astext == email_hash, AuthToken.user_id != actor.id))
    if taken:
        raise Conflict("That address already verified another account.", code="org_email_used")
    token = new_token()
    db.add(AuthToken(user_id=actor.id, purpose=ORG_EMAIL_PURPOSE, token_hash=hash_token(token),
                     payload={"org_id": str(org.id), "email_hash": email_hash}, expires_at=utcnow() + ORG_EMAIL_TTL))
    user = actor.user
    assert user is not None
    queue_email(db, email, "org_email_verify", {"name": user.display_name, "email": email, "org": org.name,
                                                "link": f"{settings.WEB_BASE_URL}/orgs/verify-email?token={token}"})
    record_audit(db, actor.id, "org.domain_verification_started", target_type="organization", target_id=org.id, org_id=org.id)
    db.commit()


def confirm_domain_verification(actor: Actor, token: str) -> Organization:
    db = actor.db
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token), AuthToken.purpose == ORG_EMAIL_PURPOSE)
                    .with_for_update())
    if row is None or row.used_at is not None or row.expires_at < utcnow() or row.user_id != actor.id:
        raise ValidationFailed("This verification link is invalid or has expired.", code="token_invalid")
    org = db.get(Organization, uuid.UUID(row.payload["org_id"]))
    if org is None:
        raise NotFound()
    row.used_at = utcnow()
    m = _existing(db, org.id, actor.id)  # type: ignore[arg-type]
    if m is None:
        m = OrgMembership(org_id=org.id, user_id=actor.id, role=OrgRole.member)
        db.add(m)
    m.status = MembershipStatus.active
    m.verification_method = VerificationMethod.domain
    m.verified_at = utcnow()
    user = actor.user
    assert user is not None
    if org.type == OrgType.university and user.university_id is None:
        user.university_id = org.id
    record_audit(db, actor.id, "org.domain_verified", target_type="org_membership", target_id=m.id, org_id=org.id)
    from app.modules.credentials.badges import evaluate_user_badges

    evaluate_user_badges(db, user.id, trigger="org_verified")
    db.commit()
    actor.invalidate_memberships()
    return org


def review_request(actor: Actor, org: Organization, membership_id: uuid.UUID, approve: bool, note: str | None) -> OrgMembership:
    db = actor.db
    m = db.get(OrgMembership, membership_id)
    if m is None or m.org_id != org.id:
        raise NotFound()
    if m.status != MembershipStatus.pending:
        raise Conflict("This request was already reviewed.", code="already_reviewed")
    m.status = MembershipStatus.active if approve else MembershipStatus.rejected
    m.reviewed_by = actor.id
    m.review_note = (note or "")[:500] or None
    if approve:
        m.verification_method = VerificationMethod.admin_review
        m.verified_at = utcnow()
    notify(db, m.user_id, NotificationKind.org_membership,
           f"Your request to join {org.name} was {'approved' if approve else 'declined'}", link=f"/orgs/{org.slug}",
           dedupe_key=f"orgreview:{m.id}:{m.status}", email_template="generic_notification",
           email_context={"title": f"Membership update from {org.name}",
                          "body": f"Your membership request was {'approved' if approve else 'declined'}.",
                          "link": f"{settings.WEB_BASE_URL}/orgs/{org.slug}"})
    record_audit(db, actor.id, "org.membership_approve" if approve else "org.membership_reject", target_type="org_membership",
                 target_id=m.id, org_id=org.id, reason=note)
    db.commit()
    return m


def _count_owners(db: Session, org_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(OrgMembership).where(
        OrgMembership.org_id == org_id, OrgMembership.role == OrgRole.owner, OrgMembership.status == MembershipStatus.active)) or 0


def change_role(actor: Actor, org: Organization, membership_id: uuid.UUID, role: str) -> OrgMembership:
    db = actor.db
    if role not in ROLE_RANK:
        raise ValidationFailed("Unknown role.")
    m = db.get(OrgMembership, membership_id)
    if m is None or m.org_id != org.id or m.status != MembershipStatus.active:
        raise NotFound()
    mine = _actor_rank(actor, org.id)
    is_owner = mine == ROLE_RANK[OrgRole.owner]
    if not is_owner and (ROLE_RANK[OrgRole(m.role)] >= mine or ROLE_RANK[OrgRole(role)] >= mine):
        raise Forbidden("Administrators can only assign member or manager roles to members below their level.")
    if m.role == OrgRole.owner and role != OrgRole.owner and _count_owners(db, org.id) <= 1:
        raise Conflict("An organization needs at least one owner.", code="last_owner")
    old = m.role
    m.role = role
    record_audit(db, actor.id, "org.role_change", target_type="org_membership", target_id=m.id, org_id=org.id,
                 meta={"from": old, "to": role, "user_id": str(m.user_id)})
    notify(db, m.user_id, NotificationKind.org_membership, f"Your role in {org.name} is now {role}", link=f"/orgs/{org.slug}",
           dedupe_key=f"orgrole:{m.id}:{role}:{utcnow().date()}")
    db.commit()
    return m


def remove_member(actor: Actor, org: Organization, membership_id: uuid.UUID, reason: str | None) -> None:
    db = actor.db
    m = db.get(OrgMembership, membership_id)
    if m is None or m.org_id != org.id:
        raise NotFound()
    mine = _actor_rank(actor, org.id)
    if mine != ROLE_RANK[OrgRole.owner] and ROLE_RANK[OrgRole(m.role)] >= mine:
        raise Forbidden("You cannot remove someone at or above your level.")
    if m.role == OrgRole.owner and _count_owners(db, org.id) <= 1:
        raise Conflict("An organization needs at least one owner.", code="last_owner")
    m.status = MembershipStatus.removed
    m.verified_at = None
    record_audit(db, actor.id, "org.member_remove", target_type="org_membership", target_id=m.id, org_id=org.id, reason=reason,
                 meta={"user_id": str(m.user_id)})
    db.commit()


def leave(actor: Actor, org: Organization) -> None:
    db = actor.db
    m = _existing(db, org.id, actor.id)  # type: ignore[arg-type]
    if m is None or m.status not in (MembershipStatus.active, MembershipStatus.pending):
        raise NotFound("You are not a member.")
    if m.role == OrgRole.owner and _count_owners(db, org.id) <= 1:
        raise Conflict("Transfer ownership before leaving.", code="last_owner")
    m.status = MembershipStatus.removed
    m.verified_at = None
    user = actor.user
    if user is not None and user.university_id == org.id:
        user.university_id = None
        user.department_id = None
    record_audit(db, actor.id, "org.leave", target_type="org_membership", target_id=m.id, org_id=org.id)
    db.commit()
    actor.invalidate_memberships()


# ----------------------------------------------------------------------------- invites


def create_invite(actor: Actor, org: Organization, *, email: str | None, role: str, max_uses: int, days: int) -> tuple[OrgInvite, str]:
    db = actor.db
    if role not in ROLE_RANK or role == OrgRole.owner:
        raise ValidationFailed(details={"fields": {"role": "Choose member, manager or admin."}})
    if ROLE_RANK[OrgRole(role)] >= _actor_rank(actor, org.id) and not actor.is_admin:
        raise Forbidden("You cannot invite someone to a role at or above your own.")
    token = new_token(24)
    inv = OrgInvite(org_id=org.id, email=(email or "").strip().lower() or None, role=role, token_hash=hash_token(token),
                    invited_by=actor.id, expires_at=utcnow() + timedelta(days=max(1, min(days, 90))),
                    max_uses=1 if email else max(1, min(max_uses, 1000)))
    db.add(inv)
    db.flush()
    link = f"{settings.WEB_BASE_URL}/orgs/join?token={token}"
    if inv.email:
        inviter = actor.user
        assert inviter is not None
        queue_email(db, inv.email, "generic_notification", {
            "name": "there", "title": f"You're invited to join {org.name} on {settings.APP_NAME}",
            "body": f"{inviter.display_name} invited you to join {org.name} as {role}. The invite expires in {days} days.",
            "link": link}, dedupe_key=f"orginvite:{inv.id}")
    record_audit(db, actor.id, "org.invite_create", target_type="org_invite", target_id=inv.id, org_id=org.id,
                 meta={"role": role, "max_uses": inv.max_uses, "email_bound": bool(inv.email)})
    db.commit()
    return inv, link


def list_invites(actor: Actor, org: Organization) -> list[OrgInvite]:
    return list(actor.db.scalars(select(OrgInvite).where(OrgInvite.org_id == org.id).order_by(OrgInvite.created_at.desc()).limit(200)))


def revoke_invite(actor: Actor, org: Organization, invite_id: uuid.UUID) -> None:
    inv = actor.db.get(OrgInvite, invite_id)
    if inv is None or inv.org_id != org.id:
        raise NotFound()
    inv.revoked_at = inv.revoked_at or utcnow()
    record_audit(actor.db, actor.id, "org.invite_revoke", target_type="org_invite", target_id=inv.id, org_id=org.id)
    actor.db.commit()


def invite_preview(db: Session, token: str) -> dict[str, Any]:
    inv = db.scalar(select(OrgInvite).where(OrgInvite.token_hash == hash_token(token)))
    if inv is None or inv.revoked_at or inv.expires_at < utcnow() or inv.uses >= inv.max_uses:
        raise NotFound("This invite link is invalid or has expired.", code="invite_invalid")
    org = db.get(Organization, inv.org_id)
    assert org is not None
    return {"org": org_mini(org), "role": inv.role, "email_bound": inv.email is not None, "expires_at": inv.expires_at}


def accept_invite(actor: Actor, token: str) -> Organization:
    db = actor.db
    inv = db.scalar(select(OrgInvite).where(OrgInvite.token_hash == hash_token(token)).with_for_update())
    if inv is None or inv.revoked_at or inv.expires_at < utcnow() or inv.uses >= inv.max_uses:
        raise NotFound("This invite link is invalid or has expired.", code="invite_invalid")
    user = actor.user
    assert user is not None
    if inv.email and inv.email != user.email:
        raise Forbidden("This invite was sent to a different email address.", code="invite_email_mismatch")
    org = db.get(Organization, inv.org_id)
    assert org is not None
    m = _existing(db, org.id, user.id)
    if m is None:
        m = OrgMembership(org_id=org.id, user_id=user.id, role=inv.role)
        db.add(m)
    elif m.status == MembershipStatus.active and ROLE_RANK[OrgRole(m.role)] >= ROLE_RANK[OrgRole(inv.role)]:
        return org
    m.role = inv.role if m.status != MembershipStatus.active or ROLE_RANK[OrgRole(inv.role)] > ROLE_RANK[OrgRole(m.role)] else m.role
    m.status = MembershipStatus.active
    m.verification_method = VerificationMethod.invite
    m.verified_at = utcnow()
    inv.uses += 1
    record_audit(db, actor.id, "org.invite_accept", target_type="org_invite", target_id=inv.id, org_id=org.id, meta={"role": m.role})
    db.commit()
    actor.invalidate_memberships()
    return org


def my_memberships(actor: Actor) -> list[dict[str, Any]]:
    rows = actor.db.execute(select(OrgMembership, Organization).join(Organization, Organization.id == OrgMembership.org_id)
                            .where(OrgMembership.user_id == actor.id, OrgMembership.status.in_([MembershipStatus.active, MembershipStatus.pending]))
                            .order_by(Organization.name)).all()
    return [{"org": org_mini(o), "role": m.role, "status": m.status, "verified": m.verified_at is not None,
             "verification_method": m.verification_method, "joined_at": m.created_at} for m, o in rows]


# ----------------------------------------------------------------------------- roster & admin analytics


def roster(actor: Actor, org: Organization, params: PageParams, *, status: str | None, role: str | None,
           department_id: uuid.UUID | None, q: str | None) -> tuple[list[dict[str, Any]], int]:
    db = actor.db
    stmt = select(OrgMembership, User).join(User, User.id == OrgMembership.user_id).where(OrgMembership.org_id == org.id)
    stmt = stmt.where(OrgMembership.status == (status or MembershipStatus.active))
    if role:
        stmt = stmt.where(OrgMembership.role == role)
    if department_id:
        stmt = stmt.where(OrgMembership.department_id == department_id)
    if q:
        like = f"%{q.strip()[:60]}%"
        stmt = stmt.where(or_(User.handle.ilike(like), User.display_name.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(OrgMembership.created_at.desc()).limit(params.page_size).offset(params.offset)).all()
    depts = {d.id: d.name for d in db.scalars(select(Department).where(Department.org_id == org.id))}
    comp_counts = dict(db.execute(select(CompetitionParticipant.user_id, func.count()).where(
        CompetitionParticipant.user_id.in_([u.id for _, u in rows])).group_by(CompetitionParticipant.user_id)).all())
    return [{
        "id": m.id, "user": user_mini(u), "role": m.role, "status": m.status, "verified": m.verified_at is not None,
        "verification_method": m.verification_method, "department": depts.get(m.department_id) if m.department_id else None,
        "department_id": m.department_id, "request_note": m.request_note, "joined_at": m.created_at,
        "competitions_joined": comp_counts.get(u.id, 0), "account_status": u.status,
    } for m, u in rows], total


def roster_csv(actor: Actor, org: Organization) -> str:
    rows, _ = roster(actor, org, PageParams(page=1, page_size=100), status=MembershipStatus.active, role=None, department_id=None, q=None)
    total_rows: list[dict[str, Any]] = list(rows)
    page = 2
    while len(rows) == 100 and page < 200:
        rows, _ = roster(actor, org, PageParams(page=page, page_size=100), status=MembershipStatus.active, role=None,
                         department_id=None, q=None)
        total_rows.extend(rows)
        page += 1
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["handle", "display_name", "role", "department", "verified", "verification_method", "joined_at_utc", "competitions_joined"])
    for r in total_rows:
        u = r["user"]
        w.writerow([u.handle, _csv_safe(u.display_name), r["role"], _csv_safe(r["department"] or ""), r["verified"],
                    r["verification_method"], r["joined_at"].isoformat(), r["competitions_joined"]])
    record_audit(actor.db, actor.id, "org.roster_export", target_type="organization", target_id=org.id, org_id=org.id,
                 meta={"rows": len(total_rows)})
    actor.db.commit()
    return buf.getvalue()


def _csv_safe(value: str) -> str:
    """Neutralize spreadsheet formula injection."""
    return "'" + value if value and value[0] in "=+-@\t\r" else value


def admin_dashboard(actor: Actor, org: Organization) -> dict[str, Any]:
    db = actor.db
    now = utcnow()
    active_members = select(OrgMembership.user_id).where(OrgMembership.org_id == org.id, OrgMembership.status == MembershipStatus.active)
    counts = db.execute(select(
        func.count().filter(OrgMembership.status == MembershipStatus.active),
        func.count().filter(and_(OrgMembership.status == MembershipStatus.active, OrgMembership.verified_at.is_not(None))),
        func.count().filter(OrgMembership.status == MembershipStatus.pending),
    ).where(OrgMembership.org_id == org.id)).one()
    joins = db.execute(select(func.date_trunc("month", OrgMembership.created_at).label("m"), func.count())
                       .where(OrgMembership.org_id == org.id, OrgMembership.status == MembershipStatus.active,
                              OrgMembership.created_at > now - timedelta(days=365))
                       .group_by("m").order_by("m")).all()
    active_30 = db.scalar(select(func.count(func.distinct(Submission.user_id))).where(
        Submission.user_id.in_(active_members), Submission.submitted_at > now - timedelta(days=30))) or 0
    comps = db.scalars(select(Competition).where(Competition.host_org_id == org.id).order_by(Competition.created_at.desc()).limit(20)).all()
    comp_rows = []
    for c in comps:
        member_participants = db.scalar(select(func.count()).select_from(CompetitionParticipant).where(
            CompetitionParticipant.competition_id == c.id, CompetitionParticipant.user_id.in_(active_members))) or 0
        from app.modules.competitions import state

        comp_rows.append({"slug": c.slug, "title": c.title, "status": state.effective_status(c, now), "visibility": c.visibility,
                          "participants": c.participant_count, "member_participants": member_participants,
                          "teams": c.team_count, "submissions": c.submission_count})
    member_comp_participation = db.execute(
        select(Competition.slug, Competition.title, func.count())
        .join(CompetitionParticipant, CompetitionParticipant.competition_id == Competition.id)
        .where(CompetitionParticipant.user_id.in_(active_members), Competition.visibility == CompetitionVisibility.public,
               Competition.lifecycle != Lifecycle.draft)
        .group_by(Competition.slug, Competition.title).order_by(func.count().desc()).limit(10)).all()
    depts = db.execute(select(Department.name, func.count(OrgMembership.id)).outerjoin(
        OrgMembership, and_(OrgMembership.department_id == Department.id, OrgMembership.status == MembershipStatus.active))
        .where(Department.org_id == org.id).group_by(Department.name).order_by(Department.name)).all()
    completions = db.scalar(select(func.count()).select_from(CourseEnrollment).where(
        CourseEnrollment.user_id.in_(active_members), CourseEnrollment.completed_at.is_not(None))) or 0
    # Top performers: only members who allow their university to appear next to results.
    top = db.execute(select(User, func.count(CompetitionResult.id), func.min(CompetitionResult.rank))
                     .join(CompetitionResult, CompetitionResult.user_id == User.id)
                     .join(Competition, Competition.id == CompetitionResult.competition_id)
                     .where(User.id.in_(active_members), User.status == UserStatus.active,
                            Competition.visibility == CompetitionVisibility.public, CompetitionResult.rank.is_not(None),
                            CompetitionResult.rank <= 10)
                     .group_by(User.id).order_by(func.count(CompetitionResult.id).desc(), func.min(CompetitionResult.rank)).limit(20)).all()
    top = [(u, n, best) for u, n, best in top if u.privacy_flag("show_university_on_leaderboards")][:10]
    return {
        "org": card(db, org, {org.id: counts[0]}),
        "members": {"active": counts[0], "verified": counts[1], "pending_requests": counts[2], "active_last_30d": active_30},
        "joins_by_month": [{"month": m.date().isoformat()[:7], "count": n} for m, n in joins],
        "hosted_competitions": comp_rows,
        "member_participation": [{"slug": s, "title": t, "members": n} for s, t, n in member_comp_participation],
        "departments": [{"name": n, "members": c} for n, c in depts],
        "course_completions": completions,
        "top_performers": [{"user": user_mini(u), "top10_finishes": n, "best_rank": best} for u, n, best in top],
        "privacy_note": "Aggregates cover active members. Individual results appear only for members who allow their "
                        "university to be shown alongside results.",
    }


def sponsor_dashboard(actor: Actor, org: Organization) -> dict[str, Any]:
    """Sponsor view: sponsored events, aggregate engagement, and a consent-based talent list."""
    db = actor.db
    rows = db.execute(select(Competition, CompetitionSponsor).join(CompetitionSponsor, CompetitionSponsor.competition_id == Competition.id)
                      .where(CompetitionSponsor.org_id == org.id).order_by(Competition.starts_at.desc().nulls_last())).all()
    comp_ids = [c.id for c, _ in rows]
    from app.modules.competitions import state

    talent: list[dict[str, Any]] = []
    if comp_ids:
        people = db.execute(select(User, func.min(CompetitionResult.rank), func.count(CompetitionResult.id))
                            .join(CompetitionResult, CompetitionResult.user_id == User.id)
                            .where(CompetitionResult.competition_id.in_(comp_ids), User.open_to_opportunities.is_(True),
                                   User.status == UserStatus.active)
                            .group_by(User.id).order_by(func.min(CompetitionResult.rank).asc().nulls_last()).limit(50)).all()
        talent = [{"user": user_mini(u), "best_rank": best, "sponsored_events": n, "headline": u.headline,
                   "skills": list(u.skills or [])[:8] if u.privacy_flag("show_skills") else []} for u, best, n in people]
    sub_counts = dict(db.execute(select(Submission.competition_id, func.count()).where(Submission.competition_id.in_(comp_ids))
                                 .group_by(Submission.competition_id)).all()) if comp_ids else {}
    return {
        "org": card(db, org),
        "sponsored": [{"slug": c.slug, "title": c.title, "tier": s.tier, "status": state.effective_status(c),
                       "participants": c.participant_count, "teams": c.team_count, "submissions": sub_counts.get(c.id, 0),
                       "views": c.view_count} for c, s in rows],
        "totals": {"events": len(rows), "participants": sum(c.participant_count for c, _ in rows),
                   "submissions": sum(sub_counts.values())},
        "talent": talent,
        "talent_note": "Only participants who turned on 'Open to opportunities' in their profile appear here. "
                       "Contact happens through their public profile links; emails are never shared.",
    }


def subscription_view(actor: Actor, org: Organization) -> dict[str, Any]:
    db = actor.db
    sub = db.scalar(select(OrgSubscription).where(OrgSubscription.org_id == org.id))
    plans = db.scalars(select(Plan).where(Plan.is_public.is_(True)).order_by(Plan.sort_order)).all()
    current = db.get(Plan, sub.plan_key if sub else org.plan_key)
    return {
        "plan": {"key": current.key, "name": current.name, "entitlements": current.entitlements} if current else None,
        "status": sub.status if sub else "active", "provider": sub.provider if sub else "manual",
        "current_period_end": sub.current_period_end if sub else None,
        "payment_failed": bool(sub and sub.last_payment_failed_at),
        "available_plans": [{"key": p.key, "name": p.name, "description": p.description, "price_cents_monthly": p.price_cents_monthly,
                             "entitlements": p.entitlements} for p in plans],
        "billing_note": "Payments are handled manually in this deployment. Contact the platform team to change plans.",
    }


def entitlement(db: Session, org_id: uuid.UUID | None, key: str, default: Any = None) -> Any:
    if org_id is None:
        return default
    org = db.get(Organization, org_id)
    if org is None:
        return default
    plan = db.get(Plan, org.plan_key)
    return (plan.entitlements or {}).get(key, default) if plan else default

