"""Identity: enterprise SSO (OIDC authorization code + PKCE) end-to-end against an in-process fake IdP."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]

ISSUER = "https://idp.acme.test"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
CLIENT_ID = "aegis-client"
CLIENT_SECRET = "s3cret-client-value"


class FakeIdP:
    """Discovery + JWKS + token endpoint. Verifies client auth, redirect_uri and the PKCE verifier."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "idp-key-1"
        self.codes: dict[str, dict] = {}
        self.token_requests: list[dict] = []
        self.sign_with = self.key

    def jwks(self) -> dict:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def register(self, authorization_url: str, code: str, **claims) -> dict:
        query = parse_qs(urlsplit(authorization_url).query)
        entry = {
            "challenge": query["code_challenge"][0],
            "nonce": query["nonce"][0],
            "redirect_uri": query["redirect_uri"][0],
            "claims": claims,
        }
        self.codes[code] = entry
        return entry

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and url == DISCOVERY:
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                },
            )
        if request.method == "GET" and url == f"{ISSUER}/jwks":
            return httpx.Response(200, json=self.jwks())
        if request.method == "POST" and url == f"{ISSUER}/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            expected_auth = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if request.headers.get("authorization") != expected_auth:
                return httpx.Response(401, json={"error": "invalid_client"})
            entry = self.codes.pop(form.get("code", ""), None)
            if entry is None or form.get("grant_type") != "authorization_code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            if challenge != entry["challenge"] or form.get("redirect_uri") != entry["redirect_uri"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            claims = {
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "sub": "idp-user-1",
                "email_verified": True,
                "nonce": entry["nonce"],
                "iat": now,
                "exp": now + 300,
                **entry["claims"],
            }
            id_token = jwt.encode(claims, self.sign_with, algorithm="RS256", headers={"kid": self.kid})
            return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer", "id_token": id_token})
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def idp(monkeypatch):
    from aegis_api.security import oidc, ssrf

    fake = FakeIdP()
    oidc.clear_caches()
    monkeypatch.setattr(
        oidc, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handler), follow_redirects=False)
    )
    # The fake IdP host does not resolve in DNS; keep every other SSRF check (schemes, IP literals, metadata).
    monkeypatch.setattr(
        oidc, "validate_outbound_url", lambda url, **kw: ssrf.validate_outbound_url(url, **{**kw, "resolve": False})
    )
    yield fake
    oidc.clear_caches()


def _enable_sso(lab) -> None:
    r = lab.ws.request("PUT", "/api/v1/organizations/current/features/enterprise_sso", json={"enabled": True})
    assert r.status_code == 200, r.text


def _provider(lab, **overrides) -> dict:
    payload = {
        "name": f"Acme IdP {uuid.uuid4().hex[:6]}",
        "discovery_url": DISCOVERY,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "email_domains": ["acme.test"],
        "jit_provisioning": True,
        "default_role": "researcher",
        "enabled": True,
    }
    payload.update(overrides)
    r = lab.post("/api/v1/sso/providers", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _login(client, idp: FakeIdP, provider_id: str, code: str, **claims):
    start = client.get(f"/api/v1/auth/sso/{provider_id}/start")
    assert start.status_code == 200, start.text
    url = start.json()["authorization_url"]
    idp.register(url, code, **claims)
    state = parse_qs(urlsplit(url).query)["state"][0]
    return client.get("/api/v1/auth/sso/callback", params={"code": code, "state": state}), url, state


def test_oidc_login_end_to_end_with_jit(client, lab, idp):
    _enable_sso(lab)
    provider = _provider(lab)
    assert provider["has_client_secret"] is True and "client_secret" not in provider
    assert provider["redirect_uri"].endswith("/api/v1/auth/sso/callback")
    client.cookies.clear()

    email = f"alice-{uuid.uuid4().hex[:6]}@acme.test"
    response, url, _ = _login(client, idp, provider["id"], "code-1", email=email, name="Alice", sub=f"sub-{email}")
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["user"]["email"] == email and session["organization"]["id"] == str(lab.org_id)
    assert session["role"] == "researcher"
    assert response.cookies.get("aegis_session")

    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"] and query["response_type"] == ["code"]
    assert query["client_id"] == [CLIENT_ID] and "openid" in query["scope"][0].split()
    assert idp.token_requests[-1]["code_verifier"]  # PKCE verifier was sent (and checked by the IdP)

    me = client.get("/api/v1/users/me")
    assert me.status_code == 200 and me.json()["organization_id"] == str(lab.org_id)
    assert me.json()["user"]["auth_provider"] == "sso" and me.json()["user"]["email_verified"] is True

    # Second login maps to the same account via the linked subject.
    client.cookies.clear()
    again, _, _ = _login(client, idp, provider["id"], "code-2", email=email, sub=f"sub-{email}")
    assert again.status_code == 200 and again.json()["user"]["id"] == session["user"]["id"]

    from aegis_api.models import AuditLog

    with lab.db() as db:
        logins = db.query(AuditLog).filter(AuditLog.action == "LOGIN", AuditLog.actor_label == email).count()
    assert logins == 2


def test_state_is_single_use_and_bound_to_browser(client, lab, idp):
    _enable_sso(lab)
    provider = _provider(lab)
    client.cookies.clear()
    start = client.get(f"/api/v1/auth/sso/{provider['id']}/start").json()
    url = start["authorization_url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    idp.register(url, "code-x", email=f"bob-{uuid.uuid4().hex[:6]}@acme.test")

    # A different browser (no state cookie) cannot complete the flow (login CSRF protection).
    client.cookies.clear()
    stolen = client.get("/api/v1/auth/sso/callback", params={"code": "code-x", "state": state})
    assert stolen.status_code == 401 and stolen.json()["error"]["code"] == "sso_state_mismatch"

    client.cookies.set("aegis_sso_state", state)
    ok = client.get("/api/v1/auth/sso/callback", params={"code": "code-x", "state": state})
    assert ok.status_code == 200, ok.text
    client.cookies.set("aegis_sso_state", state)
    replay = client.get("/api/v1/auth/sso/callback", params={"code": "code-x", "state": state})
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "sso_state_invalid"


def test_id_token_validation_failures(client, lab, idp):
    _enable_sso(lab)
    provider = _provider(lab)
    client.cookies.clear()
    email = f"carol-{uuid.uuid4().hex[:6]}@acme.test"

    wrong_nonce, _, _ = _login(client, idp, provider["id"], "c1", email=email, nonce="attacker-nonce")
    assert wrong_nonce.status_code == 401 and wrong_nonce.json()["error"]["code"] == "sso_invalid_token"
    wrong_aud, _, _ = _login(client, idp, provider["id"], "c2", email=email, aud="someone-else")
    assert wrong_aud.status_code == 401 and wrong_aud.json()["error"]["code"] == "sso_invalid_token"
    wrong_iss, _, _ = _login(client, idp, provider["id"], "c3", email=email, iss="https://evil.test")
    assert wrong_iss.status_code == 401 and wrong_iss.json()["error"]["code"] == "sso_invalid_token"
    expired, _, _ = _login(client, idp, provider["id"], "c4", email=email, exp=int(time.time()) - 3600)
    assert expired.status_code == 401 and expired.json()["error"]["code"] == "sso_invalid_token"

    idp.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # forged signature
    forged, _, _ = _login(client, idp, provider["id"], "c5", email=email)
    assert forged.status_code == 401 and forged.json()["error"]["code"] == "sso_invalid_token"
    idp.sign_with = idp.key

    unverified, _, _ = _login(client, idp, provider["id"], "c6", email=email, email_verified=False)
    assert unverified.status_code == 403 and unverified.json()["error"]["code"] == "sso_email_unverified"
    unverified_str, _, _ = _login(client, idp, provider["id"], "c6b", email=email, email_verified="false")
    assert unverified_str.status_code == 403 and unverified_str.json()["error"]["code"] == "sso_email_unverified"
    other_domain, _, _ = _login(client, idp, provider["id"], "c7", email="mallory@evil.test")
    assert other_domain.status_code == 403 and other_domain.json()["error"]["code"] == "sso_domain_not_allowed"

    # The IdP rejects a code exchange with the wrong PKCE verifier; we surface a clean 401.
    start = client.get(f"/api/v1/auth/sso/{provider['id']}/start").json()["authorization_url"]
    entry = idp.register(start, "c8", email=email)
    entry["challenge"] = "tampered-challenge"
    state = parse_qs(urlsplit(start).query)["state"][0]
    bad_pkce = client.get("/api/v1/auth/sso/callback", params={"code": "c8", "state": state})
    assert bad_pkce.status_code == 401 and bad_pkce.json()["error"]["code"] == "sso_exchange_failed"


def test_account_linking_rules(client, lab, other_lab, idp):
    _enable_sso(lab)
    no_jit = _provider(lab, jit_provisioning=False, email_domains=["acme.test", "example.com"])
    client.cookies.clear()
    unknown, _, _ = _login(client, idp, no_jit["id"], "l1", email=f"new-{uuid.uuid4().hex[:6]}@acme.test")
    assert unknown.status_code == 403 and unknown.json()["error"]["code"] == "sso_user_not_provisioned"

    # An IdP of one organization can never take over an account that has access to another organization.
    victim_email = other_lab.ws.data["user"]["email"]
    jit = _provider(lab, email_domains=["example.com"])
    takeover, _, _ = _login(client, idp, jit["id"], "l2", email=victim_email, sub="attacker-controlled")
    assert takeover.status_code == 403 and takeover.json()["error"]["code"] == "sso_account_link_forbidden"


def test_provider_management_and_protocol_support(client, lab, other_lab, idp):
    disabled_feature = lab.post(
        "/api/v1/sso/providers",
        json={"name": "x", "discovery_url": DISCOVERY, "client_id": "c", "email_domains": ["acme.test"]},
    )
    assert disabled_feature.status_code == 403 and disabled_feature.json()["error"]["code"] == "feature_disabled"
    _enable_sso(lab)

    invalid = [
        {
            "name": "a",
            "discovery_url": "http://169.254.169.254/latest",
            "client_id": "c",
            "email_domains": ["acme.test"],
        },
        {"name": "b", "discovery_url": DISCOVERY, "email_domains": ["acme.test"]},  # client_id missing
        {"name": "c", "discovery_url": DISCOVERY, "client_id": "c", "email_domains": ["*.acme.test"]},
        {"name": "d", "discovery_url": DISCOVERY, "client_id": "c", "email_domains": []},
        {
            "name": "e",
            "discovery_url": DISCOVERY,
            "client_id": "c",
            "email_domains": ["acme.test"],
            "scopes": ["email"],
        },
        {
            "name": "f",
            "discovery_url": DISCOVERY,
            "client_id": "c",
            "email_domains": ["acme.test"],
            "default_role": "owner",
        },
    ]
    for payload in invalid:
        assert lab.post("/api/v1/sso/providers", json=payload).status_code == 422, payload

    provider = _provider(lab, enabled=False)
    listed = lab.get("/api/v1/sso/providers").json()
    assert [p["id"] for p in listed] == [provider["id"]]
    assert all("client_secret" not in p for p in listed)
    client.cookies.clear()
    assert client.get(f"/api/v1/auth/sso/{provider['id']}/start").status_code == 404  # not enabled
    enabled = lab.patch(f"/api/v1/sso/providers/{provider['id']}", json={"enabled": True, "client_secret": "rotated"})
    assert enabled.status_code == 200 and enabled.json()["enabled"] is True
    assert client.get(f"/api/v1/auth/sso/{provider['id']}/start").status_code == 200

    assert other_lab.get(f"/api/v1/sso/providers/{provider['id']}").status_code == 404
    assert client.get(f"/api/v1/auth/sso/{uuid.uuid4()}/start").status_code == 404

    saml = lab.post(
        "/api/v1/sso/providers",
        json={"kind": "saml", "name": "Legacy SAML", "email_domains": ["acme.test"], "enabled": True},
    )
    assert saml.status_code == 201, saml.text
    not_supported = client.get(f"/api/v1/auth/sso/{saml.json()['id']}/start")
    assert not_supported.status_code == 501 and not_supported.json()["error"]["code"] == "sso_protocol_not_supported"

    assert lab.delete(f"/api/v1/sso/providers/{provider['id']}").status_code == 200
    assert lab.get(f"/api/v1/sso/providers/{provider['id']}").status_code == 404


def test_sso_enforcement_blocks_password_tokens(client, lab, idp):
    _enable_sso(lab)
    _provider(lab)
    enforced = lab.patch("/api/v1/organizations/current/settings", json={"sso_enforced": True})
    assert enforced.status_code == 200 and enforced.json()["sso_enforced"] is True
    email = lab.ws.data["user"]["email"]
    r = client.post("/api/v1/auth/token", json={"email": email, "password": "Str0ng-Pass!23"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "sso_required"
    # The cookie-session login applies the same rule.
    client.cookies.clear()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "Str0ng-Pass!23"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "sso_required"
