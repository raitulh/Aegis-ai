"""Core lab contracts: idempotency keys, project access control, keyset pagination, feature flags, actors."""

from __future__ import annotations

import threading
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from tests.conftest import requires_db, signup

pytestmark = [requires_db, pytest.mark.db]


class _Body(BaseModel):
    title: str


def _idempotent_app() -> tuple[FastAPI, list[int]]:
    """A test-only app exercising the idempotency dependency against a real lab table."""
    from aegis_api.app import create_app
    from aegis_api.deps import get_current_principal, get_db
    from aegis_api.lab.core.idempotency import Idempotency, idempotency
    from aegis_api.lab.models import Team
    from aegis_api.security.context import Principal

    app = create_app()
    calls: list[int] = []

    @app.post("/test/idempotent-teams", status_code=201)
    def create_team(
        body: _Body,
        idem: Idempotency = Depends(idempotency),
        principal: Principal = Depends(get_current_principal),
        db: Session = Depends(get_db),
    ):
        if (replay := idem.replay()) is not None:
            return replay
        calls.append(1)
        team = Team(organization_id=principal.organization_id, name=f"{body.title}-{uuid.uuid4().hex[:6]}")
        db.add(team)
        db.flush()
        return idem.remember(201, {"id": str(team.id), "name": team.name})

    return app, calls


def test_idempotency_replays_and_rejects_reuse():
    app, calls = _idempotent_app()
    with TestClient(app) as client:
        ws = signup(client, org="Idem Org")
        headers = {"Idempotency-Key": f"key-{uuid.uuid4().hex}"}
        r1 = ws.post("/test/idempotent-teams", json={"title": "a"}, headers=headers)
        r2 = ws.post("/test/idempotent-teams", json={"title": "a"}, headers=headers)
        assert r1.status_code == 201 and r2.status_code == 201
        assert r1.json() == r2.json()
        assert r2.headers.get("Idempotent-Replayed") == "true"
        assert len(calls) == 1
        r3 = ws.post("/test/idempotent-teams", json={"title": "different"}, headers=headers)
        assert r3.status_code == 422
        assert r3.json()["error"]["code"] == "idempotency_key_reused"
        bad = ws.post("/test/idempotent-teams", json={"title": "a"}, headers={"Idempotency-Key": "short"})
        assert bad.status_code == 422
        # Without a key, every request does the work.
        ws.post("/test/idempotent-teams", json={"title": "a"})
        ws.post("/test/idempotent-teams", json={"title": "a"})
        assert len(calls) == 3


def test_idempotency_concurrent_duplicates_create_one_resource():
    app, calls = _idempotent_app()
    with TestClient(app) as client:
        ws = signup(client, org="Idem Concurrency")
        headers = {"Idempotency-Key": f"key-{uuid.uuid4().hex}"}
        results: list[dict] = []

        def fire() -> None:
            results.append(ws.post("/test/idempotent-teams", json={"title": "c"}, headers=headers).json())

        threads = [threading.Thread(target=fire) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1
        assert len({r["id"] for r in results}) == 1


def test_idempotency_keys_are_scoped_per_tenant():
    app, calls = _idempotent_app()
    with TestClient(app) as client:
        a = signup(client, org="Idem A")
        b = signup(client, org="Idem B")
        headers = {"Idempotency-Key": "shared-key-123456"}
        ra = a.post("/test/idempotent-teams", json={"title": "x"}, headers=headers).json()
        rb = b.post("/test/idempotent-teams", json={"title": "x"}, headers=headers).json()
        assert ra["id"] != rb["id"]
        assert len(calls) == 2


def test_restricted_project_access_and_project_role_grants(lab):
    from aegis_api.db.session import session_factory
    from aegis_api.errors import Forbidden, NotFound
    from aegis_api.lab.core.access import load_project, visible_project_ids
    from aegis_api.lab.models import Project, ProjectMember

    admin = session_factory(admin=True)()
    try:
        restricted = Project(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            name="Secret",
            slug=f"secret-{uuid.uuid4().hex[:6]}",
            visibility="restricted",
        )
        admin.add(restricted)
        admin.commit()
        restricted_id = restricted.id
    finally:
        admin.close()

    outsider = lab.actor(role="researcher")
    viewer = lab.actor(role="viewer")
    owner = lab.actor(role="owner")
    with lab.db() as db:
        load_project(db, owner, restricted_id, "mission:create")
        with pytest.raises(NotFound):
            load_project(db, outsider, restricted_id)
        visible = visible_project_ids(db, outsider)
        assert visible is not None and restricted_id not in visible and lab.project_id in visible
        with pytest.raises(Forbidden):
            load_project(db, viewer, lab.project_id, "mission:create")
        db.add(
            ProjectMember(
                organization_id=lab.org_id, project_id=lab.project_id, user_id=lab.user_id, role="research_lead"
            )
        )
        db.flush()
        # The viewer's org role is read-only, but the project role grants writes inside this project only.
        load_project(db, viewer, lab.project_id, "mission:create")
        with pytest.raises(NotFound):
            load_project(db, viewer, restricted_id)


def test_get_owned_hides_foreign_rows(lab, other_lab):
    from aegis_api.errors import NotFound
    from aegis_api.lab.core.access import get_owned
    from aegis_api.lab.models import Project

    with lab.db() as db:
        assert get_owned(db, Project, lab.project_id, lab.actor()).id == lab.project_id
        with pytest.raises(NotFound):
            get_owned(db, Project, other_lab.project_id, lab.actor())
        with pytest.raises(NotFound):
            get_owned(db, Project, "not-a-uuid", lab.actor())


def test_keyset_pagination_is_stable(lab):
    from sqlalchemy import select

    from aegis_api.lab.core.events import EventType, emit
    from aegis_api.lab.core.pagination import CursorParams, paginate_by_id
    from aegis_api.lab.models import LabEvent

    with lab.db() as db:
        for i in range(7):
            emit(db, organization_id=lab.org_id, type=EventType.PHASE_CHANGED, payload={"i": i})
    seen: list[int] = []
    cursor = None
    with lab.db() as db:
        stmt = select(LabEvent).where(LabEvent.organization_id == lab.org_id)
        while True:
            page = paginate_by_id(
                db, stmt, CursorParams(cursor=cursor, limit=3), id_col=LabEvent.id, mapper=lambda e: e.id
            )
            seen.extend(page.items)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
    assert seen == sorted(seen) and len(seen) == len(set(seen)) >= 7


def test_feature_flags_org_override(lab, other_lab):
    from aegis_api.lab.core.features import feature_enabled
    from aegis_api.models import FeatureFlag

    with lab.db() as db:
        assert feature_enabled(db, lab.org_id, "evolution") is True
        db.add(FeatureFlag(organization_id=lab.org_id, key="evolution", enabled=False))
    with lab.db() as db:
        assert feature_enabled(db, lab.org_id, "evolution") is False
    with other_lab.db() as db:
        assert feature_enabled(db, other_lab.org_id, "evolution") is True


def test_agent_actor_permissions_are_an_intersection(lab):
    agent = lab.actor(role="researcher").for_agent(
        agent_run_id=uuid.uuid4(),
        agent_role="HypothesisAgent",
        agent_permissions=frozenset({"hypothesis:create", "strategy:promote", "memory:read"}),
        autonomy_level="L2_AUTOMATED_EXPERIMENT_DESIGN",
    )
    assert agent.kind == "agent"
    assert "hypothesis:create" in agent.permissions
    assert "strategy:promote" not in agent.permissions  # the researcher never had it
    assert not agent.is_human


def test_org_settings_defaults_created_once(lab):
    from aegis_api.lab.core.org_settings import allows_external_llm, get_org_settings, quota

    with lab.db() as db:
        s1 = get_org_settings(db, lab.org_id)
        s2 = get_org_settings(db, lab.org_id)
        assert s1.id == s2.id
        assert quota(db, lab.org_id, "max_concurrent_experiments") == 4
        assert allows_external_llm(db, lab.org_id) is False
