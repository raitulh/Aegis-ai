"""End-to-end competition flow: create → publish → join → submit → score → leaderboard → finalize → certificates."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import update

from app.core.db import SessionLocal
from app.core.time import utcnow
from app.models.competition import Competition
from tests.conftest import ApiClient, run_jobs, signup_and_login

GROUND_TRUTH = "id,target,Usage\n" + "\n".join(f"r{i},{i % 2},{'Public' if i < 6 else 'Private'}" for i in range(12)) + "\n"


def perfect() -> bytes:
    return ("id,target\n" + "\n".join(f"r{i},{i % 2}" for i in range(12)) + "\n").encode()


def half() -> bytes:
    return ("id,target\n" + "\n".join(f"r{i},{1 if i < 6 else i % 2}" for i in range(12)) + "\n").encode()


def setup_competition(org_client: ApiClient, *, visibility: str = "public", publish: bool = True, **overrides: Any) -> dict[str, Any]:
    signup_and_login(org_client, "org@example.com", "Olga Organizer", "olga")
    org = org_client.post("/api/v1/orgs", json={"name": "Test Data Club", "type": "club"})
    assert org.status_code == 201, org.text
    payload = {
        "title": "Binary Test Challenge", "summary": "A small binary classification challenge used in tests.",
        "description_md": "Predict the target for each id. " * 5, "rules_md": "Be nice. Use the provided data only.",
        "host_org_id": org.json()["id"], "visibility": visibility, "starts_at": (utcnow() - timedelta(days=1)).isoformat(),
        "ends_at": (utcnow() + timedelta(days=5)).isoformat(), "evaluation": {"metric": "accuracy", "id_column": "id", "target_column": "target"},
        "daily_submission_limit": 3, "team_max_size": 3, **overrides,
    }
    r = org_client.post("/api/v1/competitions", json=payload)
    assert r.status_code == 201, r.text
    comp = r.json()
    checks = org_client.get(f"/api/v1/competitions/{comp['slug']}/publish-checks").json()
    assert any(c["key"] == "ground_truth" and not c["ok"] for c in checks)
    assert org_client.post(f"/api/v1/competitions/{comp['slug']}/publish").status_code == 409
    r = org_client.post(f"/api/v1/competitions/{comp['slug']}/ground-truth", files={"file": ("solution.csv", GROUND_TRUTH, "text/csv")})
    assert r.status_code == 201, r.text
    if publish:
        r = org_client.post(f"/api/v1/competitions/{comp['slug']}/publish")
        assert r.status_code == 200, r.text
    return comp


def test_full_competition_lifecycle(make_client) -> None:  # noqa: ANN001
    org, alice, bob, anon = make_client(), make_client(), make_client(), make_client()
    comp = setup_competition(org)
    slug = comp["slug"]

    assert anon.get(f"/api/v1/competitions/{slug}").status_code == 200
    listed = anon.get("/api/v1/competitions").json()
    assert any(c["slug"] == slug for c in listed["items"])

    signup_and_login(alice, "alice@example.com", "Alice", "alice")
    signup_and_login(bob, "bob@example.com", "Bob", "bob")
    # Must accept rules; must join before submitting.
    assert alice.post(f"/api/v1/competitions/{slug}/join", json={"accept_rules": False}).status_code == 422
    r = alice.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("p.csv", perfect(), "text/csv")})
    assert r.status_code == 403
    assert alice.post(f"/api/v1/competitions/{slug}/join", json={"accept_rules": True}).status_code == 200
    assert bob.post(f"/api/v1/competitions/{slug}/join", json={"accept_rules": True}).status_code == 200

    # Idempotent upload: retrying with the same key returns the same submission.
    r1 = alice.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("p.csv", perfect(), "text/csv")},
                    headers={"Idempotency-Key": "abc-123"})
    r2 = alice.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("p.csv", perfect(), "text/csv")},
                    headers={"Idempotency-Key": "abc-123"})
    assert r1.status_code == 202 and r1.json()["id"] == r2.json()["id"]
    r = bob.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("h.csv", half(), "text/csv")})
    assert r.status_code == 202
    bad = bob.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("bad.csv", b"id,target\nr0,1\nr0,1\n", "text/csv")})
    assert bad.status_code == 202
    assert run_jobs() >= 3

    subs = alice.get(f"/api/v1/competitions/{slug}/submissions").json()["items"]
    assert subs[0]["status"] == "scored" and subs[0]["public_score"] == 1.0
    assert subs[0]["private_score"] is None  # private score hidden until finalization
    bob_subs = bob.get(f"/api/v1/competitions/{slug}/submissions").json()["items"]
    rejected = [s for s in bob_subs if s["status"] == "rejected"]
    assert rejected and rejected[0]["error_details"], "validation errors should be row-level and actionable"

    lb = anon.get(f"/api/v1/competitions/{slug}/leaderboard").json()
    assert lb["is_final"] is False and [r["rank"] for r in lb["rows"]] == [1, 2]
    assert lb["rows"][0]["members"][0]["handle"] == "alice"
    assert all("private" not in str(k) for row in lb["rows"] for k in row)

    # Daily limit.
    for _ in range(2):
        alice.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("p.csv", perfect(), "text/csv")})
    r = alice.post(f"/api/v1/competitions/{slug}/submissions", files={"file": ("p.csv", perfect(), "text/csv")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "submission_limit_reached"
    run_jobs()

    # Only organizers can finalize, and only after the end.
    assert alice.post(f"/api/v1/competitions/{slug}/finalize").status_code == 403
    assert org.post(f"/api/v1/competitions/{slug}/finalize").status_code == 409
    with SessionLocal() as db:
        db.execute(update(Competition).where(Competition.slug == slug).values(ends_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    r = org.post(f"/api/v1/competitions/{slug}/finalize")
    assert r.status_code == 200, r.text
    final = anon.get(f"/api/v1/competitions/{slug}/leaderboard").json()
    assert final["is_final"] and final["snapshot_version"] == 1
    assert final["rows"][0]["members"][0]["handle"] == "alice" and final["rows"][0]["label"] == "Winner"

    # Scoring rules are locked after finalization.
    r = org.patch(f"/api/v1/competitions/{slug}", json={"evaluation": {"metric": "macro_f1", "id_column": "id", "target_column": "target"}})
    assert r.status_code == 409

    # Certificates: preview, issue (idempotent), verify publicly, revoke.
    preview = org.get(f"/api/v1/competitions/{slug}/certificates/preview").json()
    assert {p["handle"] for p in preview} == {"alice", "bob"}
    assert org.post(f"/api/v1/competitions/{slug}/certificates/issue").json()["issued"] == 2
    assert org.post(f"/api/v1/competitions/{slug}/certificates/issue").json()["issued"] == 0
    certs = alice.get("/api/v1/me/certificates").json()
    pid = certs[0]["public_id"]
    v = anon.get(f"/api/v1/certificates/verify/{pid}").json()
    assert v["status"] == "valid" and v["recipient_name"] == "Alice" and "email" not in str(v)
    assert anon.get("/api/v1/certificates/verify/DB-0000-0000-00").status_code == 404
    assert alice.post(f"/api/v1/certificates/{pid}/revoke", json={"reason": "self-revoke attempt"}).status_code == 403
    assert org.post(f"/api/v1/certificates/{pid}/revoke", json={"reason": "Issued in error during test"}).status_code == 200
    assert anon.get(f"/api/v1/certificates/verify/{pid}").json()["status"] == "revoked"

    # Profile shows the factual result.
    profile = anon.get("/api/v1/users/alice").json()
    assert profile["results"][0]["label"] == "Winner"

    # Corrections create a new snapshot version with an audited reason.
    r = org.post(f"/api/v1/competitions/{slug}/results/corrections", json={"reason": "Re-run after review"})
    assert r.status_code == 200, r.text
    history = anon.get(f"/api/v1/competitions/{slug}/results/history").json()
    assert [h["version"] for h in history] == [2, 1]


def test_draft_and_private_visibility(make_client) -> None:  # noqa: ANN001
    org, outsider = make_client(), make_client()
    comp = setup_competition(org, publish=False)
    signup_and_login(outsider, "out@example.com", "Outsider", "outsider")
    assert outsider.get(f"/api/v1/competitions/{comp['slug']}").status_code == 404
    assert outsider.get(f"/api/v1/competitions/{comp['slug']}/leaderboard").status_code == 404
    assert org.get(f"/api/v1/competitions/{comp['slug']}").status_code == 200
    assert outsider.patch(f"/api/v1/competitions/{comp['slug']}", json={"title": "Hijacked"}).status_code == 404


def test_university_only_competition_hidden_from_non_members(make_client) -> None:  # noqa: ANN001
    org, outsider = make_client(), make_client()
    comp = setup_competition(org, visibility="university")
    signup_and_login(outsider, "stranger@example.com", "Stranger", "stranger")
    assert outsider.get(f"/api/v1/competitions/{comp['slug']}").status_code == 404
    assert not any(c["slug"] == comp["slug"] for c in outsider.get("/api/v1/competitions").json()["items"])
    assert not any(i["url"].endswith(comp["slug"]) for i in outsider.get("/api/v1/search", params={"q": "binary"}).json()["items"])


def test_invite_only_requires_code(make_client) -> None:  # noqa: ANN001
    org, student = make_client(), make_client()
    comp = setup_competition(org, visibility="invite_only")
    signup_and_login(student, "inv@example.com", "Invitee", "invitee")
    r = student.post(f"/api/v1/competitions/{comp['slug']}/join", json={"accept_rules": True})
    assert r.status_code == 403 and r.json()["error"]["code"] == "invite_required"
    code = org.post(f"/api/v1/competitions/{comp['slug']}/invite-code").json()["invite_code"]
    r = student.post(f"/api/v1/competitions/{comp['slug']}/join", json={"accept_rules": True, "invite_code": code})
    assert r.status_code == 200


def test_ground_truth_is_never_exposed(make_client) -> None:  # noqa: ANN001
    org, anon = make_client(), make_client()
    comp = setup_competition(org)
    detail = org.get(f"/api/v1/competitions/{comp['slug']}/manage").json()
    assert "storage_key" not in str(detail) and "private/" not in str(detail)
    r = anon.get("/api/v1/files/download", params={"token": "forged.token"})
    assert r.status_code == 404
