"""Shared fixtures for AI Scientist Lab tests.

``lab`` gives a signed-up organization (owner session) with one workspace and one project created
directly through the owner connection, plus helpers to obtain tenant-scoped sessions and ``Actor``s.
It deliberately does not depend on any lab HTTP endpoint, so every context can be tested in isolation.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from tests.conftest import Workspace, signup


@dataclass
class LabContext:
    ws: Workspace
    org_id: uuid.UUID
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID

    def actor(self, *, role: str = "owner", kind: str = "user"):  # type: ignore[no-untyped-def]
        from aegis_api.lab.core.actor import Actor
        from aegis_api.security.rbac import permissions_for_role

        return Actor(
            kind=kind,  # type: ignore[arg-type]
            organization_id=self.org_id,
            permissions=permissions_for_role(role),
            label=f"test:{role}",
            user_id=self.user_id if kind == "user" else None,
            role=role,
            auth_method="session" if kind == "user" else None,
        )

    @contextmanager
    def db(self) -> Iterator[Session]:
        """RLS-scoped session for this organization (commits on success)."""
        from aegis_api.db.session import session_scope

        with session_scope(self.org_id, self.user_id) as session:
            yield session

    # HTTP helpers (owner cookie session)
    def get(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.ws.get(path, **kw)

    def post(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.ws.post(path, **kw)

    def patch(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.ws.patch(path, **kw)

    def delete(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.ws.delete(path, **kw)


def make_lab(client, *, org: str = "Lab Org") -> LabContext:  # type: ignore[no-untyped-def]
    from aegis_api.db.session import session_factory
    from aegis_api.lab.models import Project, Workspace as LabWorkspace

    ws = signup(client, org=org)
    org_id = uuid.UUID(ws.org_id)
    user_id = uuid.UUID(ws.user_id)
    session = session_factory(admin=True)()
    try:
        workspace = LabWorkspace(organization_id=org_id, name="Research", slug=f"research-{uuid.uuid4().hex[:6]}")
        session.add(workspace)
        session.flush()
        project = Project(
            organization_id=org_id,
            workspace_id=workspace.id,
            name="Benchmark Efficiency",
            slug=f"bench-{uuid.uuid4().hex[:6]}",
            created_by_id=user_id,
        )
        session.add(project)
        session.commit()
        return LabContext(ws=ws, org_id=org_id, user_id=user_id, workspace_id=workspace.id, project_id=project.id)
    finally:
        session.close()


@pytest.fixture
def lab(client) -> LabContext:  # type: ignore[no-untyped-def]
    return make_lab(client)


@pytest.fixture
def other_lab(client) -> LabContext:  # type: ignore[no-untyped-def]
    """A second, independent tenant for cross-tenant isolation tests."""
    return make_lab(client, org="Other Lab Org")
