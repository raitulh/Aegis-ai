"""Bounded Pareto archive with ε-dominance (Laumanns, Thiele, Deb & Zitzler, 2002; Deb's ε-MOEA).

Objective space (oriented, maximised, normalised) is divided into boxes of side ``ε_i`` per
objective; the box of ``f`` is ``⌊f_i / ε_i⌋``. The archive keeps at most one solution per box and no
box that is box-dominated by another. Insertion rules for a new solution ``x`` with box ``B``:

1. rejected if some member's box box-dominates ``B`` (``epsilon_dominated``);
2. otherwise every member whose box is box-dominated by ``B`` is removed (``dominated_removed``);
3. if a member shares ``B``: ``x`` replaces it when ``x`` Pareto-dominates it, or — when neither
   dominates — when ``x`` is closer to the box's best corner; otherwise ``x`` is rejected;
4. if the archive exceeds ``max_size`` the member with the smallest crowding distance is pruned
   (boundary members have infinite crowding and are kept), repeatedly.

Only *feasible* solutions enter the archive. The archive is serialisable (``to_dict``/``from_dict``)
so the strategies service can store it in ``evolution_runs.archive`` between generations.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.evolution.selection import crowding_distance, pareto_dominates

ARCHIVE_FORMAT = "aegis.lab.pareto_archive"
ARCHIVE_VERSION = 1
_BOX_TOL = 1e-9


class ArchiveEntry(BaseModel):
    """One archived, feasible solution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    oriented: tuple[float, ...]
    objectives: dict[str, float | None] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    generation: int = 0


@dataclass(frozen=True)
class InsertResult:
    """Outcome of :meth:`ArchiveManager.insert`.

    Unpacks as ``accepted, dominated_removed = archive.insert(entry)``. ``pruned`` lists members
    removed afterwards to respect ``max_size``; ``reason`` explains a rejection.
    """

    accepted: bool
    dominated_removed: tuple[str, ...] = ()
    pruned: tuple[str, ...] = ()
    reason: str = "accepted"

    def __iter__(self) -> Iterator[Any]:
        yield self.accepted
        yield self.dominated_removed


def _box_dominates(a: Sequence[int], b: Sequence[int]) -> bool:
    return all(x >= y for x, y in zip(a, b, strict=True)) and any(x > y for x, y in zip(a, b, strict=True))


@dataclass
class ArchiveManager:
    """ε-Pareto archive over ``objectives`` (names, in oriented-vector order)."""

    objectives: tuple[str, ...]
    epsilon: tuple[float, ...]
    max_size: int = 100
    _entries: list[ArchiveEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.objectives = tuple(self.objectives)
        if not self.objectives:
            raise ValueError("archive requires at least one objective")
        if len(self.epsilon) != len(self.objectives):
            raise ValueError("epsilon must have one value per objective")
        if any(not math.isfinite(e) or e <= 0 for e in self.epsilon):
            raise ValueError("epsilon values must be finite and > 0")
        if self.max_size < 1:
            raise ValueError("max_size must be >= 1")

    @classmethod
    def create(
        cls, objectives: Sequence[str], epsilon: float | Sequence[float] | Mapping[str, float], max_size: int = 100
    ) -> ArchiveManager:
        """Build an archive; ``epsilon`` may be a scalar, a per-objective list, or a name→ε mapping."""
        names = tuple(objectives)
        if isinstance(epsilon, int | float):
            eps = tuple(float(epsilon) for _ in names)
        elif isinstance(epsilon, Mapping):
            missing = [n for n in names if n not in epsilon]
            if missing:
                raise ValueError(f"epsilon missing for objectives {missing}")
            eps = tuple(float(epsilon[n]) for n in names)
        else:
            eps = tuple(float(e) for e in epsilon)
        return cls(objectives=names, epsilon=eps, max_size=max_size)

    # -- container protocol ----------------------------------------------------------------------
    @property
    def entries(self) -> tuple[ArchiveEntry, ...]:
        return tuple(self._entries)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(e.id for e in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, entry_id: object) -> bool:
        return any(e.id == entry_id for e in self._entries)

    def __iter__(self) -> Iterator[ArchiveEntry]:
        return iter(tuple(self._entries))

    # -- ε-box geometry -----------------------------------------------------------------------------
    def box(self, oriented: Sequence[float]) -> tuple[int, ...]:
        return tuple(math.floor(v / e + _BOX_TOL) for v, e in zip(oriented, self.epsilon, strict=True))

    def _corner_distance(self, oriented: Sequence[float], box: Sequence[int]) -> float:
        # best corner of a box in maximisation is its upper corner ((b+1)·ε); distance in ε units
        return math.sqrt(sum(((b + 1) - v / e) ** 2 for v, b, e in zip(oriented, box, self.epsilon, strict=True)))

    # -- mutation -----------------------------------------------------------------------------------
    def insert(self, entry: ArchiveEntry, *, feasible: bool = True) -> InsertResult:
        """Offer ``entry`` to the archive (see module docstring for the rules)."""
        if len(entry.oriented) != len(self.objectives):
            raise ValueError("entry has the wrong number of objectives")
        if not feasible:
            return InsertResult(accepted=False, reason="infeasible")
        if not all(math.isfinite(v) for v in entry.oriented):
            return InsertResult(accepted=False, reason="non_finite")
        existing = next((e for e in self._entries if e.id == entry.id), None)
        if existing is not None:
            if existing.oriented == entry.oriented:
                return InsertResult(accepted=True, reason="already_archived")
            self._entries = [e for e in self._entries if e.id != entry.id]  # re-evaluated: re-insert

        new_box = self.box(entry.oriented)
        same_box: ArchiveEntry | None = None
        for member in self._entries:
            member_box = self.box(member.oriented)
            if member_box == new_box:
                same_box = member
            elif _box_dominates(member_box, new_box):
                return InsertResult(accepted=False, reason="epsilon_dominated")

        removed = [m.id for m in self._entries if _box_dominates(new_box, self.box(m.oriented))]
        if same_box is not None:
            if pareto_dominates(same_box.oriented, entry.oriented) or same_box.oriented == entry.oriented:
                return InsertResult(accepted=False, reason="dominated_in_box")
            if not pareto_dominates(entry.oriented, same_box.oriented) and self._corner_distance(
                entry.oriented, new_box
            ) >= self._corner_distance(same_box.oriented, new_box):
                return InsertResult(accepted=False, reason="farther_from_box_corner")
            removed.append(same_box.id)

        removed_set = set(removed)
        self._entries = [m for m in self._entries if m.id not in removed_set]
        self._entries.append(entry)
        pruned = self._prune()
        accepted = entry.id not in pruned
        return InsertResult(
            accepted=accepted,
            dominated_removed=tuple(removed),
            pruned=tuple(pruned),
            reason="accepted" if accepted else "pruned_for_capacity",
        )

    def insert_many(self, entries: Sequence[ArchiveEntry]) -> list[InsertResult]:
        return [self.insert(e) for e in entries]

    def _prune(self) -> list[str]:
        pruned: list[str] = []
        while len(self._entries) > self.max_size:
            distances = crowding_distance([e.oriented for e in self._entries])
            # smallest crowding first; ties → older generation first, then id (deterministic)
            victim = min(
                range(len(self._entries)),
                key=lambda i: (distances[i], self._entries[i].generation, self._entries[i].id),
            )
            pruned.append(self._entries[victim].id)
            del self._entries[victim]
        return pruned

    # -- serialisation --------------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "format": ARCHIVE_FORMAT,
            "version": ARCHIVE_VERSION,
            "objectives": list(self.objectives),
            "epsilon": list(self.epsilon),
            "max_size": self.max_size,
            "entries": [e.model_dump(mode="json") for e in self._entries],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArchiveManager:
        if data.get("format") != ARCHIVE_FORMAT or data.get("version") != ARCHIVE_VERSION:
            raise ValueError("not a serialized Pareto archive (format/version mismatch)")
        archive = cls(
            objectives=tuple(data["objectives"]),
            epsilon=tuple(float(e) for e in data["epsilon"]),
            max_size=int(data["max_size"]),
        )
        archive._entries = [ArchiveEntry.model_validate(e) for e in data.get("entries", [])]
        if len({e.id for e in archive._entries}) != len(archive._entries):
            raise ValueError("serialized archive contains duplicate ids")
        return archive
