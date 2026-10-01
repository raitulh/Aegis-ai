"""Badge rules engine.

Badges describe specific, verifiable accomplishments — never broad competence. Automatic badges are
awarded when their criteria are met (with the evidence stored on the award); manual badges are issued
by authorized organizers. Awards are unique per (badge, user). Criteria carry a version; changing a
criterion bumps the version but never revokes existing awards. Bulk recalculation is an explicit admin tool.

Criteria types:
    first_submission                  — at least one scored submission
    competition_rank {max_rank}       — a finalized placement at or above max_rank
    competitions_joined {count}       — joined at least `count` competitions
    course_completed {course_slug}    — completed a specific course
    courses_completed {count}         — completed at least `count` courses
    path_completed {path_slug}        — completed every course in a learning path
    merged_prs {count}                — merged pull requests to registered repositories (via linked GitHub)
    accepted_answers {count}          — discussion replies marked as accepted
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.ids import crockford_random, uuid7
from app.models.community import Comment, Thread
from app.models.competition import CompetitionParticipant
from app.models.credential import BadgeAward, BadgeDefinition
from app.models.enums import NotificationKind, SubmissionStatus
from app.models.github import GitHubAccount, GitHubPullRequest
from app.models.learning import Course, CourseEnrollment, LearningPath
from app.models.submission import CompetitionResult, Submission
from app.models.user import User
from app.modules.notifications.service import notify


def _check(db: Session, user_id: uuid.UUID, criteria: dict[str, Any]) -> dict[str, Any] | None:
    """Returns evidence when satisfied, else None."""
    kind = criteria.get("type")
    if kind == "first_submission":
        sub = db.scalar(select(Submission).where(Submission.user_id == user_id, Submission.status == SubmissionStatus.scored)
                        .order_by(Submission.submitted_at).limit(1))
        return {"submission_id": str(sub.id), "competition_id": str(sub.competition_id)} if sub else None
    if kind == "competition_rank":
        res = db.scalar(select(CompetitionResult).where(CompetitionResult.user_id == user_id,
                                                        CompetitionResult.rank <= int(criteria.get("max_rank", 10)))
                        .order_by(CompetitionResult.rank).limit(1))
        return {"competition_id": str(res.competition_id), "rank": res.rank, "of": res.total_ranked} if res else None
    if kind == "competitions_joined":
        n = db.scalar(select(func.count()).select_from(CompetitionParticipant).where(CompetitionParticipant.user_id == user_id)) or 0
        return {"count": n} if n >= int(criteria.get("count", 1)) else None
    if kind == "course_completed":
        row = db.scalar(select(CourseEnrollment).join(Course, Course.id == CourseEnrollment.course_id).where(
            CourseEnrollment.user_id == user_id, Course.slug == criteria.get("course_slug"), CourseEnrollment.completed_at.is_not(None)))
        return {"course_id": str(row.course_id)} if row else None
    if kind == "courses_completed":
        n = db.scalar(select(func.count()).select_from(CourseEnrollment).where(
            CourseEnrollment.user_id == user_id, CourseEnrollment.completed_at.is_not(None))) or 0
        return {"count": n} if n >= int(criteria.get("count", 1)) else None
    if kind == "path_completed":
        path = db.scalar(select(LearningPath).where(LearningPath.slug == criteria.get("path_slug")))
        if path is None or not path.course_ids:
            return None
        done = set(db.scalars(select(CourseEnrollment.course_id).where(
            CourseEnrollment.user_id == user_id, CourseEnrollment.completed_at.is_not(None),
            CourseEnrollment.course_id.in_(path.course_ids))))
        return {"path_id": str(path.id)} if done >= set(path.course_ids) else None
    if kind == "merged_prs":
        gh = db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user_id))
        if gh is None:
            return None
        n = db.scalar(select(func.count()).select_from(GitHubPullRequest).where(
            GitHubPullRequest.author_github_id == gh.github_user_id, GitHubPullRequest.merged.is_(True))) or 0
        return {"merged_prs": n, "github_login": gh.login} if n >= int(criteria.get("count", 1)) else None
    if kind == "accepted_answers":
        n = db.scalar(select(func.count()).select_from(Thread).join(Comment, Comment.id == Thread.accepted_comment_id)
                      .where(Comment.author_id == user_id)) or 0
        return {"count": n} if n >= int(criteria.get("count", 1)) else None
    return None


def _award(db: Session, badge: BadgeDefinition, user_id: uuid.UUID, evidence: dict[str, Any], awarded_by: uuid.UUID | None) -> bool:
    result = db.execute(insert(BadgeAward).values(
        id=uuid7(), badge_id=badge.id, user_id=user_id, public_id=f"BADGE-{crockford_random(10)}", evidence=evidence,
        criteria_version=badge.criteria_version, awarded_by=awarded_by,
    ).on_conflict_do_nothing(index_elements=["badge_id", "user_id"]).returning(BadgeAward.id))
    if result.first() is not None:
        notify(db, user_id, NotificationKind.badge_awarded, f"Badge earned: {badge.name}", body=badge.description,
               link="/settings/achievements", dedupe_key=f"badge:{badge.id}")
        return True
    return False


def evaluate_user_badges(db: Session, user_id: uuid.UUID, *, trigger: str | None = None) -> list[str]:
    owned = set(db.scalars(select(BadgeAward.badge_id).where(BadgeAward.user_id == user_id)))
    awarded: list[str] = []
    for badge in db.scalars(select(BadgeDefinition).where(BadgeDefinition.is_active.is_(True), BadgeDefinition.is_manual.is_(False))):
        if badge.id in owned:
            continue
        evidence = _check(db, user_id, badge.criteria or {})
        if evidence is not None and _award(db, badge, user_id, {**evidence, "trigger": trigger}, None):
            awarded.append(badge.slug)
    return awarded


def award_manual(actor: Actor, badge_slug: str, handle: str, reason: str) -> BadgeAward:
    db = actor.db
    badge = db.scalar(select(BadgeDefinition).where(BadgeDefinition.slug == badge_slug))
    if badge is None:
        raise NotFound("Badge not found.")
    if not badge.is_manual:
        raise Conflict("This badge is awarded automatically.", code="badge_automatic")
    if not (actor.is_admin or (badge.org_id and actor.is_org_manager(badge.org_id))):
        raise Forbidden()
    user = db.scalar(select(User).where(User.handle == handle.lower().lstrip("@")))
    if user is None:
        raise NotFound("No user with that handle.")
    if not _award(db, badge, user.id, {"reason": reason[:300], "manual": True}, actor.id):
        raise Conflict("This user already has the badge.", code="already_awarded")
    record_audit(db, actor.id, "badge.award_manual", target_type="user", target_id=user.id, reason=reason, org_id=badge.org_id,
                 meta={"badge": badge.slug})
    db.commit()
    award = db.scalar(select(BadgeAward).where(BadgeAward.badge_id == badge.id, BadgeAward.user_id == user.id))
    assert award is not None
    return award


def recalculate_all(actor: Actor) -> int:
    if not actor.is_admin:
        raise Forbidden()
    db = actor.db
    total = 0
    for uid in db.scalars(select(User.id).where(User.status == "active")):
        total += len(evaluate_user_badges(db, uid, trigger="admin_recalculation"))
    record_audit(db, actor.id, "badge.recalculate", meta={"awarded": total})
    db.commit()
    return total
