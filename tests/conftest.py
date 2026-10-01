"""Test fixtures. Requires a migrated PostgreSQL database.

Set ``TEST_DATABASE_URL`` (app role) and ``TEST_DATABASE_ADMIN_URL`` (owner role), or rely on the
defaults below. Every test runs against real Postgres so Row Level Security and constraints are exercised.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_DB_APP = os.environ.get("TEST_DATABASE_URL", "postgresql://aegis:aegis@127.0.0.1:5432/aegis_test")
TEST_DB_ADMIN = os.environ.get("TEST_DATABASE_ADMIN_URL", "postgresql://postgres:postgres@127.0.0.1:5432/aegis_test")

os.environ.setdefault("ENVIRONMENT", "test")
os.environ["DATABASE_URL"] = TEST_DB_APP
os.environ["DATABASE_ADMIN_URL"] = TEST_DB_ADMIN
os.environ.setdefault("JOB_BACKEND", "inline")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("WEB_BASE_URL", "http://localhost:3000")
TRUSTED_ORIGIN = "http://localhost:3000"


def _db_available() -> bool:
    import psycopg

    try:
        with psycopg.connect(TEST_DB_ADMIN.replace("+psycopg", ""), connect_timeout=2) as conn:
            conn.execute("select 1")
        return True
    except Exception:
        return False


DB_AVAILABLE = _db_available()
requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="PostgreSQL test database is not available")


@pytest.fixture(scope="session", autouse=True)
def _configure() -> None:
    from aegis_api.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from aegis_api.app import create_app

    # Browsers always send Origin on cross-origin and same-origin unsafe requests; mirror the web console.
    with TestClient(create_app(), headers={"origin": TRUSTED_ORIGIN}) as c:
        yield c


@pytest.fixture
def admin_session():
    from aegis_api.db.session import session_factory

    session = session_factory(admin=True)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


class Workspace:
    """A signed-up workspace with an authenticated TestClient session."""

    def __init__(self, client, session_out: dict, cookies) -> None:
        self.client = client
        self.data = session_out
        self.org_id = session_out["organization"]["id"]
        self.user_id = session_out["user"]["id"]
        self.role = session_out["role"]
        self.cookies = cookies

    def request(self, method: str, path: str, **kwargs):
        return self.client.request(method, path, cookies=self.cookies, **kwargs)

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw):
        return self.request("PATCH", path, **kw)

    def put(self, path: str, **kw):
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw):
        return self.request("DELETE", path, **kw)


def signup(client, *, email: str | None = None, org: str = "Test Org") -> Workspace:
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    r = client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "Str0ng-Pass!23", "full_name": "Test User", "organization_name": org},
    )
    assert r.status_code == 200, r.text
    return Workspace(client, r.json(), dict(r.cookies))


@pytest.fixture
def workspace(client) -> Workspace:
    return signup(client)


@pytest.fixture
def demo_provider_and_system(workspace: Workspace):
    """A workspace with a demo provider and a Hiring-Agent simulated system."""
    pid = workspace.post(
        "/api/v1/providers", json={"kind": "demo", "name": "Demo", "settings": {"profile": "hiring_agent"}}
    ).json()["id"]
    system = workspace.post(
        "/api/v1/systems",
        json={
            "name": "Hiring-Agent",
            "system_type": "agent",
            "environment": "production",
            "risk_tier": "high",
            "provider_id": pid,
            "model_name": "hiring-agent-sim",
            "config": {
                "demo_profile": "hiring_agent",
                "guardrails": {},
                "tools": {
                    "send_rejection_email": {"requires_human_approval": True},
                    "update_candidate_status": {"requires_human_approval": True},
                },
            },
        },
    ).json()
    return workspace, pid, system["id"]
