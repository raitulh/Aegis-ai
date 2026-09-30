"""Population bookkeeping: identity, de-duplication and lineage of candidates."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from engines.lab.evolution.types import Candidate, canonical_hash


def param_hash(params: Mapping[str, Any]) -> str:
    """Canonical hash of a parameter vector (float noise below 12 significant digits ignored)."""
    return canonical_hash(dict(params))


class DuplicateCandidate(ValueError):
    """Raised when a candidate id is added twice."""


class PopulationManager:
    """An ordered set of candidates keyed by id, with a parameter-hash index."""

    def __init__(self, candidates: Iterable[Candidate] = ()) -> None:
        self._by_id: dict[str, Candidate] = {}
        self._hash_to_ids: dict[str, list[str]] = {}
        for candidate in candidates:
            self.add(candidate)

    # -- mutation ------------------------------------------------------------------------------
    def add(self, candidate: Candidate) -> bool:
        """Add a candidate; returns ``False`` when its parameter vector was already present."""
        if candidate.id in self._by_id:
            raise DuplicateCandidate(f"candidate id {candidate.id!r} is already in the population")
        digest = param_hash(candidate.params)
        is_new = digest not in self._hash_to_ids
        self._by_id[candidate.id] = candidate
        self._hash_to_ids.setdefault(digest, []).append(candidate.id)
        return is_new

    def remove(self, candidate_id: str) -> Candidate:
        candidate = self._by_id.pop(candidate_id)
        digest = param_hash(candidate.params)
        ids = self._hash_to_ids[digest]
        ids.remove(candidate_id)
        if not ids:
            del self._hash_to_ids[digest]
        return candidate

    # -- queries --------------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Candidate]:
        return iter(list(self._by_id.values()))

    def __contains__(self, candidate_id: object) -> bool:
        return candidate_id in self._by_id

    def get(self, candidate_id: str) -> Candidate:
        return self._by_id[candidate_id]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    @property
    def candidates(self) -> tuple[Candidate, ...]:
        return tuple(self._by_id.values())

    @property
    def hashes(self) -> frozenset[str]:
        return frozenset(self._hash_to_ids)

    def contains_params(self, params: Mapping[str, Any]) -> bool:
        return param_hash(params) in self._hash_to_ids

    def duplicates(self) -> list[tuple[str, ...]]:
        """Groups of candidate ids that share an identical parameter vector."""
        return [tuple(ids) for ids in self._hash_to_ids.values() if len(ids) > 1]

    def evaluated(self) -> list[Candidate]:
        return [c for c in self._by_id.values() if c.fitness is not None]

    def unevaluated(self) -> list[Candidate]:
        return [c for c in self._by_id.values() if c.fitness is None]

    def by_generation(self, generation: int) -> list[Candidate]:
        return [c for c in self._by_id.values() if c.generation == generation]

    def select(self, ids: Iterable[str]) -> list[Candidate]:
        return [self._by_id[i] for i in ids]

    def lineage(self, candidate_id: str) -> list[str]:
        """Ancestor ids reachable within this population (breadth-first, each once)."""
        seen: list[str] = []
        queue = list(self._by_id[candidate_id].parents)
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.append(current)
            if current in self._by_id:
                queue.extend(self._by_id[current].parents)
        return seen
