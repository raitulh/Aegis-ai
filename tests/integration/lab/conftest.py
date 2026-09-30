"""Fixtures for Scientist Lab integration tests (real PostgreSQL; scripted LLM; optional Docker/Temporal)."""

from __future__ import annotations

import socket
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import DB_AVAILABLE, Workspace, signup
from tests.support.lab_fakes import ScriptedProvider, registry_with


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env(timeout=3).ping()
        return True
    except Exception:
        return False


def _temporal_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7233), timeout=1):
            return True
    except OSError:
        return False


DOCKER = _docker_available()
TEMPORAL = _temporal_available()
requires_docker = pytest.mark.skipif(not DOCKER, reason="Docker engine is not available")
requires_temporal = pytest.mark.skipif(not TEMPORAL, reason="Temporal dev server is not reachable on 127.0.0.1:7233")
pytestmark = pytest.mark.skipif(not DB_AVAILABLE, reason="PostgreSQL test database is not available")


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Isolated object storage, a scripted model provider and synchronous (test-driven) workflow execution."""
    from aegis_api.config import get_settings
    from aegis_api.infrastructure.llm.router import set_registry_override
    from aegis_api.infrastructure.storage import reset_storage
    from aegis_api.services.lab import model_gateway

    monkeypatch.setenv("OBJECT_STORAGE_LOCAL_DIR", str(tmp_path / "objects"))
    monkeypatch.setenv("OBJECT_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WORKFLOW_ENGINE", "inline")
    get_settings.cache_clear()
    reset_storage()
    provider = ScriptedProvider()
    set_registry_override(registry_with(provider))
    model_gateway._GATEWAY = None
    dispatched: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(
        "aegis_api.jobs.dispatcher.dispatch_task", lambda dotted, *args: dispatched.append((dotted, args))
    )
    yield SimpleNamespace(provider=provider, dispatched=dispatched)
    set_registry_override(None)
    get_settings.cache_clear()
    reset_storage()


def principal_for(ws: Workspace, role: str = "owner") -> Any:
    from aegis_api.security.context import Principal, build_permissions

    return Principal(
        user_id=uuid.UUID(ws.user_id),
        organization_id=uuid.UUID(ws.org_id),
        role=role,
        permissions=build_permissions(role, None),
        auth_method="session",
    )


def drive_until_idle(org_id: str | uuid.UUID, *, max_rounds: int = 60) -> None:
    """Drive every runnable inline workflow of one organization until nothing is runnable."""
    from aegis_api.workflows.engine import drive, poll_due

    org = uuid.UUID(str(org_id))
    for _ in range(max_rounds):
        due = [(r, o) for r, o in poll_due(limit=200) if o == org]
        if not due:
            return
        for run_id, org_uuid in due:
            drive(run_id, org_uuid)


class Member(Workspace):
    """A user acting inside another organization (org selected with the ``X-Aegis-Org`` header)."""

    def __init__(self, base: Workspace, org_id: str) -> None:
        super().__init__(base.client, base.data, base.cookies)
        self.org_id = org_id

    def request(self, method: str, path: str, **kwargs: Any):
        headers = {"X-Aegis-Org": self.org_id, **(kwargs.pop("headers", None) or {})}
        return self.client.request(method, path, cookies=self.cookies, headers=headers, **kwargs)


def invite(client, owner: Workspace, role: str) -> Member:
    """Create a second user and make them an active ``role`` member of the owner's organization."""
    from aegis_api.db.session import admin_session_scope
    from aegis_api.models import Membership

    other = signup(client, org=f"tmp-{uuid.uuid4().hex[:6]}")
    with admin_session_scope() as db:
        db.add(
            Membership(
                organization_id=uuid.UUID(owner.org_id), user_id=uuid.UUID(other.user_id), role=role, status="active"
            )
        )
    return Member(other, owner.org_id)
