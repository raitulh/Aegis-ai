"""Declarative, versioned policy engine.

A policy set is a list of rules. Each rule targets one or more actions, has a JSON condition over a *facts*
document and an effect (``allow`` | ``require_approval`` | ``deny``). Evaluation combines effects with
**deny-overrides**: any matching ``deny`` wins, then ``require_approval``, then ``allow``. When no rule
matches, the configured default applies (``allow`` for ordinary actions — RBAC has already authorized the
principal — and ``require_approval`` for actions marked sensitive).

The platform ships an immutable **baseline** policy set that encodes the fail-safe defaults (network denied
for execution, secrets require approval, high-risk tools require approval, no autonomous discovery approval,
no strategy auto-promotion below L4, ...). Organization policies are evaluated *in addition* to the baseline:
they can add restrictions but can never relax a baseline ``deny``/``require_approval``.

Condition grammar::

    {"all": [c, ...]} | {"any": [c, ...]} | {"not": c}
    {"fact": "mission.autonomy_rank", "op": "lt", "value": 4}
    {"fact": "estimated_cost", "op": "gt", "fact_ref": "budget.remaining_total"}

Operators: eq, ne, lt, lte, gt, gte, in, not_in, contains, exists, not_exists, truthy, falsy.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from engines.lab.enums import ApprovalKind, PolicyDecision

Operator = Literal[
    "eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "contains", "exists", "not_exists", "truthy", "falsy"
]
_OPERATORS: frozenset[str] = frozenset(Operator.__args__)  # type: ignore[attr-defined]
_MISSING = object()
MAX_CONDITION_DEPTH = 12


class PolicyError(ValueError):
    """A policy document is malformed."""


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    description: str = ""
    actions: list[str] = Field(default_factory=lambda: ["*"], description="Action names; glob patterns allowed")
    when: dict[str, Any] | None = Field(default=None, description="Condition; omitted = always matches")
    effect: PolicyDecision
    approval_kind: ApprovalKind | None = None
    reason: str = ""

    @field_validator("when")
    @classmethod
    def _validate_condition(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            validate_condition(value)
        return value


class PolicySet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    description: str = ""
    rules: list[PolicyRule] = Field(default_factory=list)
    sensitive_actions: list[str] = Field(
        default_factory=list, description="Actions that default to require_approval when no rule matches"
    )

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class MatchedRule:
    policy_key: str
    policy_version: str
    rule_id: str
    effect: PolicyDecision
    reason: str
    approval_kind: str | None


@dataclass
class PolicyResult:
    action: str
    decision: PolicyDecision
    matched: list[MatchedRule] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    approval_kinds: list[str] = field(default_factory=list)
    evaluated_policies: list[dict[str, str]] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == PolicyDecision.ALLOW

    @property
    def denied(self) -> bool:
        return self.decision == PolicyDecision.DENY

    @property
    def needs_approval(self) -> bool:
        return self.decision == PolicyDecision.REQUIRE_APPROVAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "decision": str(self.decision),
            "reasons": self.reasons,
            "approval_kinds": self.approval_kinds,
            "matched": [
                {
                    "policy": m.policy_key,
                    "version": m.policy_version,
                    "rule": m.rule_id,
                    "effect": str(m.effect),
                    "reason": m.reason,
                }
                for m in self.matched
            ],
            "evaluated_policies": self.evaluated_policies,
        }


# ---------------------------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------------------------


def validate_condition(cond: Any, depth: int = 0) -> None:
    if depth > MAX_CONDITION_DEPTH:
        raise PolicyError("Condition nesting is too deep")
    if not isinstance(cond, Mapping):
        raise PolicyError("Condition must be an object")
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        items = cond[key]
        if not isinstance(items, list) or not items:
            raise PolicyError(f"'{key}' must be a non-empty list")
        for item in items:
            validate_condition(item, depth + 1)
        return
    if "not" in cond:
        validate_condition(cond["not"], depth + 1)
        return
    if "fact" not in cond or "op" not in cond:
        raise PolicyError("Leaf conditions need 'fact' and 'op'")
    if cond["op"] not in _OPERATORS:
        raise PolicyError(f"Unknown operator '{cond['op']}'")
    if cond["op"] not in ("exists", "not_exists", "truthy", "falsy") and "value" not in cond and "fact_ref" not in cond:
        raise PolicyError(f"Operator '{cond['op']}' needs 'value' or 'fact_ref'")


def lookup(facts: Mapping[str, Any], path: str) -> Any:
    """Dotted-path lookup that works for nested dicts and flat dotted keys."""
    if path in facts:
        return facts[path]
    current: Any = facts
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "exists":
        return left is not _MISSING and left is not None
    if op == "not_exists":
        return left is _MISSING or left is None
    if op == "truthy":
        return left is not _MISSING and bool(left)
    if op == "falsy":
        return left is _MISSING or not bool(left)
    if left is _MISSING or right is _MISSING:
        # Missing facts never satisfy a comparison; rules must not match on absent data by accident.
        return op == "ne" and left is _MISSING and right is not _MISSING
    try:
        if op == "eq":
            return bool(left == right)
        if op == "ne":
            return bool(left != right)
        if op == "lt":
            return bool(left < right)
        if op == "lte":
            return bool(left <= right)
        if op == "gt":
            return bool(left > right)
        if op == "gte":
            return bool(left >= right)
        if op == "in":
            return left in right if isinstance(right, list | tuple | set | frozenset | str) else False
        if op == "not_in":
            return left not in right if isinstance(right, list | tuple | set | frozenset | str) else True
        if op == "contains":
            return right in left if isinstance(left, list | tuple | set | frozenset | str | dict) else False
    except TypeError:
        return False
    return False


def evaluate_condition(cond: Mapping[str, Any] | None, facts: Mapping[str, Any]) -> bool:
    if cond is None:
        return True
    if "all" in cond:
        return all(evaluate_condition(c, facts) for c in cond["all"])
    if "any" in cond:
        return any(evaluate_condition(c, facts) for c in cond["any"])
    if "not" in cond:
        return not evaluate_condition(cond["not"], facts)
    left = lookup(facts, str(cond["fact"]))
    right = lookup(facts, str(cond["fact_ref"])) if "fact_ref" in cond else cond.get("value")
    return _compare(str(cond["op"]), left, right)


def _action_matches(patterns: Sequence[str], action: str) -> bool:
    return any(p == "*" or p == action or fnmatch.fnmatchcase(action, p) for p in patterns)


# ---------------------------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------------------------

_PRECEDENCE = {PolicyDecision.ALLOW: 0, PolicyDecision.REQUIRE_APPROVAL: 1, PolicyDecision.DENY: 2}


class PolicyEngine:
    """Evaluate an action against the baseline plus any organization policy sets."""

    def __init__(self, policy_sets: Sequence[PolicySet] | None = None, *, include_baseline: bool = True) -> None:
        sets = list(policy_sets or [])
        if include_baseline:
            sets.insert(0, BASELINE_POLICY)
        self.policy_sets = sets

    def evaluate(self, action: str, facts: Mapping[str, Any]) -> PolicyResult:
        result = PolicyResult(action=action, decision=PolicyDecision.ALLOW)
        enriched = dict(facts)
        enriched.setdefault("action", action)
        sensitive = False
        for policy in self.policy_sets:
            result.evaluated_policies.append(
                {"key": policy.key, "version": policy.version, "fingerprint": policy.fingerprint[:16]}
            )
            if _action_matches(policy.sensitive_actions, action):
                sensitive = True
            for rule in policy.rules:
                if not _action_matches(rule.actions, action):
                    continue
                if not evaluate_condition(rule.when, enriched):
                    continue
                result.matched.append(
                    MatchedRule(
                        policy_key=policy.key,
                        policy_version=policy.version,
                        rule_id=rule.id,
                        effect=rule.effect,
                        reason=rule.reason or rule.description,
                        approval_kind=str(rule.approval_kind) if rule.approval_kind else None,
                    )
                )
        decisive = [m for m in result.matched if m.effect != PolicyDecision.ALLOW]
        if decisive:
            top = max(decisive, key=lambda m: _PRECEDENCE[m.effect])
            result.decision = top.effect
            for m in decisive:
                if m.effect == top.effect:
                    result.reasons.append(f"[{m.policy_key}@{m.policy_version}:{m.rule_id}] {m.reason}".strip())
                    if m.approval_kind and m.approval_kind not in result.approval_kinds:
                        result.approval_kinds.append(m.approval_kind)
        elif sensitive and not any(m.effect == PolicyDecision.ALLOW for m in result.matched):
            result.decision = PolicyDecision.REQUIRE_APPROVAL
            result.reasons.append(f"'{action}' is a sensitive action and no policy explicitly allows it")
            result.approval_kinds.append(str(ApprovalKind.GENERIC))
        if result.decision == PolicyDecision.REQUIRE_APPROVAL and not result.approval_kinds:
            result.approval_kinds.append(str(ApprovalKind.GENERIC))
        return result


def parse_policy_document(key: str, version: str, document: Mapping[str, Any]) -> PolicySet:
    """Validate a stored policy document (raises ``PolicyError`` with a readable message)."""
    try:
        return PolicySet.model_validate({"key": key, "version": version, **dict(document)})
    except PolicyError:
        raise
    except Exception as exc:
        raise PolicyError(f"Invalid policy document: {exc}") from exc


# ---------------------------------------------------------------------------------------------
# Baseline (immutable fail-safe defaults)
# ---------------------------------------------------------------------------------------------

BASELINE_VERSION = "2026.09.1"

BASELINE_POLICY = PolicySet(
    key="system.baseline",
    version=BASELINE_VERSION,
    description="Platform fail-safe defaults. Cannot be disabled or relaxed by organization policies.",
    sensitive_actions=["integration.production.*", "secrets.*"],
    rules=[
        PolicyRule(
            id="no-agent-autonomy-change",
            actions=["autonomy.change", "permission.grant", "role.assign"],
            when={"fact": "actor.type", "op": "in", "value": ["agent", "workflow", "system"]},
            effect=PolicyDecision.DENY,
            reason="Agents and workflows can never change autonomy levels or grant permissions.",
        ),
        PolicyRule(
            id="discovery-approval-human-only",
            actions=["discovery.approve", "discovery.publish"],
            when={"fact": "actor.type", "op": "not_in", "value": ["user"]},
            effect=PolicyDecision.DENY,
            reason="Discoveries can only be approved or published by a human reviewer.",
        ),
        PolicyRule(
            id="discovery-approval-separation-of-duties",
            actions=["discovery.approve"],
            when={"fact": "actor.id", "op": "eq", "fact_ref": "discovery.requested_by"},
            effect=PolicyDecision.DENY,
            reason="The requester of a discovery approval cannot approve it (separation of duties).",
        ),
        PolicyRule(
            id="discovery-requires-verification",
            actions=["discovery.approve"],
            when={"fact": "claim.status", "op": "ne", "value": "verified"},
            effect=PolicyDecision.DENY,
            reason="A discovery cannot be approved unless its claim is VERIFIED.",
        ),
        PolicyRule(
            id="strategy-promotion-below-l4",
            actions=["strategy.promote"],
            when={
                "all": [
                    {"fact": "actor.type", "op": "in", "value": ["agent", "workflow", "system"]},
                    {"fact": "mission.autonomy_rank", "op": "lt", "value": 4},
                ]
            },
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.STRATEGY_PROMOTION,
            reason="Autonomous strategy promotion requires mission autonomy L4 or higher.",
        ),
        PolicyRule(
            id="strategy-auto-promotion-opt-in",
            actions=["strategy.promote"],
            when={
                "all": [
                    {"fact": "actor.type", "op": "in", "value": ["agent", "workflow", "system"]},
                    {"fact": "org.allow_auto_strategy_promotion", "op": "falsy"},
                ]
            },
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.STRATEGY_PROMOTION,
            reason="Strategy auto-promotion is disabled unless the organization explicitly enables it.",
        ),
        PolicyRule(
            id="execution-network-denied-by-default",
            actions=["experiment.execute", "tool.call", "reproduction.run"],
            when={"fact": "execution.network", "op": "not_in", "value": [None, "none"]},
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.EXTERNAL_NETWORK,
            reason="Sandboxed execution has no network access unless an egress allowlist is approved.",
        ),
        PolicyRule(
            id="execution-secrets-require-approval",
            actions=["experiment.execute", "reproduction.run", "tool.call"],
            when={"fact": "execution.secrets_requested", "op": "truthy"},
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.SECRET_ACCESS,
            reason="Secrets are never injected into generated code without explicit approval.",
        ),
        PolicyRule(
            id="high-risk-tool-approval",
            actions=["tool.call"],
            when={"fact": "tool.risk", "op": "in", "value": ["high", "critical"]},
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.HIGH_RISK_TOOL,
            reason="High-risk tools require human approval.",
        ),
        PolicyRule(
            id="unregistered-mcp-denied",
            actions=["tool.call"],
            when={
                "all": [
                    {"fact": "tool.source", "op": "eq", "value": "mcp"},
                    {"fact": "tool.registered", "op": "falsy"},
                ]
            },
            effect=PolicyDecision.DENY,
            reason="MCP servers and tools must be explicitly registered and approved.",
        ),
        PolicyRule(
            id="budget-exceeded",
            actions=["experiment.execute", "research.run", "model.call", "reproduction.run", "tool.call"],
            when={"fact": "budget.would_exceed", "op": "truthy"},
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.BUDGET_INCREASE,
            reason="The estimated cost would exceed the remaining mission/project budget.",
        ),
        PolicyRule(
            id="expensive-compute",
            actions=["experiment.execute", "reproduction.run"],
            when={
                "any": [
                    {"fact": "execution.gpu_count", "op": "gt", "value": 0},
                    {"fact": "estimated_cost", "op": "gt", "fact_ref": "org.expensive_compute_threshold_usd"},
                ]
            },
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.EXPENSIVE_COMPUTE,
            reason="GPU or expensive compute requires human approval.",
        ),
        PolicyRule(
            id="production-integrations-denied",
            actions=["tool.call", "experiment.execute"],
            when={"fact": "target.environment", "op": "eq", "value": "production"},
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.PRODUCTION_INTEGRATION,
            reason="Access to production systems requires explicit approval.",
        ),
        PolicyRule(
            id="publication-human-only",
            actions=["discovery.publish", "report.publish"],
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.PUBLICATION,
            reason="Publication always requires human approval.",
        ),
        PolicyRule(
            id="evolution-cannot-escalate",
            actions=["strategy.create", "strategy.mutate", "strategy.promote"],
            when={"fact": "strategy.governance_escalation", "op": "truthy"},
            effect=PolicyDecision.DENY,
            reason="Evolved strategies may not gain tools, network, secrets or autonomy.",
        ),
        PolicyRule(
            id="memory-promotion-review",
            actions=["memory.promote"],
            when={
                "any": [
                    {"fact": "memory.injection_score", "op": "gte", "value": 0.5},
                    {"fact": "actor.type", "op": "not_in", "value": ["user"]},
                ]
            },
            effect=PolicyDecision.REQUIRE_APPROVAL,
            approval_kind=ApprovalKind.MEMORY_PROMOTION,
            reason="Automation-originated or suspicious content needs review before entering durable memory.",
        ),
    ],
)
