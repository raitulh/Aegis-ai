"""Demo seed.

Creates a coherent, clearly-labelled *synthetic* world: fictional universities and people, generated
datasets, and competitions whose leaderboards come from **really scoring** generated submissions with the
production evaluator. Every seeded row carries ``is_demo=True`` (or ``source='seed'`` for repositories) so
the UI can badge it and ``python -m app.seed purge`` can remove it.

Run:  python -m app.seed            (idempotent: skips if already seeded)
      python -m app.seed --reset    (purge demo data, then seed again)
"""

from __future__ import annotations

import io
import logging
import random
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import Actor
from app.core.markdown import render_markdown
from app.core.security import hash_password
from app.core.time import utcnow
from app.jobs.queue import drain
from app.jobs.registry import load_handlers
from app.models.community import DiscussionCategory, EmailOutbox, Report, Thread
from app.models.competition import Competition, CompetitionParticipant, Team, TeamMember
from app.models.credential import BadgeDefinition, CertificateTemplate
from app.models.enums import (
    MembershipStatus,
    OrgRole,
    OrgVerification,
    PlatformRole,
    SubmissionStatus,
    VerificationMethod,
)
from app.models.github import GitHubAccount, GitHubIssue, GitHubPullRequest, GitHubRepository
from app.models.learning import Course, LearningPath
from app.models.org import Department, Organization, OrgMembership, OrgSubscription, Plan
from app.models.project import Project, ProjectMember
from app.models.submission import Submission
from app.models.system import FeatureFlag, Job, SeedMarker
from app.models.user import User
from app.seed import data_gen
from app.storage import get_storage
from app.storage.base import PREFIX_SUBMISSIONS, new_key

logger = logging.getLogger("databattles.seed")

SEED_KEY = "demo-v1"
DEMO_PASSWORD = "DemoPass!2026"  # documented in README; demo accounts only  # noqa: S105
RNG = random.Random(2026)


# ----------------------------------------------------------------------------- platform configuration (not demo data)


def ensure_platform_config(db: Session) -> None:
    """Plans, badge catalog, discussion categories, certificate template and flags. Safe to run repeatedly."""
    plans = [
        ("free", "Free", "Core features for student communities.", 0,
         {"max_active_competitions": 3, "private_competitions": False, "custom_certificates": False, "analytics_export": False}, 0),
        ("campus", "Campus", "For universities: private events, custom certificates and analytics export.", 49_00,
         {"max_active_competitions": 25, "private_competitions": True, "custom_certificates": True, "analytics_export": True}, 1),
        ("partner", "Partner", "For sponsors: sponsored events and consent-based talent discovery.", 199_00,
         {"max_active_competitions": 10, "private_competitions": True, "custom_certificates": True, "analytics_export": True,
          "talent_discovery": True}, 2),
    ]
    for key, name, desc, price, ent, order in plans:
        if db.get(Plan, key) is None:
            db.add(Plan(key=key, name=name, description=desc, price_cents_monthly=price, entitlements=ent, sort_order=order))
    badges = [
        ("first-submission", "First Submission", "Made a first scored competition submission.", "competition", "rocket", "#22C55E",
         {"type": "first_submission"}, False, "Common"),
        ("top-10-finish", "Top 10 Finish", "Finished in the top 10 of a finalized competition.", "competition", "trophy", "#F59E0B",
         {"type": "competition_rank", "max_rank": 10}, False, "Uncommon"),
        ("podium", "Podium", "Finished in the top 3 of a finalized competition.", "competition", "medal", "#EAB308",
         {"type": "competition_rank", "max_rank": 3}, False, "Rare"),
        ("regular-competitor", "Regular Competitor", "Joined three or more competitions.", "competition", "swords", "#6366F1",
         {"type": "competitions_joined", "count": 3}, False, "Common"),
        ("first-course", "Course Finisher", "Completed a first course.", "learning", "graduation-cap", "#06B6D4",
         {"type": "courses_completed", "count": 1}, False, "Common"),
        ("scholar", "Scholar", "Completed three courses.", "learning", "book-open", "#0EA5E9",
         {"type": "courses_completed", "count": 3}, False, "Uncommon"),
        ("ml-foundations-path", "ML Foundations Path", "Completed every course in the ML Foundations path.", "learning", "map",
         "#14B8A6", {"type": "path_completed", "path_slug": "ml-foundations"}, False, "Rare"),
        ("first-merged-pr", "First Merged PR", "Had a first pull request merged into a registered repository.", "contribution",
         "git-merge", "#A855F7", {"type": "merged_prs", "count": 1}, False, "Common"),
        ("oss-regular", "Open-Source Regular", "Five merged pull requests to registered repositories.", "contribution",
         "git-pull-request", "#8B5CF6", {"type": "merged_prs", "count": 5}, False, "Uncommon"),
        ("helpful-answer", "Helpful Answer", "A discussion reply was accepted as the answer.", "community", "message-circle",
         "#F97316", {"type": "accepted_answers", "count": 1}, False, "Common"),
        ("mentor", "Mentor", "Recognized by organizers for mentoring participants.", "community", "heart-handshake", "#EC4899", {},
         True, None),
    ]
    for slug, name, desc, cat, icon, color, criteria, manual, rarity in badges:
        if not db.scalar(select(BadgeDefinition.id).where(BadgeDefinition.slug == slug)):
            db.add(BadgeDefinition(slug=slug, name=name, description=desc, category=cat, icon=icon, color=color, criteria=criteria,
                                   is_manual=manual, rarity_label=rarity))
    cats = [("announcements", "Announcements", "Platform news from the team.", True), ("general", "General", "Anything about AI and data.", False),
            ("help", "Help & Q&A", "Ask questions, get unstuck.", False), ("competitions", "Competitions", "Strategies and write-ups.", False),
            ("learning", "Learning", "Courses, resources and study groups.", False),
            ("open-source", "Open Source", "Find collaborators and good first issues.", False),
            ("showcase", "Showcase", "Share what you built.", False)]
    for i, (slug, name, desc, staff) in enumerate(cats):
        if not db.scalar(select(DiscussionCategory.id).where(DiscussionCategory.slug == slug)):
            db.add(DiscussionCategory(slug=slug, name=name, description=desc, position=i, staff_only_posting=staff))
    if not db.scalar(select(CertificateTemplate.id).where(CertificateTemplate.org_id.is_(None), CertificateTemplate.is_default.is_(True))):
        db.add(CertificateTemplate(name="Platform default", heading="Certificate of Achievement",
                                   body_template="This certifies that {recipient} achieved {result} in {event}, issued by {issuer} on {date}.",
                                   accent_color="#7C5CFF", is_default=True))
    for key, desc, enabled in (("github_integration", "Show GitHub connect & sync features", True),
                               ("sponsor_talent", "Enable sponsor talent discovery (consent-based)", True),
                               ("hackathon_presentations", "Presentation slot scheduling for judged events", True)):
        if db.get(FeatureFlag, key) is None:
            db.add(FeatureFlag(key=key, enabled=enabled, description=desc, is_public=True))
    db.commit()


# ----------------------------------------------------------------------------- helpers


def _actor(db: Session, user: User, *roles: str) -> Actor:
    return Actor(db=db, user=user, platform_roles=set(roles))


def _user(db: Session, email: str, handle: str, name: str, **kw: Any) -> User:
    u = User(email=email, handle=handle, display_name=name, password_hash=hash_password(DEMO_PASSWORD), email_verified_at=utcnow(),
             onboarding_completed_at=utcnow(), is_demo=True, **kw)
    if u.bio_md:
        u.bio_html = render_markdown(u.bio_md)
    db.add(u)
    db.flush()
    return u


def _member(db: Session, org: Organization, user: User, role: str = OrgRole.member, verified: bool = True,
            dept: Department | None = None) -> None:
    db.add(OrgMembership(org_id=org.id, user_id=user.id, role=role, status=MembershipStatus.active,
                         verification_method=VerificationMethod.domain if verified else VerificationMethod.none,
                         verified_at=utcnow() if verified else None, department_id=dept.id if dept else None))


def _ago(**kw: float) -> Any:
    return utcnow() - timedelta(**kw)


def _ahead(**kw: float) -> Any:
    return utcnow() + timedelta(**kw)


# ----------------------------------------------------------------------------- main


def seed(db: Session) -> dict[str, int]:
    if db.get(SeedMarker, SEED_KEY):
        logger.info("seed already applied")
        return {}
    settings.RATE_LIMIT_ENABLED = False
    load_handlers()
    ensure_platform_config(db)

    # --- organizations -------------------------------------------------------------------------------------------------
    def org(slug: str, name: str, type_: str, **kw: Any) -> Organization:
        o = Organization(slug=slug, name=name, type=type_, is_demo=True, **kw)
        if o.description_md:
            o.description_html = render_markdown(o.description_md)
        db.add(o)
        db.flush()
        db.add(OrgSubscription(org_id=o.id, plan_key=o.plan_key, status="active", provider="manual"))
        return o

    northbridge = org("northbridge-tech", "Northbridge Institute of Technology (demo)", "university",
                      tagline="A fictional research university used for demo data.", country="US", city="Northbridge",
                      email_domains=["northbridge.example.edu"], verification_status=OrgVerification.verified, accent_color="#7C5CFF",
                      plan_key="campus", website_url="https://example.edu",
                      description_md="**Demo organization.** Northbridge is fictional; its members, events and results are synthetic.")
    riverside = org("riverside-state", "Riverside State University (demo)", "university", tagline="Fictional public university.",
                    country="GB", city="Riverside", email_domains=["riverside.example.edu"], verification_status=OrgVerification.verified,
                    accent_color="#22C55E", description_md="**Demo organization** with synthetic members.")
    lakeshore = org("lakeshore-university", "Lakeshore University (demo)", "university", tagline="Fictional university awaiting verification.",
                    country="CA", city="Lakeshore", email_domains=[], verification_status=OrgVerification.pending, accent_color="#F97316",
                    description_md="**Demo organization.** Pending platform verification.")
    ai_society = org("northbridge-ai-society", "Northbridge AI Society (demo)", "club", tagline="Student-run ML club.",
                     parent_id=northbridge.id, accent_color="#A855F7", description_md="Weekly paper reading and practice competitions.")
    collective = org("open-data-collective", "Open Data Collective (demo)", "community", tagline="Open data for public good.",
                     accent_color="#06B6D4", description_md="A community organizing open-data hackathons.")
    sponsor = org("fictional-analytics", "Fictional Analytics Co. (demo sponsor)", "sponsor", plan_key="partner",
                  tagline="A made-up sponsor used only for demonstration.", accent_color="#F59E0B",
                  description_md="This sponsor does not exist. It shows how sponsor pages and dashboards look.")
    depts: dict[str, Department] = {}
    for o, names in ((northbridge, ["Computer Science", "Statistics", "Electrical Engineering"]),
                     (riverside, ["Data Science", "Mathematics"]), (lakeshore, ["Informatics"])):
        for n in names:
            d = Department(org_id=o.id, slug=n.lower().replace(" ", "-"), name=n)
            db.add(d)
            depts[f"{o.slug}:{n}"] = d
    db.flush()

    # --- people --------------------------------------------------------------------------------------------------------
    admin = _user(db, "admin@example.com", "admin-demo", "Avery Admin (demo)", headline="Platform administrator")
    moderator = _user(db, "moderator@example.com", "mod-demo", "Morgan Moderator (demo)", headline="Community moderator")
    organizer = _user(db, "organizer@example.com", "ines-organizer", "Inés Organizer (demo)", headline="AI Society lead · competitions",
                      university_id=northbridge.id)
    uniadmin = _user(db, "uniadmin@example.com", "uni-admin-demo", "Priya Campus Admin (demo)", headline="University admin",
                     university_id=northbridge.id)
    judge = _user(db, "judge@example.com", "judge-kofi", "Kofi Judge (demo)", headline="Industry judge")
    sponsor_user = _user(db, "sponsor@example.com", "sponsor-mara", "Mara Sponsor (demo)", headline="Talent partnerships")
    maintainer = _user(db, "maintainer@example.com", "oss-maintainer", "Sam Maintainer (demo)", headline="Maintains campus OSS tools",
                       skills=["python", "fastapi", "open-source"])
    student = _user(db, "student@example.com", "ada-demo", "Ada Student (demo)", headline="CS undergrad exploring ML",
                    bio_md="Demo student account. I like **tabular ML**, NLP and open source.", university_id=northbridge.id,
                    department_id=depts["northbridge-tech:Computer Science"].id, graduation_year=2027,
                    skills=["python", "pandas", "scikit-learn", "nlp"], interests=["regression", "nlp", "classification", "open-source"],
                    open_to_opportunities=True, cover_style="nebula")
    for u, roles in ((admin, [PlatformRole.platform_admin]), (moderator, [PlatformRole.moderator])):
        for r in roles:
            from app.models.user import PlatformRoleAssignment

            db.add(PlatformRoleAssignment(user_id=u.id, role=r))
    first = ["Liam", "Noor", "Mateo", "Yuki", "Zara", "Tomás", "Amara", "Chen", "Freya", "Omar", "Lina", "Kai", "Sofia", "Ravi",
             "Elif", "Jonas", "Maya", "Tariq", "Hana", "Leo", "Ines", "Kwame", "Aiko", "Ben"]
    last = ["Okafor", "Silva", "Novak", "Tanaka", "Haddad", "Larsen", "Mensah", "Kowalski", "Rossi", "Nguyen", "Ibrahim", "Moreau"]
    unis = [(northbridge, "northbridge.example.edu", ["Computer Science", "Statistics", "Electrical Engineering"]),
            (riverside, "riverside.example.edu", ["Data Science", "Mathematics"]), (lakeshore, "lakeshore.example.edu", ["Informatics"])]
    skills_pool = ["python", "pytorch", "pandas", "sql", "r", "nlp", "computer-vision", "statistics", "xgboost", "react", "docker"]
    students = [student]
    for i, fn in enumerate(first):
        uni, domain, dnames = unis[i % 3]
        ln = last[i % len(last)]
        handle = f"{fn.lower().replace('á', 'a').replace('é', 'e')}-{ln.lower()}"[:30]
        dept = depts[f"{uni.slug}:{dnames[i % len(dnames)]}"]
        u = _user(db, f"{handle}@{domain}", handle, f"{fn} {ln}", headline=RNG.choice(
            ["ML enthusiast", "Stats major", "Kaggle-style competitor", "NLP researcher in training", "Data engineer in the making"]),
            university_id=uni.id, department_id=dept.id, graduation_year=RNG.choice([2026, 2027, 2028]),
            skills=RNG.sample(skills_pool, 4), interests=RNG.sample(["regression", "nlp", "classification", "vision", "open-source"], 2),
            open_to_opportunities=RNG.random() < 0.5, cover_style=RNG.choice(["aurora", "nebula", "circuit", "dunes", "sunrise"]))
        students.append(u)
    db.flush()

    _member(db, northbridge, uniadmin, OrgRole.admin)
    _member(db, northbridge, organizer, OrgRole.manager)
    _member(db, ai_society, organizer, OrgRole.owner)
    _member(db, collective, maintainer, OrgRole.owner)
    _member(db, sponsor, sponsor_user, OrgRole.owner)
    _member(db, riverside, admin, OrgRole.owner)
    _member(db, lakeshore, admin, OrgRole.owner)
    _member(db, northbridge, admin, OrgRole.owner)
    for u in students:
        uni = db.get(Organization, u.university_id)
        if uni is None:
            continue
        verified = uni.verification_status == OrgVerification.verified and RNG.random() < 0.85
        _member(db, uni, u, OrgRole.member, verified=verified, dept=db.get(Department, u.department_id) if u.department_id else None)
    for u in students[:10]:
        if u.university_id == northbridge.id:
            _member(db, ai_society, u)
    pending_user = students[-1]
    db.add(OrgMembership(org_id=collective.id, user_id=pending_user.id, role=OrgRole.member, status=MembershipStatus.pending,
                         request_note="I'd love to help organize the next open-data hackathon!"))
    db.commit()

    admin_actor = _actor(db, admin, PlatformRole.platform_admin)
    org_actor = _actor(db, organizer)

    # --- datasets ------------------------------------------------------------------------------------------------------
    from app.modules.datasets import service as ds_service

    tasks = {"crop": data_gen.crop_yield(), "energy": data_gen.energy_anomaly(), "reviews": data_gen.course_reviews(),
             "survival": data_gen.survival_practice()}
    ds_specs = [
        ("crop", "Crop Yield (synthetic)", "Formula-generated farm plots with weather, soil and fertilizer features.",
         ["agriculture", "regression", "tabular"], ai_society, "cc-by-4.0"),
        ("energy", "Campus Energy Readings (synthetic)", "Hourly building energy use with injected anomalies.",
         ["energy", "anomaly-detection", "time-series"], riverside, "cc-by-4.0"),
        ("reviews", "Course Reviews (synthetic)", "Template-generated course reviews labelled with sentiment.",
         ["nlp", "sentiment", "text"], northbridge, "cc0-1.0"),
        ("survival", "Passenger Survival Practice (synthetic)", "Beginner binary-classification practice data.",
         ["beginner", "classification", "tabular"], None, "cc0-1.0"),
    ]
    datasets: dict[str, Any] = {}
    for key, title, subtitle, tags, owner_org, lic in ds_specs:
        owner_actor = admin_actor
        ds = ds_service.create_dataset(owner_actor, {"title": title, "subtitle": subtitle, "tags": tags, "license": lic,
                                                     "visibility": "public", "owner_org_id": owner_org.id if owner_org else None,
                                                     "description_md": f"**Synthetic demo dataset.** {tasks[key].readme}\n\n"
                                                                       "Files: `train.csv` (features + target), `test.csv` (features only), "
                                                                       "`sample_submission.csv` (required submission format).",
                                                     "citation": f"DataBattles demo data ({title}), 2026. Synthetic.",
                                                     "attribution": "Generated by the DataBattles seed script."})
        ds.is_demo = True
        v = ds_service.get_version(db, ds, 1)
        t = tasks[key]
        for fname, content, kind in (("train.csv", t.train, "data"), ("test.csv", t.test, "data"),
                                     ("sample_submission.csv", t.sample, "sample_submission")):
            ds_service.upload_file(owner_actor, ds, v, filename=fname, stream=io.BytesIO(content.encode()), kind=kind, idempotency_key=None)
        ds_service.update_version(owner_actor, ds, v, "Initial release.", [{"column": "id", "type": "string", "description": "Row id"}])
        ds_service.publish_version(owner_actor, ds, v)
        datasets[key] = (ds, v)
    for key, title, subtitle, tags, seed_n in (("mobility", "Campus Mobility Trips (synthetic)", "Shuttle, bike and walking trips.",
                                                ["mobility", "tabular"], 5),
                                               ("loans", "Library Book Loans (synthetic)", "Loans with late-return flags.",
                                                ["library", "tabular", "classification"], 9)):
        ds = ds_service.create_dataset(admin_actor, {"title": title, "subtitle": subtitle, "tags": tags, "license": "cc-by-4.0",
                                                     "visibility": "public", "description_md": f"**Synthetic demo dataset.** {subtitle}"})
        ds.is_demo = True
        v = ds_service.get_version(db, ds, 1)
        ds_service.upload_file(admin_actor, ds, v, filename=f"{key}.csv", stream=io.BytesIO(data_gen.simple_table(key, seed_n).encode()),
                               kind="data", idempotency_key=None)
        ds_service.publish_version(admin_actor, ds, v)
        datasets[key] = (ds, v)
    db.commit()

    # --- competitions --------------------------------------------------------------------------------------------------
    from app.modules.competitions import content as comp_content
    from app.modules.competitions import service as comp_service
    from app.modules.competitions.schemas import CompetitionWrite, SponsorIn

    rules = ("1. One account per person; teams up to the stated size.\n2. Use only the provided data plus publicly available "
             "pre-trained models.\n3. No sharing code privately between teams.\n4. Final ranking uses the private leaderboard.")

    def make_comp(actor: Actor, *, title: str, summary: str, task_key: str | None, metric: str | None, target: str | None,
                  host: Organization | None, visibility: str = "public", event_type: str = "ml_competition", task_type: str,
                  difficulty: str = "intermediate", tags: list[str], team_max: int = 3, scoring_mode: str = "automatic",
                  days_left: float = 20, prize: str | None = None, cover: str = "aurora") -> Competition:
        data = CompetitionWrite(
            title=title, summary=summary, description_md=f"## Overview\n{summary}\n\n**This is a demo competition with synthetic data.** "
            "Scores on the leaderboard come from generated submissions scored by the real evaluator.\n\n## Data\nSee the attached dataset.",
            rules_md=rules, evaluation_md=f"Submissions are scored with **{metric}**." if metric else "Entries are judged with a rubric.",
            event_type=event_type, task_type=task_type, difficulty=difficulty, tags=tags, host_org_id=host.id if host else None,
            visibility=visibility, starts_at=_ago(days=10), ends_at=_ahead(days=days_left), team_min_size=1, team_max_size=team_max,
            daily_submission_limit=5, scoring_mode=scoring_mode, has_prize=bool(prize), prize_summary=prize,
            prize_md=f"{prize} (fictional, for demonstration only)" if prize else None, cover_style=cover,
            evaluation={"metric": metric, "id_column": "id", "target_column": target, "positive_label": "1"} if metric else None,
            dataset_version_id=datasets[task_key][1].id if task_key else None,
            faq=[{"q": "Can I use pre-trained models?", "a": "Yes, if they are publicly available."},
                 {"q": "Is this data real?", "a": "No — it is synthetic demo data."}],
        )
        comp = comp_service.create_competition(actor, data)
        comp.is_demo = True
        if task_key:
            comp_content.upload_ground_truth(actor, comp, "solution.csv", io.BytesIO(tasks[task_key].ground_truth.encode()))
        return comp

    crop = make_comp(org_actor, title="Crop Yield Forecasting Challenge", summary="Predict per-plot crop yield from weather, soil and fertilizer features.",
                     task_key="crop", metric="rmse", target="yield_t_ha", host=ai_society, task_type="regression", tags=["regression", "tabular", "agriculture"],
                     days_left=18, prize="$1,000 in cloud credits", cover="dunes")
    energy = make_comp(admin_actor, title="Campus Energy Anomaly Detection", summary="Flag anomalous hourly energy readings across campus buildings.",
                       task_key="energy", metric="roc_auc", target="is_anomaly", host=riverside, task_type="classification",
                       tags=["classification", "anomaly-detection", "energy"], days_left=35, cover="circuit")
    reviews = make_comp(org_actor, title="Course Review Sentiment", summary="Classify course reviews as positive, neutral or negative.",
                        task_key="reviews", metric="macro_f1", target="sentiment", host=northbridge, task_type="nlp",
                        tags=["nlp", "sentiment", "text"], days_left=5, prize="Mentorship session with a demo sponsor", cover="nebula")
    practice = make_comp(admin_actor, title="Passenger Survival — Practice", summary="A friendly, always-open practice competition for first submissions.",
                         task_key="survival", metric="accuracy", target="survived", host=None, event_type="practice", task_type="classification",
                         difficulty="beginner", tags=["beginner", "classification", "practice"], team_max=1, days_left=365, cover="sunrise")
    internal = make_comp(org_actor, title="Northbridge Internal Datathon", summary="Members-only datathon for Northbridge students (demo).",
                         task_key="crop", metric="mae", target="yield_t_ha", host=northbridge, visibility="university",
                         event_type="datathon", task_type="regression", tags=["datathon", "members-only"], days_left=40, cover="mono")
    hackathon = make_comp(org_actor, title="Build-for-Good Hackathon 2026", summary="A 48-hour hackathon building tools for campus sustainability.",
                          task_key=None, metric=None, target=None, host=ai_society, event_type="hackathon",
                          task_type="hackathon", scoring_mode="judged", team_max=4, tags=["hackathon", "sustainability"], days_left=3,
                          prize="Demo prizes for the top 3 teams", cover="aurora")
    from app.modules.judging import service as judging

    judging.upsert_rubric(org_actor, hackathon, [
        {"key": "impact", "label": "Impact", "description": "How much does it help?", "min": 0, "max": 10, "weight": 2},
        {"key": "technical", "label": "Technical quality", "min": 0, "max": 10, "weight": 1.5},
        {"key": "presentation", "label": "Presentation", "min": 0, "max": 10, "weight": 1},
    ], reveal=False, blind=False)
    for c in (crop, energy, reviews, practice, internal, hackathon):
        comp_service.publish(org_actor if c.created_by == organizer.id else admin_actor, c)
    comp_content.add_sponsor(admin_actor, crop, SponsorIn(org_slug=sponsor.slug, tier="gold",
                                                          blurb="Supporting applied ML for agriculture (fictional sponsor)."))
    db.commit()

    # --- participation & really-scored submissions --------------------------------------------------------------------
    from app.modules.teams.service import create_solo_team

    storage = get_storage()
    skills = {u.id: RNG.uniform(0.15, 0.95) for u in students}
    skills[student.id] = 0.8

    def join(comp: Competition, user: User, joined_days_ago: float) -> Team:
        part = CompetitionParticipant(competition_id=comp.id, user_id=user.id, accepted_rules_at=_ago(days=joined_days_ago),
                                      rules_version=comp.config_version, joined_at=_ago(days=joined_days_ago))
        db.add(part)
        comp.participant_count += 1
        team = create_solo_team(db, comp, user)
        part.team_id = team.id
        return team

    def team_up(comp: Competition, name: str, members: list[User]) -> Team:
        captain = members[0]
        team = Team(competition_id=comp.id, name=name, captain_id=captain.id)
        db.add(team)
        db.flush()
        comp.team_count += 1
        for i, m in enumerate(members):
            db.add(CompetitionParticipant(competition_id=comp.id, user_id=m.id, team_id=team.id, accepted_rules_at=_ago(days=9),
                                          rules_version=comp.config_version, joined_at=_ago(days=9)))
            db.add(TeamMember(team_id=team.id, competition_id=comp.id, user_id=m.id, role="captain" if i == 0 else "member"))
            comp.participant_count += 1
        return team

    def submit(comp: Competition, team: Team, user: User, task_key: str, skill: float, days_ago: float, note: str | None = None) -> None:
        csv_text = tasks[task_key].predict(max(0.0, min(1.0, skill)), random.Random(RNG.random()))
        key = new_key(PREFIX_SUBMISSIONS, ".csv")
        stored = storage.save_stream(key, io.BytesIO(csv_text.encode()), max_bytes=50 * 1024 * 1024, content_type="text/csv")
        sub = Submission(competition_id=comp.id, team_id=team.id, user_id=user.id, status=SubmissionStatus.queued, description=note,
                         filename="predictions.csv", storage_key=key, size_bytes=stored.size_bytes, sha256=stored.sha256,
                         submitted_at=_ago(days=days_ago), is_demo=True)
        db.add(sub)
        db.flush()
        comp.submission_count += 1
        from app.jobs.queue import enqueue

        enqueue(db, "score_submission", {"submission_id": str(sub.id)}, idempotency_key=f"score:{sub.id}")

    # Crop: individuals + two teams; the demo student participates.
    crop_team_a = team_up(crop, "Root Mean Squad", [students[1], students[4], students[7]])
    crop_team_b = team_up(crop, "Yield Signs", [students[2], students[5]])
    solo_crop = [join(crop, u, RNG.uniform(3, 9)) for u in [student, *students[8:18]]]
    for team, members in ((crop_team_a, [students[1], students[4], students[7]]), (crop_team_b, [students[2], students[5]])):
        base = sum(skills[m.id] for m in members) / len(members) + 0.08
        for k in range(RNG.randint(3, 6)):
            submit(crop, team, members[k % len(members)], "crop", base - 0.25 + 0.06 * k, 8 - k * 1.3)
    for team, u in zip(solo_crop, [student, *students[8:18]], strict=True):
        for k in range(RNG.randint(1, 5)):
            submit(crop, team, u, "crop", skills[u.id] - 0.3 + 0.07 * k, RNG.uniform(0.2, 8))
    # Energy
    for u in students[3:20]:
        t = join(energy, u, RNG.uniform(2, 9))
        for k in range(RNG.randint(1, 4)):
            submit(energy, t, u, "energy", skills[u.id] - 0.2 + 0.05 * k, RNG.uniform(0.1, 8))
    # Practice (the demo student has a scored submission, completing the course challenge)
    for u in [student, *students[10:24]]:
        t = join(practice, u, RNG.uniform(1, 9))
        for k in range(RNG.randint(1, 3)):
            submit(practice, t, u, "survival", skills[u.id] + 0.05 * k, RNG.uniform(0.1, 8))
    # Reviews: will be finalized below
    rev_team = team_up(reviews, "Sentimental Values", [student, students[6]])
    for k in range(4):
        submit(reviews, rev_team, student if k % 2 == 0 else students[6], "reviews", 0.72 + 0.05 * k, 9 - 2 * k,
               note=f"TF-IDF + logistic regression v{k + 1}")
    for u in students[11:24]:
        t = join(reviews, u, RNG.uniform(3, 9))
        for k in range(RNG.randint(1, 4)):
            submit(reviews, t, u, "reviews", skills[u.id] - 0.1 + 0.05 * k, RNG.uniform(0.5, 8.5))
    # Internal datathon (university-only): northbridge members only
    for u in [s for s in students if s.university_id == northbridge.id][:6]:
        t = join(internal, u, RNG.uniform(1, 5))
        submit(internal, t, u, "crop", skills[u.id], RNG.uniform(0.1, 4))
    db.commit()

    processed = drain(SessionLocal, worker_id="seed", max_jobs=5000)
    logger.info("scored demo submissions", extra={"jobs": processed})
    db.expire_all()

    # Mark some final selections on the reviews competition and finalize it (ends in the past for the demo).
    for team in db.scalars(select(Team).where(Team.competition_id == reviews.id)):
        best = db.scalars(select(Submission).where(Submission.team_id == team.id, Submission.status == SubmissionStatus.scored)
                          .order_by(Submission.public_score.desc()).limit(2)).all()
        for s in best:
            s.is_final_selected = True
    reviews = db.get(Competition, reviews.id)
    reviews.starts_at, reviews.ends_at = _ago(days=40), _ago(days=6)
    db.commit()
    from app.modules.leaderboards.service import finalize

    finalize(_actor(db, db.get(User, admin.id), PlatformRole.platform_admin), reviews)
    from app.modules.credentials.certificates import issue_for_competition

    issue_for_competition(_actor(db, db.get(User, organizer.id)), db.get(Competition, reviews.id))
    db.commit()

    # --- hackathon: teams, project submissions, judges and partial scores ---------------------------------------------
    hackathon = db.get(Competition, hackathon.id)
    hack_teams = [team_up(hackathon, "Solar Sprinters", [students[3], students[9], students[15]]),
                  team_up(hackathon, "Compost Compass", [students[12], students[18]]),
                  team_up(hackathon, "Bike Share Brains", [students[20], students[21], students[22], students[23]])]
    db.commit()
    from app.models.submission import EventSubmission

    for team, (title, summary) in zip(hack_teams, [("SunTrack", "Predicts rooftop solar output to plan campus charging."),
                                                   ("CompostIQ", "Computer vision sorting helper for dining-hall waste."),
                                                   ("RideRadar", "Forecasts bike-share demand across campus stations.")], strict=True):
        db.add(EventSubmission(competition_id=hackathon.id, team_id=team.id, title=title, summary=summary,
                               description_md=f"{summary}\n\n*Demo hackathon entry.*", description_html=render_markdown(f"{summary}\n\n*Demo hackathon entry.*"),
                               submitted_by=team.captain_id))
    db.commit()
    judging.assign_judge(org_actor, hackathon, judge.handle, None, "Main panel")
    judging.assign_judge(org_actor, hackathon, organizer.handle, None, "Main panel")
    judge_actor = _actor(db, judge)
    hackathon = db.get(Competition, hackathon.id)
    hackathon.ends_at = _ago(hours=6)
    hackathon.starts_at = _ago(days=2, hours=6)
    db.commit()
    for team, sc in zip(hack_teams[:2], [{"impact": 8, "technical": 7, "presentation": 9}, {"impact": 7, "technical": 8, "presentation": 6}], strict=True):
        judging.submit_score(judge_actor, hackathon, team.id, sc, "Strong demo — clear user story. (Demo feedback)", True)

    # --- learning ------------------------------------------------------------------------------------------------------
    from app.modules.learning import service as learn

    def course(title: str, category: str, difficulty: str, summary: str, lessons: list[dict[str, Any]], *, badge: str | None = None,
               cert: bool = False, org: Organization | None = None) -> Course:
        c = learn.create_course(admin_actor, {"title": title, "category": category, "difficulty": difficulty, "summary": summary,
                                              "description_md": f"{summary}\n\n*Demo course content.*", "issues_certificate": cert,
                                              "estimated_minutes": 20 * len(lessons), "badge_slug": badge, "tags": [category],
                                              "org_id": org.id if org else None})
        c.is_demo = True
        for les in lessons:
            questions = les.pop("questions", None)
            lesson = learn.upsert_lesson(admin_actor, c, les)
            if questions:
                learn.set_questions(admin_actor, c, lesson.slug, questions)
        learn.publish_course(admin_actor, c)
        return c

    py = course("Python for Data Science", "python", "beginner", "Variables to pandas: the essentials for data work.", [
        {"title": "Why Python for data?", "kind": "article", "body_md": "Python's ecosystem (NumPy, pandas, scikit-learn) makes it the lingua franca of data science."},
        {"title": "Working with pandas", "kind": "article", "body_md": "```python\nimport pandas as pd\ndf = pd.read_csv('train.csv')\ndf.describe()\n```"},
        {"title": "Check your understanding", "kind": "quiz", "pass_threshold": 70, "questions": [
            {"prompt": "Which library provides DataFrames?", "options": ["NumPy", "pandas", "Matplotlib"], "correct_option": 1,
             "explanation": "pandas provides the DataFrame type."},
            {"prompt": "What does df.describe() return?", "options": ["Summary statistics", "The column names only", "A plot"],
             "correct_option": 0, "explanation": "describe() summarizes numeric columns."}]},
    ], badge=None)
    ml = course("Machine Learning Foundations", "machine-learning", "beginner", "Train/validation splits, overfitting and your first model.", [
        {"title": "Train, validation, test", "kind": "article", "body_md": "Hold out data to estimate generalization. Never tune on the test set."},
        {"title": "Overfitting in one picture", "kind": "article", "body_md": "A model that memorizes noise scores well on training data and poorly on new data."},
        {"title": "Quiz: generalization", "kind": "quiz", "pass_threshold": 60, "questions": [
            {"prompt": "Where should hyper-parameters be tuned?", "options": ["Test set", "Validation set", "Training set only"],
             "correct_option": 1, "explanation": "Use a validation set (or cross-validation)."},
            {"prompt": "High train score + low validation score suggests…", "options": ["Underfitting", "Overfitting", "Data leakage fixed"],
             "correct_option": 1}]},
        {"title": "Challenge: first submission", "kind": "challenge", "body_md": "Make a scored submission to the practice competition.",
         "challenge": {"competition_slug": practice.slug, "instructions": "Submit predictions for test.csv to the practice competition."}},
    ], cert=True)
    nlp = course("Intro to NLP", "nlp", "intermediate", "Tokenization, bag-of-words and sentiment baselines.", [
        {"title": "From text to vectors", "kind": "article", "body_md": "Bag-of-words and TF-IDF turn text into sparse feature vectors."},
        {"title": "Baselines first", "kind": "article", "body_md": "A logistic-regression baseline often beats a rushed deep model."},
    ])
    course("Responsible AI Basics", "ethics", "beginner", "Bias, privacy and documentation for ML projects.", [
        {"title": "Dataset documentation", "kind": "article", "body_md": "Datasheets and model cards record intent, limits and risks."},
    ], org=northbridge)
    db.add(LearningPath(slug="ml-foundations", title="ML Foundations Path", summary="From Python basics to your first competition submission.",
                        course_ids=[py.id, ml.id, nlp.id],
                        badge_id=db.scalar(select(BadgeDefinition.id).where(BadgeDefinition.slug == "ml-foundations-path"))))
    db.commit()
    stu_actor = _actor(db, db.get(User, student.id))
    for c in (py, ml):
        learn.enroll(stu_actor, c)
    for les in ("why-python-for-data", "working-with-pandas"):
        learn.complete_lesson(stu_actor, db.get(Course, py.id), les)
    qs = learn.lesson_detail(stu_actor, db.get(Course, py.id), "check-your-understanding")["questions"]
    learn.submit_quiz(stu_actor, db.get(Course, py.id), "check-your-understanding", {str(qs[0]["id"]): 1, str(qs[1]["id"]): 0})
    learn.complete_lesson(stu_actor, db.get(Course, ml.id), "train-validation-test")
    learn.enroll(stu_actor, nlp)
    for u in students[1:12]:
        a = _actor(db, u)
        learn.enroll(a, db.get(Course, py.id))
        for les in ("why-python-for-data", "working-with-pandas"):
            learn.complete_lesson(a, db.get(Course, py.id), les)

    # --- open source -----------------------------------------------------------------------------------------------------
    gh_ids = {maintainer.id: 9_100_000_001, student.id: 9_100_000_002, students[1].id: 9_100_000_003, students[4].id: 9_100_000_004}
    for uid, gid in gh_ids.items():
        u = db.get(User, uid)
        db.add(GitHubAccount(user_id=uid, github_user_id=gid, login=f"{u.handle}-gh", avatar_url=None, access_token_enc=None, scopes="read:user"))
    repos = []
    for i, (name, desc, lang, topics) in enumerate([
        ("campus-shuttle-api", "Open API for (fictional) campus shuttle arrivals.", "Python", ["api", "fastapi", "transport"]),
        ("dataset-card-kit", "Templates and a linter for dataset documentation.", "TypeScript", ["documentation", "datasets"]),
        ("tiny-automl", "A teaching-sized AutoML library for tabular data.", "Python", ["machine-learning", "automl", "education"]),
    ]):
        r = GitHubRepository(github_repo_id=9_200_000_000 + i, full_name=f"demo-org/{name}", owner_login="oss-maintainer-gh", name=name,
                             description=desc, language=lang, stars=RNG.randint(12, 240), forks=RNG.randint(2, 40), open_issues=0,
                             topics=topics, html_url="", license="MIT", default_branch="main", pushed_at=_ago(days=RNG.randint(1, 20)),
                             contributors_count=RNG.randint(3, 15), sync_status="ok", last_synced_at=_ago(hours=3), source="seed",
                             registered_by=maintainer.id)
        db.add(r)
        repos.append(r)
    db.flush()
    issue_titles = ["Add retry with backoff to the arrivals client", "Document the /stops endpoint", "Support CSV export",
                    "Fix timezone bug in schedule parser", "Add dark mode to the docs site", "Write tests for the linter CLI",
                    "Add a regression example notebook", "Improve error message for empty datasets"]
    issue_id = 9_300_000_000
    for r in repos:
        for j in range(RNG.randint(3, 5)):
            issue_id += 1
            beginner = RNG.random() < 0.6
            db.add(GitHubIssue(repo_id=r.id, github_issue_id=issue_id, number=10 + j, title=RNG.choice(issue_titles), state="open",
                               labels=(["good first issue"] if beginner else []) + [RNG.choice(["docs", "enhancement", "bug"])],
                               is_beginner_friendly=beginner, is_promoted=j == 0, html_url="", author_login="oss-maintainer-gh",
                               comments=RNG.randint(0, 6), gh_created_at=_ago(days=RNG.randint(2, 40)), gh_updated_at=_ago(days=RNG.randint(0, 2))))
        r.open_issues = db.scalar(select(func.count()).select_from(GitHubIssue).where(GitHubIssue.repo_id == r.id))
    pr_id = 9_400_000_000
    for uid, n in ((student.id, 3), (students[1].id, 6), (students[4].id, 1), (maintainer.id, 8)):
        for k in range(n):
            pr_id += 1
            r = repos[k % len(repos)]
            db.add(GitHubPullRequest(repo_id=r.id, github_pr_id=pr_id, number=100 + k, title=RNG.choice(issue_titles), state="closed",
                                     merged=True, merged_at=_ago(days=RNG.randint(1, 120)), author_github_id=gh_ids[uid],
                                     author_login=f"{db.get(User, uid).handle}-gh", html_url="", additions=RNG.randint(5, 400),
                                     deletions=RNG.randint(0, 120), gh_created_at=_ago(days=130), gh_updated_at=_ago(days=1)))
    db.commit()

    # --- projects (≥10) ------------------------------------------------------------------------------------------------
    project_specs = [
        (maintainer, "Campus Shuttle API", "Open API and dashboard for shuttle arrival predictions.", ["api", "transport"], ["python", "fastapi"], True, 0),
        (maintainer, "Dataset Card Kit", "Templates and a linter for dataset documentation.", ["documentation"], ["typescript"], True, 1),
        (maintainer, "Tiny AutoML", "Teaching-sized AutoML for tabular data.", ["automl", "education"], ["python", "scikit-learn"], True, 2),
        (student, "Crop Yield Baselines", "Write-up of gradient-boosting baselines for the crop yield challenge.", ["regression", "write-up"], ["python", "xgboost"], False, None),
        (student, "Review Sentiment Explorer", "Interactive explorer for the course-review sentiment data.", ["nlp", "visualization"], ["python", "streamlit"], False, None),
        (students[1], "Energy Anomaly Notebook", "Isolation-forest approach to campus energy anomalies.", ["anomaly-detection"], ["python", "pandas"], False, None),
        (students[2], "Study Group Matcher", "Matches students into study groups by course and schedule.", ["education"], ["react", "sql"], False, None),
        (students[3], "SunTrack", "Rooftop solar forecasting from the Build-for-Good hackathon.", ["hackathon", "energy"], ["python", "pytorch"], False, None),
        (students[5], "Library Loans Dashboard", "Visualizes loan patterns in the synthetic library dataset.", ["visualization"], ["r", "shiny"], False, None),
        (students[8], "Paper Reading Tracker", "Tracks the AI Society's weekly paper reading list.", ["community"], ["typescript", "nextjs"], False, None),
        (students[9], "Mobility Heatmaps", "Heatmaps of the synthetic campus mobility dataset.", ["mobility", "visualization"], ["python", "plotly"], False, None),
    ]
    from app.modules.projects import service as proj

    for owner, title, summary, tags, tech, oss, repo_idx in project_specs:
        a = _actor(db, db.get(User, owner.id))
        p = proj.create_project(a, {"title": title, "summary": summary, "tags": tags, "technologies": tech, "is_open_source": oss,
                                    "description_md": f"{summary}\n\n*Demo project — synthetic content.*",
                                    "cover_style": RNG.choice(["aurora", "nebula", "circuit", "dunes", "sunrise"]),
                                    "dataset_slugs": [datasets["crop"][0].slug] if "Crop" in title else None,
                                    "competition_slug": crop.slug if "Crop" in title else None})
        p.is_demo = True
        if repo_idx is not None:
            p.github_repo_id = repos[repo_idx].id
            p.maintainer_verified_at = utcnow()
        if title in ("Campus Shuttle API", "Crop Yield Baselines"):
            p.is_featured = True
        if owner.id == student.id and title.startswith("Review"):
            db.add(ProjectMember(project_id=p.id, user_id=students[6].id, role="contributor"))
    db.commit()

    # --- discussions -----------------------------------------------------------------------------------------------------
    from app.modules.discussions import service as disc

    def thread(author: User, title: str, body: str, *, category: str | None = None, competition: Competition | None = None,
               replies: list[tuple[User, str]] | None = None, accept: int | None = None, pinned: bool = False) -> Thread:
        a = _actor(db, db.get(User, author.id), *([PlatformRole.platform_admin] if author.id == admin.id else []))
        t = disc.create_thread(a, title=title, body_md=body, category=category, competition=competition.slug if competition else None,
                               project=None)
        comments = []
        for u, text in replies or []:
            comments.append(disc.add_comment(_actor(db, db.get(User, u.id)), db.get(Thread, t.id), text, None))
        if accept is not None:
            disc.accept_answer(a, db.get(Thread, t.id), comments[accept].id)
        if pinned:
            db.get(Thread, t.id).pinned = True
        return t

    thread(admin, "Welcome to the DataBattles demo", "Everything you see here is **synthetic demo data**. Explore competitions, "
           "courses and projects — and feel free to break things.", category="announcements", pinned=True)
    thread(students[2], "How do you choose between RMSE and MAE?", "For the crop yield challenge the metric is RMSE. When would MAE be the better choice?",
           category="help", replies=[(students[1], "RMSE punishes large errors more. If outliers matter a lot, RMSE is the right lens."),
                                     (student, "MAE is more robust to outliers and easier to explain to non-technical folks. @liam-okafor has a good point though.")],
           accept=0)
    thread(student, "Study group for Intro to NLP?", "Anyone want to go through the NLP course together this week?", category="learning",
           replies=[(students[6], "Count me in!"), (students[11], "Same here — evenings work best for me.")])
    thread(maintainer, "Good first issues in campus-shuttle-api", "We tagged several beginner-friendly issues. Ask here if you get stuck.",
           category="open-source", replies=[(students[4], "Picked up the docs issue, PR coming soon.")])
    thread(students[3], "Show & tell: SunTrack from the hackathon", "We forecast rooftop solar output with a small LSTM. Feedback welcome!",
           category="showcase", replies=[(judge, "Great demo at the event. Consider adding a baseline comparison.")])
    thread(organizer, "Official Q&A thread", "Ask questions about the Crop Yield challenge rules here.", competition=crop,
           replies=[(students[8], "Can we use external weather data?"), (organizer, "Only the provided data plus public pre-trained models.")])
    thread(students[1], "Validation strategy that matched the private LB", "5-fold CV with region stratification tracked the leaderboard well.",
           competition=reviews)
    spam = thread(students[23], "Buy cheap followers now!!!", "Totally legit offer, click the link in my profile.", category="general")
    db.add(Report(reporter_id=student.id, target_type="thread", target_id=spam.id, reason="spam", details="Looks like spam (demo report)."))
    db.add(Report(reporter_id=students[2].id, target_type="thread", target_id=spam.id, reason="spam"))
    db.commit()

    # --- manual badge, notifications, recent activity ---------------------------------------------------------------------
    from app.modules.credentials.badges import award_manual, evaluate_user_badges

    award_manual(admin_actor, "mentor", students[1].handle, "Mentored first-time participants in the crop challenge (demo).")
    for u in db.scalars(select(User).where(User.is_demo.is_(True))):
        evaluate_user_badges(db, u.id, trigger="seed")
    db.commit()
    stu = db.get(User, student.id)
    from app.modules.notifications.service import notify

    notify(db, stu.id, "announcement", "New demo announcement in Crop Yield Forecasting Challenge", link=f"/competitions/{crop.slug}",
           dedupe_key="seed:ann:1")
    db.commit()

    # Seeded content must never send real email: suppress anything queued during seeding.
    db.execute(update(Job).where(Job.kind == "send_email", Job.status.in_(["queued", "failed"])).values(status="succeeded",
                                                                                                       last_error="suppressed by demo seed"))
    db.execute(update(EmailOutbox).where(EmailOutbox.status == "queued").values(status="suppressed"))
    from app.modules.search.indexer import reindex_all

    reindex_all(db)
    db.add(SeedMarker(key=SEED_KEY, note="Demo data. Remove with: python -m app.seed purge"))
    db.commit()
    counts = {
        "users": db.scalar(select(func.count()).select_from(User).where(User.is_demo.is_(True))) or 0,
        "competitions": db.scalar(select(func.count()).select_from(Competition).where(Competition.is_demo.is_(True))) or 0,
        "submissions": db.scalar(select(func.count()).select_from(Submission).where(
            Submission.competition_id.in_(select(Competition.id).where(Competition.is_demo.is_(True))))) or 0,
        "projects": db.scalar(select(func.count()).select_from(Project).where(Project.is_demo.is_(True))) or 0,
    }
    return counts
