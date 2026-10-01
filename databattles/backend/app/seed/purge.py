"""Removes all demo data created by the seed (rows flagged is_demo / source='seed').

Platform configuration (plans, badge catalog, discussion categories, default certificate template,
feature flags) is kept. Audit entries survive with the actor pseudonymized (ON DELETE SET NULL).
"""

from __future__ import annotations

from contextlib import suppress

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.models.community import Comment, Notification, Report, Thread
from app.models.competition import Competition, Team, TeamMember
from app.models.credential import BadgeAward, Certificate
from app.models.dataset import Dataset
from app.models.github import GitHubRepository
from app.models.learning import Course, LearningPath
from app.models.org import Organization
from app.models.project import Project
from app.models.system import SearchDocument, SeedMarker
from app.models.user import User
from app.storage import get_storage


def purge_demo_data(db: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    demo_users = select(User.id).where(User.is_demo.is_(True))
    comp_ids = list(db.scalars(select(Competition.id).where(Competition.is_demo.is_(True))))
    # Storage objects for demo submissions/datasets are removed best-effort.
    storage = get_storage()
    from app.models.dataset import DatasetFile
    from app.models.submission import EvaluationAsset, Submission

    keys = list(db.scalars(select(Submission.storage_key).where(Submission.competition_id.in_(comp_ids))))
    keys += list(db.scalars(select(EvaluationAsset.storage_key).where(EvaluationAsset.competition_id.in_(comp_ids))))
    from app.models.dataset import DatasetVersion

    keys += list(db.scalars(select(DatasetFile.storage_key).join(DatasetVersion, DatasetVersion.id == DatasetFile.version_id)
                            .join(Dataset, Dataset.id == DatasetVersion.dataset_id).where(Dataset.is_demo.is_(True))))
    counts["certificates"] = db.execute(delete(Certificate).where(or_(Certificate.is_demo.is_(True),
                                                                       Certificate.competition_id.in_(comp_ids)))).rowcount or 0
    db.execute(delete(Thread).where(or_(Thread.competition_id.in_(comp_ids), Thread.author_id.in_(demo_users))))
    db.execute(delete(Comment).where(Comment.author_id.in_(demo_users)))
    db.execute(delete(Report).where(or_(Report.reporter_id.in_(demo_users))))
    # Teams in non-demo competitions captained by demo users: hand captaincy to another member or remove the team.
    stmt = select(Team).where(Team.captain_id.in_(demo_users))
    if comp_ids:
        stmt = stmt.where(Team.competition_id.not_in(comp_ids))
    for team in db.scalars(stmt):
        other = db.scalar(select(TeamMember.user_id).where(TeamMember.team_id == team.id, TeamMember.user_id.not_in(demo_users)).limit(1))
        if other:
            team.captain_id = other
        else:
            db.delete(team)
    counts["competitions"] = db.execute(delete(Competition).where(Competition.id.in_(comp_ids))).rowcount or 0
    counts["projects"] = db.execute(delete(Project).where(Project.is_demo.is_(True))).rowcount or 0
    db.execute(update(Dataset).where(Dataset.is_demo.is_(True)).values(latest_version_id=None))
    counts["datasets"] = db.execute(delete(Dataset).where(Dataset.is_demo.is_(True))).rowcount or 0
    db.execute(delete(LearningPath).where(LearningPath.slug == "ml-foundations"))
    counts["courses"] = db.execute(delete(Course).where(Course.is_demo.is_(True))).rowcount or 0
    counts["repositories"] = db.execute(delete(GitHubRepository).where(GitHubRepository.source == "seed")).rowcount or 0
    db.execute(delete(BadgeAward).where(BadgeAward.user_id.in_(demo_users)))
    db.execute(delete(Notification).where(Notification.user_id.in_(demo_users)))
    db.execute(update(User).where(User.is_demo.is_(True)).values(university_id=None, department_id=None))
    counts["users"] = db.execute(delete(User).where(User.is_demo.is_(True))).rowcount or 0
    counts["organizations"] = db.execute(delete(Organization).where(Organization.is_demo.is_(True))).rowcount or 0
    db.execute(delete(SeedMarker).where(SeedMarker.key.like("demo-%")))
    db.execute(delete(SearchDocument))
    from app.modules.search.indexer import reindex_all

    reindex_all(db)
    db.commit()
    for key in keys:
        with suppress(Exception):
            storage.delete(key)
    return counts
