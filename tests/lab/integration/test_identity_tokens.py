"""Identity: JWT access tokens, rotating refresh tokens (reuse detection), revocation, password reset and
email verification."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from tests.conftest import requires_db, signup

pytestmark = [requires_db, pytest.mark.db]

PASSWORD = "Str0ng-Pass!23"


def _account(client, org: str = "Token Org"):
    email = f"tok-{uuid.uuid4().hex[:10]}@example.com"
    ws = signup(client, email=email, org=org)
    client.cookies.clear()  # the Workspace helper sends its cookie explicitly; bare client calls stay anonymous
    return ws, email


def _token(client, email: str, password: str = PASSWORD, **extra):
    return client.post("/api/v1/auth/token", json={"email": email, "password": password, **extra})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _audit_actions(org_id: str) -> list[str]:
    from sqlalchemy import select

    from aegis_api.db.session import session_factory
    from aegis_api.models import AuditLog

    session = session_factory(admin=True)()
    try:
        return list(session.scalars(select(AuditLog.action).where(AuditLog.organization_id == uuid.UUID(org_id))).all())
    finally:
        session.close()


def test_token_issue_and_bearer_access(client):
    ws, email = _account(client)
    r = _token(client, email)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 900
    assert body["refresh_token"].startswith("aegr_") and body["organization_id"] == ws.org_id

    me = client.get("/api/v1/users/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200, me.text
    assert me.json()["auth_method"] == "jwt" and me.json()["user"]["email"] == email
    assert me.json()["organization_id"] == ws.org_id
    # Core endpoints accept the JWT too.
    assert client.get("/api/v1/auth/session", headers=_bearer(body["access_token"])).status_code == 200
    actions = _audit_actions(ws.org_id)
    assert "LOGIN" in actions and "TOKEN_ISSUED" in actions


def test_token_rejects_bad_credentials_uniformly(client):
    _, email = _account(client)
    wrong = _token(client, email, password="Wr0ng-Pass!99")
    unknown = _token(client, f"nobody-{uuid.uuid4().hex[:6]}@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert wrong.json()["error"]["code"] == "invalid_credentials"


def test_token_for_specific_organization(client):
    ws, email = _account(client)
    second = ws.post("/api/v1/organizations", json={"name": "Second"}).json()
    r = _token(client, email, organization_id=second["id"])
    assert r.status_code == 200 and r.json()["organization_id"] == second["id"]
    me = client.get("/api/v1/users/me", headers=_bearer(r.json()["access_token"])).json()
    assert me["organization_id"] == second["id"]

    foreign, _ = _account(client, org="Foreign")
    assert _token(client, email, organization_id=foreign.org_id).status_code == 403


def test_invalid_or_foreign_jwt_rejected(client):
    import jwt as pyjwt

    from aegis_api.config import get_settings

    ws, email = _account(client)
    access = _token(client, email).json()["access_token"]
    tampered = access[:-3] + ("abc" if not access.endswith("abc") else "xyz")
    assert client.get("/api/v1/users/me", headers=_bearer(tampered)).status_code == 401
    s = get_settings()
    forged = pyjwt.encode(
        {"iss": s.jwt_issuer, "aud": s.jwt_audience, "sub": ws.user_id, "org": ws.org_id, "typ": "access"},
        "attacker-secret",
        algorithm="HS256",
        headers={"kid": s.jwt_active_kid},
    )
    assert client.get("/api/v1/users/me", headers=_bearer(forged)).status_code == 401


def test_jwt_requires_current_active_membership(client):
    from aegis_api.db.session import session_factory
    from aegis_api.models import Membership

    ws, email = _account(client)
    access = _token(client, email).json()["access_token"]
    session = session_factory(admin=True)()
    try:
        membership = session.query(Membership).filter_by(user_id=uuid.UUID(ws.user_id)).one()
        membership.role = "viewer"
        session.commit()
        me = client.get("/api/v1/users/me", headers=_bearer(access)).json()
        assert me["role"] == "viewer" and "mission:create" not in me["lab_permissions"]  # role applies immediately
        membership.status = "suspended"
        session.commit()
    finally:
        session.close()
    assert client.get("/api/v1/users/me", headers=_bearer(access)).status_code == 403


def test_refresh_rotation_and_reuse_detection(client):
    ws, email = _account(client)
    first = _token(client, email).json()

    rotated = client.post("/api/v1/auth/token/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert client.get("/api/v1/users/me", headers=_bearer(second["access_token"])).status_code == 200

    third = client.post("/api/v1/auth/token/refresh", json={"refresh_token": second["refresh_token"]}).json()
    assert client.get("/api/v1/users/me", headers=_bearer(third["access_token"])).status_code == 200

    # Replaying an already-rotated token is treated as theft: the whole family is revoked.
    replay = client.post("/api/v1/auth/token/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "refresh_token_reused"
    newest = client.post("/api/v1/auth/token/refresh", json={"refresh_token": third["refresh_token"]})
    assert newest.status_code == 401
    # Access tokens of the revoked sign-in stop working immediately.
    dead = client.get("/api/v1/users/me", headers=_bearer(third["access_token"]))
    assert dead.status_code == 401 and dead.json()["error"]["code"] == "session_revoked"
    # Other sign-ins are unaffected.
    fresh = _token(client, email).json()
    assert client.get("/api/v1/users/me", headers=_bearer(fresh["access_token"])).status_code == 200

    from aegis_api.db.session import session_factory
    from aegis_api.lab.models import RefreshToken

    session = session_factory(admin=True)()
    try:
        family = session.query(RefreshToken).filter(RefreshToken.user_id == uuid.UUID(ws.user_id)).all()
        reused = [t for t in family if t.revoked_reason == "reuse_detected"]
        assert len(reused) == 3
        assert all(t.token_hash != first["refresh_token"] for t in family)  # stored hashed only
    finally:
        session.close()
    assert "TOKEN_REUSE_DETECTED" in _audit_actions(ws.org_id)


def test_refresh_rejects_unknown_and_expired(client):
    from aegis_api.db.base import utcnow
    from aegis_api.db.session import session_factory
    from aegis_api.lab.models import RefreshToken

    ws, email = _account(client)
    assert client.post("/api/v1/auth/token/refresh", json={"refresh_token": "aegr_nope"}).status_code == 401
    assert client.post("/api/v1/auth/token/refresh", json={"refresh_token": "garbage"}).status_code == 401
    pair = _token(client, email).json()
    session = session_factory(admin=True)()
    try:
        for token in session.query(RefreshToken).filter(RefreshToken.user_id == uuid.UUID(ws.user_id)):
            token.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    expired = client.post("/api/v1/auth/token/refresh", json={"refresh_token": pair["refresh_token"]})
    assert expired.status_code == 401 and expired.json()["error"]["code"] == "refresh_token_expired"


def test_revoke(client):
    ws, email = _account(client)
    pair = _token(client, email).json()
    assert client.post("/api/v1/auth/token/revoke", json={"refresh_token": pair["refresh_token"]}).status_code == 200
    assert client.post("/api/v1/auth/token/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/users/me", headers=_bearer(pair["access_token"])).status_code == 401
    # Unknown tokens: still 200 (no oracle).
    assert client.post("/api/v1/auth/token/revoke", json={"refresh_token": "aegr_unknown"}).status_code == 200
    assert "TOKEN_REVOKED" in _audit_actions(ws.org_id)


def test_password_reset_flow(client, monkeypatch):
    from aegis_api.lab.identity import credentials
    from aegis_api.lab.identity import email as email_module

    ws, email = _account(client)
    pair = _token(client, email).json()
    issued: list[str] = []
    delivered: list = []
    real_issue = credentials._issue_password_reset_token

    def capture(db, user):
        token = real_issue(db, user)
        issued.append(token)
        return token

    monkeypatch.setattr(credentials, "_issue_password_reset_token", capture)
    monkeypatch.setattr(email_module, "deliver", lambda message, purpose: delivered.append((message, purpose)))

    unknown = client.post("/api/v1/auth/password-reset/request", json={"email": "ghost@example.com"})
    assert unknown.status_code == 202 and not issued and not delivered
    known = client.post("/api/v1/auth/password-reset/request", json={"email": email.upper()})
    assert known.status_code == 202 and unknown.json() == known.json()
    assert len(issued) == 1 and len(delivered) == 1
    message, purpose = delivered[0]
    assert purpose == "password_reset" and message.to == email and issued[0] in message.text

    weak = client.post("/api/v1/auth/password-reset/confirm", json={"token": issued[0], "new_password": "weak"})
    assert weak.status_code == 422
    new_password = "N3w-Secure-Pass!"
    done = client.post("/api/v1/auth/password-reset/confirm", json={"token": issued[0], "new_password": new_password})
    assert done.status_code == 200, done.text
    again = client.post("/api/v1/auth/password-reset/confirm", json={"token": issued[0], "new_password": new_password})
    assert again.status_code == 422

    # Every session and refresh token of the user was revoked.
    assert ws.get("/api/v1/auth/session").status_code == 401
    assert client.post("/api/v1/auth/token/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/users/me", headers=_bearer(pair["access_token"])).status_code == 401
    assert _token(client, email).status_code == 401
    assert _token(client, email, password=new_password).status_code == 200
    login = client.post("/api/v1/auth/login", json={"email": email, "password": new_password})
    assert login.status_code == 200


def test_password_reset_superseded_and_expired_tokens(client, monkeypatch):
    from aegis_api.db.base import utcnow
    from aegis_api.db.session import session_factory
    from aegis_api.lab.identity import credentials
    from aegis_api.lab.identity import email as email_module
    from aegis_api.models import AuthToken

    _, email = _account(client)
    issued: list[str] = []
    real_issue = credentials._issue_password_reset_token
    monkeypatch.setattr(
        credentials, "_issue_password_reset_token", lambda db, u: issued.append(real_issue(db, u)) or issued[-1]
    )
    monkeypatch.setattr(email_module, "deliver", lambda message, purpose: None)
    client.post("/api/v1/auth/password-reset/request", json={"email": email})
    client.post("/api/v1/auth/password-reset/request", json={"email": email})
    first, second = issued
    assert (
        client.post(
            "/api/v1/auth/password-reset/confirm", json={"token": first, "new_password": "An0ther-Pass!!"}
        ).status_code
        == 422
    )
    session = session_factory(admin=True)()
    try:
        for row in session.query(AuthToken).filter(AuthToken.email == email):
            row.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    assert (
        client.post(
            "/api/v1/auth/password-reset/confirm", json={"token": second, "new_password": "An0ther-Pass!!"}
        ).status_code
        == 422
    )


def test_email_verification_flow(client, monkeypatch):
    from aegis_api.db.session import session_factory
    from aegis_api.lab.identity import email as email_module
    from aegis_api.models import User

    ws, _ = _account(client)
    delivered: list = []
    monkeypatch.setattr(email_module, "deliver", lambda message, purpose: delivered.append((message, purpose)))
    assert client.post("/api/v1/auth/email/verify/request").status_code == 401
    r = ws.post("/api/v1/auth/email/verify/request")
    assert r.status_code == 202 and len(delivered) == 1
    message, purpose = delivered[0]
    assert purpose == "email_verify"
    token = message.text.split("token=")[1].split()[0]
    bad = client.post("/api/v1/auth/email/verify/confirm", json={"token": "not-a-token"})
    assert bad.status_code == 422
    ok = client.post("/api/v1/auth/email/verify/confirm", json={"token": token})
    assert ok.status_code == 200, ok.text
    session = session_factory(admin=True)()
    try:
        assert session.get(User, uuid.UUID(ws.user_id)).email_verified is True
    finally:
        session.close()
    assert client.post("/api/v1/auth/email/verify/confirm", json={"token": token}).status_code == 422
    again = ws.post("/api/v1/auth/email/verify/request")
    assert again.status_code == 202 and again.json()["message"] == "Email address is already verified"


def test_email_delivery_not_configured_never_logs_token(client, monkeypatch, capsys):
    from aegis_api.lab.identity import credentials

    _, email = _account(client)
    tokens: list[str] = []
    real_issue = credentials._issue_password_reset_token
    monkeypatch.setattr(
        credentials, "_issue_password_reset_token", lambda db, u: tokens.append(real_issue(db, u)) or tokens[-1]
    )
    assert client.post("/api/v1/auth/password-reset/request", json={"email": email}).status_code == 202
    captured = capsys.readouterr()
    assert tokens and tokens[0] not in captured.out + captured.err


def test_smtp_send_uses_starttls_and_login(monkeypatch):
    from aegis_api.config import get_settings
    from aegis_api.lab.identity import email as email_module

    calls: list[tuple] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            calls.append(("quit",))

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message["To"], message["Subject"]))

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "mailer")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    get_settings.cache_clear()
    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)
    try:
        email_module.send_email(email_module.OutgoingEmail(to="a@example.com", subject="Hi", text="body"))
        with pytest.raises(Exception, match="line breaks"):
            email_module.send_email(email_module.OutgoingEmail(to="a@example.com", subject="x\r\nBcc: b@x", text="b"))
    finally:
        get_settings.cache_clear()
    names = [c[0] for c in calls]
    assert names[:4] == ["connect", "ehlo", "starttls", "ehlo"]
    assert ("login", "mailer") in calls and ("send", "a@example.com", "Hi") in calls


def test_password_reset_requests_are_throttled_per_address(client, monkeypatch):
    from aegis_api.config import get_settings
    from aegis_api.lab.identity import email as email_module
    from aegis_api.ratelimit import reset_limiter

    _, email = _account(client)
    monkeypatch.setattr(email_module, "deliver", lambda message, purpose: None)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_EXPENSIVE_PER_MIN", "2")
    get_settings.cache_clear()
    reset_limiter()
    try:
        for target in (email, f"ghost-{uuid.uuid4().hex[:6]}@example.com"):  # same behaviour: no oracle
            statuses = [
                client.post("/api/v1/auth/password-reset/request", json={"email": target}).status_code for _ in range(3)
            ]
            assert statuses == [202, 202, 429], (target, statuses)
    finally:
        get_settings.cache_clear()
        reset_limiter()


def test_identity_session_is_released_before_the_endpoint_runs(client):
    """The owner-connection session used for authentication must not stay checked out for the request."""
    from starlette.requests import Request

    from aegis_api import deps
    from aegis_api.db.session import session_factory
    from aegis_api.models import User

    ws, email = _account(client)
    access = _token(client, email).json()["access_token"]
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/users/me",
            "headers": [(b"authorization", f"Bearer {access}".encode())],
            "query_string": b"",
            "client": ("127.0.0.1", 50000),
            "state": {},
        }
    )
    db = session_factory(admin=True)()
    try:
        principal = deps.get_current_principal(request, db)
        assert principal.auth_method == "jwt" and str(principal.user_id) == ws.user_id
        assert not db.in_transaction()  # committed and closed: no admin connection held
        assert db.get(User, principal.user_id) is not None  # still usable by endpoints that share it
    finally:
        db.close()
