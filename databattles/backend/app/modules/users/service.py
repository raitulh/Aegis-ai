"""Profiles, privacy, onboarding and the student dashboard.

Public profiles separate *verified facts* (results, certificates, badges, GitHub-backed contributions,
verified university membership) from *self-declared* information (headline, bio, skills). Every public
field respects the owner's privacy settings; private competitions never leak through a profile.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.types import Date

from app.core.deps import Actor
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import is_valid_handle
from app.core.markdown import render_markdown
from app.core.schemas import org_mini
from app.core.time import is_valid_timezone, utcnow
from app.core.validators import clean_tags, validate_external_url
from app.evaluation.registry import metric_direction
from app.models.community import Comment, Notification, Thread
from app.models.competition import (
    Competition,
    CompetitionParticipant,
    ScheduleItem,
    Team,
    TeamInvitation,
    TeamMember,
)
from app.models.credential import BadgeAward, BadgeDefinition, Certificate
from app.models.dataset import Dataset
from app.models.enums import (
    CertificateStatus,
    CompetitionVisibility,
    Lifecycle,
    MembershipStatus,
    ProjectVisibility,
    UserStatus,
)
from app.models.github import GitHubAccount, GitHubPullRequest, GitHubRepository
from app.models.learning import Course, CourseEnrollment, Lesson, LessonProgress
from app.models.org import Department, Organization, OrgMembership
from app.models.project import Project, ProjectMember
from app.models.submission import CompetitionResult, Submission
from app.models.user import DEFAULT_PRIVACY, RecentView, User
from app.modules.competitions import state
from app.modules.search.indexer import index_user

PROFILE_FIELDS = ("display_name", "headline", "bio_md", "website_url", "country", "graduation_year", "skills", "interests",
                  "university_id", "department_id", "timezone", "cover_style", "open_to_opportunities")
COVER_STYLES = {"aurora", "nebula", "circuit", "dunes", "mono", "sunrise"}


def get_user_by_handle(db: Session, handle: str) -> User:
    user = db.scalar(select(User).where(User.handle == handle.lower()))
    if user is None or user.status == UserStatus.deleted:
        raise NotFound("Profile not found.")
    return user


def update_profile(actor: Actor, data: dict[str, Any]) -> User:
    db = actor.db
    user = actor.user
    assert user is not None
    if "handle" in data and data["handle"] and data["handle"] != user.handle:
        handle = data["handle"].lower()
        if not is_valid_handle(handle):
            raise ValidationFailed(details={"fields": {"handle": "Use 3–30 lowercase letters, numbers, - or _."}})
        if db.scalar(select(User.id).where(User.handle == handle)):
            raise Conflict("That handle is taken.", code="handle_taken", details={"fields": {"handle": "That handle is taken."}})
        user.handle = handle
    for key in PROFILE_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if key == "display_name":
            value = (value or "").strip()
            if not value:
                raise ValidationFailed(details={"fields": {"display_name": "Enter your name."}})
        if key == "bio_md":
            user.bio_html = render_markdown(value or "")
        if key == "website_url":
            value = validate_external_url(value, "website_url")
        if key in ("skills", "interests"):
            value = clean_tags(value, limit=20)
        if key == "timezone" and value and not is_valid_timezone(value):
            raise ValidationFailed(details={"fields": {"timezone": "Unknown time zone."}})
        if key == "graduation_year" and value is not None and not 1950 <= int(value) <= 2100:
            raise ValidationFailed(details={"fields": {"graduation_year": "Enter a valid year."}})
        if key == "university_id" and value is not None:
            org = db.get(Organization, value)
            if org is None or org.type != "university":
                raise ValidationFailed(details={"fields": {"university_id": "Choose a university from the list."}})
        if key == "department_id" and value is not None:
            dept = db.get(Department, value)
            uni = data.get("university_id", user.university_id)
            if dept is None or dept.org_id != uni:
                raise ValidationFailed(details={"fields": {"department_id": "Department does not belong to your university."}})
        if key == "cover_style" and value not in COVER_STYLES:
            raise ValidationFailed(details={"fields": {"cover_style": "Unknown style."}})
        setattr(user, key, value)
    index_user(db, user)
    db.commit()
    return user


def update_privacy(actor: Actor, changes: dict[str, bool]) -> dict[str, bool]:
    user = actor.user
    assert user is not None
    merged = {**DEFAULT_PRIVACY, **(user.privacy or {})}
    for key, value in changes.items():
        if key in DEFAULT_PRIVACY:
            merged[key] = bool(value)
    user.privacy = merged
    index_user(actor.db, user)
    actor.db.commit()
    return merged


def complete_onboarding(actor: Actor, data: dict[str, Any]) -> User:
    user = update_profile(actor, data)
    user.onboarding_completed_at = user.onboarding_completed_at or utcnow()
    actor.db.commit()
    return user


# ----------------------------------------------------------------------------- public profile


def _verified_university(db: Session, user: User) -> tuple[Organization | None, bool]:
    if not user.university_id:
        return None, False
    org = db.get(Organization, user.university_id)
    membership = db.scalar(select(OrgMembership).where(OrgMembership.user_id == user.id, OrgMembership.org_id == user.university_id,
                                                       OrgMembership.status == MembershipStatus.active))
    return org, bool(membership and membership.verified_at)


def _public_competition_clause() -> Any:
    return and_(Competition.visibility == CompetitionVisibility.public, Competition.lifecycle != Lifecycle.draft)


def activity_counts(db: Session, user_id: uuid.UUID, days: int = 365, include_github: bool = True) -> dict[str, int]:
    """Daily activity totals (aggregates only): submissions, discussion posts, lessons completed, merged PRs."""
    since = utcnow() - timedelta(days=days)
    counter: Counter[str] = Counter()
    queries = [
        select(cast(Submission.submitted_at, Date), func.count()).join(Competition, Competition.id == Submission.competition_id)
        .where(Submission.user_id == user_id, Submission.submitted_at > since, _public_competition_clause())
        .group_by(cast(Submission.submitted_at, Date)),
        select(cast(Comment.created_at, Date), func.count()).where(Comment.author_id == user_id, Comment.created_at > since,
                                                                     Comment.deleted_at.is_(None)).group_by(cast(Comment.created_at, Date)),
        select(cast(Thread.created_at, Date), func.count()).where(Thread.author_id == user_id, Thread.created_at > since,
                                                                   Thread.deleted_at.is_(None)).group_by(cast(Thread.created_at, Date)),
        select(cast(LessonProgress.completed_at, Date), func.count()).where(
            LessonProgress.user_id == user_id, LessonProgress.completed_at > since).group_by(cast(LessonProgress.completed_at, Date)),
    ]
    if include_github:
        gh = db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user_id))
        if gh:
            queries.append(select(cast(GitHubPullRequest.merged_at, Date), func.count()).where(
                GitHubPullRequest.author_github_id == gh.github_user_id, GitHubPullRequest.merged.is_(True),
                GitHubPullRequest.merged_at > since).group_by(cast(GitHubPullRequest.merged_at, Date)))
    for q in queries:
        for day, n in db.execute(q):
            if day:
                counter[day.isoformat()] += n
    return dict(counter)


def contributions(db: Session, user_id: uuid.UUID, limit: int = 50) -> dict[str, Any]:
    gh = db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user_id))
    if gh is None:
        return {"github_login": None, "merged_count": 0, "items": [], "repositories": 0}
    rows = db.execute(select(GitHubPullRequest, GitHubRepository).join(GitHubRepository, GitHubRepository.id == GitHubPullRequest.repo_id)
                      .where(GitHubPullRequest.author_github_id == gh.github_user_id, GitHubPullRequest.merged.is_(True),
                             GitHubRepository.is_private.is_(False))
                      .order_by(GitHubPullRequest.merged_at.desc()).limit(limit)).all()
    total = db.scalar(select(func.count()).select_from(GitHubPullRequest).join(GitHubRepository, GitHubRepository.id == GitHubPullRequest.repo_id)
                      .where(GitHubPullRequest.author_github_id == gh.github_user_id, GitHubPullRequest.merged.is_(True),
                             GitHubRepository.is_private.is_(False))) or 0
    return {
        "github_login": gh.login, "merged_count": total, "repositories": len({r.id for _, r in rows}),
        "items": [{"title": pr.title, "number": pr.number, "url": pr.html_url, "repo": repo.full_name, "merged_at": pr.merged_at,
                   "additions": pr.additions, "deletions": pr.deletions, "is_demo": repo.source == "seed"} for pr, repo in rows],
    }


def public_profile(actor: Actor, handle: str) -> dict[str, Any]:
    db = actor.db
    user = get_user_by_handle(db, handle)
    is_self = actor.id == user.id
    if user.status != UserStatus.active and not is_self and not actor.is_moderator:
        raise NotFound("Profile not found.")
    flag = (lambda k: True) if is_self else user.privacy_flag
    uni, uni_verified = _verified_university(db, user)
    dept = db.get(Department, user.department_id) if user.department_id else None

    comp_clause = _public_competition_clause() if not is_self else Competition.lifecycle != Lifecycle.draft
    results = db.execute(select(CompetitionResult, Competition).join(Competition, Competition.id == CompetitionResult.competition_id)
                         .where(CompetitionResult.user_id == user.id, comp_clause)
                         .order_by(CompetitionResult.rank.asc().nulls_last())).all()
    results = [(r, c) for r, c in results if is_self or not r.hidden_on_profile]
    joined = db.execute(select(Competition, CompetitionParticipant.joined_at).join(CompetitionParticipant, CompetitionParticipant.competition_id == Competition.id)
                        .where(CompetitionParticipant.user_id == user.id, comp_clause)
                        .order_by(CompetitionParticipant.joined_at.desc()).limit(30)).all()
    certs = db.scalars(select(Certificate).where(Certificate.recipient_id == user.id, Certificate.status == CertificateStatus.valid)
                       .order_by(Certificate.issued_at.desc())).all() if flag("show_certificates") else []
    certs = [c for c in certs if is_self or not c.hidden_on_profile]
    badges = db.execute(select(BadgeAward, BadgeDefinition).join(BadgeDefinition, BadgeDefinition.id == BadgeAward.badge_id)
                        .where(BadgeAward.user_id == user.id).order_by(BadgeAward.awarded_at.desc())).all() if flag("show_badges") else []
    badges = [(a, b) for a, b in badges if is_self or not a.hidden_on_profile]
    projects = db.execute(select(Project).outerjoin(ProjectMember, and_(ProjectMember.project_id == Project.id, ProjectMember.user_id == user.id))
                          .where(or_(Project.owner_id == user.id, ProjectMember.user_id == user.id), Project.taken_down.is_(False),
                                 or_(Project.visibility == ProjectVisibility.public, is_self), Project.status != "draft")
                          .order_by(Project.updated_at.desc()).limit(24)).scalars().unique().all()
    courses = db.execute(select(CourseEnrollment, Course).join(Course, Course.id == CourseEnrollment.course_id)
                         .where(CourseEnrollment.user_id == user.id, CourseEnrollment.completed_at.is_not(None),
                                or_(Course.visibility == "public", is_self))
                         .order_by(CourseEnrollment.completed_at.desc())).all()
    contrib = contributions(db, user.id) if flag("show_contributions") else None
    activity = activity_counts(db, user.id, include_github=bool(contrib)) if flag("show_activity") else None

    timeline: list[dict[str, Any]] = []
    for c, joined_at in joined[:10]:
        timeline.append({"kind": "competition_joined", "title": f"Joined {c.title}", "url": f"/competitions/{c.slug}", "at": joined_at})
    for a, b in badges[:10]:
        timeline.append({"kind": "badge", "title": f"Earned the {b.name} badge", "url": None, "at": a.awarded_at})
    for e, course in courses[:10]:
        timeline.append({"kind": "course_completed", "title": f"Completed {course.title}", "url": f"/learn/{course.slug}", "at": e.completed_at})
    for cert in certs[:10]:
        timeline.append({"kind": "certificate", "title": f"{cert.result_label} · {cert.event_title}", "url": f"/verify/{cert.public_id}",
                         "at": cert.issued_at})
    if contrib:
        for pr in contrib["items"][:10]:
            timeline.append({"kind": "contribution", "title": f"Merged #{pr['number']} in {pr['repo']}", "url": pr["url"], "at": pr["merged_at"]})
    timeline.sort(key=lambda t: t["at"] or datetime.min, reverse=True)

    return {
        "id": user.id, "handle": user.handle, "display_name": user.display_name, "headline": user.headline,
        "bio_html": user.bio_html, "avatar_url": user.avatar_url, "cover_style": user.cover_style, "website_url": user.website_url,
        "joined_at": user.created_at, "is_self": is_self, "is_demo": user.is_demo,
        "indexable": user.privacy_flag("indexable") and user.status == UserStatus.active,
        "verified": {
            "university": org_mini(uni).model_dump(mode="json") if (uni and uni_verified and flag("show_university")) else None,  # type: ignore[union-attr]
            "github_login": contrib["github_login"] if contrib else None,
            "email_verified": user.email_verified_at is not None,
        },
        "self_declared": {
            "university": org_mini(uni).model_dump(mode="json") if (uni and not uni_verified and flag("show_university")) else None,  # type: ignore[union-attr]
            "department": dept.name if dept and flag("show_department") else None,
            "graduation_year": user.graduation_year if flag("show_graduation_year") else None,
            "skills": list(user.skills or []) if flag("show_skills") else [],
            "interests": list(user.interests or []) if is_self else [],
        },
        "stats": {
            "competitions": len(joined), "top10_finishes": sum(1 for r, _ in results if r.rank and r.rank <= 10 and r.total_ranked > 10) +
            sum(1 for r, _ in results if r.rank == 1 and r.total_ranked <= 10),
            "certificates": len(certs), "badges": len(badges), "projects": len(projects), "courses_completed": len(courses),
            "merged_prs": contrib["merged_count"] if contrib else 0,
        },
        "results": [{"competition": {"slug": c.slug, "title": c.title, "is_demo": c.is_demo}, "is_demo": c.is_demo, "rank": r.rank, "total_ranked": r.total_ranked,
                     "label": r.label, "score": r.score, "hidden": r.hidden_on_profile, "id": r.id} for r, c in results],
        "competitions": [{"slug": c.slug, "title": c.title, "status": state.effective_status(c), "joined_at": j, "is_demo": c.is_demo}
                         for c, j in joined],
        "certificates": [{"public_id": c.public_id, "event_title": c.event_title, "result_label": c.result_label,
                          "issuer_name": c.issuer_name, "issued_at": c.issued_at, "hidden": c.hidden_on_profile, "is_demo": c.is_demo}
                         for c in certs],
        "badges": [{"id": a.id, "public_id": a.public_id, "slug": b.slug, "name": b.name, "description": b.description, "icon": b.icon,
                    "color": b.color, "category": b.category, "rarity_label": b.rarity_label, "awarded_at": a.awarded_at,
                    "manual": a.awarded_by is not None, "hidden": a.hidden_on_profile} for a, b in badges],
        "projects": [{"slug": p.slug, "title": p.title, "summary": p.summary, "tags": list(p.tags or []), "is_open_source": p.is_open_source,
                      "repo_url": p.repo_url, "demo_url": p.demo_url, "cover_style": p.cover_style, "is_demo": p.is_demo} for p in projects],
        "courses": [{"slug": c.slug, "title": c.title, "completed_at": e.completed_at} for e, c in courses],
        "contributions": contrib,
        "activity": activity,
        "timeline": timeline[:25] if flag("show_activity") else [],
    }


# ----------------------------------------------------------------------------- dashboard


def dashboard(actor: Actor) -> dict[str, Any]:
    db = actor.db
    user = actor.user
    assert user is not None
    now = utcnow()
    from app.modules.leaderboards.service import team_rank

    part_rows = db.execute(select(Competition, CompetitionParticipant).join(CompetitionParticipant, CompetitionParticipant.competition_id == Competition.id)
                           .where(CompetitionParticipant.user_id == user.id, Competition.lifecycle == Lifecycle.published)
                           .order_by(Competition.ends_at.asc().nulls_last())).all()
    active = []
    deadlines: list[dict[str, Any]] = []
    for comp, part in part_rows:
        team = db.get(Team, part.team_id) if part.team_id else None
        best = None
        rank = total = None
        if team is not None:
            agg = func.max if metric_direction(comp.evaluation or {}) == "maximize" else func.min
            best = db.scalar(select(agg(Submission.public_score)).where(Submission.team_id == team.id, Submission.status == "scored",
                                                                                  Submission.invalidated_at.is_(None)))
            if comp.scoring_mode == "automatic":
                rank, total = team_rank(db, comp, team.id)
        active.append({"slug": comp.slug, "title": comp.title, "status": state.effective_status(comp, now), "ends_at": comp.ends_at,
                       "team_name": team.name if team else None, "is_solo": bool(team and team.is_solo), "best_score": best,
                       "rank": rank, "ranked_teams": total, "metric": (comp.evaluation or {}).get("metric"),
                       "cover_style": comp.cover_style, "is_demo": comp.is_demo})
        for label, when in (("Submissions close", comp.ends_at), ("Registration closes", comp.registration_closes_at),
                            ("Team merge deadline", comp.team_lock_at)):
            if when and now < when < now + timedelta(days=30):
                deadlines.append({"title": f"{label}: {comp.title}", "at": when, "url": f"/competitions/{comp.slug}", "kind": "deadline"})
        for item in db.scalars(select(ScheduleItem).where(ScheduleItem.competition_id == comp.id, ScheduleItem.starts_at > now,
                                                          ScheduleItem.starts_at < now + timedelta(days=14))):
            deadlines.append({"title": f"{item.title} · {comp.title}", "at": item.starts_at, "url": f"/competitions/{comp.slug}", "kind": item.kind})
    deadlines.sort(key=lambda d: d["at"])

    team_ids = select(TeamMember.team_id).where(TeamMember.user_id == user.id)
    recent_subs = db.execute(select(Submission, Competition).join(Competition, Competition.id == Submission.competition_id)
                             .where(Submission.team_id.in_(team_ids)).order_by(Submission.submitted_at.desc()).limit(6)).all()
    invitations = db.execute(select(TeamInvitation, Team, Competition).join(Team, Team.id == TeamInvitation.team_id)
                             .join(Competition, Competition.id == TeamInvitation.competition_id)
                             .where(TeamInvitation.invitee_user_id == user.id, TeamInvitation.status == "pending")).all()
    contrib_notes = db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None),
                                                          Notification.kind.in_(["contribution_merged", "github_sync"]))
                               .order_by(Notification.created_at.desc()).limit(5)).all()
    recent_badges = db.execute(select(BadgeAward, BadgeDefinition).join(BadgeDefinition, BadgeDefinition.id == BadgeAward.badge_id)
                               .where(BadgeAward.user_id == user.id).order_by(BadgeAward.awarded_at.desc()).limit(3)).all()
    recent_certs = db.scalars(select(Certificate).where(Certificate.recipient_id == user.id).order_by(Certificate.issued_at.desc()).limit(3)).all()

    # Recommendations: declared interests only — labeled as such, no claims of personalization accuracy.
    interests = [i.lower() for i in (user.interests or [])]
    joined_ids = {c.id for c, _ in part_rows}
    candidates = db.scalars(select(Competition).where(Competition.lifecycle == Lifecycle.published,
                                                      Competition.visibility == CompetitionVisibility.public,
                                                      or_(Competition.ends_at.is_(None), Competition.ends_at > now))
                            .order_by(Competition.ends_at.asc().nulls_last()).limit(50)).all()
    recommended = []
    for c in candidates:
        if c.id in joined_ids:
            continue
        overlap = sorted({*(t.lower() for t in c.tags or [])} & set(interests) | ({c.task_type} & set(interests)))
        if interests and not overlap:
            continue
        recommended.append({"slug": c.slug, "title": c.title, "summary": c.summary, "ends_at": c.ends_at, "matched": overlap,
                            "cover_style": c.cover_style, "task_type": c.task_type, "is_demo": c.is_demo})
        if len(recommended) >= 4:
            break

    views = db.execute(select(RecentView).where(RecentView.user_id == user.id).order_by(RecentView.viewed_at.desc()).limit(8)).scalars().all()
    recent_items = []
    for v in views:
        if v.entity_type == "competition":
            c = db.get(Competition, v.entity_id)
            if c:
                recent_items.append({"type": "competition", "title": c.title, "url": f"/competitions/{c.slug}", "viewed_at": v.viewed_at})
        elif v.entity_type == "dataset":
            d = db.get(Dataset, v.entity_id)
            if d:
                recent_items.append({"type": "dataset", "title": d.title, "url": f"/datasets/{d.slug}", "viewed_at": v.viewed_at})

    learning = []
    for e, course in db.execute(select(CourseEnrollment, Course).join(Course, Course.id == CourseEnrollment.course_id)
                                .where(CourseEnrollment.user_id == user.id, CourseEnrollment.completed_at.is_(None))
                                .order_by(CourseEnrollment.enrolled_at.desc()).limit(4)).all():
        lesson = db.get(Lesson, e.last_lesson_id) if e.last_lesson_id else None
        learning.append({"slug": course.slug, "title": course.title, "progress_pct": e.progress_pct,
                         "resume_url": f"/learn/{course.slug}/{lesson.slug}" if lesson else f"/learn/{course.slug}"})

    checks = {
        "avatar": bool(user.avatar_url), "headline": bool(user.headline), "bio": bool(user.bio_md), "skills": bool(user.skills),
        "university": bool(user.university_id), "github": bool(db.scalar(select(GitHubAccount.id).where(GitHubAccount.user_id == user.id))),
        "verified_university": _verified_university(db, user)[1],
    }
    return {
        "greeting_name": user.display_name.split(" ")[0],
        "active_competitions": active,
        "deadlines": deadlines[:10],
        "recent_submissions": [{"id": s.id, "competition": {"slug": c.slug, "title": c.title}, "status": s.status,
                                "public_score": s.public_score, "submitted_at": s.submitted_at, "error_message": s.error_message}
                               for s, c in recent_subs],
        "invitations": [{"id": i.id, "team_name": t.name, "competition": {"slug": c.slug, "title": c.title}, "expires_at": i.expires_at}
                        for i, t, c in invitations],
        "contribution_notifications": [{"id": n.id, "title": n.title, "link": n.link, "created_at": n.created_at} for n in contrib_notes],
        "recent_badges": [{"name": b.name, "icon": b.icon, "color": b.color, "awarded_at": a.awarded_at} for a, b in recent_badges],
        "recent_certificates": [{"public_id": c.public_id, "event_title": c.event_title, "result_label": c.result_label,
                                 "issued_at": c.issued_at, "is_demo": c.is_demo} for c in recent_certs],
        "recommended": recommended,
        "recommendation_basis": "declared_interests" if interests else "upcoming_public",
        "recently_viewed": recent_items,
        "learning": learning,
        "activity": activity_counts(db, user.id, days=182),
        "profile_completion": {"percent": round(100 * sum(checks.values()) / len(checks)), "checks": checks},
        "prefs": user.dashboard_prefs or {},
    }


def set_dashboard_prefs(actor: Actor, hidden: list[str], order: list[str]) -> dict[str, Any]:
    user = actor.user
    assert user is not None
    allowed = {"competitions", "deadlines", "submissions", "invitations", "contributions", "credentials", "recommended",
               "recent", "learning", "activity", "completion"}
    user.dashboard_prefs = {"hidden": [h for h in hidden if h in allowed], "order": [o for o in order if o in allowed]}
    actor.db.commit()
    return user.dashboard_prefs


def set_achievement_visibility(actor: Actor, kind: str, item_id: str, hidden: bool) -> None:
    db = actor.db
    if kind != "certificate":
        try:
            uuid.UUID(item_id)
        except ValueError:
            raise NotFound() from None
    if kind == "certificate":
        obj = db.scalar(select(Certificate).where(Certificate.public_id == item_id, Certificate.recipient_id == actor.id))
    elif kind == "badge":
        obj = db.scalar(select(BadgeAward).where(BadgeAward.id == uuid.UUID(item_id), BadgeAward.user_id == actor.id))
    elif kind == "result":
        obj = db.scalar(select(CompetitionResult).where(CompetitionResult.id == uuid.UUID(item_id), CompetitionResult.user_id == actor.id))
    else:
        raise ValidationFailed("Unknown achievement type.")
    if obj is None:
        raise NotFound()
    obj.hidden_on_profile = hidden
    db.commit()


def activity_since(days: int) -> date:
    return date.today() - timedelta(days=days)
