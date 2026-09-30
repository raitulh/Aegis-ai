"""Strategy genome: a mutable *behaviour* (parameters within a declared space) and an immutable *governance*
envelope (tools, network, secrets, permissions, autonomy ceiling).

Evolution changes behaviour only. ``check_escalation`` compares a child against its parent and reports any
attempt to gain tools, network access, secrets, permissions or autonomy; such children are rejected before
they are persisted (section 29 guardrails). Governance can only be changed by a human through the control
plane, which creates a new *root* strategy rather than a mutation.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from engines.lab.enums import AutonomyLevel, StrategyKind


class GuardrailViolation(ValueError):
    """A strategy definition violates its governance envelope or tries to escalate privileges."""


class ParameterDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    kind: Literal["float", "int", "categorical", "bool"]
    low: float | None = None
    high: float | None = None
    choices: list[Any] | None = None
    log: bool = False
    mutation_scale: float = Field(default=0.15, gt=0, le=1.0, description="Std-dev as a fraction of the range")
    mutable: bool = True

    @model_validator(mode="after")
    def _check(self) -> ParameterDef:
        if self.kind in ("float", "int"):
            if self.low is None or self.high is None or self.low > self.high:
                raise ValueError(f"parameter '{self.name}' needs low <= high")
            if self.log and self.low <= 0:
                raise ValueError(f"log-scaled parameter '{self.name}' needs low > 0")
        if self.kind == "categorical" and not self.choices:
            raise ValueError(f"categorical parameter '{self.name}' needs choices")
        return self

    def validate_value(self, value: Any) -> Any:
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise GuardrailViolation(f"'{self.name}' must be a boolean")
            return value
        if self.kind == "categorical":
            if value not in (self.choices or []):
                raise GuardrailViolation(f"'{self.name}'={value!r} is not an allowed choice")
            return value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise GuardrailViolation(f"'{self.name}' must be numeric")
        low, high = float(self.low or 0.0), float(self.high or 0.0)
        if not low <= float(value) <= high:
            raise GuardrailViolation(f"'{self.name}'={value} outside [{low}, {high}]")
        return round(value) if self.kind == "int" else float(value)

    def normalize(self, value: Any) -> float:
        """Map a value to [0, 1] for distance/novelty computations."""
        if self.kind == "bool":
            return 1.0 if value else 0.0
        if self.kind == "categorical":
            choices = self.choices or [value]
            return choices.index(value) / max(len(choices) - 1, 1) if value in choices else 0.0
        low, high = float(self.low or 0.0), float(self.high or 0.0)
        if high == low:
            return 0.0
        if self.log:
            return (math.log(float(value)) - math.log(low)) / (math.log(high) - math.log(low))
        return (float(value) - low) / (high - low)


class StrategyGovernance(BaseModel):
    """Immutable envelope. Evolution may never widen any of these fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: list[str] = Field(default_factory=list)
    network: Literal["none", "allowlist"] = "none"
    egress_allowlist: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    max_autonomy: AutonomyLevel = AutonomyLevel.L3_AUTOMATED_EXECUTION
    model_tiers: list[str] = Field(default_factory=lambda: ["fast", "default", "reasoning"])
    production_access: bool = False


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: StrategyKind
    description: str = ""
    parameter_space: list[ParameterDef] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    behavior: dict[str, Any] = Field(default_factory=dict)
    governance: StrategyGovernance = Field(default_factory=StrategyGovernance)

    @model_validator(mode="after")
    def _validate(self) -> StrategyDefinition:
        space = {p.name: p for p in self.parameter_space}
        if len(space) != len(self.parameter_space):
            raise ValueError("parameter names must be unique")
        unknown = set(self.parameters) - set(space)
        if unknown:
            raise ValueError(f"parameters outside the declared space: {', '.join(sorted(unknown))}")
        for name, value in list(self.parameters.items()):
            self.parameters[name] = space[name].validate_value(value)
        sequence = self.behavior.get("tool_sequence") or []
        if not isinstance(sequence, list):
            raise ValueError("behavior.tool_sequence must be a list")
        outside = [t for t in sequence if t not in self.governance.tools]
        if outside:
            raise ValueError(f"behavior uses tools outside governance: {', '.join(outside)}")
        tier = self.behavior.get("model_tier")
        if tier is not None and tier not in self.governance.model_tiers:
            raise ValueError(f"behavior model_tier '{tier}' is not permitted by governance")
        return self

    def space(self) -> dict[str, ParameterDef]:
        return {p.name: p for p in self.parameter_space}

    def parameter_hash(self) -> str:
        canonical = json.dumps(self.parameters, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:24]

    def fingerprint(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def normalized_vector(self) -> list[float]:
        space = self.space()
        return [space[name].normalize(self.parameters[name]) for name in sorted(self.parameters)]


def check_escalation(parent: StrategyGovernance, child: StrategyGovernance) -> list[str]:
    """Return every way ``child`` would be more privileged than ``parent`` (empty list = safe)."""
    problems: list[str] = []
    new_tools = set(child.tools) - set(parent.tools)
    if new_tools:
        problems.append(f"gains tools: {', '.join(sorted(new_tools))}")
    if parent.network == "none" and child.network != "none":
        problems.append("gains network access")
    new_hosts = set(child.egress_allowlist) - set(parent.egress_allowlist)
    if new_hosts:
        problems.append(f"gains egress hosts: {', '.join(sorted(new_hosts))}")
    new_secrets = set(child.secrets) - set(parent.secrets)
    if new_secrets:
        problems.append(f"gains secrets: {', '.join(sorted(new_secrets))}")
    new_perms = set(child.permissions) - set(parent.permissions)
    if new_perms:
        problems.append(f"gains permissions: {', '.join(sorted(new_perms))}")
    if child.max_autonomy.rank > parent.max_autonomy.rank:
        problems.append(f"raises autonomy ceiling to {child.max_autonomy.value}")
    new_tiers = set(child.model_tiers) - set(parent.model_tiers)
    if new_tiers:
        problems.append(f"gains model tiers: {', '.join(sorted(new_tiers))}")
    if child.production_access and not parent.production_access:
        problems.append("gains production access")
    return problems


def assert_no_escalation(parent: StrategyDefinition, child: StrategyDefinition) -> None:
    problems = check_escalation(parent.governance, child.governance)
    if parent.kind != child.kind:
        problems.append("changes strategy kind")
    if parent.space() != child.space():
        problems.append("changes the parameter space")
    if problems:
        raise GuardrailViolation("Evolved strategy rejected: " + "; ".join(problems))
