"""Canonical enumerations.

Roles and statuses live here — in one place — rather than as string literals
scattered through services and UI code. The database stores the string values.
"""

from __future__ import annotations

from enum import StrEnum


class PlatformRole(StrEnum):
    platform_admin = "platform_admin"
    moderator = "moderator"


class UserStatus(StrEnum):
    active = "active"
    suspended = "suspended"
    banned = "banned"
    deleted = "deleted"


class OrgType(StrEnum):
    university = "university"
    club = "club"
    community = "community"
    sponsor = "sponsor"
    company = "company"


class OrgRole(StrEnum):
    owner = "owner"          # full control incl. billing and ownership transfer
    admin = "admin"          # University Admin — institution-level management
    manager = "manager"      # Club / Community Manager
    member = "member"


class MembershipStatus(StrEnum):
    pending = "pending"
    active = "active"
    rejected = "rejected"
    removed = "removed"


class VerificationMethod(StrEnum):
    domain = "domain"
    invite = "invite"
    admin_review = "admin_review"
    none = "none"


class OrgVerification(StrEnum):
    unverified = "unverified"
    pending = "pending"
    verified = "verified"


class CompetitionVisibility(StrEnum):
    public = "public"            # listed, anyone may view and join
    university = "university"    # listed/visible only to members of the host organization
    invite_only = "invite_only"  # unlisted; viewable by link; joining needs an invite code
    private = "private"          # hidden from everyone except staff and invited participants


class Lifecycle(StrEnum):
    draft = "draft"
    published = "published"
    finalized = "finalized"
    archived = "archived"


class EffectiveStatus(StrEnum):
    """Derived from lifecycle + timestamps; never stored."""
    draft = "draft"
    upcoming = "upcoming"
    active = "active"
    ended = "ended"          # submissions closed, results pending
    completed = "completed"  # results finalized
    archived = "archived"


class EventType(StrEnum):
    ml_competition = "ml_competition"
    datathon = "datathon"
    hackathon = "hackathon"
    coding_contest = "coding_contest"
    research_challenge = "research_challenge"
    workshop = "workshop"
    demo_day = "demo_day"
    practice = "practice"


class TaskType(StrEnum):
    classification = "classification"
    regression = "regression"
    nlp = "nlp"
    computer_vision = "computer_vision"
    data_analysis = "data_analysis"
    coding = "coding"
    research = "research"
    hackathon = "hackathon"
    other = "other"


class Difficulty(StrEnum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class ScoringMode(StrEnum):
    automatic = "automatic"  # CSV predictions scored by an evaluator
    judged = "judged"        # human judging with rubrics
    none = "none"            # participation-only events (workshops)


class LeaderboardVisibility(StrEnum):
    visible = "visible"
    ranks_only = "ranks_only"
    hidden = "hidden"


class EventFormat(StrEnum):
    online = "online"
    offline = "offline"
    hybrid = "hybrid"


class StaffRole(StrEnum):
    organizer = "organizer"
    judge = "judge"


class SubmissionStatus(StrEnum):
    queued = "queued"
    validating = "validating"
    scoring = "scoring"
    scored = "scored"
    failed = "failed"      # infrastructure failure after retries
    rejected = "rejected"  # deterministic validation failure
    canceled = "canceled"


PENDING_SUBMISSION_STATUSES = (SubmissionStatus.queued, SubmissionStatus.validating, SubmissionStatus.scoring)


class InvitationStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    revoked = "revoked"
    expired = "expired"


class ContentVisibility(StrEnum):
    public = "public"
    org = "org"
    private = "private"


class ContentStatus(StrEnum):
    active = "active"
    taken_down = "taken_down"
    archived = "archived"


class VersionStatus(StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class ProjectStatus(StrEnum):
    draft = "draft"
    active = "active"
    archived = "archived"


class ProjectVisibility(StrEnum):
    public = "public"
    unlisted = "unlisted"
    private = "private"


class ProjectRole(StrEnum):
    owner = "owner"
    maintainer = "maintainer"
    contributor = "contributor"


class CertificateStatus(StrEnum):
    valid = "valid"
    revoked = "revoked"


class CertificateKind(StrEnum):
    competition_participation = "competition_participation"
    competition_award = "competition_award"
    course_completion = "course_completion"
    event_award = "event_award"


class BadgeCategory(StrEnum):
    learning = "learning"
    competition = "competition"
    contribution = "contribution"
    community = "community"


class LessonKind(StrEnum):
    article = "article"
    quiz = "quiz"
    challenge = "challenge"


class CourseStatus(StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class ReportStatus(StrEnum):
    open = "open"
    actioned = "actioned"
    dismissed = "dismissed"


class ReportReason(StrEnum):
    spam = "spam"
    abuse = "abuse"
    misleading = "misleading"
    copyright = "copyright"
    privacy = "privacy"
    other = "other"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"  # will retry
    dead = "dead"      # exhausted retries / permanent failure


class AnnouncementStatus(StrEnum):
    published = "published"
    pending_review = "pending_review"
    rejected = "rejected"


class JudgeScoreStatus(StrEnum):
    draft = "draft"
    submitted = "submitted"


class NotificationKind(StrEnum):
    team_invite = "team_invite"
    team_update = "team_update"
    submission_scored = "submission_scored"
    submission_rejected = "submission_rejected"
    deadline_reminder = "deadline_reminder"
    results_published = "results_published"
    certificate_issued = "certificate_issued"
    badge_awarded = "badge_awarded"
    mention = "mention"
    reply = "reply"
    announcement = "announcement"
    contribution_merged = "contribution_merged"
    github_sync = "github_sync"
    moderation = "moderation"
    org_membership = "org_membership"
    course_completed = "course_completed"
    judging = "judging"


# Kinds that email by default (users can opt out in notification settings).
EMAIL_DEFAULT_KINDS = {
    NotificationKind.team_invite,
    NotificationKind.deadline_reminder,
    NotificationKind.results_published,
    NotificationKind.certificate_issued,
    NotificationKind.moderation,
    NotificationKind.org_membership,
}
