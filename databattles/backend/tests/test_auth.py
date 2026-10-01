from __future__ import annotations

from tests.conftest import PASSWORD, ApiClient, last_email_token, signup_and_login


def test_signup_verify_login_logout(client: ApiClient) -> None:
    r = client.post("/api/v1/auth/signup", json={"email": "Ada@Example.com", "password": PASSWORD, "display_name": "Ada"})
    assert r.status_code == 202
    # Unverified users cannot sign in.
    r = client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "email_not_verified"
    token = last_email_token("ada@example.com", "/verify-email")
    r = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert me["email"] == "ada@example.com"
    # Tokens are single use.
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 400
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").json() is None
    r = client.post("/api/v1/auth/login", json={"email": "ADA@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert "session_token" not in r.json() or r.json()["session_token"] is None


def test_login_errors_are_generic(client: ApiClient) -> None:
    signup_and_login(client, "bob@example.com", "Bob")
    client.post("/api/v1/auth/logout")
    wrong = client.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": "nope-nope-nope"})
    missing = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "nope-nope-nope"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]


def test_duplicate_signup_does_not_reveal_account(client: ApiClient) -> None:
    signup_and_login(client, "carol@example.com", "Carol")
    r = client.post("/api/v1/auth/signup", json={"email": "carol@example.com", "password": PASSWORD, "display_name": "Imposter"})
    assert r.status_code == 202  # same response as a new signup


def test_weak_password_rejected(client: ApiClient) -> None:
    r = client.post("/api/v1/auth/signup", json={"email": "dan@example.com", "password": "password", "display_name": "Dan"})
    assert r.status_code == 422


def test_password_reset_revokes_sessions(make_client) -> None:  # noqa: ANN001
    a, b = make_client(), make_client()
    signup_and_login(a, "erin@example.com", "Erin")
    assert b.post("/api/v1/auth/login", json={"email": "erin@example.com", "password": PASSWORD}).status_code == 200
    assert b.post("/api/v1/auth/forgot-password", json={"email": "erin@example.com"}).status_code == 202
    token = last_email_token("erin@example.com", "/reset-password")
    assert b.post("/api/v1/auth/reset-password", json={"token": token, "password": "Another-Strong-99"}).status_code == 200
    assert a.get("/api/v1/auth/me").json() is None
    assert a.post("/api/v1/auth/login", json={"email": "erin@example.com", "password": "Another-Strong-99"}).status_code == 200


def test_csrf_required_for_cookie_sessions(client: ApiClient) -> None:
    signup_and_login(client, "fay@example.com", "Fay")
    # Bypass the helper's automatic header to simulate a cross-site form post.
    r = super(ApiClient, client).request("PATCH", "/api/v1/me/profile", json={"headline": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.patch("/api/v1/me/profile", json={"headline": "ML student"}, headers={"Origin": "https://evil.example.net"})
    assert r.status_code == 403
    assert client.patch("/api/v1/me/profile", json={"headline": "ML student"}).status_code == 200


def test_bearer_tokens_for_api_clients(client: ApiClient, make_client) -> None:  # noqa: ANN001
    signup_and_login(client, "gus@example.com", "Gus")
    r = client.post("/api/v1/auth/login", json={"email": "gus@example.com", "password": PASSWORD, "issue_bearer": True})
    token = r.json()["session_token"]
    other = make_client()
    r = super(ApiClient, other).request("GET", "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["email"] == "gus@example.com"


def test_login_rate_limit(client: ApiClient) -> None:
    codes = [client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "wrong-password-1"}).status_code
             for _ in range(25)]
    assert 429 in codes


def test_account_deletion_anonymizes(client: ApiClient) -> None:
    signup_and_login(client, "hal@example.com", "Hal", "hal")
    r = client.post("/api/v1/me/delete", json={"password": PASSWORD, "confirm": "DELETE"})
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").json() is None
    assert client.get("/api/v1/users/hal").status_code == 404
