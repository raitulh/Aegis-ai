"""Import every model module so SQLAlchemy metadata (and Alembic) sees all tables."""

from app.models.community import (  # noqa: F401
    Comment,
    DiscussionCategory,
    EmailOutbox,
    Notification,
    NotificationPreference,
    PostRevision,
    Report,
    Thread,
    ThreadMute,
)
from app.models.competition import (  # noqa: F401
    Announcement,
    AnnouncementRead,
    AwardCategory,
    Competition,
    CompetitionConfigVersion,
    CompetitionParticipant,
    CompetitionSponsor,
    CompetitionStaff,
    ScheduleItem,
    Team,
    TeamInvitation,
    TeamMember,
)
from app.models.credential import BadgeAward, BadgeDefinition, Certificate, CertificateTemplate  # noqa: F401
from app.models.dataset import (  # noqa: F401
    Dataset,
    DatasetDownloadStat,
    DatasetFile,
    DatasetTermsAcceptance,
    DatasetVersion,
)
from app.models.github import (  # noqa: F401
    GitHubAccount,
    GitHubIssue,
    GitHubPullRequest,
    GitHubRepository,
    WebhookDelivery,
)
from app.models.judging import (  # noqa: F401
    JudgeAssignment,
    JudgeConflict,
    JudgeScore,
    PresentationSlot,
    Rubric,
)
from app.models.learning import (  # noqa: F401
    Course,
    CourseEnrollment,
    LearningPath,
    Lesson,
    LessonProgress,
    QuizQuestion,
)
from app.models.org import (  # noqa: F401
    Department,
    Organization,
    OrgInvite,
    OrgMembership,
    OrgSubscription,
    Plan,
)
from app.models.project import Project, ProjectDataset, ProjectMedia, ProjectMember, Upload  # noqa: F401
from app.models.submission import (  # noqa: F401
    CompetitionResult,
    EvaluationAsset,
    EventSubmission,
    LeaderboardSnapshot,
    Submission,
)
from app.models.system import (  # noqa: F401
    AppErrorLog,
    AuditLog,
    FeatureFlag,
    Job,
    SearchDocument,
    SeedMarker,
)
from app.models.user import (  # noqa: F401
    AuthEvent,
    AuthSession,
    AuthToken,
    OAuthIdentity,
    PlatformRoleAssignment,
    RecentView,
    User,
)
