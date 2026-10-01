"""Test harness.

Integration tests run against a real PostgreSQL database (``TEST_DATABASE_URL``, default
``databattles_test``). The schema is created by running the Alembic migrations once per session,
and every test starts from empty tables.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from typing import Any

_STORAGE = tempfile.mkdtemp(prefix="dbtest-storage-")
os.environ.setdefault("TEST_DATABASE_URL", "postgresql+psycopg://databattles:databattles@localhost:5432/databattles_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["ENV"] = "test"
os.environ["STORAGE_LOCAL_ROOT"] = _STORAGE
os.environ["EMAIL_BACKEND"] = "console"
os.environ["EMBEDDED_WORKER"] = "false"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.deps import CSRF_COOKIE  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.jobs.queue import drain  # noqa: E402
from app.jobs.registry import load_handlers  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    from alembic.config import Config

    from alembic import command

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(cfg, "head")
    load_handlers()
    yield
    shutil.rmtree(_STORAGE, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    with engine.begin() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"))]
        conn.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_no_truncate"))
        conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
        conn.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_no_truncate"))
    limiter.reset()
    from app.core.config import settings

    settings.RATE_LIMIT_ENABLED = True
    from app.seed.demo import ensure_platform_config

    with SessionLocal() as db:
        ensure_platform_config(db)
    yield


class ApiClient(TestClient):
    """TestClient that behaves like the web app: echoes the CSRF cookie in the X-CSRF-Token header."""

    def request(self, method: str, url: str, **kwargs: Any):  # type: ignore[override]
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            token = self.cookies.get(CSRF_COOKIE)
            if token is None:
                super().request("GET", "/api/v1/auth/csrf")
                token = self.cookies.get(CSRF_COOKIE)
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-CSRF-Token", token or "")
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture
def app():  # noqa: ANN201
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app) -> Iterator[ApiClient]:  # noqa: ANN001
    with ApiClient(app, base_url="http://localhost:8000") as c:
        yield c


@pytest.fixture
def make_client(app):  # noqa: ANN001, ANN201
    clients: list[ApiClient] = []

    def factory() -> ApiClient:
        c = ApiClient(app, base_url="http://localhost:8000")
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


PASSWORD = "Correct-Horse-42"


def last_email_token(to: str, path_fragment: str) -> str:
    from sqlalchemy import select

    from app.models.community import EmailOutbox

    with SessionLocal() as db:
        body = db.scalar(select(EmailOutbox.text_body).where(EmailOutbox.to_email == to).order_by(EmailOutbox.created_at.desc()).limit(1))
    assert body, f"no email for {to}"
    m = re.search(re.escape(path_fragment) + r"\?token=([A-Za-z0-9_\-\.]+)", body)
    assert m, body
    return m.group(1)


def signup_and_login(client: ApiClient, email: str, name: str = "Test User", handle: str | None = None) -> dict[str, Any]:
    r = client.post("/api/v1/auth/signup", json={"email": email, "password": PASSWORD, "display_name": name, "handle": handle})
    assert r.status_code == 202, r.text
    token = last_email_token(email, "/verify-email")
    r = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def grant_role(email: str, role: str) -> None:
    from sqlalchemy import select

    from app.models.user import PlatformRoleAssignment, User

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        db.add(PlatformRoleAssignment(user_id=user.id, role=role))
        db.commit()


def run_jobs() -> int:
    return drain(SessionLocal, worker_id="test", include_delayed=True)


@pytest.fixture
def admin_client(make_client) -> ApiClient:  # noqa: ANN001
    c = make_client()
    signup_and_login(c, "admin@example.com", "Admin", "admin")
    grant_role("admin@example.com", "platform_admin")
    return c
