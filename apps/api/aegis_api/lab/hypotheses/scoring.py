"""Deterministic hypothesis selection scoring (pure: no database, no clock, no model calls).

A candidate's selection score is a weighted sum of independent critique dimensions minus a risk and a cost
penalty::

    score = w_n·novelty + w_f·feasibility + w_t·testability + w_e·evidence − w_r·risk − w_c·min(1, cost / scale)

* Dimension values are the **means of the recorded critiques** (each in ``[0, 1]``). Critiques come from a
  critic that is independent of the generator (human, rule or a separate critic agent), so a generator can never
  score its own proposals.
* Before any critique exists a dimension is *unassessed*: benefit dimensions (novelty, testability, evidence)
  count ``0.0`` and risk counts ``0.5`` — unassessed claims never rank above assessed ones. The proposer's own
  feasibility estimate is used only while no critique has assessed feasibility.
* Weights come from :data:`DEFAULT_WEIGHTS` or, when the mission uses a ``hypothesis`` strategy, from that
  strategy version's ``parameters.selection_weights`` (+ ``cost_scale_usd``, ``min_score``); invalid values fall
  back to the defaults and are reported in ``notes``.
* Eligibility: a hypothesis without a valid measurable prediction is **never** selected, nor is one whose latest
  critique recommends ``reject``.
* Ties are broken by ``created_at`` (older first) and then by id, so the ranking is a total order and the same
  inputs always produce the same selection.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

SCORING_VERSION = "hypothesis-selection-1.0.0"

DIMENSIONS: tuple[str, ...] = ("novelty", "feasibility", "testability", "evidence_strength", "risk")
RECOMMENDATIONS: tuple[str, ...] = ("select", "revise", "reject")
UNASSESSED_BENEFIT = 0.0
UNASSESSED_RISK = 0.5
SELECTABLE_STATUSES = frozenset({"GENERATED", "CRITIQUED", "SELECTED"})
SCORE_DECIMALS = 9


@dataclass(frozen=True)
class ScoringWeights:
    """Non-negative weights of the selection score (see module docstring)."""

    novelty: float = 0.25
    feasibility: float = 0.20
    testability: float = 0.30
    evidence: float = 0.25
    risk: float = 0.20
    cost: float = 0.10
    cost_scale_usd: float = 100.0
    min_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "novelty": self.novelty,
            "feasibility": self.feasibility,
            "testability": self.testability,
            "evidence": self.evidence,
            "risk": self.risk,
            "cost": self.cost,
            "cost_scale_usd": self.cost_scale_usd,
            "min_score": self.min_score,
        }

    @classmethod
    def from_parameters(cls, parameters: Mapping[str, Any] | None) -> tuple[ScoringWeights, list[str]]:
        """Weights from strategy parameters → ``(weights, notes)``.

        Recognised keys: ``selection_weights`` (mapping with any of ``novelty``, ``feasibility``,
        ``testability``, ``evidence``, ``risk``, ``cost``), ``cost_scale_usd`` and ``min_score``. Values must be
        finite and non-negative (``min_score`` may be any finite number); anything else is ignored with a note.
        """
        weights = cls()
        notes: list[str] = []
        if not parameters:
            return weights, notes
        raw = parameters.get("selection_weights")
        updates: dict[str, Any] = {}
        if raw is not None:
            if not isinstance(raw, Mapping):
                notes.append("selection_weights is not a mapping; default weights used")
            else:
                for key, value in raw.items():
                    if key not in ("novelty", "feasibility", "testability", "evidence", "risk", "cost"):
                        notes.append(f"unknown selection weight {str(key)[:40]!r} ignored")
                        continue
                    number = _finite(value)
                    if number is None or number < 0:
                        notes.append(f"selection weight {key!r} must be a finite non-negative number; default kept")
                        continue
                    updates[str(key)] = number
        if "cost_scale_usd" in parameters:
            scale = _finite(parameters.get("cost_scale_usd"))
            if scale is None or scale < 0:
                notes.append("cost_scale_usd must be a finite non-negative number; default kept")
            else:
                updates["cost_scale_usd"] = scale
        if "min_score" in parameters and parameters.get("min_score") is not None:
            threshold = _finite(parameters.get("min_score"))
            if threshold is None:
                notes.append("min_score must be a finite number; ignored")
            else:
                updates["min_score"] = threshold
        weights = replace(weights, **updates)
        if all(getattr(weights, k) == 0 for k in ("novelty", "feasibility", "testability", "evidence")):
            notes.append("all benefit weights are zero; default weights used")
            weights = cls()
        return weights, notes


DEFAULT_WEIGHTS = ScoringWeights()


@dataclass(frozen=True)
class SelectionCandidate:
    """What the scorer needs to know about one hypothesis."""

    id: str
    created_at: datetime
    status: str
    has_measurable_prediction: bool
    critique_scores: Mapping[str, float] = field(default_factory=dict)
    critique_count: int = 0
    latest_recommendation: str | None = None
    feasibility_estimate: float | None = None
    estimated_cost_usd: float = 0.0


@dataclass(frozen=True)
class RankedHypothesis:
    id: str
    score: float
    components: dict[str, float]
    eligible: bool
    reason: str | None
    rank: int | None = None
    selected: bool = False
    newly_selected: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "score": self.score,
            "rank": self.rank,
            "eligible": self.eligible,
            "reason": self.reason,
            "selected": self.selected,
            "newly_selected": self.newly_selected,
            "components": dict(self.components),
        }


@dataclass(frozen=True)
class SelectionResult:
    ranking: list[RankedHypothesis]
    selected_ids: list[str]
    newly_selected_ids: list[str]
    weights: ScoringWeights
    scoring_version: str = SCORING_VERSION


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unit(value: Any) -> float | None:
    number = _finite(value)
    if number is None or number < 0.0 or number > 1.0:
        return None
    return number


def aggregate_critiques(critiques: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate critiques (oldest first; each ``{"scores": {...}, "recommendation": ...}``) into per-dimension
    means, the number of critiques, recommendation counts and the latest recommendation.

    Out-of-range or non-numeric dimension values are ignored (never coerced)."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    recommendations = dict.fromkeys(RECOMMENDATIONS, 0)
    latest: str | None = None
    for critique in critiques:
        scores = critique.get("scores") or {}
        if isinstance(scores, Mapping):
            for dim in DIMENSIONS:
                value = _unit(scores.get(dim))
                if value is not None:
                    sums[dim] = sums.get(dim, 0.0) + value
                    counts[dim] = counts.get(dim, 0) + 1
        rec = critique.get("recommendation")
        if rec in recommendations:
            recommendations[str(rec)] += 1
            latest = str(rec)
    means = {dim: round(sums[dim] / counts[dim], SCORE_DECIMALS) for dim in DIMENSIONS if counts.get(dim)}
    return {
        "means": means,
        "n_critiques": len(critiques),
        "recommendations": recommendations,
        "latest_recommendation": latest,
    }


def score_components(candidate: SelectionCandidate, weights: ScoringWeights = DEFAULT_WEIGHTS) -> dict[str, float]:
    """The dimension values used for ``candidate`` and its total ``score``."""
    scores = candidate.critique_scores
    novelty = _unit(scores.get("novelty"))
    feasibility = _unit(scores.get("feasibility"))
    if feasibility is None:
        feasibility = _unit(candidate.feasibility_estimate)
    testability = _unit(scores.get("testability"))
    evidence = _unit(scores.get("evidence_strength"))
    risk = _unit(scores.get("risk"))
    cost = max(_finite(candidate.estimated_cost_usd) or 0.0, 0.0)
    cost_penalty = min(1.0, cost / weights.cost_scale_usd) if weights.cost_scale_usd > 0 else 0.0
    values = {
        "novelty": novelty if novelty is not None else UNASSESSED_BENEFIT,
        "feasibility": feasibility if feasibility is not None else UNASSESSED_BENEFIT,
        "testability": testability if testability is not None else UNASSESSED_BENEFIT,
        "evidence": evidence if evidence is not None else UNASSESSED_BENEFIT,
        "risk": risk if risk is not None else UNASSESSED_RISK,
        "cost_penalty": cost_penalty,
    }
    total = (
        weights.novelty * values["novelty"]
        + weights.feasibility * values["feasibility"]
        + weights.testability * values["testability"]
        + weights.evidence * values["evidence"]
        - weights.risk * values["risk"]
        - weights.cost * values["cost_penalty"]
    )
    values["score"] = round(total, SCORE_DECIMALS)
    return {k: round(v, SCORE_DECIMALS) for k, v in values.items()}


def eligibility(candidate: SelectionCandidate) -> str | None:
    """``None`` when the candidate may be selected, else the reason it may not."""
    if candidate.status not in SELECTABLE_STATUSES:
        return f"status {candidate.status} is not selectable"
    if not candidate.has_measurable_prediction:
        return "no measurable prediction (metric, comparator and threshold are required)"
    if candidate.latest_recommendation == "reject":
        return "the latest critique recommends rejection"
    return None


def rank_candidates(
    candidates: Sequence[SelectionCandidate], weights: ScoringWeights = DEFAULT_WEIGHTS
) -> list[RankedHypothesis]:
    """Score every candidate; eligible ones get ranks 1..n by (score desc, created_at asc, id asc)."""
    scored: list[tuple[SelectionCandidate, dict[str, float], str | None]] = [
        (c, score_components(c, weights), eligibility(c)) for c in candidates
    ]
    eligible = sorted(
        (item for item in scored if item[2] is None),
        key=lambda item: (-item[1]["score"], item[0].created_at, item[0].id),
    )
    ineligible = sorted(
        (item for item in scored if item[2] is not None),
        key=lambda item: (item[0].created_at, item[0].id),
    )
    ranking = [
        RankedHypothesis(id=c.id, score=comp["score"], components=comp, eligible=True, reason=None, rank=i + 1)
        for i, (c, comp, _reason) in enumerate(eligible)
    ]
    ranking += [
        RankedHypothesis(id=c.id, score=comp["score"], components=comp, eligible=False, reason=reason)
        for c, comp, reason in ineligible
    ]
    return ranking


def select_top(
    candidates: Sequence[SelectionCandidate], top_k: int, weights: ScoringWeights = DEFAULT_WEIGHTS
) -> SelectionResult:
    """Select up to ``top_k`` hypotheses. Hypotheses already ``SELECTED`` keep their selection and occupy slots;
    remaining slots go to the best eligible candidates (at or above ``weights.min_score`` when set)."""
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    ranking = rank_candidates(candidates, weights)
    status_by_id = {c.id: c.status for c in candidates}
    already = [r.id for r in ranking if status_by_id.get(r.id) == "SELECTED"]
    slots = max(top_k - len(already), 0)
    newly: list[str] = []
    for entry in ranking:
        if len(newly) >= slots:
            break
        if not entry.eligible or status_by_id.get(entry.id) == "SELECTED":
            continue
        if weights.min_score is not None and entry.score < weights.min_score:
            continue
        newly.append(entry.id)
    chosen = set(already) | set(newly)
    final = [
        replace(entry, selected=entry.id in chosen, newly_selected=entry.id in newly)
        if entry.id in chosen
        else (
            replace(entry, reason=f"score below min_score {weights.min_score:g}")
            if entry.eligible and weights.min_score is not None and entry.score < weights.min_score
            else entry
        )
        for entry in ranking
    ]
    selected_ids = [entry.id for entry in final if entry.selected]
    return SelectionResult(ranking=final, selected_ids=selected_ids, newly_selected_ids=newly, weights=weights)
