"""Deterministic rule engine for custom policy rules and output constraints.

A rule is a small, safe boolean expression over an evaluation ``fact`` dict. No code execution: only the
declared operators are supported. Used by ``CustomRuleEvaluator`` and ``OutputConstraintEvaluator`` so
governance rules like "IF human_approval_required AND NOT agent_action_approval THEN violation" need no LLM.
"""

from __future__ import annotations

import re
from typing import Any

OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "not_contains",
    "matches",
    "not_matches",
    "in",
    "not_in",
    "exists",
    "not_exists",
}
COMBINATORS = {"all", "any", "not"}


class RuleError(ValueError):
    """A rule condition is malformed."""


def _get(fact: dict[str, Any], path: str) -> Any:
    cur: Any = fact
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _compare(op: str, left: Any, right: Any) -> bool:
    try:
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op in ("gt", "gte", "lt", "lte"):
            if left is None or right is None:
                return False
            lf, rf = float(left), float(right)
            return {"gt": lf > rf, "gte": lf >= rf, "lt": lf < rf, "lte": lf <= rf}[op]
        if op == "contains":
            return right is not None and str(right).lower() in str(left or "").lower()
        if op == "not_contains":
            return right is None or str(right).lower() not in str(left or "").lower()
        if op == "matches":
            return bool(re.search(str(right), str(left or ""), re.I))
        if op == "not_matches":
            return not re.search(str(right), str(left or ""), re.I)
        if op == "in":
            return left in (right or [])
        if op == "not_in":
            return left not in (right or [])
        if op == "exists":
            return left is not None
        if op == "not_exists":
            return left is None
    except (ValueError, TypeError):
        return False
    raise RuleError(f"unsupported operator '{op}'")


def evaluate_condition(condition: dict[str, Any], fact: dict[str, Any], depth: int = 0) -> bool:
    if depth > 10:
        raise RuleError("condition nesting too deep")
    if not isinstance(condition, dict):
        raise RuleError("condition must be an object")
    combinator = next((c for c in COMBINATORS if c in condition), None)
    if combinator == "all":
        return all(evaluate_condition(c, fact, depth + 1) for c in condition["all"])
    if combinator == "any":
        return any(evaluate_condition(c, fact, depth + 1) for c in condition["any"])
    if combinator == "not":
        return not evaluate_condition(condition["not"], fact, depth + 1)
    field = condition.get("field")
    op = condition.get("op", "eq")
    if not field or op not in OPERATORS:
        raise RuleError(f"clause needs a 'field' and a valid 'op' (got field={field!r}, op={op!r})")
    return _compare(op, _get(fact, field), condition.get("value"))


def validate_condition(condition: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems (empty if the condition is well-formed)."""
    problems: list[str] = []
    try:
        evaluate_condition(condition, {}, 0)
    except RuleError as exc:
        problems.append(str(exc))
    return problems
