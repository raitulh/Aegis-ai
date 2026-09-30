"""Strategy guardrails — evolution changes *behaviour*, never *authority*.

:meth:`StrategyGuardrails.validate` checks a strategy version (``definition`` + ``parameters``) against
its parameter schema and, optionally, its parent version:

* ``FORBIDDEN_KEY`` — a key *anywhere* (recursively, inside dicts and lists, case/format-insensitive:
  ``apiKey`` ≡ ``API-KEY`` ≡ ``api_key``) names an authority concept: permissions, roles, scopes,
  secrets, credentials, tokens, network/egress, autonomy, policy, approvals, admin/sudo/privileged,
  docker/socket, MCP servers, tool allow-lists, audit, bypass, production. Exact matches use the full
  vocabulary; unambiguous authority terms (``permission``, ``secret``, ``credential``, ``api_key``,
  ``egress``, ``autonomy``, ``approval``, ``admin``, ``sudo``, ``bypass`` …) are also caught inside
  compound keys such as ``tool_permissions`` or ``bypass_review``;
* ``UNKNOWN_PARAMETER`` / ``MISSING_PARAMETER`` and the per-type value codes ``TYPE_MISMATCH``,
  ``OUT_OF_BOUNDS``, ``STEP_MISALIGNED``, ``INVALID_CHOICE``, ``INVALID_ITEM``, ``DUPLICATE_ITEM``,
  ``LENGTH_OUT_OF_BOUNDS``, ``NOT_FINITE``;
* ``IMMUTABLE_CHANGED`` — a ``mutable=False`` parameter differs from the parent's value;
* ``DEFINITION_KEY_NOT_ALLOWED`` — a top-level definition key outside ``schema.definition_keys``;
* ``TOOL_ESCALATION`` — the child references tools its parent's definition does not;
* ``INVALID_KEY`` / ``INVALID_VALUE`` / ``TOO_DEEP`` / ``TOO_LARGE`` — non-ASCII or oversized keys
  (homoglyph smuggling), non-JSON values, excessive nesting or size;
* ``INVALID_SCHEMA`` / ``INVALID_STRUCTURE`` — malformed schema or non-object definition/parameters.

The report is deterministic (violations sorted by path, then code).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict

from engines.lab.evolution.types import ParameterSchema, SchemaError, canonical_json

# Exact (normalised) key matches — the full forbidden vocabulary.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "permission",
        "permissions",
        "grant",
        "grants",
        "role",
        "roles",
        "scope",
        "scopes",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "password",
        "passwords",
        "token",
        "api_key",
        "api_keys",
        "private_key",
        "env",
        "environment_variables",
        "network",
        "egress",
        "allowed_hosts",
        "proxy",
        "autonomy",
        "autonomy_level",
        "policy",
        "policies",
        "approval",
        "approvals",
        "admin",
        "sudo",
        "privileged",
        "docker",
        "socket",
        "mcp_server",
        "mcp_servers",
        "tools_allowlist",
        "audit",
        "bypass",
        "production",
    }
)

# Terms that are authority-bearing wherever they appear inside a compound key (token n-grams).
AUTHORITY_TERMS: frozenset[str] = frozenset(
    {
        "permission",
        "permissions",
        "privilege",
        "privileges",
        "privileged",
        "grant",
        "grants",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "password",
        "passwords",
        "passwd",
        "api_key",
        "api_keys",
        "apikey",
        "private_key",
        "access_token",
        "auth_token",
        "bearer",
        "sudo",
        "admin",
        "bypass",
        "egress",
        "allowed_hosts",
        "allowlist",
        "autonomy",
        "approval",
        "approvals",
        "mcp_server",
        "mcp_servers",
        "environment_variables",
        "env_vars",
        "docker",
        "socket",
    }
)

# Keys whose values reference tools (for TOOL_ESCALATION).
TOOL_KEYS: frozenset[str] = frozenset({"tool", "tools", "tool_name", "tool_names", "tool_sequence", "tool_chain"})

MAX_KEY_LENGTH = 128
_PRINTABLE_ASCII = re.compile(r"^[\x20-\x7e]+$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ViolationCode(StrEnum):
    FORBIDDEN_KEY = "FORBIDDEN_KEY"
    UNKNOWN_PARAMETER = "UNKNOWN_PARAMETER"
    MISSING_PARAMETER = "MISSING_PARAMETER"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    STEP_MISALIGNED = "STEP_MISALIGNED"
    INVALID_CHOICE = "INVALID_CHOICE"
    INVALID_ITEM = "INVALID_ITEM"
    DUPLICATE_ITEM = "DUPLICATE_ITEM"
    LENGTH_OUT_OF_BOUNDS = "LENGTH_OUT_OF_BOUNDS"
    NOT_FINITE = "NOT_FINITE"
    IMMUTABLE_CHANGED = "IMMUTABLE_CHANGED"
    DEFINITION_KEY_NOT_ALLOWED = "DEFINITION_KEY_NOT_ALLOWED"
    TOOL_ESCALATION = "TOOL_ESCALATION"
    INVALID_KEY = "INVALID_KEY"
    INVALID_VALUE = "INVALID_VALUE"
    TOO_DEEP = "TOO_DEEP"
    TOO_LARGE = "TOO_LARGE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    path: str
    message: str


class GuardrailReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    violations: tuple[Violation, ...] = ()

    @property
    def codes(self) -> set[str]:
        return {v.code for v in self.violations}

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": [v.model_dump() for v in self.violations]}


class GuardrailViolation(Exception):
    """Raised by :meth:`StrategyGuardrails.assert_safe`; carries the full report."""

    def __init__(self, report: GuardrailReport) -> None:
        self.report = report
        summary = "; ".join(f"{v.code} at {v.path}" for v in report.violations[:5])
        more = f" (+{len(report.violations) - 5} more)" if len(report.violations) > 5 else ""
        super().__init__(f"strategy guardrail violation: {summary}{more}")


def normalize_key(key: str) -> str:
    """``apiKey`` / ``API-Key`` / ``api.key`` → ``api_key`` (camelCase split, separators → ``_``)."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s)
    return s.strip("_").lower()


@lru_cache(maxsize=4096)
def forbidden_term(key: str) -> str | None:
    """The forbidden term matched by ``key``, or ``None`` when the key is allowed."""
    normalized = normalize_key(key)
    if not normalized:
        return None
    if normalized in FORBIDDEN_KEYS:
        return normalized
    compact = normalized.replace("_", "")
    for term in FORBIDDEN_KEYS:
        if "_" in term and compact == term.replace("_", ""):
            return term
    tokens = normalized.split("_")
    for i in range(len(tokens)):
        for j in range(i + 1, min(len(tokens), i + 3) + 1):
            gram = "_".join(tokens[i:j])
            if gram in AUTHORITY_TERMS:
                return gram
    return None


def _child_path(path: str, key: str) -> str:
    return f"{path}.{key}" if _IDENTIFIER.match(key) else f"{path}[{key!r}]"


class StrategyGuardrails:
    """Validates strategy versions produced by humans, agents or the evolution engine."""

    def __init__(self, *, max_depth: int = 32, max_nodes: int = 20_000) -> None:
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    # -- public API -------------------------------------------------------------------------------
    def validate(
        self,
        definition: Mapping[str, Any] | None,
        parameters: Mapping[str, Any] | None,
        schema: ParameterSchema | Mapping[str, Any] | None,
        parent: Mapping[str, Any] | None = None,
        *,
        check_schema: bool = True,
    ) -> GuardrailReport:
        """Check a strategy version; ``parent`` is ``{"definition": …, "parameters": …}`` or ``None``.

        ``check_schema=False`` skips re-validating the schema itself (callers that validated it once,
        e.g. the candidate generator, avoid repeating the work per child).
        """
        violations: list[Violation] = []
        definition = {} if definition is None else definition
        parameters = {} if parameters is None else parameters

        parsed: ParameterSchema | None
        try:
            parsed = ParameterSchema.from_dict(schema)
        except SchemaError as exc:
            parsed = None
            violations.append(Violation(code=ViolationCode.INVALID_SCHEMA, path="schema", message=str(exc)[:500]))
        if parsed is not None and check_schema:
            violations.extend(self.validate_schema(parsed).violations)

        for name, value in (("definition", definition), ("parameters", parameters)):
            if not isinstance(value, Mapping):
                violations.append(
                    Violation(code=ViolationCode.INVALID_STRUCTURE, path=name, message=f"{name} must be an object")
                )
            else:
                violations.extend(self._scan(value, name))

        if parsed is not None and isinstance(parameters, Mapping):
            violations.extend(self._check_parameters(parameters, parsed, parent))
        if parsed is not None and isinstance(definition, Mapping):
            allowed = set(parsed.definition_keys)
            for key in definition:
                if key not in allowed:
                    violations.append(
                        Violation(
                            code=ViolationCode.DEFINITION_KEY_NOT_ALLOWED,
                            path=_child_path("definition", str(key)),
                            message=f"definition key {str(key)[:64]!r} is not declared in schema.definition_keys",
                        )
                    )
        if parent is not None and isinstance(definition, Mapping):
            parent_definition = parent.get("definition") or {}
            parent_tools = self.tool_names(parent_definition) if isinstance(parent_definition, Mapping) else set()
            escalated = sorted(self.tool_names(definition) - parent_tools)
            if escalated:
                violations.append(
                    Violation(
                        code=ViolationCode.TOOL_ESCALATION,
                        path="definition",
                        message=f"child adds tools not present in the parent: {', '.join(escalated)[:400]}",
                    )
                )
        return self._report(violations)

    def validate_schema(self, schema: ParameterSchema | Mapping[str, Any] | None) -> GuardrailReport:
        """Check that a schema itself declares no authority-bearing parameters or definition keys."""
        try:
            parsed = ParameterSchema.from_dict(schema)
        except SchemaError as exc:
            return self._report([Violation(code=ViolationCode.INVALID_SCHEMA, path="schema", message=str(exc)[:500])])
        violations: list[Violation] = []
        for name in parsed.parameters:
            term = forbidden_term(name)
            if term:
                violations.append(
                    Violation(
                        code=ViolationCode.FORBIDDEN_KEY,
                        path=f"schema.parameters.{name}",
                        message=f"parameter {name!r} names an authority concept ({term}); not evolvable",
                    )
                )
        for i, key in enumerate(parsed.definition_keys):
            term = forbidden_term(key)
            if term:
                violations.append(
                    Violation(
                        code=ViolationCode.FORBIDDEN_KEY,
                        path=f"schema.definition_keys[{i}]",
                        message=f"definition key {key!r} names an authority concept ({term})",
                    )
                )
        return self._report(violations)

    def assert_safe(
        self,
        definition: Mapping[str, Any] | None,
        parameters: Mapping[str, Any] | None,
        schema: ParameterSchema | Mapping[str, Any] | None,
        parent: Mapping[str, Any] | None = None,
    ) -> None:
        report = self.validate(definition, parameters, schema, parent)
        if not report.ok:
            raise GuardrailViolation(report)

    @staticmethod
    def tool_names(definition: Mapping[str, Any]) -> set[str]:
        """Every tool referenced under a tool key (``tools``, ``tool``, ``tool_name`` …) anywhere."""
        found: set[str] = set()

        def collect(value: Any) -> None:
            if isinstance(value, str):
                found.add(value)
            elif isinstance(value, list | tuple):
                for item in value:
                    collect(item)
            elif isinstance(value, Mapping):
                named = value.get("name", value.get("tool"))
                if isinstance(named, str):
                    found.add(named)
                else:
                    found.update(str(k) for k in value)

        def walk(value: Any, depth: int) -> None:
            if depth > 64:
                return
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if isinstance(key, str) and normalize_key(key) in TOOL_KEYS:
                        collect(item)
                    walk(item, depth + 1)
            elif isinstance(value, list | tuple):
                for item in value:
                    walk(item, depth + 1)

        walk(definition, 0)
        return found

    # -- internals ----------------------------------------------------------------------------------
    @staticmethod
    def _report(violations: list[Violation]) -> GuardrailReport:
        unique = {(v.path, v.code, v.message): v for v in violations}
        ordered = tuple(unique[k] for k in sorted(unique))
        return GuardrailReport(ok=not ordered, violations=ordered)

    def _scan(self, root: Mapping[str, Any], root_path: str) -> list[Violation]:
        violations: list[Violation] = []
        nodes = 0
        too_large = False
        stack: list[tuple[Any, str, int]] = [(root, root_path, 0)]
        while stack:
            value, path, depth = stack.pop()
            nodes += 1
            if nodes > self.max_nodes:
                if not too_large:
                    violations.append(
                        Violation(
                            code=ViolationCode.TOO_LARGE,
                            path=root_path,
                            message=f"{root_path} exceeds {self.max_nodes} nodes",
                        )
                    )
                    too_large = True
                break
            if depth > self.max_depth:
                violations.append(
                    Violation(code=ViolationCode.TOO_DEEP, path=path, message=f"nesting deeper than {self.max_depth}")
                )
                continue
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if not isinstance(key, str):
                        violations.append(
                            Violation(code=ViolationCode.INVALID_KEY, path=path, message="object keys must be strings")
                        )
                        continue
                    child = _child_path(path, key[:MAX_KEY_LENGTH])
                    if len(key) > MAX_KEY_LENGTH or not _PRINTABLE_ASCII.match(key):
                        violations.append(
                            Violation(
                                code=ViolationCode.INVALID_KEY,
                                path=child,
                                message="keys must be printable ASCII of at most 128 characters",
                            )
                        )
                    term = forbidden_term(key)
                    if term:
                        violations.append(
                            Violation(
                                code=ViolationCode.FORBIDDEN_KEY,
                                path=child,
                                message=f"key {key[:64]!r} names an authority concept ({term}); "
                                "strategies may change behaviour, never authority",
                            )
                        )
                    stack.append((item, child, depth + 1))
            elif isinstance(value, list | tuple):
                for i, item in enumerate(value):
                    stack.append((item, f"{path}[{i}]", depth + 1))
            elif isinstance(value, float):
                if not math.isfinite(value):
                    violations.append(
                        Violation(
                            code=ViolationCode.NOT_FINITE, path=path, message="non-finite numbers are not allowed"
                        )
                    )
            elif value is not None and not isinstance(value, str | int | bool):
                violations.append(
                    Violation(
                        code=ViolationCode.INVALID_VALUE,
                        path=path,
                        message=f"value of type {type(value).__name__} is not JSON-serialisable",
                    )
                )
        return violations

    @staticmethod
    def _check_parameters(
        parameters: Mapping[str, Any], schema: ParameterSchema, parent: Mapping[str, Any] | None
    ) -> list[Violation]:
        violations: list[Violation] = []
        for key in parameters:
            if key not in schema.parameters:
                violations.append(
                    Violation(
                        code=ViolationCode.UNKNOWN_PARAMETER,
                        path=_child_path("parameters", str(key)[:MAX_KEY_LENGTH]),
                        message=f"parameter {str(key)[:64]!r} is not declared in the parameter schema",
                    )
                )
        parent_params: Mapping[str, Any] = {}
        if parent is not None:
            raw = parent.get("parameters", parent.get("params"))
            parent_params = raw if isinstance(raw, Mapping) else {}
        for name, spec in schema.parameters.items():
            path = f"parameters.{name}"
            if name not in parameters:
                if spec.default is None:
                    violations.append(
                        Violation(
                            code=ViolationCode.MISSING_PARAMETER,
                            path=path,
                            message=f"parameter {name!r} is required (no default declared)",
                        )
                    )
                continue
            value = parameters[name]
            for code, message in spec.check_value(value):
                violations.append(Violation(code=code, path=path, message=message))
            if (
                not spec.mutable
                and name in parent_params
                and canonical_json(parent_params[name]) != canonical_json(value)
            ):
                violations.append(
                    Violation(
                        code=ViolationCode.IMMUTABLE_CHANGED,
                        path=path,
                        message=f"parameter {name!r} is immutable and differs from the parent version",
                    )
                )
        return violations


DEFAULT_GUARDRAILS = StrategyGuardrails()
