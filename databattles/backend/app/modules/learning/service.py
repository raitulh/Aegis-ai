"""Learning: courses, lessons, quizzes, challenges, learning paths and progress.

* Quiz answers are graded on the server; correct options are never sent to the client before an attempt.
* Challenge lessons are completed only when the server observes the linked evidence (e.g. a scored
  submission in the linked practice competition) — clients cannot self-certify.
* Completing a course may award its badge and (if enabled) a verifiable course certificate.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.deps import Actor
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.markdown import render_markdown
from app.core.pagination import PageParams
from app.core.permissions import can_manage_org_content
from app.core.schemas import org_mini, user_mini
from app.core.slugs import unique_slug
from app.core.time import utcnow
from app.core.validators import clean_tags
from app.models.competition import Competition, TeamMember
from app.models.credential import BadgeDefinition, Certificate
from app.models.enums import ContentVisibility, CourseStatus, LessonKind, NotificationKind, SubmissionStatus
from app.models.learning import Course, CourseEnrollment, LearningPath, Lesson, LessonProgress, QuizQuestion
from app.models.org import Organization
from app.models.submission import Submission
from app.models.user import User
from app.modules.notifications.service import notify
from app.modules.search.indexer import index_course, remove

CATEGORIES = ("python", "data-analysis", "machine-learning", "deep-learning", "nlp", "computer-vision", "statistics",
              "sql", "git", "open-source", "competitions", "research", "ethics")


# ----------------------------------------------------------------------------- access


def can_author(actor: Actor, course: Course) -> bool:
    if not actor.is_authenticated:
        return False
    if actor.is_admin or course.author_id == actor.id:
        return True
    return bool(course.org_id and can_manage_org_content(actor, course.org_id))


def can_view_course(actor: Actor, course: Course) -> bool:
    if can_author(actor, course):
        return True
    if course.status != CourseStatus.published:
        return False
    if course.visibility == ContentVisibility.public:
        return True
    return course.visibility == ContentVisibility.org and bool(course.org_id and actor.is_org_member(course.org_id))


def load_course(actor: Actor, slug: str) -> Course:
    course = actor.db.scalar(select(Course).where(Course.slug == slug.lower()))
    if course is None or not can_view_course(actor, course):
        raise NotFound("Course not found.")
    return course


def load_authorable(actor: Actor, slug: str) -> Course:
    course = load_course(actor, slug)
    if not can_author(actor, course):
        raise Forbidden("Only the course authors can edit it.")
    return course


def _enrollment(db: Session, course_id: uuid.UUID, user_id: uuid.UUID | None) -> CourseEnrollment | None:
    if user_id is None:
        return None
    return db.scalar(select(CourseEnrollment).where(CourseEnrollment.course_id == course_id, CourseEnrollment.user_id == user_id))


# ----------------------------------------------------------------------------- catalog


def course_card(db: Session, c: Course, enrollment: CourseEnrollment | None = None, lesson_count: int | None = None) -> dict[str, Any]:
    org = db.get(Organization, c.org_id) if c.org_id else None
    return {
        "id": c.id, "slug": c.slug, "title": c.title, "summary": c.summary, "category": c.category, "difficulty": c.difficulty,
        "estimated_minutes": c.estimated_minutes, "tags": list(c.tags or []), "org": org_mini(org), "cover_style": c.cover_style,
        "enrollment_count": c.enrollment_count, "lesson_count": lesson_count, "status": c.status, "visibility": c.visibility,
        "issues_certificate": c.issues_certificate, "has_badge": c.badge_id is not None, "is_demo": c.is_demo,
        "progress": {"enrolled": enrollment is not None, "percent": enrollment.progress_pct if enrollment else 0,
                     "completed": bool(enrollment and enrollment.completed_at)},
    }


def _visible_clause(actor: Actor) -> Any:
    clause = (Course.status == CourseStatus.published) & (Course.visibility == ContentVisibility.public)
    if actor.is_authenticated:
        orgs = actor.member_org_ids()
        extra = [Course.author_id == actor.id]
        if orgs:
            extra.append((Course.status == CourseStatus.published) & (Course.visibility == ContentVisibility.org) & Course.org_id.in_(orgs))
        clause = or_(clause, *extra)
    return clause


def list_courses(actor: Actor, params: PageParams, *, q: str | None, category: str | None, difficulty: str | None,
                 org: str | None, enrolled: bool | None) -> tuple[list[dict[str, Any]], int]:
    db = actor.db
    stmt = select(Course).where(_visible_clause(actor))
    if q:
        like = f"%{q.strip()[:80]}%"
        stmt = stmt.where(or_(Course.title.ilike(like), Course.summary.ilike(like)))
    if category:
        stmt = stmt.where(Course.category == category)
    if difficulty:
        stmt = stmt.where(Course.difficulty == difficulty)
    if org:
        stmt = stmt.join(Organization, Organization.id == Course.org_id).where(Organization.slug == org)
    if enrolled and actor.is_authenticated:
        stmt = stmt.where(Course.id.in_(select(CourseEnrollment.course_id).where(CourseEnrollment.user_id == actor.id)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Course.enrollment_count.desc(), Course.title).limit(params.page_size).offset(params.offset)).all()
    ids = [c.id for c in rows]
    lesson_counts = dict(db.execute(select(Lesson.course_id, func.count()).where(Lesson.course_id.in_(ids)).group_by(Lesson.course_id)).all())
    enrollments = {e.course_id: e for e in db.scalars(select(CourseEnrollment).where(
        CourseEnrollment.course_id.in_(ids), CourseEnrollment.user_id == actor.id))} if actor.is_authenticated else {}
    return [course_card(db, c, enrollments.get(c.id), lesson_counts.get(c.id, 0)) for c in rows], total


def list_paths(actor: Actor) -> list[dict[str, Any]]:
    db = actor.db
    out = []
    for path in db.scalars(select(LearningPath).order_by(LearningPath.title)):
        courses = {c.id: c for c in db.scalars(select(Course).where(Course.id.in_(path.course_ids or [])))}
        ordered = [courses[cid] for cid in path.course_ids or [] if cid in courses and can_view_course(actor, courses[cid])]
        done = set()
        if actor.is_authenticated:
            done = set(db.scalars(select(CourseEnrollment.course_id).where(
                CourseEnrollment.user_id == actor.id, CourseEnrollment.completed_at.is_not(None),
                CourseEnrollment.course_id.in_([c.id for c in ordered]))))
        badge = db.get(BadgeDefinition, path.badge_id) if path.badge_id else None
        out.append({"slug": path.slug, "title": path.title, "summary": path.summary,
                    "courses": [{"slug": c.slug, "title": c.title, "difficulty": c.difficulty, "completed": c.id in done,
                                 "estimated_minutes": c.estimated_minutes} for c in ordered],
                    "completed_count": len(done), "badge": {"name": badge.name, "icon": badge.icon, "color": badge.color} if badge else None})
    return out


def course_detail(actor: Actor, course: Course) -> dict[str, Any]:
    db = actor.db
    lessons = db.scalars(select(Lesson).where(Lesson.course_id == course.id).order_by(Lesson.position)).all()
    enrollment = _enrollment(db, course.id, actor.id)
    progress: dict[uuid.UUID, LessonProgress] = {}
    if enrollment:
        progress = {p.lesson_id: p for p in db.scalars(select(LessonProgress).where(
            LessonProgress.course_id == course.id, LessonProgress.user_id == actor.id))}
    badge = db.get(BadgeDefinition, course.badge_id) if course.badge_id else None
    author = db.get(User, course.author_id) if course.author_id else None
    resume = None
    if enrollment:
        nxt = next((lesson for lesson in lessons if (p := progress.get(lesson.id)) is None or p.status != "completed"), None)
        resume = (nxt or (lessons[-1] if lessons else None))
    cert = None
    if enrollment and enrollment.completed_at and course.issues_certificate:
        cert = db.scalar(select(Certificate.public_id).where(Certificate.course_id == course.id, Certificate.recipient_id == actor.id))
    return {
        **course_card(db, course, enrollment, len(lessons)),
        "description_html": course.description_html, "prerequisites": list(course.prerequisites or []), "author": user_mini(author),
        "version": course.version, "published_at": course.published_at,
        "badge": {"slug": badge.slug, "name": badge.name, "icon": badge.icon, "color": badge.color, "description": badge.description}
        if badge else None,
        "lessons": [{"id": lesson.id, "slug": lesson.slug, "title": lesson.title, "kind": lesson.kind, "position": lesson.position,
                     "estimated_minutes": lesson.estimated_minutes,
                     "status": progress[lesson.id].status if lesson.id in progress else "not_started",
                     "quiz_score": progress[lesson.id].quiz_score if lesson.id in progress else None} for lesson in lessons],
        "resume_lesson_slug": resume.slug if resume else None,
        "certificate_public_id": cert,
        "can_author": can_author(actor, course),
        "outdated_enrollment": bool(enrollment and enrollment.course_version < course.version),
    }


# ----------------------------------------------------------------------------- enrollment & progress


def enroll(actor: Actor, course: Course) -> CourseEnrollment:
    db = actor.db
    if course.status != CourseStatus.published:
        raise Conflict("This course is not published yet.", code="course_unpublished")
    e = _enrollment(db, course.id, actor.id)
    if e:
        return e
    e = CourseEnrollment(course_id=course.id, user_id=actor.id, course_version=course.version)
    db.add(e)
    course.enrollment_count = (course.enrollment_count or 0) + 1
    db.commit()
    return e


def unenroll(actor: Actor, course: Course) -> None:
    db = actor.db
    e = _enrollment(db, course.id, actor.id)
    if e is None:
        raise NotFound("You are not enrolled.")
    if e.completed_at:
        raise Conflict("Completed courses stay on your record.", code="course_completed")
    db.delete(e)
    course.enrollment_count = max(0, (course.enrollment_count or 1) - 1)
    db.commit()


def _lesson(db: Session, course: Course, lesson_slug: str) -> Lesson:
    lesson = db.scalar(select(Lesson).where(Lesson.course_id == course.id, Lesson.slug == lesson_slug))
    if lesson is None:
        raise NotFound("Lesson not found.")
    return lesson


def lesson_detail(actor: Actor, course: Course, lesson_slug: str) -> dict[str, Any]:
    db = actor.db
    lesson = _lesson(db, course, lesson_slug)
    slugs = [row for row in db.execute(select(Lesson.slug, Lesson.title).where(Lesson.course_id == course.id).order_by(Lesson.position))]
    idx = next((i for i, row in enumerate(slugs) if row[0] == lesson.slug), 0)
    enrollment = _enrollment(db, course.id, actor.id)
    progress = None
    if enrollment:
        progress = db.scalar(select(LessonProgress).where(LessonProgress.lesson_id == lesson.id, LessonProgress.user_id == actor.id))
        if enrollment.last_lesson_id != lesson.id:
            enrollment.last_lesson_id = lesson.id
            db.commit()
    passed = bool(progress and progress.status == "completed")
    questions = []
    if lesson.kind == LessonKind.quiz:
        for q in db.scalars(select(QuizQuestion).where(QuizQuestion.lesson_id == lesson.id).order_by(QuizQuestion.position)):
            item: dict[str, Any] = {"id": q.id, "prompt": q.prompt, "options": q.options}
            if passed:  # reveal answers only after the learner has passed
                item["correct_option"] = q.correct_option
                item["explanation"] = q.explanation
            questions.append(item)
    challenge = None
    if lesson.kind == LessonKind.challenge and lesson.challenge:
        comp_slug = lesson.challenge.get("competition_slug")
        comp = db.scalar(select(Competition).where(Competition.slug == comp_slug)) if comp_slug else None
        challenge = {"type": lesson.challenge.get("type", "prediction"), "instructions": lesson.challenge.get("instructions", ""),
                     "competition": {"slug": comp.slug, "title": comp.title} if comp else None}
    return {
        "course": {"slug": course.slug, "title": course.title}, "id": lesson.id, "slug": lesson.slug, "title": lesson.title,
        "kind": lesson.kind, "body_html": lesson.body_html, "estimated_minutes": lesson.estimated_minutes,
        "pass_threshold": lesson.pass_threshold, "questions": questions, "challenge": challenge,
        "progress": {"status": progress.status if progress else "not_started", "quiz_score": progress.quiz_score if progress else None,
                     "attempts": progress.attempts if progress else 0},
        "enrolled": enrollment is not None,
        "prev": {"slug": slugs[idx - 1][0], "title": slugs[idx - 1][1]} if idx > 0 else None,
        "next": {"slug": slugs[idx + 1][0], "title": slugs[idx + 1][1]} if idx + 1 < len(slugs) else None,
        "position": idx + 1, "total": len(slugs),
    }


def _require_enrollment(actor: Actor, course: Course) -> CourseEnrollment:
    e = _enrollment(actor.db, course.id, actor.id)
    if e is None:
        raise Conflict("Enroll in the course to track progress.", code="not_enrolled")
    return e


def _progress_row(db: Session, lesson: Lesson, user_id: uuid.UUID) -> LessonProgress:
    p = db.scalar(select(LessonProgress).where(LessonProgress.lesson_id == lesson.id, LessonProgress.user_id == user_id))
    if p is None:
        p = LessonProgress(lesson_id=lesson.id, course_id=lesson.course_id, user_id=user_id, status="in_progress", attempts=0)
        db.add(p)
        db.flush()
    return p


def _recompute(actor: Actor, course: Course, enrollment: CourseEnrollment) -> None:
    db = actor.db
    total = db.scalar(select(func.count()).select_from(Lesson).where(Lesson.course_id == course.id)) or 0
    done = db.scalar(select(func.count()).select_from(LessonProgress).join(Lesson, Lesson.id == LessonProgress.lesson_id).where(
        LessonProgress.course_id == course.id, LessonProgress.user_id == actor.id, LessonProgress.status == "completed")) or 0
    enrollment.progress_pct = round(100 * done / total) if total else 0
    if total and done >= total and enrollment.completed_at is None:
        enrollment.completed_at = utcnow()
        user = actor.user
        assert user is not None
        notify(db, user.id, NotificationKind.course_completed, f"You completed {course.title}", link=f"/learn/{course.slug}",
               dedupe_key=f"coursedone:{course.id}:{user.id}")
        if course.badge_id:
            from app.modules.credentials.badges import _award

            badge = db.get(BadgeDefinition, course.badge_id)
            if badge and badge.is_active:
                _award(db, badge, user.id, {"course": course.slug, "trigger": "course_completed"}, None)
        if course.issues_certificate:
            from app.modules.credentials.certificates import issue_course_certificate

            issue_course_certificate(db, user, course)
        db.flush()
        from app.modules.credentials.badges import evaluate_user_badges

        evaluate_user_badges(db, user.id, trigger="course_completed")


def complete_lesson(actor: Actor, course: Course, lesson_slug: str) -> dict[str, Any]:
    db = actor.db
    enrollment = _require_enrollment(actor, course)
    lesson = _lesson(db, course, lesson_slug)
    if lesson.kind == LessonKind.quiz:
        raise Conflict("Pass the quiz to complete this lesson.", code="quiz_required")
    if lesson.kind == LessonKind.challenge:
        return check_challenge(actor, course, lesson_slug)
    p = _progress_row(db, lesson, actor.id)  # type: ignore[arg-type]
    if p.status != "completed":
        p.status = "completed"
        p.completed_at = utcnow()
    enrollment.last_lesson_id = lesson.id
    _recompute(actor, course, enrollment)
    db.commit()
    return {"status": "completed", "course_progress": enrollment.progress_pct, "course_completed": enrollment.completed_at is not None}


def submit_quiz(actor: Actor, course: Course, lesson_slug: str, answers: dict[str, int]) -> dict[str, Any]:
    db = actor.db
    enrollment = _require_enrollment(actor, course)
    lesson = _lesson(db, course, lesson_slug)
    if lesson.kind != LessonKind.quiz:
        raise ValidationFailed("This lesson has no quiz.")
    questions = db.scalars(select(QuizQuestion).where(QuizQuestion.lesson_id == lesson.id).order_by(QuizQuestion.position)).all()
    if not questions:
        raise Conflict("This quiz has no questions yet.", code="quiz_empty")
    p = _progress_row(db, lesson, actor.id)  # type: ignore[arg-type]
    if p.attempts >= 50:
        raise Conflict("Attempt limit reached for this quiz.", code="quiz_attempt_limit")
    results = []
    correct = 0
    for q in questions:
        chosen = answers.get(str(q.id))
        ok = chosen is not None and chosen == q.correct_option
        correct += int(ok)
        results.append({"question_id": q.id, "correct": ok, "chosen": chosen})
    score = round(100 * correct / len(questions))
    passed = score >= lesson.pass_threshold
    p.attempts += 1
    p.quiz_score = max(p.quiz_score or 0, score)
    if passed and p.status != "completed":
        p.status = "completed"
        p.completed_at = utcnow()
    # Explanations/correct answers are revealed only once passed (prevents answer harvesting by guessing).
    if passed:
        by_id = {q.id: q for q in questions}
        for r in results:
            q = by_id[r["question_id"]]
            r["correct_option"] = q.correct_option
            r["explanation"] = q.explanation
    enrollment.last_lesson_id = lesson.id
    _recompute(actor, course, enrollment)
    db.commit()
    return {"score": score, "passed": passed, "pass_threshold": lesson.pass_threshold, "correct": correct, "total": len(questions),
            "attempts": p.attempts, "results": results, "course_progress": enrollment.progress_pct,
            "course_completed": enrollment.completed_at is not None}


def check_challenge(actor: Actor, course: Course, lesson_slug: str) -> dict[str, Any]:
    db = actor.db
    enrollment = _require_enrollment(actor, course)
    lesson = _lesson(db, course, lesson_slug)
    if lesson.kind != LessonKind.challenge or not lesson.challenge:
        raise ValidationFailed("This lesson is not a challenge.")
    comp = db.scalar(select(Competition).where(Competition.slug == lesson.challenge.get("competition_slug", "")))
    if comp is None:
        raise Conflict("The linked practice competition is unavailable.", code="challenge_unavailable")
    team_ids = select(TeamMember.team_id).where(TeamMember.user_id == actor.id, TeamMember.competition_id == comp.id)
    min_score = lesson.challenge.get("min_score")
    stmt = select(Submission).where(Submission.competition_id == comp.id, Submission.team_id.in_(team_ids),
                                    Submission.status == SubmissionStatus.scored, Submission.invalidated_at.is_(None))
    evidence = db.scalars(stmt.order_by(Submission.submitted_at.desc()).limit(20)).all()
    ok = bool(evidence)
    if ok and min_score is not None:
        from app.evaluation.registry import metric_direction

        maximize = metric_direction(comp.evaluation or {}) == "maximize"
        ok = any(s.public_score is not None and (s.public_score >= float(min_score) if maximize else s.public_score <= float(min_score))
                 for s in evidence)
    p = _progress_row(db, lesson, actor.id)  # type: ignore[arg-type]
    p.attempts += 1
    if ok and p.status != "completed":
        p.status = "completed"
        p.completed_at = utcnow()
    enrollment.last_lesson_id = lesson.id
    _recompute(actor, course, enrollment)
    db.commit()
    return {"status": p.status, "passed": ok, "competition": {"slug": comp.slug, "title": comp.title},
            "message": "Challenge complete — a scored submission was found." if ok else
            ("Make a scored submission to the linked competition" + (f" reaching {min_score}" if min_score is not None else "")
             + ", then check again."),
            "course_progress": enrollment.progress_pct, "course_completed": enrollment.completed_at is not None}


def my_learning(actor: Actor) -> list[dict[str, Any]]:
    db = actor.db
    rows = db.execute(select(CourseEnrollment, Course).join(Course, Course.id == CourseEnrollment.course_id)
                      .where(CourseEnrollment.user_id == actor.id).order_by(CourseEnrollment.enrolled_at.desc())).all()
    out = []
    for e, c in rows:
        lesson = db.get(Lesson, e.last_lesson_id) if e.last_lesson_id else None
        out.append({**course_card(db, c, e), "enrolled_at": e.enrolled_at, "completed_at": e.completed_at,
                    "resume_url": f"/learn/{c.slug}/{lesson.slug}" if lesson else f"/learn/{c.slug}"})
    return out


# ----------------------------------------------------------------------------- authoring


def _apply_course(actor: Actor, c: Course, data: dict[str, Any]) -> None:
    if "title" in data and data["title"]:
        c.title = data["title"].strip()[:140]
    if "summary" in data and data["summary"] is not None:
        c.summary = data["summary"].strip()[:300]
    if "description_md" in data and data["description_md"] is not None:
        c.description_md = data["description_md"]
        c.description_html = render_markdown(c.description_md)
    if "category" in data and data["category"]:
        if data["category"] not in CATEGORIES:
            raise ValidationFailed(details={"fields": {"category": "Unknown category."}})
        c.category = data["category"]
    if "difficulty" in data and data["difficulty"]:
        c.difficulty = data["difficulty"]
    if "estimated_minutes" in data and data["estimated_minutes"]:
        c.estimated_minutes = max(5, min(int(data["estimated_minutes"]), 10_000))
    if "prerequisites" in data and data["prerequisites"] is not None:
        c.prerequisites = [p.strip()[:120] for p in data["prerequisites"] if p and p.strip()][:10]
    if "tags" in data and data["tags"] is not None:
        c.tags = clean_tags(data["tags"])
    if "visibility" in data and data["visibility"]:
        if data["visibility"] == ContentVisibility.org and not c.org_id:
            raise ValidationFailed(details={"fields": {"visibility": "Organization-only courses need an organization."}})
        c.visibility = data["visibility"]
    if "issues_certificate" in data and data["issues_certificate"] is not None:
        c.issues_certificate = bool(data["issues_certificate"])
    if "cover_style" in data and data["cover_style"]:
        c.cover_style = data["cover_style"][:32]
    if "badge_slug" in data:
        if data["badge_slug"]:
            badge = actor.db.scalar(select(BadgeDefinition).where(BadgeDefinition.slug == data["badge_slug"]))
            if badge is None:
                raise ValidationFailed(details={"fields": {"badge_slug": "Unknown badge."}})
            c.badge_id = badge.id
        else:
            c.badge_id = None


def create_course(actor: Actor, data: dict[str, Any]) -> Course:
    db = actor.db
    org_id = data.get("org_id")
    if org_id and not can_manage_org_content(actor, org_id):
        raise Forbidden("You can only create courses for organizations you manage.")
    if not org_id and not actor.is_admin:
        managed = [oid for oid in actor.member_org_ids() if actor.is_org_manager(oid)]
        if not managed:
            raise Forbidden("Course authoring is available to platform staff and organization managers.")
    title = (data.get("title") or "").strip()
    if len(title) < 3:
        raise ValidationFailed(details={"fields": {"title": "Enter at least 3 characters."}})
    c = Course(slug=unique_slug(db, Course, title, requested=data.get("slug")), title=title, org_id=org_id, author_id=actor.id,
               status=CourseStatus.draft, category=data.get("category") or "machine-learning", visibility=ContentVisibility.public)
    _apply_course(actor, c, data)
    db.add(c)
    db.flush()
    record_audit(db, actor.id, "course.create", target_type="course", target_id=c.id, org_id=org_id)
    db.commit()
    return c


def update_course(actor: Actor, c: Course, data: dict[str, Any]) -> Course:
    _apply_course(actor, c, data)
    if c.status == CourseStatus.published:
        if c.visibility == ContentVisibility.public:
            index_course(actor.db, c)
        else:
            remove(actor.db, "course", c.id)
    record_audit(actor.db, actor.id, "course.update", target_type="course", target_id=c.id, org_id=c.org_id)
    actor.db.commit()
    return c


def publish_course(actor: Actor, c: Course) -> Course:
    db = actor.db
    lessons = db.scalars(select(Lesson).where(Lesson.course_id == c.id)).all()
    if not lessons:
        raise Conflict("Add at least one lesson before publishing.", code="course_empty")
    for lesson in lessons:
        if lesson.kind == LessonKind.quiz and not db.scalar(select(QuizQuestion.id).where(QuizQuestion.lesson_id == lesson.id).limit(1)):
            raise Conflict(f"The quiz “{lesson.title}” has no questions.", code="quiz_empty")
    if c.status == CourseStatus.published:
        c.version += 1  # re-publish after edits: enrollments on older versions are flagged, not reset
    c.status = CourseStatus.published
    c.published_at = c.published_at or utcnow()
    if c.visibility == ContentVisibility.public:
        index_course(db, c)
    record_audit(db, actor.id, "course.publish", target_type="course", target_id=c.id, org_id=c.org_id, meta={"version": c.version})
    db.commit()
    return c


def archive_course(actor: Actor, c: Course) -> Course:
    c.status = CourseStatus.archived
    remove(actor.db, "course", c.id)
    record_audit(actor.db, actor.id, "course.archive", target_type="course", target_id=c.id, org_id=c.org_id)
    actor.db.commit()
    return c


def upsert_lesson(actor: Actor, c: Course, data: dict[str, Any], lesson_slug: str | None = None) -> Lesson:
    db = actor.db
    if lesson_slug:
        lesson = _lesson(db, c, lesson_slug)
    else:
        title = (data.get("title") or "").strip()
        if len(title) < 2:
            raise ValidationFailed(details={"fields": {"title": "Enter a title."}})
        from app.core.ids import slugify

        base = slugify(title, 60)
        slug, n = base, 2
        while db.scalar(select(Lesson.id).where(Lesson.course_id == c.id, Lesson.slug == slug)):
            slug, n = f"{base}-{n}", n + 1
        position = (db.scalar(select(func.max(Lesson.position)).where(Lesson.course_id == c.id)) or 0) + 1
        lesson = Lesson(course_id=c.id, slug=slug, title=title[:140], position=position, kind=LessonKind.article)
        db.add(lesson)
    if "title" in data and data["title"]:
        lesson.title = data["title"].strip()[:140]
    if "kind" in data and data["kind"]:
        lesson.kind = LessonKind(data["kind"])
    if "body_md" in data and data["body_md"] is not None:
        lesson.body_md = data["body_md"]
        lesson.body_html = render_markdown(lesson.body_md)
    if "estimated_minutes" in data and data["estimated_minutes"]:
        lesson.estimated_minutes = max(1, min(int(data["estimated_minutes"]), 600))
    if "pass_threshold" in data and data["pass_threshold"] is not None:
        lesson.pass_threshold = max(1, min(int(data["pass_threshold"]), 100))
    if "challenge" in data:
        ch = data["challenge"]
        if ch:
            slug = str(ch.get("competition_slug") or "")
            if not db.scalar(select(Competition.id).where(Competition.slug == slug)):
                raise ValidationFailed(details={"fields": {"challenge": "Link an existing competition."}})
            lesson.challenge = {"type": "prediction", "competition_slug": slug, "instructions": str(ch.get("instructions") or "")[:2000],
                                "min_score": float(ch["min_score"]) if ch.get("min_score") is not None else None}
        else:
            lesson.challenge = None
    if lesson_slug:
        lesson.version += 1
    db.commit()
    return lesson


def delete_lesson(actor: Actor, c: Course, lesson_slug: str) -> None:
    db = actor.db
    lesson = _lesson(db, c, lesson_slug)
    db.delete(lesson)
    db.flush()
    for i, row in enumerate(db.scalars(select(Lesson).where(Lesson.course_id == c.id).order_by(Lesson.position)), start=1):
        row.position = i
    db.commit()


def reorder_lessons(actor: Actor, c: Course, slugs: list[str]) -> None:
    db = actor.db
    lessons = {lesson.slug: lesson for lesson in db.scalars(select(Lesson).where(Lesson.course_id == c.id))}
    if set(slugs) != set(lessons):
        raise ValidationFailed("Provide every lesson exactly once.")
    for i, s in enumerate(slugs, start=1):
        lessons[s].position = i + 10_000  # avoid transient collisions on unique indexes if added later
    db.flush()
    for i, s in enumerate(slugs, start=1):
        lessons[s].position = i
    db.commit()


def set_questions(actor: Actor, c: Course, lesson_slug: str, questions: list[dict[str, Any]]) -> list[QuizQuestion]:
    db = actor.db
    lesson = _lesson(db, c, lesson_slug)
    if lesson.kind != LessonKind.quiz:
        raise ValidationFailed("Only quiz lessons have questions.")
    for q in db.scalars(select(QuizQuestion).where(QuizQuestion.lesson_id == lesson.id)):
        db.delete(q)
    db.flush()
    out = []
    for i, item in enumerate(questions[:50], start=1):
        options = [str(o)[:300] for o in item.get("options") or [] if str(o).strip()][:8]
        if len(options) < 2:
            raise ValidationFailed(details={"fields": {f"questions.{i}": "Each question needs at least two options."}})
        correct = int(item.get("correct_option", -1))
        if not 0 <= correct < len(options):
            raise ValidationFailed(details={"fields": {f"questions.{i}": "Mark the correct option."}})
        row = QuizQuestion(lesson_id=lesson.id, position=i, prompt=str(item.get("prompt") or "")[:2000], options=options,
                           correct_option=correct, explanation=(str(item.get("explanation") or "")[:2000] or None))
        db.add(row)
        out.append(row)
    lesson.version += 1
    db.commit()
    return out


def authoring_view(actor: Actor, c: Course) -> dict[str, Any]:
    db = actor.db
    lessons = db.scalars(select(Lesson).where(Lesson.course_id == c.id).order_by(Lesson.position)).all()
    qs: dict[uuid.UUID, list[QuizQuestion]] = {}
    for q in db.scalars(select(QuizQuestion).where(QuizQuestion.lesson_id.in_([lesson.id for lesson in lessons])).order_by(QuizQuestion.position)):
        qs.setdefault(q.lesson_id, []).append(q)
    enrolled = db.scalar(select(func.count()).select_from(CourseEnrollment).where(CourseEnrollment.course_id == c.id)) or 0
    completed = db.scalar(select(func.count()).select_from(CourseEnrollment).where(
        CourseEnrollment.course_id == c.id, CourseEnrollment.completed_at.is_not(None))) or 0
    badge = db.get(BadgeDefinition, c.badge_id) if c.badge_id else None
    return {
        **course_card(db, c, None, len(lessons)), "description_md": c.description_md, "prerequisites": list(c.prerequisites or []),
        "version": c.version, "badge_slug": badge.slug if badge else None, "org_id": c.org_id,
        "stats": {"enrolled": enrolled, "completed": completed},
        "lessons": [{"id": lesson.id, "slug": lesson.slug, "title": lesson.title, "kind": lesson.kind, "position": lesson.position,
                     "body_md": lesson.body_md, "estimated_minutes": lesson.estimated_minutes, "pass_threshold": lesson.pass_threshold,
                     "challenge": lesson.challenge,
                     "questions": [{"id": q.id, "prompt": q.prompt, "options": q.options, "correct_option": q.correct_option,
                                    "explanation": q.explanation} for q in qs.get(lesson.id, [])]} for lesson in lessons],
    }


def authored_courses(actor: Actor) -> list[dict[str, Any]]:
    db = actor.db
    managed_orgs = [oid for oid in actor.member_org_ids() if actor.is_org_manager(oid)]
    clause = Course.author_id == actor.id
    if managed_orgs:
        clause = or_(clause, Course.org_id.in_(managed_orgs))
    if actor.is_admin:
        clause = or_(clause, Course.org_id.is_(None))
    return [course_card(db, c) for c in db.scalars(select(Course).where(clause).order_by(Course.updated_at.desc()).limit(200))]
