"""Child generation: crossover + mutation under guardrails, with de-duplication and a retry budget.

For child attempt *a* the generator pairs mating-pool entries ``2a`` and ``2a+1`` (random pairs once
the first pass is exhausted), applies crossover with probability ``crossover_rate`` (blend crossover
for float-bearing schemas with probability ``blend_probability``, otherwise uniform), then mutates
each mutable parameter with probability ``mutation_rate`` (default ``1 / #mutable``). A child
identical to its first parent always receives at least one mutation.

The schema itself is validated once (``GuardrailViolation`` if it declares authority-bearing
parameters). A child is **rejected** — and another attempt is made, up to ``n × max_attempts_factor`` attempts —
when it violates the strategy guardrails (checked against its first parent, so immutable parameters
and tool sets cannot escalate) or when its parameter vector duplicates an existing one (population,
archive or a sibling). Children inherit the first parent's ``definition`` unchanged: evolution varies
parameters, never the authority-bearing structure of a strategy.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engines.lab.evolution.guardrails import DEFAULT_GUARDRAILS, GuardrailViolation, StrategyGuardrails
from engines.lab.evolution.mutation import MutationEngine, MutationError
from engines.lab.evolution.population import param_hash
from engines.lab.evolution.rng import make_rng
from engines.lab.evolution.types import Candidate, MutationRecord, ParameterSchema, ParamType


class RejectedChild(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt: int
    reason: str  # guardrail | duplicate | no_variation
    parents: tuple[str, ...] = ()
    detail: dict[str, Any] = Field(default_factory=dict)


class GenerationBatch(BaseModel):
    """Children produced by one :meth:`CandidateGenerator.generate` call."""

    model_config = ConfigDict(extra="forbid")

    children: list[Candidate]
    rejected: list[RejectedChild] = Field(default_factory=list)
    attempts: int = 0
    exhausted: bool = False


class CandidateGenerator:
    """Produces guardrail-valid, unique children from a mating pool."""

    def __init__(
        self,
        schema: ParameterSchema | Mapping[str, Any],
        *,
        seed: int = 0,
        mutation_rate: float | None = None,
        crossover_rate: float = 0.9,
        blend_probability: float = 0.5,
        max_attempts_factor: int = 10,
        guardrails: StrategyGuardrails | None = None,
        mutation_engine: MutationEngine | None = None,
    ) -> None:
        self.schema = ParameterSchema.from_dict(schema)
        if mutation_rate is not None and not 0 < mutation_rate <= 1:
            raise ValueError("mutation_rate must be in (0, 1]")
        if not 0 <= crossover_rate <= 1:
            raise ValueError("crossover_rate must be in [0, 1]")
        if not 0 <= blend_probability <= 1:
            raise ValueError("blend_probability must be in [0, 1]")
        if max_attempts_factor < 1:
            raise ValueError("max_attempts_factor must be >= 1")
        self.seed = seed
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.blend_probability = blend_probability
        self.max_attempts_factor = max_attempts_factor
        self.guardrails = guardrails or DEFAULT_GUARDRAILS
        self.mutation = mutation_engine or MutationEngine(seed)
        schema_report = self.guardrails.validate_schema(self.schema)
        if not schema_report.ok:
            raise GuardrailViolation(schema_report)
        self._mutable = self.schema.mutable_names
        self._has_float = any(self.schema.parameters[n].type is ParamType.FLOAT for n in self._mutable)

    @property
    def effective_mutation_rate(self) -> float:
        if self.mutation_rate is not None:
            return self.mutation_rate
        return 1.0 / len(self._mutable) if self._mutable else 0.0

    def generate(
        self,
        parents: Sequence[Candidate],
        n: int,
        *,
        generation: int,
        existing_hashes: Collection[str] = (),
        id_prefix: str = "",
    ) -> GenerationBatch:
        """Produce up to ``n`` children from the mating pool ``parents`` (in mating order)."""
        if n < 0:
            raise ValueError("n must be >= 0")
        if n and not parents:
            raise ValueError("cannot generate children from an empty mating pool")
        known = set(existing_hashes)
        children: list[Candidate] = []
        rejected: list[RejectedChild] = []
        budget = max(n, 1) * self.max_attempts_factor
        attempt = 0
        while len(children) < n and attempt < budget:
            first, second = self._pair(parents, attempt, n, generation)
            try:
                params, records = self._vary(first, second, generation=generation, attempt=attempt)
            except MutationError as exc:
                rejected.append(
                    RejectedChild(
                        attempt=attempt, reason="no_variation", parents=(first.id,), detail={"error": str(exc)}
                    )
                )
                attempt += 1
                continue
            lineage = tuple(dict.fromkeys(p for r in records for p in r.parents)) or (first.id,)
            report = self.guardrails.validate(
                first.definition,
                params,
                self.schema,
                parent={"definition": first.definition or {}, "parameters": first.params},
                check_schema=False,
            )
            digest = param_hash(params)
            if not report.ok:
                rejected.append(
                    RejectedChild(
                        attempt=attempt,
                        reason="guardrail",
                        parents=lineage,
                        detail={"codes": sorted(report.codes), "violations": report.as_dict()["violations"][:10]},
                    )
                )
            elif digest in known:
                rejected.append(
                    RejectedChild(attempt=attempt, reason="duplicate", parents=lineage, detail={"hash": digest})
                )
            else:
                known.add(digest)
                children.append(
                    Candidate(
                        id=f"{id_prefix}g{generation}-{len(children):03d}-{digest[:10]}",
                        params=params,
                        parents=lineage,
                        generation=generation,
                        definition=copy.deepcopy(first.definition) if first.definition is not None else None,
                        mutations=tuple(records),
                    )
                )
            attempt += 1
        return GenerationBatch(children=children, rejected=rejected, attempts=attempt, exhausted=len(children) < n)

    # -- internals ------------------------------------------------------------------------------------
    def _pair(self, pool: Sequence[Candidate], attempt: int, n: int, generation: int) -> tuple[Candidate, Candidate]:
        size = len(pool)
        if attempt < n:
            return pool[(2 * attempt) % size], pool[(2 * attempt + 1) % size]
        rng = make_rng(self.seed, "pair", generation, attempt)
        return pool[rng.randrange(size)], pool[rng.randrange(size)]

    def _vary(
        self, first: Candidate, second: Candidate, *, generation: int, attempt: int
    ) -> tuple[dict[str, Any], list[MutationRecord]]:
        rng = make_rng(self.seed, "child", generation, attempt)
        records: list[MutationRecord] = []
        params = self.schema.with_defaults(first.params)
        base = dict(params)
        parents: tuple[str, ...] = (first.id,)
        if first.id != second.id and first.params != second.params and rng.random() < self.crossover_rate:
            use_blend = self._has_float and rng.random() < self.blend_probability
            operator = "blend_crossover" if use_blend else "uniform_crossover"
            crossed, record = self.mutation.crossover(
                first.params,
                second.params,
                self.schema,
                operator=operator,
                generation=generation,
                index=attempt,
                parents=(first.id, second.id),
            )
            if record.changed_paths:
                params = crossed
                parents = (first.id, second.id)
                records.append(record)
        rate = self.effective_mutation_rate
        step = 0
        for name in self._mutable:
            if rng.random() >= rate:
                continue
            try:
                params, record = self.mutation.mutate(
                    params, self.schema, generation=generation, index=attempt, step=step, path=name
                )
            except MutationError:
                continue
            records.append(record.model_copy(update={"parents": parents}))
            step += 1
        if params == base:
            params, record = self.mutation.mutate(params, self.schema, generation=generation, index=attempt, step=step)
            records.append(record.model_copy(update={"parents": parents}))
        return params, records
