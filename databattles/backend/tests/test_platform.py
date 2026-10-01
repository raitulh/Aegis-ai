"""Organizations, learning, discussions, moderation, notifications, datasets, GitHub webhooks, admin authorization."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

from sqlalchemy import select

from app.core.db import SessionLocal
from tests.conftest import ApiClient, last_email_token, signup_and_login


def _verified_university(admin: ApiClient, owner: ApiClient) -> dict:
    signup_and_login(owner, "dean@example.com", "Dean", "dean")
    org = owner.post("/api/v1/orgs", json={"name": "Test University", "type": "university"}).json()
    # Org admins cannot set trusted email domains themselves…
    r = owner.patch(f"/api/v1/orgs/{org['slug']}", json={"email_domains": ["gmail.com"]})
    assert r.status_code == 403
    # …only platform admins can, and public providers are refused.
    assert admin.patch(f"/api/v1/orgs/{org['slug']}", json={"email_domains": ["gmail.com"]}).status_code == 422
    assert admin.patch(f"/api/v1/orgs/{org['slug']}", json={"email_domains": ["testuni.example.edu"]}).status_code == 200
    assert admin.put(f"/api/v1/orgs/{org['slug']}/verification", json={"status": "verified", "reason": "Checked registry"}).status_code == 200
    return org


def test_domain_verification_and_membership_review(make_client, admin_client) -> None:  # noqa: ANN001
    owner, student, other = make_client(), make_client(), make_client()
    org = _verified_university(admin_client, owner)
    signup_and_login(student, "stu@example.com", "Stu", "stu")
    r = student.post(f"/api/v1/orgs/{org['slug']}/membership/verify-email", json={"email": "stu@elsewhere.example.org"})
    assert r.status_code == 422
    assert student.post(f"/api/v1/orgs/{org['slug']}/membership/verify-email", json={"email": "stu@testuni.example.edu"}).status_code == 202
    token = last_email_token("stu@testuni.example.edu", "/orgs/verify-email")
    # Another account cannot use someone else's link.
    signup_and_login(other, "other@example.com", "Other", "other")
    assert other.post("/api/v1/orgs/verify-email/confirm", json={"token": token}).status_code == 422
    r = student.post("/api/v1/orgs/verify-email/confirm", json={"token": token})
    assert r.status_code == 200 and r.json()["viewer"]["verified"] is True
    profile = other.get("/api/v1/users/stu").json()
    assert profile["verified"]["university"]["slug"] == org["slug"]

    # Membership requests are reviewed by admins; notes to reviewers stay private.
    assert other.post(f"/api/v1/orgs/{org['slug']}/membership/request", json={"note": "Exchange student"}).status_code == 201
    assert other.get(f"/api/v1/orgs/{org['slug']}/members").status_code == 403
    pending = owner.get(f"/api/v1/orgs/{org['slug']}/members", params={"status": "pending"}).json()["items"]
    assert pending[0]["request_note"] == "Exchange student"
    assert owner.post(f"/api/v1/orgs/{org['slug']}/members/{pending[0]['id']}/review", json={"approve": True}).status_code == 200
    dash = owner.get(f"/api/v1/orgs/{org['slug']}/admin/dashboard").json()
    assert dash["members"]["active"] == 3 and dash["members"]["verified"] == 3
    # Last owner cannot leave.
    assert owner.post(f"/api/v1/orgs/{org['slug']}/membership/leave").status_code == 409


def test_org_invites_and_role_limits(make_client, admin_client) -> None:  # noqa: ANN001
    owner, invitee = make_client(), make_client()
    signup_and_login(owner, "own@example.com", "Owner", "owner1")
    org = owner.post("/api/v1/orgs", json={"name": "Robotics Club", "type": "club"}).json()
    inv = owner.post(f"/api/v1/orgs/{org['slug']}/invites", json={"role": "admin", "max_uses": 1}).json()
    token = inv["link"].split("token=")[1]
    signup_and_login(invitee, "newadmin@example.com", "New Admin", "newadmin")
    assert invitee.get("/api/v1/orgs/invites/preview", params={"token": token}).json()["role"] == "admin"
    assert invitee.post("/api/v1/orgs/invites/accept", json={"token": token}).status_code == 200
    assert invitee.post("/api/v1/orgs/invites/accept", json={"token": token}).status_code in (200, 404)
    # Admins cannot mint admin invites or promote themselves to owner.
    assert invitee.post(f"/api/v1/orgs/{org['slug']}/invites", json={"role": "admin"}).status_code == 403
    members = owner.get(f"/api/v1/orgs/{org['slug']}/members").json()["items"]
    me = next(m for m in members if m["user"]["handle"] == "newadmin")
    assert invitee.put(f"/api/v1/orgs/{org['slug']}/members/{me['id']}/role", json={"role": "owner"}).status_code == 403


def _course(admin: ApiClient) -> str:
    c = admin.post("/api/v1/learn/courses", json={"title": "Stats 101", "category": "statistics", "summary": "Basics"}).json()
    slug = c["slug"]
    admin.post(f"/api/v1/learn/courses/{slug}/lessons", json={"title": "Means", "kind": "article", "body_md": "The mean is the average."})
    admin.post(f"/api/v1/learn/courses/{slug}/lessons", json={"title": "Quiz", "kind": "quiz", "pass_threshold": 100})
    assert admin.post(f"/api/v1/learn/courses/{slug}/publish").status_code == 409  # quiz has no questions
    admin.put(f"/api/v1/learn/courses/{slug}/lessons/quiz/questions", json={"questions": [
        {"prompt": "Mean of 1,2,3?", "options": ["1", "2", "3"], "correct_option": 1, "explanation": "(1+2+3)/3 = 2"}]})
    admin.patch(f"/api/v1/learn/courses/{slug}", json={"issues_certificate": True})
    assert admin.post(f"/api/v1/learn/courses/{slug}/publish").status_code == 200
    return slug


def test_learning_quiz_grading_is_server_side(make_client, admin_client) -> None:  # noqa: ANN001
    learner = make_client()
    slug = _course(admin_client)
    signup_and_login(learner, "learn@example.com", "Learner", "learner")
    lesson = learner.get(f"/api/v1/learn/courses/{slug}/lessons/quiz").json()
    assert "correct_option" not in lesson["questions"][0]  # answers never shipped before passing
    assert learner.post(f"/api/v1/learn/courses/{slug}/lessons/means/complete").status_code == 409  # must enroll
    learner.post(f"/api/v1/learn/courses/{slug}/enroll")
    learner.post(f"/api/v1/learn/courses/{slug}/lessons/means/complete")
    qid = lesson["questions"][0]["id"]
    wrong = learner.post(f"/api/v1/learn/courses/{slug}/lessons/quiz/quiz", json={"answers": {qid: 0}}).json()
    assert wrong["passed"] is False and "correct_option" not in wrong["results"][0]
    right = learner.post(f"/api/v1/learn/courses/{slug}/lessons/quiz/quiz", json={"answers": {qid: 1}}).json()
    assert right["passed"] and right["course_completed"] and right["results"][0]["correct_option"] == 1
    detail = learner.get(f"/api/v1/learn/courses/{slug}").json()
    assert detail["progress"]["percent"] == 100 and detail["certificate_public_id"]
    badges = learner.get("/api/v1/me/badges").json()
    assert any(b["badge"]["slug"] == "first-course" for b in badges)


def test_discussions_moderation_and_mentions(make_client, admin_client) -> None:  # noqa: ANN001
    alice, bob, mod = make_client(), make_client(), make_client()
    signup_and_login(alice, "a1@example.com", "Alice", "alice1")
    signup_and_login(bob, "b1@example.com", "Bob", "bob1")
    signup_and_login(mod, "m1@example.com", "Mod", "mod1")
    from tests.conftest import grant_role

    grant_role("m1@example.com", "moderator")
    assert alice.post("/api/v1/discussions/threads", json={"title": "Staff only?", "body_md": "Trying to post an announcement.",
                                                           "category": "announcements"}).status_code == 403
    t = alice.post("/api/v1/discussions/threads", json={"title": "Help with RMSE", "body_md": "How do I compute RMSE? cc @bob1 <script>alert(1)</script>",
                                                        "category": "help"}).json()
    thread = bob.get(f"/api/v1/discussions/threads/{t['id']}").json()
    assert "<script>" not in thread["body_html"] and 'href="/u/bob1"' in thread["body_html"]
    notes = bob.get("/api/v1/notifications").json()["items"]
    assert any(n["kind"] == "mention" for n in notes)
    c = bob.post(f"/api/v1/discussions/threads/{t['id']}/comments", json={"body_md": "sqrt(mean((y - p)^2))"}).json()
    assert bob.post(f"/api/v1/discussions/threads/{t['id']}/accept", json={"comment_id": c["id"]}).status_code == 403
    assert alice.post(f"/api/v1/discussions/threads/{t['id']}/accept", json={"comment_id": c["id"]}).status_code == 200
    # Editing keeps history.
    assert bob.patch(f"/api/v1/discussions/comments/{c['id']}", json={"body_md": "sqrt(mean((y - p)**2))"}).status_code == 200
    assert alice.patch(f"/api/v1/discussions/comments/{c['id']}", json={"body_md": "hijack"}).status_code == 403
    assert len(mod.get(f"/api/v1/discussions/revisions/comment/{c['id']}").json()) == 1
    # Report → moderation queue → hide.
    assert bob.post("/api/v1/reports", json={"target_type": "thread", "target_id": t["id"], "reason": "spam"}).status_code == 201
    assert alice.post("/api/v1/reports", json={"target_type": "thread", "target_id": t["id"], "reason": "spam"}).status_code == 409
    assert bob.get("/api/v1/moderation/queue").status_code == 403
    queue = mod.get("/api/v1/moderation/queue").json()
    assert queue["items"][0]["report_count"] == 1
    assert mod.post("/api/v1/moderation/resolve", json={"target_type": "thread", "target_id": t["id"], "action": "hide",
                                                        "note": "Hidden for review"}).status_code == 200
    assert bob.get(f"/api/v1/discussions/threads/{t['id']}").status_code == 404
    assert alice.get(f"/api/v1/discussions/threads/{t['id']}").status_code == 200  # author still sees it
    # Suspension revokes sessions.
    uid = alice.get("/api/v1/auth/me").json()["id"]
    assert mod.put(f"/api/v1/moderation/users/{uid}/status", json={"status": "suspended", "reason": "Repeated spam"}).status_code == 200
    assert alice.get("/api/v1/auth/me").json() is None


def test_datasets_signed_downloads_and_privacy(make_client) -> None:  # noqa: ANN001
    owner, anon = make_client(), make_client()
    signup_and_login(owner, "ds@example.com", "Data Owner", "dsowner")
    ds = owner.post("/api/v1/datasets", json={"title": "Weather Sample", "license": "cc-by-4.0", "visibility": "private"}).json()
    slug = ds["slug"]
    f = owner.post(f"/api/v1/datasets/{slug}/versions/1/files", files={"file": ("w.csv", b"a,b\n1,2\n3,4\n", "text/csv")}).json()
    assert f["row_count"] == 2
    bad = owner.post(f"/api/v1/datasets/{slug}/versions/1/files", files={"file": ("x.html", b"<script>", "text/html")})
    assert bad.status_code == 422
    assert anon.get(f"/api/v1/datasets/{slug}").status_code == 404
    owner.post(f"/api/v1/datasets/{slug}/versions/1/publish")
    owner.patch(f"/api/v1/datasets/{slug}", json={"visibility": "public", "requires_terms": True, "terms_md": "Research use only."})
    r = anon.post(f"/api/v1/datasets/{slug}/files/{f['id']}/download")
    assert r.status_code in (401, 403)
    signup_and_login(anon, "reader@example.com", "Reader", "reader")
    assert anon.post(f"/api/v1/datasets/{slug}/files/{f['id']}/download").status_code == 403
    anon.post(f"/api/v1/datasets/{slug}/accept-terms")
    url = anon.post(f"/api/v1/datasets/{slug}/files/{f['id']}/download").json()["url"]
    got = anon.get(url.replace("http://localhost:8000", ""))
    assert got.status_code == 200 and got.content == b"a,b\n1,2\n3,4\n"
    assert got.headers["content-disposition"].startswith("attachment")
    tampered = url[:-3] + ("AAA" if not url.endswith("AAA") else "BBB")
    assert anon.get(tampered.replace("http://localhost:8000", "")).status_code == 404


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()


def test_github_webhook_signature_and_idempotency(client: ApiClient) -> None:
    from app.models.github import GitHubIssue, GitHubRepository

    with SessionLocal() as db:
        db.add(GitHubRepository(github_repo_id=42, full_name="acme/tool", owner_login="acme", name="tool", html_url="https://github.com/acme/tool"))
        db.commit()
    payload = {"action": "opened", "repository": {"id": 42}, "issue": {
        "id": 777, "number": 5, "title": "Add docs", "state": "open", "labels": [{"name": "good first issue"}],
        "html_url": "https://github.com/acme/tool/issues/5", "user": {"login": "someone"}, "comments": 0,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}}
    body = json.dumps(payload).encode()
    raw = super(ApiClient, client).request
    r = raw("POST", "/api/v1/opensource/github/webhook", content=body,
            headers={"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-1", "X-Hub-Signature-256": "sha256=deadbeef"})
    assert r.status_code == 401
    headers = {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "d-1", "X-Hub-Signature-256": _sign(body), "Content-Type": "application/json"}
    assert raw("POST", "/api/v1/opensource/github/webhook", content=body, headers=headers).json()["status"] == "processed"
    assert raw("POST", "/api/v1/opensource/github/webhook", content=body, headers=headers).json()["status"] == "duplicate"
    # An older (out-of-order) update must not overwrite newer state.
    payload["issue"]["title"] = "Stale title"
    payload["issue"]["updated_at"] = "2025-12-31T00:00:00Z"
    body2 = json.dumps(payload).encode()
    headers2 = {**headers, "X-GitHub-Delivery": "d-2", "X-Hub-Signature-256": _sign(body2)}
    raw("POST", "/api/v1/opensource/github/webhook", content=body2, headers=headers2)
    with SessionLocal() as db:
        issue = db.scalar(select(GitHubIssue).where(GitHubIssue.github_issue_id == 777))
        assert issue is not None and issue.title == "Add docs" and issue.is_beginner_friendly
    issues = client.get("/api/v1/opensource/issues", params={"beginner": True}).json()
    assert issues["total"] == 1


def test_admin_endpoints_require_admin(make_client, admin_client) -> None:  # noqa: ANN001
    user = make_client()
    signup_and_login(user, "plain@example.com", "Plain", "plain")
    for path in ("/api/v1/admin/health", "/api/v1/admin/audit", "/api/v1/admin/jobs", "/api/v1/admin/flags"):
        assert user.get(path).status_code == 403
        assert admin_client.get(path).status_code == 200
    audit = admin_client.get("/api/v1/admin/audit").json()
    assert audit["total"] >= 0
    health = admin_client.get("/api/v1/admin/health").json()
    assert health["database"] == "ok"


def test_notifications_and_preferences(make_client) -> None:  # noqa: ANN001
    u = make_client()
    signup_and_login(u, "n@example.com", "Nora", "nora")
    uid = u.get("/api/v1/auth/me").json()["id"]
    from app.modules.notifications.service import notify, unsubscribe_url

    with SessionLocal() as db:
        for i in range(3):
            notify(db, uid, "announcement", f"Update {i}", dedupe_key=f"t:{i}")
        notify(db, uid, "announcement", "Update 0 again", dedupe_key="t:0")  # deduplicated
        db.commit()
    page = u.get("/api/v1/notifications", params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    rest = u.get("/api/v1/notifications", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert len(rest["items"]) == 1
    assert u.get("/api/v1/notifications/unread-count").json()["count"] == 3
    u.post("/api/v1/notifications/read", json={"all": True})
    assert u.get("/api/v1/notifications/unread-count").json()["count"] == 0
    token = unsubscribe_url(uuid.UUID(uid), "announcement").split("token=")[1]
    assert u.post("/api/v1/notifications/unsubscribe", json={"token": token}).status_code == 200
    assert u.get("/api/v1/notifications/preferences").json()["announcement"]["email"] is False


def test_search_respects_visibility(make_client) -> None:  # noqa: ANN001
    u = make_client()
    signup_and_login(u, "s@example.com", "Searcher", "searcher")
    u.post("/api/v1/projects", json={"title": "Quantum Pancake Detector", "summary": "Finds pancakes", "visibility": "public"})
    u.post("/api/v1/projects", json={"title": "Secret Pancake Plan", "summary": "Hidden", "visibility": "private"})
    anon = make_client()
    res = anon.get("/api/v1/search", params={"q": "pancake"}).json()
    titles = [i["title"] for i in res["items"]]
    assert "Quantum Pancake Detector" in titles and "Secret Pancake Plan" not in titles
    assert res["facets"].get("project") == 1
