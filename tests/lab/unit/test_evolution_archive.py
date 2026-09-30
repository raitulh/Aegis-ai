"""ε-Pareto archive: ε-dominance, same-box replacement, bounded pruning, serialisation."""

from __future__ import annotations

import json

import pytest

from engines.lab.evolution.archive import ArchiveEntry, ArchiveManager


def entry(entry_id: str, *oriented: float, generation: int = 0) -> ArchiveEntry:
    return ArchiveEntry(id=entry_id, oriented=tuple(oriented), params={"id": entry_id}, generation=generation)


def archive(eps: float = 0.1, max_size: int = 100) -> ArchiveManager:
    return ArchiveManager.create(["a", "b"], eps, max_size)


def test_create_accepts_scalar_list_and_mapping_epsilon() -> None:
    assert archive(0.1).epsilon == (0.1, 0.1)
    assert ArchiveManager.create(["a", "b"], [0.1, 0.2]).epsilon == (0.1, 0.2)
    assert ArchiveManager.create(["a", "b"], {"b": 0.3, "a": 0.2}).epsilon == (0.2, 0.3)
    for bad in (0.0, -1.0, [0.1], {"a": 0.1}):
        with pytest.raises(ValueError):
            ArchiveManager.create(["a", "b"], bad)


def test_mutually_non_dominated_boxes_are_all_kept() -> None:
    arc = archive()
    for i, point in enumerate([(0.95, 0.05), (0.55, 0.55), (0.05, 0.95)]):
        accepted, removed = arc.insert(entry(f"p{i}", *point))
        assert accepted and removed == ()
    assert arc.ids == ("p0", "p1", "p2")


def test_epsilon_dominated_candidate_is_rejected() -> None:
    arc = archive()
    arc.insert(entry("strong", 0.75, 0.75))
    result = arc.insert(entry("weak", 0.55, 0.65))
    assert not result.accepted and result.reason == "epsilon_dominated"
    assert "weak" not in arc


def test_dominating_candidate_removes_box_dominated_members() -> None:
    arc = archive()
    arc.insert(entry("old1", 0.35, 0.15))
    arc.insert(entry("old2", 0.15, 0.35))
    accepted, removed = arc.insert(entry("new", 0.55, 0.55))
    assert accepted and set(removed) == {"old1", "old2"}
    assert arc.ids == ("new",)


def test_same_box_keeps_the_dominating_or_corner_closest_solution() -> None:
    arc = archive()
    arc.insert(entry("first", 0.51, 0.52))
    better = arc.insert(entry("better", 0.55, 0.56))  # same box, Pareto-dominates
    assert better.accepted and better.dominated_removed == ("first",)
    far = arc.insert(entry("far", 0.59, 0.50))  # same box, incomparable, farther from corner (0.6, 0.6)
    assert not far.accepted and far.reason == "farther_from_box_corner"
    near = arc.insert(entry("near", 0.595, 0.58))  # incomparable with "better"? no: dominates it
    assert near.accepted and near.dominated_removed == ("better",)
    closer = arc.insert(entry("closer", 0.59, 0.599))  # incomparable with "near", closer to the corner
    assert closer.accepted and closer.dominated_removed == ("near",)
    assert len(arc) == 1  # ε-dominance keeps at most one solution per box


def test_infeasible_and_non_finite_entries_are_rejected() -> None:
    arc = archive()
    assert arc.insert(entry("bad", 0.9, 0.9), feasible=False).reason == "infeasible"
    assert arc.insert(entry("nan", float("nan"), 0.9)).reason == "non_finite"
    with pytest.raises(ValueError):
        arc.insert(entry("wrong", 0.1, 0.2, 0.3))
    assert len(arc) == 0


def test_reinserting_an_id_is_idempotent_or_refreshes_stale_values() -> None:
    arc = archive()
    arc.insert(entry("x", 0.5, 0.5))
    assert arc.insert(entry("x", 0.5, 0.5)).reason == "already_archived"
    refreshed = arc.insert(entry("x", 0.25, 0.85))
    assert refreshed.accepted
    assert arc.entries[0].oriented == (0.25, 0.85)


def test_pruning_by_crowding_keeps_extremes() -> None:
    arc = archive(eps=0.01, max_size=3)
    points = [(0.0, 1.0), (0.3, 0.7), (0.5, 0.5), (0.55, 0.45), (1.0, 0.0)]
    pruned: list[str] = []
    for i, p in enumerate(points):
        pruned.extend(arc.insert(entry(f"p{i}", *p, generation=i)).pruned)
    assert len(arc) == 3
    assert "p0" in arc and "p4" in arc  # boundary solutions (infinite crowding) survive
    assert len(pruned) == 2


def test_prune_can_reject_the_newcomer_itself() -> None:
    arc = archive(eps=0.01, max_size=2)
    arc.insert(entry("left", 0.0, 1.0))
    arc.insert(entry("right", 1.0, 0.0))
    result = arc.insert(entry("mid", 0.5, 0.5, generation=5))
    assert not result.accepted and result.reason == "pruned_for_capacity" and result.pruned == ("mid",)


def test_serialisation_round_trip_is_json_safe() -> None:
    arc = archive(eps=0.05, max_size=10)
    arc.insert(entry("a", 0.9, 0.1))
    arc.insert(entry("b", 0.1, 0.9))
    data = json.loads(json.dumps(arc.to_dict(), allow_nan=False))
    restored = ArchiveManager.from_dict(data)
    assert restored.entries == arc.entries
    assert restored.epsilon == arc.epsilon and restored.max_size == 10
    with pytest.raises(ValueError):
        ArchiveManager.from_dict({**data, "format": "other"})
    with pytest.raises(ValueError):
        ArchiveManager.from_dict({**data, "entries": data["entries"] + data["entries"][:1]})
