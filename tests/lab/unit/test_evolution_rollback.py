"""Rollback planning against the strategy state machine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from engines.lab.evolution.rollback import RollbackError, RollbackManager
from engines.lab.states import can_transition

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def history() -> list[dict[str, Any]]:
    return [
        {"version_id": "v1", "status": "RETIRED", "promoted_at": T0},
        {"version_id": "v2", "status": "RETIRED", "promoted_at": T0 + timedelta(days=10)},
        {"version_id": "v3", "status": "PROMOTED", "promoted_at": T0 + timedelta(days=20)},
        {"version_id": "v4", "status": "SURVIVING", "promoted_at": None},
    ]


def test_plan_rolls_back_to_the_most_recent_previous_promotion() -> None:
    plan = RollbackManager().plan(history(), reason="regression on literature_bench")
    assert plan.current_version_id == "v3" and plan.target_version_id == "v2"
    assert [c.as_tuple() for c in plan.changes] == [("v3", "PROMOTED", "ROLLED_BACK"), ("v2", "RETIRED", "PROMOTED")]
    assert all(can_transition("strategy", c.from_status, c.to_status) for c in plan.changes)
    assert plan.reason == "regression on literature_bench"


def test_explicit_target_must_be_an_eligible_previous_promotion() -> None:
    manager = RollbackManager()
    assert manager.plan(history(), target_version_id="v1").target_version_id == "v1"
    for bad in ("v4", "v3", "missing"):
        with pytest.raises(RollbackError):
            manager.plan(history(), target_version_id=bad)


def test_rolled_back_and_later_promoted_versions_are_not_targets() -> None:
    items = history()
    items[1]["status"] = "ROLLED_BACK"
    items.append({"version_id": "v5", "status": "RETIRED", "promoted_at": T0 + timedelta(days=30)})
    assert RollbackManager().plan(items).target_version_id == "v1"


def test_an_older_version_still_marked_promoted_is_a_target_without_a_status_change() -> None:
    items = history()
    items[1]["status"] = "PROMOTED"  # inconsistent history: two PROMOTED versions
    plan = RollbackManager().plan(items)
    assert plan.current_version_id == "v3" and plan.target_version_id == "v2"
    assert [c.as_tuple() for c in plan.changes] == [("v3", "PROMOTED", "ROLLED_BACK")]


def test_refuses_without_a_promoted_version_or_a_target() -> None:
    manager = RollbackManager()
    with pytest.raises(RollbackError, match="no PROMOTED version"):
        manager.plan([{"version_id": "v1", "status": "RETIRED", "promoted_at": T0}])
    with pytest.raises(RollbackError, match="no previously promoted version"):
        manager.plan([{"version_id": "v1", "status": "PROMOTED", "promoted_at": T0}])
    with pytest.raises(RollbackError):
        manager.plan(history(), current_version_id="v4")  # not PROMOTED
    with pytest.raises(RollbackError):
        manager.plan([{"version_id": "v1", "status": "PROMOTED", "promoted_at": None}])
    with pytest.raises(RollbackError):
        duplicate: dict[str, Any] = {"version_id": "v1", "status": "RETIRED", "promoted_at": T0}
        manager.plan([*history(), duplicate])


def test_accepts_iso_strings_naive_datetimes_and_id_alias() -> None:
    items: list[dict[str, Any]] = [
        {"id": "a", "status": "RETIRED", "promoted_at": "2026-01-01T00:00:00"},
        {"id": "b", "status": "RETIRED", "promoted_at": datetime(2026, 2, 1)},  # naive → UTC
        {"id": "c", "status": "PROMOTED", "promoted_at": "2026-03-01T00:00:00+00:00"},
    ]
    assert RollbackManager().plan(items).target_version_id == "b"
