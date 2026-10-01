"""Certificates (issue, verify, revoke, templates) and badges (catalog, awards, verification)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import func, select

from app.core.audit import record_audit
from app.core.deps import Actor, get_actor, require_admin, require_user
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.rate_limit import client_ip, enforce
from app.core.schemas import Message, Schema
from app.core.slugs import unique_slug
from app.models.credential import BadgeAward, BadgeDefinition, Certificate, CertificateTemplate
from app.models.enums import BadgeCategory
from app.models.org import Organization
from app.models.user import User
from app.modules.competitions.service import load_managed
from app.modules.credentials import badges, certificates

router = APIRouter(tags=["credentials"])

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ----------------------------------------------------------------------------- schemas


class CertificateOut(Schema):
    public_id: str
    status: str
    recipient_name: str
    kind: str
    event_title: str
    issuer_name: str
    result_label: str
    rank: int | None
    issued_at: datetime
    rendered: dict[str, Any]
    template_version: int
    revoked_at: datetime | None
    revoked_reason: str | None
    verification_url: str
    is_demo: bool


class MyCertificateOut(CertificateOut):
    hidden_on_profile: bool


class EligibilityRow(Schema):
    user_id: uuid.UUID
    handle: str
    display_name: str
    kind: str
    label: str
    rank: int | None
    already_issued: bool
    team: str | None = None


class IssueResult(Schema):
    issued: int


class ReasonIn(Schema):
    reason: str = Field(min_length=3, max_length=500)


class TemplateIn(Schema):
    org_id: uuid.UUID | None = None
    name: str = Field(min_length=2, max_length=120)
    heading: str = Field(default="Certificate of Achievement", max_length=160)
    body_template: str = Field(default="Awarded to {recipient} for {result} in {event}.", max_length=600)
    accent_color: str = Field(default="#7C5CFF", max_length=9)
    signatory_name: str | None = Field(default=None, max_length=120)
    signatory_title: str | None = Field(default=None, max_length=120)
    is_default: bool = False


class TemplateOut(Schema):
    id: uuid.UUID
    org_id: uuid.UUID | None
    name: str
    heading: str
    body_template: str
    accent_color: str
    signatory_name: str | None
    signatory_title: str | None
    version: int
    is_default: bool
    created_at: datetime
    preview: dict[str, Any] | None = None


class BadgeOut(Schema):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    category: str
    icon: str
    color: str
    is_manual: bool
    rarity_label: str | None
    criteria: dict[str, Any]
    criteria_version: int
    org_id: uuid.UUID | None
    awarded_count: int = 0
    earned: bool = False


class BadgeIn(Schema):
    slug: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=5, max_length=300)
    category: str = Field(pattern="^(learning|competition|contribution|community)$")
    icon: str = Field(default="award", pattern="^[a-z0-9-]{2,40}$")
    color: str = Field(default="#7C5CFF", max_length=9)
    org_id: uuid.UUID | None = None
    criteria: dict[str, Any] = Field(default_factory=dict)
    is_manual: bool = False
    rarity_label: str | None = Field(default=None, max_length=24)


class AwardIn(Schema):
    badge_slug: str = Field(max_length=80)
    handle: str = Field(max_length=31)
    reason: str = Field(min_length=3, max_length=300)


class BadgeVerifyOut(Schema):
    public_id: str
    badge: BadgeOut
    recipient: dict[str, Any] | None
    awarded_at: datetime
    manual: bool
    evidence_summary: str
    criteria_version: int


_CRITERIA_TYPES = {"first_submission", "competition_rank", "competitions_joined", "course_completed", "courses_completed",
                   "path_completed", "merged_prs", "accepted_answers"}


def _validate_criteria(criteria: dict[str, Any], is_manual: bool) -> dict[str, Any]:
    if is_manual:
        return {}
    kind = criteria.get("type")
    if kind not in _CRITERIA_TYPES:
        raise ValidationFailed(details={"fields": {"criteria": f"type must be one of {sorted(_CRITERIA_TYPES)}"}})
    clean: dict[str, Any] = {"type": kind}
    for key in ("max_rank", "count"):
        if key in criteria:
            try:
                clean[key] = max(1, min(10_000, int(criteria[key])))
            except (TypeError, ValueError):
                raise ValidationFailed(details={"fields": {"criteria": f"{key} must be a number"}}) from None
    for key in ("course_slug", "path_slug"):
        if key in criteria:
            clean[key] = str(criteria[key])[:80]
    return clean


def _cert_out(cert: Certificate) -> dict[str, Any]:
    return certificates.public_view(cert)


def _badge_out(b: BadgeDefinition, count: int = 0, earned: bool = False) -> BadgeOut:
    out = BadgeOut.model_validate(b)
    out.awarded_count = count
    out.earned = earned
    return out


# ----------------------------------------------------------------------------- certificates


@router.get("/certificates/verify/{public_id}", response_model=CertificateOut)
def verify_certificate(public_id: str, request: Request, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    enforce("cert_verify", client_ip(request), limit=120, window_seconds=60)
    return _cert_out(certificates.verify(actor.db, public_id[:40]))


@router.get("/certificates/{public_id}/qr.svg", response_class=Response)
def certificate_qr(public_id: str, actor: Actor = Depends(get_actor)) -> Response:
    cert = certificates.verify(actor.db, public_id[:40])
    return Response(certificates.qr_svg(cert.public_id), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


@router.get("/me/certificates", response_model=list[MyCertificateOut], tags=["me"])
def my_certificates(actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    rows = actor.db.scalars(select(Certificate).where(Certificate.recipient_id == actor.id).order_by(Certificate.issued_at.desc()))
    return [{**_cert_out(c), "hidden_on_profile": c.hidden_on_profile} for c in rows]


@router.get("/competitions/{slug}/certificates/preview", response_model=list[EligibilityRow])
def certificate_preview(slug: str, actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    comp = load_managed(actor, slug)
    return certificates.competition_eligibility(actor.db, comp)


@router.post("/competitions/{slug}/certificates/issue", response_model=IssueResult)
def certificate_issue(slug: str, actor: Actor = Depends(require_user)) -> IssueResult:
    comp = load_managed(actor, slug)
    return IssueResult(issued=certificates.issue_for_competition(actor, comp))


@router.get("/competitions/{slug}/certificates", response_model=list[CertificateOut])
def competition_certificates(slug: str, actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    comp = load_managed(actor, slug)
    rows = actor.db.scalars(select(Certificate).where(Certificate.competition_id == comp.id).order_by(Certificate.issued_at.desc()))
    return [_cert_out(c) for c in rows]


@router.post("/certificates/{public_id}/revoke", response_model=CertificateOut)
def revoke_certificate(public_id: str, data: ReasonIn, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    cert = certificates.verify(actor.db, public_id)
    return _cert_out(certificates.revoke(actor, cert, data.reason))


# ----------------------------------------------------------------------------- certificate templates


def _can_manage_templates(actor: Actor, org_id: uuid.UUID | None) -> bool:
    return actor.is_admin if org_id is None else actor.is_org_admin(org_id)


def _template_out(t: CertificateTemplate) -> TemplateOut:
    out = TemplateOut.model_validate(t)
    out.preview = {"heading": t.heading, "body": certificates.render_template_text(t.body_template, {
        "recipient": "Ada Example", "event": "Sample Challenge", "result": "Top 10", "issuer": "Your organization",
        "date": "January 1, 2026"}), "accent_color": t.accent_color, "signatory_name": t.signatory_name,
        "signatory_title": t.signatory_title}
    return out


@router.get("/certificate-templates", response_model=list[TemplateOut])
def list_templates(org_id: uuid.UUID | None = Query(None), actor: Actor = Depends(require_user)) -> list[TemplateOut]:
    if not _can_manage_templates(actor, org_id):
        raise Forbidden()
    rows = actor.db.scalars(select(CertificateTemplate).where(
        CertificateTemplate.org_id == org_id if org_id else CertificateTemplate.org_id.is_(None)).order_by(CertificateTemplate.created_at))
    return [_template_out(t) for t in rows]


def _apply_template(t: CertificateTemplate, data: TemplateIn) -> None:
    if not _HEX_RE.match(data.accent_color):
        raise ValidationFailed(details={"fields": {"accent_color": "Use a hex color like #7C5CFF."}})
    t.name, t.heading, t.body_template = data.name, data.heading, data.body_template
    t.accent_color, t.signatory_name, t.signatory_title = data.accent_color, data.signatory_name, data.signatory_title


@router.post("/certificate-templates", response_model=TemplateOut, status_code=201)
def create_template(data: TemplateIn, actor: Actor = Depends(require_user)) -> TemplateOut:
    if not _can_manage_templates(actor, data.org_id):
        raise Forbidden()
    db = actor.db
    t = CertificateTemplate(org_id=data.org_id, created_by=actor.id, version=1)
    _apply_template(t, data)
    if data.is_default:
        for other in db.scalars(select(CertificateTemplate).where(
                CertificateTemplate.org_id == data.org_id if data.org_id else CertificateTemplate.org_id.is_(None))):
            other.is_default = False
    t.is_default = data.is_default
    db.add(t)
    db.flush()
    record_audit(db, actor.id, "certificate_template.create", target_type="certificate_template", target_id=t.id, org_id=data.org_id)
    db.commit()
    return _template_out(t)


@router.put("/certificate-templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: uuid.UUID, data: TemplateIn, actor: Actor = Depends(require_user)) -> TemplateOut:
    """Edits bump the template version. Issued certificates keep their snapshotted rendering."""
    db = actor.db
    t = db.get(CertificateTemplate, template_id)
    if t is None or not _can_manage_templates(actor, t.org_id):
        raise NotFound()
    _apply_template(t, data)
    t.version += 1
    if data.is_default and not t.is_default:
        for other in db.scalars(select(CertificateTemplate).where(
                CertificateTemplate.org_id == t.org_id if t.org_id else CertificateTemplate.org_id.is_(None))):
            other.is_default = False
        t.is_default = True
    record_audit(db, actor.id, "certificate_template.update", target_type="certificate_template", target_id=t.id,
                 org_id=t.org_id, meta={"version": t.version})
    db.commit()
    return _template_out(t)


# ----------------------------------------------------------------------------- badges


@router.get("/badges", response_model=list[BadgeOut])
def list_badges(category: str | None = Query(None, pattern="^(learning|competition|contribution|community)$"),
                actor: Actor = Depends(get_actor)) -> list[BadgeOut]:
    db = actor.db
    stmt = select(BadgeDefinition).where(BadgeDefinition.is_active.is_(True))
    if category:
        stmt = stmt.where(BadgeDefinition.category == category)
    defs = db.scalars(stmt.order_by(BadgeDefinition.category, BadgeDefinition.name)).all()
    counts = dict(db.execute(select(BadgeAward.badge_id, func.count()).group_by(BadgeAward.badge_id)).all())
    earned: set[uuid.UUID] = set()
    if actor.is_authenticated:
        earned = set(db.scalars(select(BadgeAward.badge_id).where(BadgeAward.user_id == actor.id)))
    return [_badge_out(b, counts.get(b.id, 0), b.id in earned) for b in defs]


@router.get("/me/badges", tags=["me"])
def my_badges(actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    rows = actor.db.execute(select(BadgeAward, BadgeDefinition).join(BadgeDefinition, BadgeDefinition.id == BadgeAward.badge_id)
                            .where(BadgeAward.user_id == actor.id).order_by(BadgeAward.awarded_at.desc())).all()
    return [{"id": str(a.id), "public_id": a.public_id, "badge": _badge_out(b).model_dump(mode="json"), "awarded_at": a.awarded_at,
             "evidence": a.evidence, "hidden_on_profile": a.hidden_on_profile, "manual": a.awarded_by is not None} for a, b in rows]


@router.post("/badges", response_model=BadgeOut, status_code=201)
def create_badge(data: BadgeIn, actor: Actor = Depends(require_user)) -> BadgeOut:
    if not (actor.is_admin or (data.org_id and actor.is_org_manager(data.org_id))):
        raise Forbidden()
    if data.org_id and not data.is_manual and not actor.is_admin:
        raise Forbidden("Organizations can create manual badges; automatic badges are platform-managed.")
    if not _HEX_RE.match(data.color):
        raise ValidationFailed(details={"fields": {"color": "Use a hex color like #7C5CFF."}})
    if data.org_id and actor.db.get(Organization, data.org_id) is None:
        raise NotFound("Organization not found.")
    db = actor.db
    badge = BadgeDefinition(slug=unique_slug(db, BadgeDefinition, data.name, requested=data.slug), name=data.name,
                            description=data.description, category=BadgeCategory(data.category), icon=data.icon, color=data.color,
                            org_id=data.org_id, criteria=_validate_criteria(data.criteria, data.is_manual),
                            is_manual=data.is_manual, rarity_label=data.rarity_label)
    db.add(badge)
    db.flush()
    record_audit(db, actor.id, "badge.create", target_type="badge", target_id=badge.id, org_id=data.org_id, meta={"slug": badge.slug})
    db.commit()
    return _badge_out(badge)


@router.patch("/badges/{slug}", response_model=BadgeOut)
def update_badge(slug: str, data: BadgeIn, actor: Actor = Depends(require_admin)) -> BadgeOut:
    """Criteria edits bump criteria_version; existing awards are never revoked by a criteria change."""
    db = actor.db
    badge = db.scalar(select(BadgeDefinition).where(BadgeDefinition.slug == slug))
    if badge is None:
        raise NotFound()
    new_criteria = _validate_criteria(data.criteria, data.is_manual)
    if new_criteria != (badge.criteria or {}):
        badge.criteria = new_criteria
        badge.criteria_version += 1
    badge.name, badge.description, badge.icon, badge.color = data.name, data.description, data.icon, data.color
    badge.rarity_label = data.rarity_label
    record_audit(db, actor.id, "badge.update", target_type="badge", target_id=badge.id, meta={"criteria_version": badge.criteria_version})
    db.commit()
    return _badge_out(badge)


@router.post("/badges/award", response_model=Message)
def award_badge(data: AwardIn, actor: Actor = Depends(require_user)) -> Message:
    badges.award_manual(actor, data.badge_slug, data.handle, data.reason)
    return Message(message="Badge awarded.")


@router.get("/badges/verify/{public_id}", response_model=BadgeVerifyOut)
def verify_badge(public_id: str, actor: Actor = Depends(get_actor)) -> BadgeVerifyOut:
    db = actor.db
    award = db.scalar(select(BadgeAward).where(BadgeAward.public_id == public_id.upper()[:32]))
    if award is None:
        raise NotFound("No badge award exists with this id.")
    badge = db.get(BadgeDefinition, award.badge_id)
    user = db.get(User, award.user_id)
    assert badge is not None
    visible = user is not None and user.status == "active" and (not award.hidden_on_profile) and user.privacy_flag("show_badges")
    ev = award.evidence or {}
    summary = "Awarded manually by an authorized organizer." if award.awarded_by else {
        "first_submission": "Made a first scored competition submission.",
        "competition_rank": f"Finished at rank {ev.get('rank', '?')} in a finalized competition.",
        "competitions_joined": f"Joined {ev.get('count', '?')} competitions.",
        "course_completed": "Completed the linked course.",
        "courses_completed": f"Completed {ev.get('count', '?')} courses.",
        "path_completed": "Completed every course in a learning path.",
        "merged_prs": f"{ev.get('count', '?')} merged pull requests to registered repositories.",
        "accepted_answers": f"{ev.get('count', '?')} accepted discussion answers.",
    }.get((badge.criteria or {}).get("type", ""), "Criteria met.")
    return BadgeVerifyOut(public_id=award.public_id, badge=_badge_out(badge),
                          recipient={"handle": user.handle, "display_name": user.display_name} if visible and user else None,
                          awarded_at=award.awarded_at, manual=award.awarded_by is not None, evidence_summary=summary,
                          criteria_version=award.criteria_version)


@router.post("/me/badges/check", response_model=list[str], tags=["me"], summary="Re-check automatic badge criteria for yourself")
def check_my_badges(actor: Actor = Depends(require_user)) -> list[str]:
    enforce("badge_check", str(actor.id), limit=6, window_seconds=600)
    assert actor.id is not None
    awarded = badges.evaluate_user_badges(actor.db, actor.id, trigger="self_check")
    actor.db.commit()
    return awarded

