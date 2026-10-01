from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from app.core.db import SessionLocal
from app.core.time import utcnow
from app.models.competition import Competition
from tests.conftest import ApiClient, run_jobs, signup_and_login
from tests.test_competitions import half, perfect, setup_competition


def test_team_invites_membership_rules_and_lock(make_client) -> None:  # noqa: ANN001
    org, cap, mate, third, fourth = make_client(), make_client(), make_client(), make_client(), make_client()
    comp = setup_competition(org, team_max_size=2)
    slug = comp["slug"]
    for c, email, handle in ((cap, "cap@example.com", "cap"), (mate, "mate@example.com", "mate"), (third, "third@example.com", "third"),
                             (fourth, "fourth@example.com", "fourth")):
        signup_and_login(c, email, handle.title(), handle)
    cap.post(f"/api/v1/competitions/{slug}/join", json={"accept_rules": True})
    team = cap.post(f"/api/v1/competitions/{slug}/teams", json={"name": "Gradient Gang"}).json()
    # Team names are unique per competition (case-insensitive).
    third.post(f"/api/v1/competitions/{slug}/join", json={"accept_rules": True})
    assert third.post(f"/api/v1/competitions/{slug}/teams", json={"name": "gradient gang"}).status_code in (409, 422)
    assert mate.post(f"/api/v1/competitions/{slug}/teams/{team['id']}/invitations", json={"handle": "third"}).status_code == 403
    assert cap.post(f"/api/v1/competitions/{slug}/teams/{team['id']}/invitations", json={"handle": "mate"}).status_code == 201
    inv = mate.get("/api/v1/me/team-invitations").json()[0]
    # Joining through an invite still requires accepting the rules.
    r = mate.post(f"/api/v1/me/team-invitations/{inv['id']}/respond", json={"accept": True, "accept_rules": False})
    assert r.status_code == 422
    assert mate.post(f"/api/v1/me/team-invitations/{inv['id']}/respond", json={"accept": True, "accept_rules": True}).status_code == 200
    detail = cap.get(f"/api/v1/competitions/{slug}/team").json()
    assert {m["user"]["handle"] for m in detail["members"]} == {"cap", "mate"}
    # Team is full.
    cap.post(f"/api/v1/competitions/{slug}/teams/{team['id']}/invitations", json={"handle": "fourth"})
    inv4 = fourth.get("/api/v1/me/team-invitations").json()
    if inv4:
        r = fourth.post(f"/api/v1/me/team-invitations/{inv4[0]['id']}/respond", json={"accept": True, "accept_rules": True})
        assert r.status_code == 409
    # A submission by one member counts for the team; the daily limit is shared.
    assert mate.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("p.csv", perfect(), "text/csv")}).status_code == 202
    run_jobs()
    assert cap.get(f"/api/v1/competitions/{slug}/submissions").json()["total"] == 1
    # After the team lock, membership changes are refused.
    with SessionLocal() as db:
        db.execute(update(Competition).where(Competition.slug == slug).values(team_lock_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    r = mate.post(f"/api/v1/competitions/{slug}/teams/{team['id']}/leave")
    assert r.status_code == 409


def test_judged_event_flow(make_client) -> None:  # noqa: ANN001
    org, judge, t1, t2 = make_client(), make_client(), make_client(), make_client()
    signup_and_login(org, "host@example.com", "Host", "host")
    o = org.post("/api/v1/orgs", json={"name": "Hack Club", "type": "club"}).json()
    comp = org.post("/api/v1/competitions", json={
        "title": "Weekend Hackathon", "summary": "Build something useful for campus in a weekend.",
        "description_md": "Teams build and demo a project. " * 5, "rules_md": "Original work only; open source licenses allowed.",
        "host_org_id": o["id"], "event_type": "hackathon", "task_type": "hackathon", "scoring_mode": "judged",
        "starts_at": (utcnow() - timedelta(days=1)).isoformat(), "ends_at": (utcnow() + timedelta(days=1)).isoformat(),
        "team_max_size": 4}).json()
    slug = comp["slug"]
    assert org.post(f"/api/v1/competitions/{slug}/publish").status_code == 409  # rubric required
    assert org.put(f"/api/v1/competitions/{slug}/rubric", json={"criteria": [
        {"key": "impact", "label": "Impact", "min": 0, "max": 10, "weight": 2},
        {"key": "execution", "label": "Execution", "min": 0, "max": 10, "weight": 1}]}).status_code == 200
    assert org.post(f"/api/v1/competitions/{slug}/publish").status_code == 200
    for c, email, handle, title in ((t1, "t1@example.com", "teamone", "EcoMap"), (t2, "t2@example.com", "teamtwo", "StudyBuddy")):
        signup_and_login(c, email, handle.title(), handle)
        c.post(f"/api/v1/competitions/{slug}/join", json={"accept_rules": True})
        assert c.put(f"/api/v1/competitions/{slug}/project-submission", json={"title": title, "summary": "Demo",
                                                                           "repo_url": "https://example.com/repo"}).status_code == 200
    signup_and_login(judge, "judge@example.com", "Judy", "judy")
    assert judge.get(f"/api/v1/competitions/{slug}/judging/queue").status_code in (403, 404)
    assert org.post(f"/api/v1/competitions/{slug}/judges", json={"handle": "judy"}).status_code == 201
    queue = judge.get(f"/api/v1/competitions/{slug}/judging/queue").json()
    entries = queue["entries"]
    assert len(entries) == 2
    for e, (impact, execution) in zip(entries, [(9, 7), (6, 8)], strict=True):
        r = judge.put(f"/api/v1/competitions/{slug}/judging/scores/{e['team_id']}",
                      json={"scores": {"impact": impact, "execution": execution}, "feedback": "Nice", "submit": True})
        assert r.status_code == 200, r.text
    bad = judge.put(f"/api/v1/competitions/{slug}/judging/scores/{entries[0]['team_id']}",
                    json={"scores": {"impact": 11, "execution": 5}, "submit": True})
    assert bad.status_code == 422
    # Rubric locked once scores exist.
    assert org.put(f"/api/v1/competitions/{slug}/rubric", json={"criteria": [{"key": "novelty", "label": "Novelty", "min": 0, "max": 5, "weight": 1}]}).status_code == 409
    # Participants never see judges' raw scores before results.
    lb = t1.get(f"/api/v1/competitions/{slug}/leaderboard").json()
    assert lb["rows"] == [] or lb["hidden_reason"]
    with SessionLocal() as db:
        db.execute(update(Competition).where(Competition.slug == slug).values(ends_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    assert org.post(f"/api/v1/competitions/{slug}/finalize").status_code == 200
    final = t1.get(f"/api/v1/competitions/{slug}/leaderboard").json()
    assert final["is_final"] and final["rows"][0]["team_name"] is not None and final["rows"][0]["rank"] == 1


def test_leaderboard_ties_break_by_earlier_submission(make_client) -> None:  # noqa: ANN001
    org, early, late = make_client(), make_client(), make_client()
    comp = setup_competition(org)
    slug = comp["slug"]
    for c, email, handle in ((early, "early@example.com", "early"), (late, "late@example.com", "late")):
        signup_and_login(c, email, handle.title(), handle)
        c.post(f"/api/v1/competitions/{slug}/join", json={"accept_rules": True})
    early.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("a.csv", half(), "text/csv")})
    run_jobs()
    late.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("b.csv", half(), "text/csv")})
    run_jobs()
    rows = ApiClient.get(early, f"/api/v1/competitions/{slug}/leaderboard").json()["rows"]
    assert [r["members"][0]["handle"] for r in rows] == ["early", "late"]
    assert rows[0]["score"] == rows[1]["score"] or rows[0]["public_score"] == rows[1]["public_score"]
