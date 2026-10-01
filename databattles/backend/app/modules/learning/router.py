from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.core.deps import Actor, get_actor, require_user, require_verified_user
from app.core.pagination import Page, PageParams, make_page
from app.core.rate_limit import enforce
from app.core.schemas import Message, Schema
from app.modules.learning import service

router = APIRouter(prefix="/learn", tags=["learning"])


class CourseWrite(Schema):
    title: str | None = Field(default=None, max_length=140)
    slug: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=300)
    description_md: str | None = Field(default=None, max_length=50_000)
    category: str | None = Field(default=None, max_length=32)
    difficulty: str | None = Field(default=None, pattern="^(beginner|intermediate|advanced)$")
    estimated_minutes: int | None = Field(default=None, ge=5, le=10_000)
    prerequisites: list[str] | None = Field(default=None, max_length=10)
    tags: list[str] | None = Field(default=None, max_length=20)
    visibility: str | None = Field(default=None, pattern="^(public|org)$")
    issues_certificate: bool | None = None
    cover_style: str | None = Field(default=None, max_length=32)
    badge_slug: str | None = Field(default=None, max_length=80)
    org_id: uuid.UUID | None = None


class LessonWrite(Schema):
    title: str | None = Field(default=None, max_length=140)
    kind: str | None = Field(default=None, pattern="^(article|quiz|challenge)$")
    body_md: str | None = Field(default=None, max_length=100_000)
    estimated_minutes: int | None = Field(default=None, ge=1, le=600)
    pass_threshold: int | None = Field(default=None, ge=1, le=100)
    challenge: dict[str, Any] | None = None


class QuestionIn(Schema):
    prompt: str = Field(min_length=3, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=8)
    correct_option: int = Field(ge=0, le=7)
    explanation: str | None = Field(default=None, max_length=2000)


class QuestionsIn(Schema):
    questions: list[QuestionIn] = Field(max_length=50)


class QuizAnswersIn(Schema):
    answers: dict[str, int] = Field(max_length=50)


class ReorderIn(Schema):
    slugs: list[str] = Field(max_length=200)


@router.get("/categories")
def categories() -> list[str]:
    return list(service.CATEGORIES)


@router.get("/courses", response_model=Page[dict])
def courses(params: PageParams = Depends(), q: str | None = Query(None, max_length=80), category: str | None = Query(None, max_length=32),
            difficulty: str | None = Query(None, pattern="^(beginner|intermediate|advanced)$"), org: str | None = Query(None, max_length=80),
            enrolled: bool | None = None, actor: Actor = Depends(get_actor)) -> dict:
    items, total = service.list_courses(actor, params, q=q, category=category, difficulty=difficulty, org=org, enrolled=enrolled)
    return make_page(items, total, params)


@router.get("/paths")
def paths(actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    return service.list_paths(actor)


@router.get("/me")
def my_learning(actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    return service.my_learning(actor)


@router.get("/authoring")
def authored(actor: Actor = Depends(require_user)) -> list[dict[str, Any]]:
    return service.authored_courses(actor)


@router.post("/courses", status_code=201)
def create_course(data: CourseWrite, actor: Actor = Depends(require_verified_user)) -> dict[str, Any]:
    return service.authoring_view(actor, service.create_course(actor, data.model_dump(exclude_unset=True)))


@router.get("/courses/{slug}")
def course_detail(slug: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return service.course_detail(actor, service.load_course(actor, slug))


@router.patch("/courses/{slug}")
def update_course(slug: str, data: CourseWrite, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    c = service.load_authorable(actor, slug)
    return service.authoring_view(actor, service.update_course(actor, c, data.model_dump(exclude_unset=True, exclude={"org_id", "slug"})))


@router.get("/courses/{slug}/authoring")
def authoring_view(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.authoring_view(actor, service.load_authorable(actor, slug))


@router.post("/courses/{slug}/publish")
def publish(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.authoring_view(actor, service.publish_course(actor, service.load_authorable(actor, slug)))


@router.post("/courses/{slug}/archive")
def archive(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.authoring_view(actor, service.archive_course(actor, service.load_authorable(actor, slug)))


@router.post("/courses/{slug}/lessons", status_code=201)
def add_lesson(slug: str, data: LessonWrite, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    c = service.load_authorable(actor, slug)
    service.upsert_lesson(actor, c, data.model_dump(exclude_unset=True))
    return service.authoring_view(actor, c)


@router.patch("/courses/{slug}/lessons/{lesson_slug}")
def update_lesson(slug: str, lesson_slug: str, data: LessonWrite, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    c = service.load_authorable(actor, slug)
    service.upsert_lesson(actor, c, data.model_dump(exclude_unset=True), lesson_slug)
    return service.authoring_view(actor, c)


@router.delete("/courses/{slug}/lessons/{lesson_slug}", response_model=Message)
def delete_lesson(slug: str, lesson_slug: str, actor: Actor = Depends(require_user)) -> Message:
    service.delete_lesson(actor, service.load_authorable(actor, slug), lesson_slug)
    return Message(message="Lesson deleted.")


@router.put("/courses/{slug}/lessons-order", response_model=Message)
def reorder(slug: str, data: ReorderIn, actor: Actor = Depends(require_user)) -> Message:
    service.reorder_lessons(actor, service.load_authorable(actor, slug), data.slugs)
    return Message(message="Lessons reordered.")


@router.put("/courses/{slug}/lessons/{lesson_slug}/questions", response_model=Message)
def set_questions(slug: str, lesson_slug: str, data: QuestionsIn, actor: Actor = Depends(require_user)) -> Message:
    service.set_questions(actor, service.load_authorable(actor, slug), lesson_slug, [q.model_dump() for q in data.questions])
    return Message(message="Questions saved.")


@router.post("/courses/{slug}/enroll")
def enroll(slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    c = service.load_course(actor, slug)
    service.enroll(actor, c)
    return service.course_detail(actor, c)


@router.post("/courses/{slug}/unenroll", response_model=Message)
def unenroll(slug: str, actor: Actor = Depends(require_user)) -> Message:
    service.unenroll(actor, service.load_course(actor, slug))
    return Message(message="You left the course.")


@router.get("/courses/{slug}/lessons/{lesson_slug}")
def lesson(slug: str, lesson_slug: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return service.lesson_detail(actor, service.load_course(actor, slug), lesson_slug)


@router.post("/courses/{slug}/lessons/{lesson_slug}/complete")
def complete(slug: str, lesson_slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    return service.complete_lesson(actor, service.load_course(actor, slug), lesson_slug)


@router.post("/courses/{slug}/lessons/{lesson_slug}/quiz")
def submit_quiz(slug: str, lesson_slug: str, data: QuizAnswersIn, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    enforce("quiz_submit", str(actor.id), limit=30, window_seconds=300)
    return service.submit_quiz(actor, service.load_course(actor, slug), lesson_slug, data.answers)


@router.post("/courses/{slug}/lessons/{lesson_slug}/check")
def check_challenge(slug: str, lesson_slug: str, actor: Actor = Depends(require_user)) -> dict[str, Any]:
    enforce("challenge_check", str(actor.id), limit=30, window_seconds=300)
    return service.check_challenge(actor, service.load_course(actor, slug), lesson_slug)
