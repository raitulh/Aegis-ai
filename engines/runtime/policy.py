"""Runtime policy DSL: parse, validate, compile and evaluate (deterministic, side-effect free).

Example::

    name: Prevent sensitive exfiltration
    description: Confidential data must not leave the organization without approval.
    params:
      internal_domains: [acme.example]
    rules:
      - id: confidential-external-transfer
        when:
          event: [network.request, tool.call]
          destination: external
          data_classification: confidential     # at least this level
        action: require_approval
        severity: high
        message: Confidential data sent outside the organization requires approval.

``when`` keys (all must match; omitted keys match anything):

* ``event`` — event type or list (``*`` suffix globs: ``file.*``)
* ``tool`` / ``agent`` / ``environment`` / ``source`` — value or list (globs allowed)
* ``destination`` — ``external`` | ``internal`` (host / recipient domains vs ``params.internal_domains``;
  private and loopback hosts are internal)
* ``data_classification`` — minimum level: public < internal < confidential < restricted
* ``contains_pii`` / ``contains_secret`` / ``approved`` — booleans from deterministic detectors
* ``statement`` — database statement kinds, e.g. ``[delete, drop, truncate]``
* ``conditions`` — list of ``{field, op, value}`` over ``payload.*``, ``signals.*`` or envelope fields;
  ops: eq ne in not_in contains not_contains starts_with ends_with matches gt gte lt lte exists

``action``: allow | flag | block | require_approval. The most restrictive matching action wins:
block > require_approval > flag > allow.
"""

from __future__ import annotations

import fnmatch
import hashlib
import ipaddress
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from engines.runtime.schema import CLASSIFICATION_RANK, EVENT_TYPES

Action = Literal["allow", "flag", "block", "require_approval"]
ACTION_RANK = {"allow": 0, "flag": 1, "require_approval": 2, "block": 3}
OPS = {
    "eq",
    "ne",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "matches",
    "gt",
    "gte",
    "lt",
    "lte",
    "exists",
}
MAX_RULES = 100
MAX_PATTERN = 200
MAX_SOURCE_BYTES = 64 * 1024
_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")


class PolicyError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class Condition(BaseModel):
    field: str = Field(max_length=120)
    op: str
    value: Any = None

    @field_validator("op")
    @classmethod
    def _op(cls, value: str) -> str:
        if value not in OPS:
            raise ValueError(f"unknown op '{value}' (allowed: {sorted(OPS)})")
        return value

    @field_validator("field")
    @classmethod
    def _field(cls, value: str) -> str:
        root = value.split(".", 1)[0]
        if root not in {"payload", "signals", "event_type", "tool", "agent", "environment", "source", "actor"}:
            raise ValueError(f"field must start with payload., signals. or be an envelope field (got '{value}')")
        return value


class When(BaseModel):
    event: list[str] | None = None
    tool: list[str] | None = None
    agent: list[str] | None = None
    environment: list[str] | None = None
    source: list[str] | None = None
    destination: Literal["external", "internal"] | None = None
    data_classification: str | None = None
    contains_pii: bool | None = None
    contains_secret: bool | None = None
    approved: bool | None = None
    statement: list[str] | None = None
    conditions: list[Condition] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @field_validator("event", "tool", "agent", "environment", "source", "statement", mode="before")
    @classmethod
    def _listify(cls, value: Any) -> Any:
        if value is None or isinstance(value, list):
            return value
        return [value]

    @field_validator("event")
    @classmethod
    def _events(cls, value: list[str] | None) -> list[str] | None:
        for pattern in value or []:
            if not any(fnmatch.fnmatchcase(t, pattern) for t in EVENT_TYPES):
                raise ValueError(f"event '{pattern}' matches no known event type")
        return value

    @field_validator("data_classification")
    @classmethod
    def _classification(cls, value: str | None) -> str | None:
        if value is not None and value not in CLASSIFICATION_RANK:
            raise ValueError(f"data_classification must be one of {list(CLASSIFICATION_RANK)}")
        return value


class Rule(BaseModel):
    id: str
    description: str | None = None
    when: When = Field(default_factory=When)
    action: Action
    severity: Literal["critical", "high", "medium", "low", "info"] = "medium"
    message: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not _RULE_ID.match(value):
            raise ValueError("rule id must be lowercase letters, digits, '-', '_' or '.' (2-64 chars)")
        return value


class PolicyDoc(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    params: dict[str, Any] = Field(default_factory=dict)
    rules: list[Rule] = Field(min_length=1)

    model_config = {"extra": "forbid"}


def _validate_patterns(doc: PolicyDoc) -> list[str]:
    errors = []
    for rule in doc.rules:
        for cond in rule.when.conditions:
            if cond.op == "matches":
                if not isinstance(cond.value, str) or len(cond.value) > MAX_PATTERN:
                    errors.append(f"rule {rule.id}: 'matches' needs a regex string of at most {MAX_PATTERN} chars")
                    continue
                try:
                    re.compile(cond.value)
                except re.error as exc:
                    errors.append(f"rule {rule.id}: invalid regex ({exc})")
            if cond.op in ("in", "not_in") and not isinstance(cond.value, list):
                errors.append(f"rule {rule.id}: '{cond.op}' needs a list value")
    ids = [r.id for r in doc.rules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        errors.append(f"duplicate rule ids: {', '.join(duplicates)}")
    if len(doc.rules) > MAX_RULES:
        errors.append(f"a policy may contain at most {MAX_RULES} rules")
    return errors


def compile_policy(source: str) -> dict[str, Any]:
    """Parse + validate YAML source. Returns the compiled (normalized) policy or raises ``PolicyError``."""
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise PolicyError([f"policy source exceeds {MAX_SOURCE_BYTES // 1024} KB"])
    try:
        raw = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise PolicyError([f"YAML syntax error: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise PolicyError(["policy must be a YAML mapping with 'name' and 'rules'"])
    try:
        doc = PolicyDoc.model_validate(raw)
    except ValidationError as exc:
        raise PolicyError([f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]) from exc
    errors = _validate_patterns(doc)
    if errors:
        raise PolicyError(errors)
    compiled = doc.model_dump(mode="json", exclude_none=True)
    compiled["checksum"] = hashlib.sha256(source.encode()).hexdigest()
    return compiled


# --- evaluation ----------------------------------------------------------------------------------
def _glob_any(value: str | None, patterns: list[str]) -> bool:
    return value is not None and any(fnmatch.fnmatchcase(value, p) for p in patterns)


def _lookup(event: dict[str, Any], path: str) -> Any:
    envelope = {
        "event_type": event.get("event_type"),
        "tool": event.get("tool_name"),
        "agent": event.get("agent_name"),
        "environment": event.get("environment"),
        "source": event.get("source"),
        "actor": event.get("actor"),
    }
    root, _, rest = path.partition(".")
    if root in envelope and not rest:
        return envelope[root]
    current: Any = event.get(root, {})
    for part in rest.split(".") if rest else []:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "exists":
        return (actual is not None) == bool(expected if expected is not None else True)
    if actual is None:
        return op in ("ne", "not_in", "not_contains")
    try:
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "in":
            return actual in expected
        if op == "not_in":
            return actual not in expected
        if op == "contains":
            return str(expected).lower() in str(actual).lower() if not isinstance(actual, list) else expected in actual
        if op == "not_contains":
            return (
                str(expected).lower() not in str(actual).lower()
                if not isinstance(actual, list)
                else expected not in actual
            )
        if op == "starts_with":
            return str(actual).startswith(str(expected))
        if op == "ends_with":
            return str(actual).endswith(str(expected))
        if op == "matches":
            return re.search(str(expected), str(actual)[:2000]) is not None
        if op in ("gt", "gte", "lt", "lte"):
            a, b = float(actual), float(expected)
            return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    except (TypeError, ValueError, re.error):
        return False
    return False


def _is_internal(host: str, internal_domains: list[str]) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return not ip.is_global
    except ValueError:
        pass
    if host in ("localhost",) or host.endswith((".local", ".internal", ".svc", ".cluster.local")):
        return True
    return any(host == d or host.endswith("." + d) for d in internal_domains)


def destination_of(event: dict[str, Any], internal_domains: list[str]) -> str | None:
    signals = event.get("signals", {})
    hosts = [h for h in [signals.get("host")] if h] + list(signals.get("recipient_domains") or [])
    if not hosts:
        return None
    return "internal" if all(_is_internal(h, internal_domains) for h in hosts) else "external"


def rule_matches(rule: dict[str, Any], event: dict[str, Any], params: dict[str, Any]) -> bool:
    when = rule.get("when") or {}
    signals = event.get("signals", {})
    checks: list[tuple[str, Any]] = [
        ("event", event.get("event_type")),
        ("tool", event.get("tool_name")),
        ("agent", event.get("agent_name")),
        ("environment", event.get("environment")),
        ("source", event.get("source")),
    ]
    for key, value in checks:
        if when.get(key) and not _glob_any(value, when[key]):
            return False
    if when.get("destination"):
        internal = [str(d).lower() for d in params.get("internal_domains", [])]
        if destination_of(event, internal) != when["destination"]:
            return False
    if when.get("data_classification"):
        level = CLASSIFICATION_RANK.get(signals.get("data_classification", "internal"), 1)
        if level < CLASSIFICATION_RANK[when["data_classification"]]:
            return False
    for flag in ("contains_pii", "contains_secret", "approved"):
        if flag in when and bool(signals.get(flag)) != bool(when[flag]):
            return False
    if when.get("statement") and (signals.get("statement_kind") or "") not in [s.lower() for s in when["statement"]]:
        return False
    return all(_compare(c["op"], _lookup(event, c["field"]), c.get("value")) for c in when.get("conditions", []))


def evaluate(event: dict[str, Any], policies: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate a normalized event against compiled policies.

    ``policies`` items: ``{"policy_id", "policy_key", "version", "compiled"}``. Returns the combined decision,
    every matching rule and a human-readable reason."""
    matches: list[dict[str, Any]] = []
    for policy in policies:
        compiled = policy["compiled"]
        params = compiled.get("params", {})
        for rule in compiled.get("rules", []):
            if rule_matches(rule, event, params):
                matches.append(
                    {
                        "policy_id": policy.get("policy_id"),
                        "policy_key": policy.get("policy_key"),
                        "version": policy.get("version"),
                        "rule_id": rule["id"],
                        "action": rule["action"],
                        "severity": rule.get("severity", "medium"),
                        "message": rule.get("message") or rule.get("description") or rule["id"],
                    }
                )
    decision = "allow"
    for match in matches:
        if ACTION_RANK[match["action"]] > ACTION_RANK[decision]:
            decision = match["action"]
    deciding = [m for m in matches if m["action"] == decision and decision != "allow"]
    severity_rank = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    risk = max((m["severity"] for m in matches if m["action"] != "allow"), key=lambda s: severity_rank[s], default=None)
    return {
        "decision": decision,
        "matches": matches,
        "risk_level": risk,
        "reason": "; ".join(m["message"] for m in deciding)
        or ("No policy matched" if not matches else "Allowed by policy"),
    }


def diff_rules(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    old_rules = {r["id"]: r for r in old.get("rules", [])}
    new_rules = {r["id"]: r for r in new.get("rules", [])}
    return {
        "added": sorted(new_rules.keys() - old_rules.keys()),
        "removed": sorted(old_rules.keys() - new_rules.keys()),
        "changed": sorted(k for k in old_rules.keys() & new_rules.keys() if old_rules[k] != new_rules[k]),
    }
