"""Verifiable certificates.

Public ids look like ``DB-7K2M-9QXA-F3`` — random Crockford base32 plus an HMAC check segment, so
forged or mistyped ids are rejected before any database lookup. The rendered content is snapshotted at
issuance; template edits never change issued certificates. Verification pages expose only what the
certificate states (recipient display name, event, issuer, result, date) — never emails or private data.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from typing import Any

import segno
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.ids import crockford_random, uuid7
from app.core.permissions import can_manage_competition
from app.core.security import sign
from app.core.time import utcnow
from app.models.competition import AwardCategory, Competition, CompetitionParticipant, Team, TeamMember
from app.models.credential import Certificate, CertificateTemplate
from app.models.enums import (
    CertificateKind,
    CertificateStatus,
    Lifecycle,
    NotificationKind,
    ScoringMode,
    SubmissionStatus,
)
from app.models.learning import Course
from app.models.org import Organization
from app.models.submission import CompetitionResult, EventSubmission, Submission
from app.models.user import User
from app.modules.notifications.service import notify

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _check_segment(body: str) -> str:
    digest = hashlib.sha256(sign(body, "certificate").encode()).digest()
    return "".join(_CROCKFORD[b % 32] for b in digest[:2])


def new_public_id() -> str:
    body = crockford_random(8)
    return f"DB-{body[:4]}-{body[4:]}-{_check_segment(body)}"


def is_well_formed(public_id: str) -> bool:
    parts = public_id.upper().split("-")
    if len(parts) != 4 or parts[0] != "DB" or len(parts[1]) != 4 or len(parts[2]) != 4 or len(parts[3]) != 2:
        return False
    body = parts[1] + parts[2]
    if any(ch not in _CROCKFORD for ch in body):
        return False
    return _check_segment(body) == parts[3]


def verification_url(public_id: str) -> str:
    return f"{settings.WEB_BASE_URL}/verify/{public_id}"


def default_template(db: Session, org_id: uuid.UUID | None) -> CertificateTemplate:
    tpl = None
    if org_id:
        tpl = db.scalar(select(CertificateTemplate).where(CertificateTemplate.org_id == org_id, CertificateTemplate.is_default.is_(True)))
    if tpl is None:
        tpl = db.scalar(select(CertificateTemplate).where(CertificateTemplate.org_id.is_(None), CertificateTemplate.is_default.is_(True)))
    if tpl is None:
        tpl = CertificateTemplate(name="Platform default", is_default=True, signatory_name=None)
        db.add(tpl)
        db.flush()
    return tpl


_PLACEHOLDER_RE = re.compile(r"\{(recipient|event|result|issuer|date)\}")


def render_template_text(template: str, ctx: dict[str, str]) -> str:
    """Only whitelisted placeholders are substituted (no str.format — templates are organizer-supplied)."""
    return _PLACEHOLDER_RE.sub(lambda m: ctx.get(m.group(1), ""), template)


def _render(tpl: CertificateTemplate, ctx: dict[str, str]) -> dict[str, Any]:
    return {"heading": tpl.heading, "body": render_template_text(tpl.body_template, ctx), "accent_color": tpl.accent_color,
            "signatory_name": tpl.signatory_name, "signatory_title": tpl.signatory_title, "template_name": tpl.name}


def issue(db: Session, *, recipient: User, kind: CertificateKind, event_title: str, result_label: str,
          issuer: Organization | None, competition: Competition | None = None, course: Course | None = None,
          rank: int | None = None, issued_by: uuid.UUID | None = None, dedupe_key: str, is_demo: bool = False) -> Certificate | None:
    """Idempotent: returns None if a certificate with the same dedupe key already exists."""
    tpl = default_template(db, issuer.id if issuer else None)
    issuer_name = issuer.name if issuer else settings.APP_NAME
    issued_on = utcnow()
    rendered = _render(tpl, {"recipient": recipient.display_name, "event": event_title, "result": result_label,
                             "issuer": issuer_name, "date": issued_on.strftime("%B %d, %Y")})
    cert_id = uuid7()
    public_id = new_public_id()
    result = db.execute(insert(Certificate).values(
        id=cert_id, public_id=public_id, dedupe_key=dedupe_key, recipient_id=recipient.id, recipient_name=recipient.display_name,
        kind=kind.value, competition_id=competition.id if competition else None, course_id=course.id if course else None,
        issuer_org_id=issuer.id if issuer else None, issuer_name=issuer_name, event_title=event_title[:160],
        result_label=result_label[:80], rank=rank, template_id=tpl.id, template_version=tpl.version, rendered=rendered,
        status=CertificateStatus.valid, issued_at=issued_on, issued_by=issued_by, is_demo=is_demo,
    ).on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(Certificate.id))
    if result.first() is None:
        return None
    cert = db.get(Certificate, cert_id)
    notify(db, recipient.id, NotificationKind.certificate_issued, f"Certificate issued: {event_title}", body=result_label,
           link=f"/verify/{public_id}", dedupe_key=f"certificate:{cert_id}", email_template="certificate_issued",
           email_context={"event": event_title, "result": result_label, "link": verification_url(public_id)})
    return cert


def competition_eligibility(db: Session, comp: Competition) -> list[dict[str, Any]]:
    """Who would receive which certificate. Backed entirely by event data (results, awards, submissions)."""
    rules = comp.certificate_rules or {}
    top_n = int(rules.get("award_top_n", 10) or 0)
    results = {r.user_id: r for r in db.scalars(select(CompetitionResult).where(CompetitionResult.competition_id == comp.id))}
    participants = db.scalars(select(CompetitionParticipant).where(CompetitionParticipant.competition_id == comp.id)).all()
    if comp.scoring_mode == ScoringMode.judged:
        active_teams = set(db.scalars(select(EventSubmission.team_id).where(EventSubmission.competition_id == comp.id)))
    else:
        active_teams = set(db.scalars(select(Submission.team_id).where(Submission.competition_id == comp.id,
                                                                       Submission.status == SubmissionStatus.scored,
                                                                       Submission.invalidated_at.is_(None))))
    memberships = {m.user_id: m.team_id for m in db.scalars(select(TeamMember).where(TeamMember.competition_id == comp.id))}
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_([p.user_id for p in participants])))}
    existing = set(db.scalars(select(Certificate.dedupe_key).where(Certificate.competition_id == comp.id)))
    rows: list[dict[str, Any]] = []
    for p in participants:
        user = users.get(p.user_id)
        if user is None or user.status == "deleted":
            continue
        res = results.get(p.user_id)
        if res and res.rank and top_n and res.rank <= top_n:
            label = res.label if res.label != "Participant" else f"Rank {res.rank} of {res.total_ranked}"
            key = f"comp:{comp.id}:user:{user.id}:award"
            rows.append({"user_id": user.id, "handle": user.handle, "display_name": user.display_name,
                         "kind": CertificateKind.competition_award, "label": label, "rank": res.rank,
                         "dedupe_key": key, "already_issued": key in existing})
        elif rules.get("participation", True) and memberships.get(p.user_id) in active_teams:
            key = f"comp:{comp.id}:user:{user.id}:participation"
            rows.append({"user_id": user.id, "handle": user.handle, "display_name": user.display_name,
                         "kind": CertificateKind.competition_participation, "label": "Participant", "rank": None,
                         "dedupe_key": key, "already_issued": key in existing})
    for award in db.scalars(select(AwardCategory).where(AwardCategory.competition_id == comp.id,
                                                        AwardCategory.winner_team_id.is_not(None))):
        team = db.get(Team, award.winner_team_id)
        for uid in db.scalars(select(TeamMember.user_id).where(TeamMember.team_id == award.winner_team_id)):
            user = users.get(uid) or db.get(User, uid)
            if user is None:
                continue
            key = f"comp:{comp.id}:user:{uid}:award_cat:{award.id}"
            rows.append({"user_id": uid, "handle": user.handle, "display_name": user.display_name,
                         "kind": CertificateKind.event_award, "label": award.name[:80], "rank": None, "dedupe_key": key,
                         "already_issued": key in existing, "team": team.name if team else None})
    return rows


def issue_for_competition(actor: Actor, comp: Competition) -> int:
    db = actor.db
    if not can_manage_competition(actor, comp):
        raise Forbidden()
    if comp.lifecycle not in (Lifecycle.finalized, Lifecycle.archived):
        raise Conflict("Certificates can be issued after results are finalized.", code="not_finalized")
    issuer = db.get(Organization, comp.host_org_id) if comp.host_org_id else None
    count = 0
    for row in competition_eligibility(db, comp):
        if row["already_issued"]:
            continue
        user = db.get(User, row["user_id"])
        if user is None:
            continue
        cert = issue(db, recipient=user, kind=row["kind"], event_title=comp.title, result_label=row["label"], issuer=issuer,
                     competition=comp, rank=row["rank"], issued_by=actor.id, dedupe_key=row["dedupe_key"], is_demo=comp.is_demo)
        if cert:
            count += 1
    record_audit(db, actor.id, "certificates.bulk_issue", target_type="competition", target_id=comp.id, competition_id=comp.id,
                 meta={"issued": count})
    db.commit()
    return count


def public_view(cert: Certificate) -> dict[str, Any]:
    return {
        "public_id": cert.public_id, "status": cert.status, "recipient_name": cert.recipient_name, "kind": cert.kind,
        "event_title": cert.event_title, "issuer_name": cert.issuer_name, "result_label": cert.result_label, "rank": cert.rank,
        "issued_at": cert.issued_at, "rendered": cert.rendered, "template_version": cert.template_version,
        "revoked_at": cert.revoked_at, "revoked_reason": cert.revoked_reason if cert.status == CertificateStatus.revoked else None,
        "verification_url": verification_url(cert.public_id), "is_demo": cert.is_demo,
    }


def verify(db: Session, public_id: str) -> Certificate:
    pid = public_id.strip().upper()
    if not is_well_formed(pid):
        raise NotFound("This certificate id is not valid.", code="certificate_invalid")
    cert = db.scalar(select(Certificate).where(Certificate.public_id == pid))
    if cert is None:
        raise NotFound("No certificate exists with this id.", code="certificate_not_found")
    return cert


def revoke(actor: Actor, cert: Certificate, reason: str) -> Certificate:
    db = actor.db
    allowed = actor.is_admin
    if cert.competition_id:
        comp = db.get(Competition, cert.competition_id)
        allowed = allowed or (comp is not None and can_manage_competition(actor, comp))
    if cert.issuer_org_id:
        allowed = allowed or actor.is_org_admin(cert.issuer_org_id)
    if not allowed:
        raise Forbidden()
    if cert.status == CertificateStatus.revoked:
        return cert
    cert.status = CertificateStatus.revoked
    cert.revoked_at = utcnow()
    cert.revoked_by = actor.id
    cert.revoked_reason = reason[:500]
    record_audit(db, actor.id, "certificate.revoke", target_type="certificate", target_id=cert.public_id, reason=reason,
                 competition_id=cert.competition_id, org_id=cert.issuer_org_id)
    db.commit()
    return cert


def qr_svg(public_id: str) -> str:
    buf = io.BytesIO()
    segno.make(verification_url(public_id), error="m").save(buf, kind="svg", scale=4, border=2, dark="#111827", light="#ffffff")
    return buf.getvalue().decode()


def issue_course_certificate(db: Session, user: User, course: Course) -> Certificate | None:
    issuer = db.get(Organization, course.org_id) if course.org_id else None
    return issue(db, recipient=user, kind=CertificateKind.course_completion, event_title=course.title,
                 result_label="Completed", issuer=issuer, course=course, dedupe_key=f"course:{course.id}:user:{user.id}",
                 is_demo=course.is_demo)
