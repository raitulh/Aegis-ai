"""Foundation checks: lab schema, RLS on lab tables, immutability triggers, evidence chain, idempotency."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.conftest import requires_db

pytestmark = [requires_db, pytest.mark.db]


def test_lab_tables_are_rls_isolated(lab, other_lab):
    from aegis_api.lab.models import Project

    with lab.db() as db:
        ids = {p.id for p in db.query(Project).all()}
    assert lab.project_id in ids
    assert other_lab.project_id not in ids


def test_version_rows_are_immutable(lab):
    from sqlalchemy.exc import DBAPIError

    from aegis_api.lab.models import Mission, MissionVersion

    with lab.db() as db:
        mission = Mission(
            organization_id=lab.org_id,
            workspace_id=lab.workspace_id,
            project_id=lab.project_id,
            title="t",
            objective="o",
        )
        db.add(mission)
        db.flush()
        db.add(MissionVersion(organization_id=lab.org_id, mission_id=mission.id, version=1, content_hash="x" * 64))
    with pytest.raises(DBAPIError), lab.db() as db:
        db.execute(text("update mission_versions set change_summary = 'tampered'"))


def test_composite_project_fk_blocks_cross_tenant_attachment(lab, other_lab):
    from sqlalchemy.exc import IntegrityError

    from aegis_api.db.session import session_factory
    from aegis_api.lab.models import Mission

    session = session_factory(admin=True)()
    try:
        session.add(
            Mission(
                organization_id=lab.org_id,
                workspace_id=other_lab.workspace_id,
                project_id=other_lab.project_id,
                title="escape",
                objective="attach to another tenant's project",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
    finally:
        session.rollback()
        session.close()


def test_lab_evidence_chain_is_valid(lab):
    from aegis_api.lab.core.evidence import append_evidence, verify_lab_chain

    with lab.db() as db:
        for i in range(3):
            append_evidence(db, organization_id=lab.org_id, kind="test", title=f"e{i}", content={"i": i})
    with lab.db() as db:
        result = verify_lab_chain(db, lab.org_id)
    assert result["valid"] and result["records"] >= 3


def test_events_outbox_and_after_commit_notify(lab):
    from aegis_api.lab.core.events import EventType, emit
    from aegis_api.lab.models import LabEvent

    fired: list[int] = []
    with lab.db() as db:
        ev = emit(db, organization_id=lab.org_id, type=EventType.MISSION_CREATED, payload={"x": 1})
        db.info.setdefault("after_commit_callbacks", []).append(lambda: fired.append(1))
        event_id = ev.id
    assert fired == [1]
    with lab.db() as db:
        assert db.get(LabEvent, event_id) is not None


def test_rollback_discards_after_commit_callbacks(lab):
    fired: list[int] = []
    with pytest.raises(RuntimeError), lab.db() as db:
        db.info.setdefault("after_commit_callbacks", []).append(lambda: fired.append(1))
        raise RuntimeError("boom")
    assert fired == []
