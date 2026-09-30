"""Rollback planning for strategy versions.

Given a strategy's version history, :class:`RollbackManager` finds the version to restore — the
previously PROMOTED/RETIRED version most recently promoted strictly before the current one — and
returns the status changes that restore it, each validated against
``engines.lab.states.STRATEGY_TRANSITIONS``:

* current: ``PROMOTED → ROLLED_BACK``
* target:  ``RETIRED → PROMOTED`` (a target that is somehow still ``PROMOTED`` needs no change)

Versions that were themselves rolled back are never rollback targets. The manager is pure: the
strategies service applies the plan inside one transaction (optimistic locking on each version).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from engines.lab.states import StrategyStatus, assert_transition


class RollbackError(ValueError):
    """Raised when no valid rollback exists (nothing promoted, no earlier target, bad target)."""


class VersionState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version_id: str
    status: StrategyStatus
    promoted_at: datetime | None = None


class StatusChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version_id: str
    from_status: str
    to_status: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.version_id, self.from_status, self.to_status)


class RollbackPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    current_version_id: str
    target_version_id: str
    changes: tuple[StatusChange, ...]
    reason: str


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse(item: Mapping[str, Any] | VersionState) -> VersionState:
    if isinstance(item, VersionState):
        state = item
    else:
        data = dict(item)
        if "version_id" not in data and "id" in data:
            data["version_id"] = data["id"]
        data["version_id"] = str(data.get("version_id"))
        state = VersionState.model_validate(data)
    if state.promoted_at is not None:
        state = state.model_copy(update={"promoted_at": _aware(state.promoted_at)})
    return state


class RollbackManager:
    """Computes and validates rollback plans from version history."""

    def plan(
        self,
        history: Sequence[Mapping[str, Any] | VersionState],
        *,
        current_version_id: str | None = None,
        target_version_id: str | None = None,
        reason: str = "rollback",
    ) -> RollbackPlan:
        """Plan a rollback of the current PROMOTED version.

        ``current_version_id`` defaults to the PROMOTED version (the most recently promoted one if
        several are PROMOTED). ``target_version_id`` defaults to the automatic target; an explicit
        target must satisfy the same rules. Raises :class:`RollbackError` when impossible.
        """
        versions = [_parse(v) for v in history]
        by_id = {v.version_id: v for v in versions}
        if len(by_id) != len(versions):
            raise RollbackError("version history contains duplicate version ids")

        current = self._current(versions, by_id, current_version_id)
        assert current.promoted_at is not None
        candidates = self.eligible_targets(versions, current)
        if target_version_id is not None:
            target = by_id.get(str(target_version_id))
            if target is None:
                raise RollbackError(f"target version {target_version_id} is not in the history")
            if target not in candidates:
                raise RollbackError(
                    f"version {target_version_id} is not a valid rollback target (it must have been promoted "
                    f"before the current version and be RETIRED or PROMOTED)"
                )
        elif not candidates:
            raise RollbackError("no previously promoted version to roll back to")
        else:
            target = candidates[0]

        changes = [
            StatusChange(
                version_id=current.version_id, from_status=current.status, to_status=StrategyStatus.ROLLED_BACK
            )
        ]
        if target.status != StrategyStatus.PROMOTED:
            changes.append(
                StatusChange(version_id=target.version_id, from_status=target.status, to_status=StrategyStatus.PROMOTED)
            )
        for change in changes:
            assert_transition("strategy", change.from_status, change.to_status)
        return RollbackPlan(
            current_version_id=current.version_id,
            target_version_id=target.version_id,
            changes=tuple(changes),
            reason=reason,
        )

    @staticmethod
    def eligible_targets(versions: Sequence[VersionState], current: VersionState) -> list[VersionState]:
        """RETIRED/PROMOTED versions promoted before ``current``, most recently promoted first."""
        if current.promoted_at is None:
            return []
        promoted_at = current.promoted_at
        eligible = [
            v
            for v in versions
            if v.version_id != current.version_id
            and v.status in (StrategyStatus.RETIRED, StrategyStatus.PROMOTED)
            and v.promoted_at is not None
            and v.promoted_at < promoted_at
        ]
        return sorted(eligible, key=lambda v: (v.promoted_at, v.version_id), reverse=True)

    @staticmethod
    def _current(
        versions: Sequence[VersionState], by_id: Mapping[str, VersionState], current_version_id: str | None
    ) -> VersionState:
        if current_version_id is not None:
            current = by_id.get(str(current_version_id))
            if current is None:
                raise RollbackError(f"current version {current_version_id} is not in the history")
            if current.status != StrategyStatus.PROMOTED:
                raise RollbackError(f"version {current_version_id} is {current.status}, not PROMOTED")
        else:
            promoted = [v for v in versions if v.status == StrategyStatus.PROMOTED]
            if not promoted:
                raise RollbackError("no PROMOTED version to roll back")
            epoch = datetime.min.replace(tzinfo=UTC)
            current = max(promoted, key=lambda v: (v.promoted_at or epoch, v.version_id))
        if current.promoted_at is None:
            raise RollbackError(f"version {current.version_id} is PROMOTED but has no promoted_at timestamp")
        return current
