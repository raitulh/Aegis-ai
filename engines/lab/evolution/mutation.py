"""MutationEngine: seeded, bounded mutation and crossover over a strategy's declared parameter space.

Mutations only touch ``parameters`` (never behaviour structure or governance). Every child is re-validated and
checked against its parent's governance before it is returned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from engines.lab.evolution.genome import GuardrailViolation, ParameterDef, StrategyDefinition, assert_no_escalation


@dataclass(frozen=True)
class ParameterChange:
    parameter: str
    old: Any
    new: Any
    operator: str


@dataclass
class MutationRecord:
    operator: str
    parent_hashes: list[str]
    changes: list[ParameterChange] = field(default_factory=list)
    seed: int | None = None
    source: str = "engine"  # engine | agent_proposal

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "parent_hashes": self.parent_hashes,
            "changes": [c.__dict__ for c in self.changes],
            "seed": self.seed,
            "source": self.source,
        }


def _mutate_value(param: ParameterDef, value: Any, rng: np.random.Generator) -> Any:
    if param.kind == "bool":
        return not value
    if param.kind == "categorical":
        options = [c for c in (param.choices or []) if c != value]
        return options[int(rng.integers(len(options)))] if options else value
    low, high = float(param.low or 0.0), float(param.high or 0.0)
    if high == low:
        return value
    if param.log:
        lv, llow, lhigh = math.log(float(value)), math.log(low), math.log(high)
        new = math.exp(float(np.clip(lv + rng.normal(0.0, param.mutation_scale * (lhigh - llow)), llow, lhigh)))
    else:
        new = float(np.clip(float(value) + rng.normal(0.0, param.mutation_scale * (high - low)), low, high))
    if param.kind == "int":
        new_int = round(new)
        if new_int == value:  # guarantee an actual move for integer parameters
            step = 1 if rng.random() < 0.5 else -1
            new_int = int(np.clip(new_int + step, low, high))
        return new_int
    return new


class MutationEngine:
    def __init__(self, mutation_rate: float = 0.3) -> None:
        if not 0.0 < mutation_rate <= 1.0:
            raise ValueError("mutation_rate must be in (0, 1]")
        self.mutation_rate = mutation_rate

    def mutate(
        self, parent: StrategyDefinition, rng: np.random.Generator, *, seed: int | None = None
    ) -> tuple[StrategyDefinition, MutationRecord]:
        space = [p for p in parent.parameter_space if p.mutable and p.name in parent.parameters]
        if not space:
            raise GuardrailViolation("strategy has no mutable parameters")
        chosen = [p for p in space if rng.random() < self.mutation_rate]
        if not chosen:
            chosen = [space[int(rng.integers(len(space)))]]
        params = dict(parent.parameters)
        record = MutationRecord(operator="mutation", parent_hashes=[parent.parameter_hash()], seed=seed)
        for p in chosen:
            old = params[p.name]
            new = _mutate_value(p, old, rng)
            if new != old:
                params[p.name] = new
                record.changes.append(ParameterChange(p.name, old, new, f"{p.kind}_mutation"))
        child = self._child(parent, params)
        return child, record

    def crossover(
        self, a: StrategyDefinition, b: StrategyDefinition, rng: np.random.Generator, *, seed: int | None = None
    ) -> tuple[StrategyDefinition, MutationRecord]:
        if a.space() != b.space() or a.kind != b.kind:
            raise GuardrailViolation("crossover requires parents with the same kind and parameter space")
        if a.governance != b.governance:
            raise GuardrailViolation("crossover requires parents under the same governance envelope")
        params: dict[str, Any] = {}
        record = MutationRecord(operator="crossover", parent_hashes=[a.parameter_hash(), b.parameter_hash()], seed=seed)
        for name in sorted(a.parameters):
            take_b = rng.random() < 0.5 and name in b.parameters
            params[name] = b.parameters[name] if take_b else a.parameters[name]
            if take_b and b.parameters[name] != a.parameters[name]:
                record.changes.append(
                    ParameterChange(name, a.parameters[name], b.parameters[name], "uniform_crossover")
                )
        return self._child(a, params), record

    def apply_proposal(
        self, parent: StrategyDefinition, changes: dict[str, Any]
    ) -> tuple[StrategyDefinition, MutationRecord]:
        """Validate a mutation proposed by an agent (EvolutionAgent). Only declared, mutable parameters may change."""
        space = parent.space()
        params = dict(parent.parameters)
        record = MutationRecord(
            operator="agent_proposal", parent_hashes=[parent.parameter_hash()], source="agent_proposal"
        )
        for name, value in changes.items():
            param = space.get(name)
            if param is None:
                raise GuardrailViolation(f"proposal changes undeclared parameter '{name}'")
            if not param.mutable:
                raise GuardrailViolation(f"parameter '{name}' is not mutable")
            validated = param.validate_value(value)
            if validated != params.get(name):
                record.changes.append(ParameterChange(name, params.get(name), validated, "agent_proposal"))
                params[name] = validated
        if not record.changes:
            raise GuardrailViolation("proposal does not change any parameter")
        return self._child(parent, params), record

    @staticmethod
    def _child(parent: StrategyDefinition, params: dict[str, Any]) -> StrategyDefinition:
        child = StrategyDefinition(
            kind=parent.kind,
            description=parent.description,
            parameter_space=list(parent.parameter_space),
            parameters=params,
            behavior=dict(parent.behavior),
            governance=parent.governance,
        )
        assert_no_escalation(parent, child)
        return child
