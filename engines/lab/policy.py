"""Versioned governance policy DSL, fail-safe baseline and deterministic evaluator (pure).

Rules are JSON documents validated by strict Pydantic models::

    {
      "id": "org.no-gpu-for-agents",
      "description": "Agents may not request GPUs",
      "action": "execution.*",                 # exact action, "*" or "<namespace>.*"
      "when": {"all": [{"field": "actor_kind", "op": "in", "value": ["agent", "workflow"]},
                        {"field": "gpu_count", "op": "gt", "value": 0}]},
      "effect": "deny",                        # allow | deny | require_approval
      "reason": "GPU jobs must be started by a human",
      "approver_permission": null,             # only for require_approval
      "obligations": {},                       # constraints attached to allow / require_approval
      "priority": 100
    }

Conditions: ``{"all": [...]}``, ``{"any": [...]}``, ``{"not": <condition>}``,
``{"field": "<context key>", "op": <op>, "value": ... | "value_from": "<context key>"}``,
``{"autonomy_below": "L3_AUTOMATED_EXECUTION"}``, ``{"autonomy_at_least": "L2"}`` and
``{"risk_at_least": "HIGH", "field": "tool_risk"}`` (``field`` defaults to ``autonomy_level`` /
``risk_level``).

Semantics (all deterministic — the same inputs always produce the same decision):

* A comparison against a missing (or ``None``) field — or a missing ``value_from`` key — is **false**.
  That is fail-safe for ``allow`` rules; restrictive rules are written defensively (see the baseline).
  A missing or unknown autonomy level is treated as ``L0_ASSISTED`` (the least autonomy): it is *below*
  every other level and never *at least* anything above L0.
* Combining: ``deny`` > ``require_approval`` > ``allow``. The platform BASELINE is always evaluated first
  and organization rules can only ADD restrictions — an organization ``allow`` never overrides a
  baseline ``deny``/``require_approval``.
* If nothing matches, :data:`DEFAULT_EFFECT` applies. A restrictive default can only be relaxed by an
  organization ``allow`` rule that names the action *exactly* (a wildcard allow never relaxes it).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    field_validator,
    model_validator,
)

from engines.lab.states import AUTONOMY_RANK, RISK_RANK, AutonomyLevel, RiskLevel

Effect = Literal["allow", "deny", "require_approval"]
Operator = Literal[
    "eq",
    "ne",
    "lt",
    "lte",
    "gt",
    "gte",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "exists",
    "not_exists",
    "subset_of",
    "matches",
]

BASELINE_VERSION = "baseline-2026.09"
BASELINE_PREFIX = "baseline."
EFFECT_RANK: dict[str, int] = {"allow": 0, "require_approval": 1, "deny": 2}

# -------------------------------------------------------------------------------------------------
# Action catalog
# -------------------------------------------------------------------------------------------------
ACTIONS: tuple[str, ...] = (
    "execution.submit",
    "experiment.execute",
    "tool.invoke",
    "mcp.invoke",
    "research.deep_research",
    "llm.external_processing",
    "strategy.promote",
    "strategy.auto_promote",
    "discovery.approve",
    "discovery.publish",
    "memory.promote",
    "mission.start",
    "mission.autonomy_change",
    "integration.production",
)
ACTION_NAMESPACES: frozenset[str] = frozenset(a.split(".", 1)[0] for a in ACTIONS)

DEFAULT_EFFECT: dict[str, Effect] = {
    "discovery.approve": "require_approval",
    "discovery.publish": "require_approval",
    "strategy.promote": "require_approval",
    "integration.production": "require_approval",
    "memory.promote": "require_approval",
    "strategy.auto_promote": "deny",
}

# Context keys understood by the evaluator. Callers may pass custom attributes prefixed with ``x_``.
CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        # supplied by callers (contract §4.5)
        "autonomy_level",
        "risk_level",
        "estimated_cost_usd",
        "budget_remaining_usd",
        "network_mode",
        "egress_hosts",
        "gpu_count",
        "secrets_requested",
        "tool_name",
        "tool_risk",
        "mcp_registered",
        "actor_kind",
        "is_human",
        "external_provider",
        "production",
        # enriched by the governance service
        "org_egress_allowlist",
        "data_processing_consent",
        "ceiling_rank",
        "requested_rank",
        "current_rank",
        "requested_autonomy_level",
        # action specific
        "source_trust",
        "scope",
        "tool_source",
        "provider",
        "model",
        "image",
        "timeout_seconds",
        "cpu",
        "memory_mb",
        "purpose",
        "mission_status",
        "project_visibility",
        "subject_type",
        "domain",
    }
)
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{1,79}$")
_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_PERMISSION_RE = re.compile(r"^[a-z_]+:[a-z_]+$")
# Heuristic guard against catastrophic backtracking: a quantified group that contains a quantifier.
_NESTED_QUANTIFIER_RE = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")
MAX_PATTERN_LENGTH = 200
MAX_MATCH_SUBJECT = 4096
MAX_CONDITION_DEPTH = 8
MAX_RULES = 200
MAX_OBLIGATIONS_BYTES = 4096

_LEVEL_SHORTHAND = {level.value.split("_", 1)[0]: level.value for level in AutonomyLevel}


def _normalize_level(value: Any) -> Any:
    if isinstance(value, str):
        key = value.strip()
        return _LEVEL_SHORTHAND.get(key.upper(), key)
    return value


def is_known_field(name: str) -> bool:
    return name in CONTEXT_KEYS or name.startswith("x_")


def _check_field_name(value: str) -> str:
    if not _FIELD_RE.match(value):
        raise ValueError(f"'{value}' is not a valid context key (lower_snake_case, max 64 chars)")
    if not is_known_field(value):
        raise ValueError(
            f"unknown context key '{value}'. Use one of: {', '.join(sorted(CONTEXT_KEYS))} (or an 'x_' custom key)"
        )
    return value


def is_valid_action_name(action: str) -> bool:
    """``namespace.verb`` in lower_snake_case (catalog actions and service-defined approval actions)."""
    return len(action) <= 64 and _ACTION_RE.match(action) is not None


def action_matches(pattern: str, action: str) -> bool:
    """``*`` matches everything, ``ns.*`` matches every action in namespace ``ns``, else exact."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return action.startswith(pattern[:-1])
    return pattern == action


# -------------------------------------------------------------------------------------------------
# Condition models
# -------------------------------------------------------------------------------------------------
_STRICT = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class FieldCondition(BaseModel):
    model_config = _STRICT

    field: str
    op: Operator
    value: Any = None
    value_from: str | None = None

    @field_validator("field", "value_from")
    @classmethod
    def _field_name(cls, v: str | None) -> str | None:
        return None if v is None else _check_field_name(v)

    @model_validator(mode="after")
    def _operand(self) -> FieldCondition:
        has_value = "value" in self.model_fields_set
        has_from = self.value_from is not None
        if self.op in ("exists", "not_exists"):
            if has_value or has_from:
                raise ValueError(f"operator '{self.op}' takes no 'value' or 'value_from'")
            return self
        if has_value == has_from:
            raise ValueError(f"operator '{self.op}' needs exactly one of 'value' or 'value_from'")
        if not has_value:
            return self
        value = self.value
        if self.op in ("lt", "lte", "gt", "gte") and _number(value) is None:
            raise ValueError(f"operator '{self.op}' needs a numeric 'value'")
        if self.op in ("in", "not_in", "subset_of") and not isinstance(value, list):
            raise ValueError(f"operator '{self.op}' needs a list 'value'")
        if self.op == "matches":
            if not isinstance(value, str) or not value:
                raise ValueError("operator 'matches' needs a non-empty regular expression string")
            if len(value) > MAX_PATTERN_LENGTH:
                raise ValueError(f"regular expression longer than {MAX_PATTERN_LENGTH} characters")
            if _NESTED_QUANTIFIER_RE.search(value):
                raise ValueError("regular expression has nested quantifiers (catastrophic backtracking risk)")
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regular expression: {exc}") from exc
        _ensure_json(value, "value")
        return self


class AllOf(BaseModel):
    model_config = _STRICT

    all_: list[Condition] = Field(alias="all", min_length=1, max_length=50)


class AnyOf(BaseModel):
    model_config = _STRICT

    any_: list[Condition] = Field(alias="any", min_length=1, max_length=50)


class NotOf(BaseModel):
    model_config = _STRICT

    not_: Condition = Field(alias="not")


class AutonomyBelow(BaseModel):
    model_config = _STRICT

    autonomy_below: AutonomyLevel
    field: str = "autonomy_level"

    @field_validator("autonomy_below", mode="before")
    @classmethod
    def _level(cls, v: Any) -> Any:
        return _normalize_level(v)

    @field_validator("field")
    @classmethod
    def _field_name(cls, v: str) -> str:
        return _check_field_name(v)


class AutonomyAtLeast(BaseModel):
    model_config = _STRICT

    autonomy_at_least: AutonomyLevel
    field: str = "autonomy_level"

    @field_validator("autonomy_at_least", mode="before")
    @classmethod
    def _level(cls, v: Any) -> Any:
        return _normalize_level(v)

    @field_validator("field")
    @classmethod
    def _field_name(cls, v: str) -> str:
        return _check_field_name(v)


class RiskAtLeast(BaseModel):
    model_config = _STRICT

    risk_at_least: RiskLevel
    field: str = "risk_level"

    @field_validator("risk_at_least", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("field")
    @classmethod
    def _field_name(cls, v: str) -> str:
        return _check_field_name(v)


_CONDITION_TAGS = ("all", "any", "not", "autonomy_below", "autonomy_at_least", "risk_at_least")
_MODEL_TAGS: dict[type[BaseModel], str] = {
    AllOf: "all",
    AnyOf: "any",
    NotOf: "not",
    AutonomyBelow: "autonomy_below",
    AutonomyAtLeast: "autonomy_at_least",
    RiskAtLeast: "risk_at_least",
    FieldCondition: "field",
}


def _condition_tag(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for tag in _CONDITION_TAGS:
            if tag in value:
                return tag
        return "field" if "field" in value or "op" in value else None
    return _MODEL_TAGS.get(type(value))


Condition = Annotated[
    Annotated[AllOf, Tag("all")]
    | Annotated[AnyOf, Tag("any")]
    | Annotated[NotOf, Tag("not")]
    | Annotated[AutonomyBelow, Tag("autonomy_below")]
    | Annotated[AutonomyAtLeast, Tag("autonomy_at_least")]
    | Annotated[RiskAtLeast, Tag("risk_at_least")]
    | Annotated[FieldCondition, Tag("field")],
    Discriminator(
        _condition_tag,
        custom_error_type="invalid_condition",
        custom_error_message=(
            "A condition must be one of {all: [...]}, {any: [...]}, {not: ...}, {field, op, value|value_from}, "
            "{autonomy_below}, {autonomy_at_least} or {risk_at_least}"
        ),
        custom_error_context={"tags": ", ".join((*_CONDITION_TAGS, "field"))},
    ),
]

for _model in (AllOf, AnyOf, NotOf):
    _model.model_rebuild()


class Rule(BaseModel):
    """One policy rule (see module docstring)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str = Field(default="", max_length=500)
    action: str
    when: Condition | None = None
    effect: Effect
    reason: str = Field(min_length=1, max_length=500)
    approver_permission: str | None = None
    obligations: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=100, ge=0, le=10_000)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _RULE_ID_RE.match(v):
            raise ValueError("rule id must be 2-80 chars of lowercase letters, digits, '.', '_' or '-'")
        return v

    @field_validator("action")
    @classmethod
    def _action(cls, v: str) -> str:
        if v == "*":
            return v
        if v.endswith(".*"):
            namespace = v[:-2]
            if namespace not in ACTION_NAMESPACES:
                raise ValueError(
                    f"unknown action namespace '{namespace}'. Known: {', '.join(sorted(ACTION_NAMESPACES))}"
                )
            return v
        if v not in ACTIONS:
            raise ValueError(f"unknown action '{v}'. Known actions: {', '.join(ACTIONS)} (or '*' / '<namespace>.*')")
        return v

    @field_validator("approver_permission")
    @classmethod
    def _permission(cls, v: str | None) -> str | None:
        if v is not None and not _PERMISSION_RE.match(v):
            raise ValueError("approver_permission must look like 'resource:verb' (e.g. 'approval:decide')")
        return v

    @field_validator("obligations")
    @classmethod
    def _obligations(cls, v: dict[str, Any]) -> dict[str, Any]:
        encoded = _ensure_json(v, "obligations")
        if len(encoded) > MAX_OBLIGATIONS_BYTES:
            raise ValueError(f"obligations must serialize to at most {MAX_OBLIGATIONS_BYTES} bytes")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> Rule:
        if self.approver_permission and self.effect != "require_approval":
            raise ValueError("approver_permission is only valid for effect 'require_approval'")
        if self.when is not None and condition_depth(self.when) > MAX_CONDITION_DEPTH:
            raise ValueError(f"conditions may be nested at most {MAX_CONDITION_DEPTH} levels deep")
        return self

    def to_document(self) -> dict[str, Any]:
        """Canonical JSON document (aliases, no defaults dropped) for storage and hashing."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


# -------------------------------------------------------------------------------------------------
# Policy sets & decisions
# -------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PolicySet:
    """A named, versioned list of rules (the baseline, an organization or a project policy version)."""

    key: str
    version: str
    rules: tuple[Rule, ...]
    scope: Literal["baseline", "organization", "project", "mission", "inline"] = "organization"

    @property
    def ref(self) -> str:
        return f"{self.key}@{self.version}"


@dataclass(frozen=True)
class Decision:
    effect: Effect
    reasons: tuple[str, ...]
    matched_rules: tuple[str, ...]
    policy_versions: tuple[str, ...]
    obligations: dict[str, Any] = field(default_factory=dict)
    approver_permission: str | None = None
    default_applied: bool = False
    action: str = ""

    @property
    def allowed(self) -> bool:
        return self.effect == "allow"

    @property
    def denied(self) -> bool:
        return self.effect == "deny"

    @property
    def requires_approval(self) -> bool:
        return self.effect == "require_approval"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "effect": self.effect,
            "reasons": list(self.reasons),
            "matched_rules": list(self.matched_rules),
            "policy_versions": list(self.policy_versions),
            "obligations": dict(self.obligations),
            "approver_permission": self.approver_permission,
            "default_applied": self.default_applied,
        }


class PolicyValidationError(ValueError):
    """Raised by :func:`parse_rules` with human-friendly messages in ``errors``."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


# -------------------------------------------------------------------------------------------------
# Validation
# -------------------------------------------------------------------------------------------------
def condition_depth(condition: Any) -> int:
    if isinstance(condition, AllOf):
        return 1 + max(condition_depth(c) for c in condition.all_)
    if isinstance(condition, AnyOf):
        return 1 + max(condition_depth(c) for c in condition.any_)
    if isinstance(condition, NotOf):
        return 1 + condition_depth(condition.not_)
    return 1


def _format_loc(loc: Sequence[Any]) -> str:
    """Render a Pydantic error location as ``when.all[0].op`` (discriminator tags removed)."""
    parts: list[str] = []
    expecting_tag = False
    previous: Any = None
    for element in loc:
        if expecting_tag and isinstance(element, str) and (element in _CONDITION_TAGS or element == "field"):
            expecting_tag = False
            continue  # discriminator tag, not a user-visible key
        expecting_tag = False
        if isinstance(element, int):
            parts.append(f"[{element}]")
            expecting_tag = previous in ("all", "any")
        else:
            parts.append(("." if parts else "") + str(element))
            expecting_tag = element in ("when", "not")
        previous = element
    return "".join(parts) or "rule"


def _humanize(exc: ValidationError) -> list[str]:
    messages = []
    for err in exc.errors(include_url=False):
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        messages.append(f"{_format_loc(err.get('loc', ()))}: {msg}")
    return messages


def _label(index: int, raw: Any) -> str:
    rid = raw.get("id") if isinstance(raw, Mapping) else getattr(raw, "id", None)
    return f"rule #{index + 1}" + (f" ('{rid}')" if isinstance(rid, str) and rid else "")


def parse_rules(rules: Iterable[Mapping[str, Any] | Rule], *, allow_reserved: bool = False) -> list[Rule]:
    """Validate and parse rules, raising :class:`PolicyValidationError` with every problem found."""
    items = list(rules)
    errors: list[str] = []
    parsed: list[Rule] = []
    if len(items) > MAX_RULES:
        errors.append(f"a policy may contain at most {MAX_RULES} rules (got {len(items)})")
    seen: set[str] = set()
    for index, raw in enumerate(items):
        label = _label(index, raw)
        try:
            rule = raw if isinstance(raw, Rule) else Rule.model_validate(raw)
        except ValidationError as exc:
            errors.extend(f"{label}: {m}" for m in _humanize(exc))
            continue
        if not allow_reserved and rule.id.startswith(BASELINE_PREFIX):
            errors.append(f"{label}: rule ids starting with '{BASELINE_PREFIX}' are reserved for the platform baseline")
        if rule.id in seen:
            errors.append(f"{label}: duplicate rule id '{rule.id}'")
        seen.add(rule.id)
        parsed.append(rule)
    if errors:
        raise PolicyValidationError(errors)
    return parsed


def validate_rules(rules: Iterable[Mapping[str, Any] | Rule], *, allow_reserved: bool = False) -> list[str]:
    """Human-friendly validation errors (empty list = valid)."""
    try:
        parse_rules(rules, allow_reserved=allow_reserved)
    except PolicyValidationError as exc:
        return exc.errors
    return []


# -------------------------------------------------------------------------------------------------
# Evaluation
# -------------------------------------------------------------------------------------------------
def _ensure_json(value: Any, what: str) -> str:
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be JSON-serializable") from exc


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int | float):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    return None


def _equals(left: Any, right: Any) -> bool:
    a, b = _number(left), _number(right)
    if a is not None and b is not None:
        return a == b
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, tuple | set | frozenset):
        left = list(left)
    if isinstance(right, tuple | set | frozenset):
        right = list(right)
    try:
        return bool(left == right)
    except Exception:  # pragma: no cover - exotic objects
        return False


def _member(item: Any, collection: Sequence[Any]) -> bool:
    return any(_equals(item, candidate) for candidate in collection)


def _host_member(item: Any, allowlist: Sequence[Any]) -> bool:
    """Membership for :func:`subset_of`: exact match, case-insensitive for strings; an allowlist entry
    starting with ``.`` matches that domain and every subdomain (``.example.org``)."""
    if not isinstance(item, str):
        return _member(item, allowlist)
    needle = item.strip().lower()
    for entry in allowlist:
        if not isinstance(entry, str):
            continue
        candidate = entry.strip().lower()
        if candidate.startswith("."):
            if needle.endswith(candidate) or needle == candidate[1:]:
                return True
        elif needle == candidate:
            return True
    return False


def _as_list(value: Any) -> list[Any] | None:
    if isinstance(value, list | tuple | set | frozenset):
        return list(value)
    return None


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _missing(context: Mapping[str, Any], key: str) -> bool:
    return context.get(key) is None


def _eval_field(cond: FieldCondition, context: Mapping[str, Any]) -> bool:
    if cond.op == "exists":
        return not _missing(context, cond.field)
    if cond.op == "not_exists":
        return _missing(context, cond.field)
    if _missing(context, cond.field):
        return False
    left = context[cond.field]
    if cond.value_from is not None:
        if _missing(context, cond.value_from):
            return False
        right = context[cond.value_from]
    else:
        right = cond.value
    op = cond.op
    if op == "eq":
        return _equals(left, right)
    if op == "ne":
        return not _equals(left, right)
    if op in ("lt", "lte", "gt", "gte"):
        a, b = _number(left), _number(right)
        if a is None or b is None:
            return False
        return {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]
    if op in ("in", "not_in"):
        options = _as_list(right)
        if options is None:
            return False
        return _member(left, options) if op == "in" else not _member(left, options)
    if op in ("contains", "not_contains"):
        if isinstance(left, str):
            if not isinstance(right, str):
                return False
            found = right in left
        elif isinstance(left, Mapping):
            found = right in left if isinstance(right, str) else False
        else:
            items = _as_list(left)
            if items is None:
                return False
            found = _member(right, items)
        return found if op == "contains" else not found
    if op == "subset_of":
        items = [left] if isinstance(left, str) else _as_list(left)
        allowlist = _as_list(right)
        if items is None or allowlist is None:
            return False
        return all(_host_member(item, allowlist) for item in items)
    if op == "matches":
        if not isinstance(left, str) or not isinstance(right, str):
            return False
        try:
            return _compiled(right).search(left[:MAX_MATCH_SUBJECT]) is not None
        except re.error:
            return False
    return False  # pragma: no cover - exhaustive Literal


def _autonomy_rank_of(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    return AUTONOMY_RANK.get(_normalize_level(value))


def evaluate_condition(condition: Any, context: Mapping[str, Any]) -> bool:
    """Evaluate one parsed condition against a context mapping."""
    if isinstance(condition, AllOf):
        return all(evaluate_condition(c, context) for c in condition.all_)
    if isinstance(condition, AnyOf):
        return any(evaluate_condition(c, context) for c in condition.any_)
    if isinstance(condition, NotOf):
        return not evaluate_condition(condition.not_, context)
    if isinstance(condition, FieldCondition):
        return _eval_field(condition, context)
    if isinstance(condition, AutonomyBelow):
        rank = _autonomy_rank_of(context.get(condition.field))
        return (rank if rank is not None else 0) < AUTONOMY_RANK[condition.autonomy_below]
    if isinstance(condition, AutonomyAtLeast):
        rank = _autonomy_rank_of(context.get(condition.field))
        return rank is not None and rank >= AUTONOMY_RANK[condition.autonomy_at_least]
    if isinstance(condition, RiskAtLeast):
        value = context.get(condition.field)
        if not isinstance(value, str):
            return False
        rank = RISK_RANK.get(value.strip().upper())
        return rank is not None and rank >= RISK_RANK[condition.risk_at_least]
    raise TypeError(f"Unsupported condition type: {type(condition).__name__}")


def rule_matches(rule: Rule, action: str, context: Mapping[str, Any]) -> bool:
    if not action_matches(rule.action, action):
        return False
    return rule.when is None or evaluate_condition(rule.when, context)


def _coerce_sets(
    baseline: PolicySet | Sequence[Rule], org_rules: Sequence[PolicySet | Rule]
) -> tuple[PolicySet, list[PolicySet]]:
    base = baseline if isinstance(baseline, PolicySet) else PolicySet("baseline", BASELINE_VERSION, tuple(baseline))
    sets: list[PolicySet] = []
    loose: list[Rule] = []
    for item in org_rules:
        if isinstance(item, PolicySet):
            sets.append(item)
        else:
            loose.append(item)
    if loose:
        sets.append(PolicySet("inline", "0", tuple(loose), scope="inline"))
    return base, sets


def evaluate(
    action: str,
    context: Mapping[str, Any] | None = None,
    baseline: PolicySet | Sequence[Rule] | None = None,
    org_rules: Sequence[PolicySet | Rule] = (),
) -> Decision:
    """Deterministically evaluate ``action`` in ``context`` against the baseline and organization rules."""
    ctx: Mapping[str, Any] = context or {}
    base, sets = _coerce_sets(BASELINE if baseline is None else baseline, org_rules)
    default = DEFAULT_EFFECT.get(action, "allow")

    matched: list[tuple[int, int, Rule]] = []  # (tier: 0 = baseline, 1 = org, -priority, rule)
    for tier, policy in ((0, base), *((1, s) for s in sets)):
        for rule in sorted(policy.rules, key=lambda r: (-r.priority, r.id)):
            if rule_matches(rule, action, ctx):
                matched.append((tier, -rule.priority, rule))
    matched.sort(key=lambda m: (m[0], m[1], m[2].id))
    versions = (base.ref, *(s.ref for s in sets))

    if not matched:
        return Decision(
            effect=default,
            reasons=(f"No policy rule matched; the default for '{action}' is {default}",),
            matched_rules=(),
            policy_versions=versions,
            default_applied=True,
            action=action,
        )

    effect: Effect = max((m[2].effect for m in matched), key=lambda e: EFFECT_RANK[e])
    default_applied = False
    reasons = [m[2].reason for m in matched if m[2].effect == effect]
    if effect == "allow" and default != "allow":
        exact_allow = any(m[0] == 1 and m[2].action == action for m in matched)
        if not exact_allow:
            effect = default
            default_applied = True
            reasons = [
                f"Only wildcard allow rules matched; the default for '{action}' ({default}) "
                "can only be relaxed by a rule naming the action exactly"
            ]

    approver = next(
        (m[2].approver_permission for m in matched if m[2].effect == effect and m[2].approver_permission), None
    )
    obligations: dict[str, Any] = {}
    if effect != "deny":
        # Later entries win: org rules by ascending priority, then the baseline (never overridable).
        for m in sorted(matched, key=lambda m: (-m[0], -m[1])):
            obligations.update(m[2].obligations)
    return Decision(
        effect=effect,
        reasons=tuple(dict.fromkeys(reasons)),
        matched_rules=tuple(m[2].id for m in matched),
        policy_versions=versions,
        obligations=obligations,
        approver_permission=approver if effect == "require_approval" else None,
        default_applied=default_applied,
        action=action,
    )


# -------------------------------------------------------------------------------------------------
# Baseline (fail-safe platform defaults; immutable; always evaluated first)
# -------------------------------------------------------------------------------------------------
_NOT_HUMAN: dict[str, Any] = {
    "any": [{"field": "is_human", "op": "not_exists"}, {"field": "is_human", "op": "ne", "value": True}]
}
_NOT_USER: dict[str, Any] = {
    "any": [{"field": "actor_kind", "op": "not_exists"}, {"field": "actor_kind", "op": "ne", "value": "user"}]
}
_NON_EMPTY_SECRETS: dict[str, Any] = {"field": "secrets_requested", "op": "not_in", "value": [[], {}, "", False, 0]}


def _execution_rules(action: str) -> list[dict[str, Any]]:
    prefix = f"{BASELINE_PREFIX}{action}"
    return [
        {
            "id": f"{prefix}.network_requires_approval",
            "action": action,
            "description": "Sandboxes run without network access unless a human approves egress.",
            "when": {
                "all": [
                    {"field": "network_mode", "op": "exists"},
                    {"field": "network_mode", "op": "ne", "value": "none"},
                ]
            },
            "effect": "require_approval",
            "reason": "Network access from the experiment sandbox requires human approval",
            "priority": 900,
        },
        {
            "id": f"{prefix}.egress_outside_allowlist",
            "action": action,
            "description": "Egress hosts must be on the organization egress allowlist.",
            "when": {
                "all": [
                    {"field": "egress_hosts", "op": "exists"},
                    {"field": "egress_hosts", "op": "not_in", "value": [[], ""]},
                    {
                        "not": {
                            "field": "egress_hosts",
                            "op": "subset_of",
                            "value_from": "org_egress_allowlist",
                        }
                    },
                ]
            },
            "effect": "deny",
            "reason": "Requested egress hosts are not on the organization egress allowlist",
            "priority": 1000,
        },
        {
            "id": f"{prefix}.secrets_denied",
            "action": action,
            "description": "Secrets are never mounted into experiment sandboxes by default.",
            "when": _NON_EMPTY_SECRETS,
            "effect": "deny",
            "reason": "Experiments may not request secrets",
            "priority": 1000,
        },
        {
            "id": f"{prefix}.production_denied",
            "action": action,
            "description": "Experiments never touch production systems.",
            "when": {"field": "production", "op": "eq", "value": True},
            "effect": "deny",
            "reason": "Experiments may not target production systems",
            "priority": 1000,
        },
        {
            "id": f"{prefix}.gpu_requires_approval",
            "action": action,
            "description": "GPU jobs below L3 autonomy need a human approval.",
            "when": {
                "all": [
                    {"field": "gpu_count", "op": "gt", "value": 0},
                    {"autonomy_below": AutonomyLevel.L3_AUTOMATED_EXECUTION.value},
                ]
            },
            "effect": "require_approval",
            "reason": "GPU execution below autonomy level L3 requires human approval",
            "priority": 800,
        },
        {
            "id": f"{prefix}.cost_over_budget",
            "action": action,
            "description": "A job estimated to cost more than the remaining budget needs approval.",
            "when": {"field": "estimated_cost_usd", "op": "gt", "value_from": "budget_remaining_usd"},
            "effect": "require_approval",
            "reason": "Estimated cost exceeds the remaining budget",
            "priority": 800,
        },
        {
            "id": f"{prefix}.automation_below_l3",
            "action": action,
            "description": "Agents and workflows execute automatically only at L3 autonomy or above.",
            "when": {
                "all": [
                    {"field": "actor_kind", "op": "in", "value": ["agent", "workflow"]},
                    {"autonomy_below": AutonomyLevel.L3_AUTOMATED_EXECUTION.value},
                ]
            },
            "effect": "require_approval",
            "reason": "Automated execution requires autonomy level L3; a human must approve this run",
            "priority": 800,
        },
    ]


_BASELINE_RULES: list[dict[str, Any]] = [
    *_execution_rules("execution.submit"),
    *_execution_rules("experiment.execute"),
    {
        "id": "baseline.tool.invoke.high_risk",
        "action": "tool.invoke",
        "when": {"risk_at_least": RiskLevel.HIGH.value, "field": "tool_risk"},
        "effect": "require_approval",
        "reason": "High-risk tools require human approval",
        "priority": 800,
    },
    {
        "id": "baseline.tool.invoke.agent_below_l1",
        "action": "tool.invoke",
        "when": {
            "all": [
                {"field": "actor_kind", "op": "eq", "value": "agent"},
                {"autonomy_below": AutonomyLevel.L1_RESEARCH_AUTOMATION.value},
            ]
        },
        "effect": "require_approval",
        "reason": "At autonomy L0 every agent tool call needs a human",
        "priority": 800,
    },
    {
        "id": "baseline.mcp.invoke.unregistered",
        "action": "mcp.invoke",
        "when": {
            "any": [
                {"field": "mcp_registered", "op": "not_exists"},
                {"field": "mcp_registered", "op": "ne", "value": True},
            ]
        },
        "effect": "deny",
        "reason": "MCP tools must come from a registered, approved MCP server",
        "priority": 1000,
    },
    {
        "id": "baseline.mcp.invoke.high_risk",
        "action": "mcp.invoke",
        "when": {"risk_at_least": RiskLevel.HIGH.value, "field": "tool_risk"},
        "effect": "require_approval",
        "reason": "High-risk MCP tools require human approval",
        "priority": 800,
    },
    {
        "id": "baseline.research.deep_research.automation_below_l1",
        "action": "research.deep_research",
        "when": {"all": [{"autonomy_below": AutonomyLevel.L1_RESEARCH_AUTOMATION.value}, _NOT_USER]},
        "effect": "require_approval",
        "reason": "Automated deep research requires autonomy level L1",
        "priority": 800,
    },
    {
        "id": "baseline.research.deep_research.expensive",
        "action": "research.deep_research",
        "when": {"field": "estimated_cost_usd", "op": "gt", "value": 5},
        "effect": "require_approval",
        "reason": "Deep research estimated above USD 5 requires human approval",
        "priority": 800,
    },
    {
        "id": "baseline.llm.external_processing.no_consent",
        "action": "llm.external_processing",
        "when": {
            "all": [
                {"field": "external_provider", "op": "exists"},
                {"field": "external_provider", "op": "not_in", "value": [False, ""]},
                {
                    "any": [
                        {"field": "data_processing_consent", "op": "not_exists"},
                        {"field": "data_processing_consent", "op": "ne", "value": True},
                    ]
                },
            ]
        },
        "effect": "deny",
        "reason": "The organization has not consented to processing its data with external model providers",
        "priority": 1000,
    },
    {
        "id": "baseline.strategy.auto_promote.denied",
        "action": "strategy.auto_promote",
        "effect": "deny",
        "reason": "Automatic strategy promotion is disabled by default",
        "priority": 1000,
    },
    {
        "id": "baseline.strategy.promote.non_human",
        "action": "strategy.promote",
        "when": _NOT_HUMAN,
        "effect": "require_approval",
        "reason": "Strategy promotion by a non-human actor requires human approval",
        "approver_permission": "strategy:promote",
        "priority": 800,
    },
    {
        "id": "baseline.strategy.promote.automation_below_l4",
        "action": "strategy.promote",
        "when": {"all": [{"autonomy_below": AutonomyLevel.L4_CLOSED_LOOP_EVOLUTION.value}, _NOT_USER]},
        "effect": "deny",
        "reason": "Automated strategy promotion requires autonomy level L4",
        "priority": 1000,
    },
    {
        "id": "baseline.discovery.approve.non_human",
        "action": "discovery.approve",
        "when": _NOT_HUMAN,
        "effect": "deny",
        "reason": "Only a human reviewer can approve a discovery",
        "priority": 1000,
    },
    {
        "id": "baseline.discovery.publish.approval",
        "action": "discovery.publish",
        "effect": "require_approval",
        "reason": "Publishing a discovery always requires human approval",
        "approver_permission": "discovery:publish",
        "priority": 800,
    },
    {
        "id": "baseline.memory.promote.untrusted_shared",
        "action": "memory.promote",
        "when": {
            "all": [
                {"field": "source_trust", "op": "eq", "value": "untrusted"},
                {"field": "scope", "op": "in", "value": ["organization", "project", "ORGANIZATION", "PROJECT"]},
            ]
        },
        "effect": "require_approval",
        "reason": "Promoting untrusted content into shared memory requires human review",
        "approver_permission": "memory:review",
        "priority": 800,
    },
    {
        "id": "baseline.mission.autonomy_change.non_human",
        "action": "mission.autonomy_change",
        "when": {"any": [_NOT_USER, _NOT_HUMAN]},
        "effect": "deny",
        "reason": "Only a signed-in human can change autonomy; agents never change autonomy",
        "priority": 1000,
    },
    {
        "id": "baseline.mission.autonomy_change.above_ceiling",
        "action": "mission.autonomy_change",
        "when": {
            "any": [
                {"field": "requested_rank", "op": "gt", "value_from": "ceiling_rank"},
                {
                    "all": [
                        {"field": "requested_rank", "op": "exists"},
                        {"field": "ceiling_rank", "op": "not_exists"},
                    ]
                },
            ]
        },
        "effect": "deny",
        "reason": "The requested autonomy level exceeds the organization/project ceiling",
        "priority": 1000,
    },
    {
        "id": "baseline.integration.production.denied",
        "action": "integration.production",
        "effect": "deny",
        "reason": "Production integrations are disabled by the platform baseline",
        "priority": 1000,
    },
]

BASELINE = PolicySet(
    key="baseline",
    version=BASELINE_VERSION,
    rules=tuple(parse_rules(_BASELINE_RULES, allow_reserved=True)),
    scope="baseline",
)


def baseline_documents() -> list[dict[str, Any]]:
    """The baseline rules as canonical JSON documents (for the API)."""
    return [rule.to_document() for rule in BASELINE.rules]
