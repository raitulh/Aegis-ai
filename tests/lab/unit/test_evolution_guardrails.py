"""Guardrails: evolution may change behaviour, never authority."""

from __future__ import annotations

from typing import Any

import pytest

from engines.lab.evolution.candidates import CandidateGenerator
from engines.lab.evolution.guardrails import (
    GuardrailReport,
    GuardrailViolation,
    StrategyGuardrails,
    forbidden_term,
    normalize_key,
)

SCHEMA = {
    "parameters": {
        "temperature": {"type": "float", "min": 0.0, "max": 1.0},
        "depth": {"type": "int", "min": 1, "max": 5, "mutable": False},
        "mode": {"type": "choice", "choices": ["a", "b"]},
        "retries": {"type": "int", "min": 0, "max": 3, "default": 1},
    },
    "definition_keys": ["steps", "tools", "prompt_template"],
}
PARAMS = {"temperature": 0.5, "depth": 3, "mode": "a"}
DEFINITION = {"steps": [{"name": "search", "tool": "paper_search"}], "tools": ["paper_search"], "prompt_template": "t"}
G = StrategyGuardrails()


def codes(report: GuardrailReport) -> set[str]:
    return {v.code for v in report.violations}


def test_valid_version_passes() -> None:
    report = G.validate(DEFINITION, PARAMS, SCHEMA, parent={"definition": DEFINITION, "parameters": PARAMS})
    assert report.ok and report.violations == ()


def test_nested_forbidden_key_inside_list_is_rejected() -> None:
    definition = {"steps": [{"name": "s", "permissions": ["admin"]}]}
    report = G.validate(definition, PARAMS, SCHEMA)
    [violation] = [v for v in report.violations if v.code == "FORBIDDEN_KEY"]
    assert violation.path == "definition.steps[0].permissions"
    deeper = {"steps": [{"config": {"options": [[{"Secrets": {"x": 1}}]]}}]}
    assert "FORBIDDEN_KEY" in codes(G.validate(deeper, PARAMS, SCHEMA))


@pytest.mark.parametrize(
    "key",
    [
        "permissions",
        "PERMISSION",
        "apiKey",
        "API-KEY",
        "x_api_key",
        "privateKey",
        "Secrets",
        "credential",
        "password",
        "token",
        "role",
        "scopes",
        "grant",
        "env",
        "environment_variables",
        "network",
        "egress_rules",
        "allowedHosts",
        "proxy",
        "autonomyLevel",
        "autonomy",
        "policy",
        "policies",
        "approvals",
        "skip_approval",
        "admin",
        "run_as_admin",
        "sudo",
        "privileged",
        "docker",
        "docker_socket",
        "mcpServers",
        "tools_allowlist",
        "audit",
        "bypass_review",
        "production",
        "tool_permissions",
    ],
)
def test_forbidden_vocabulary_is_case_and_format_insensitive(key: str) -> None:
    assert forbidden_term(key) is not None
    report = G.validate({"steps": [{key: True}]}, PARAMS, SCHEMA)
    assert "FORBIDDEN_KEY" in codes(report)


@pytest.mark.parametrize(
    "key", ["max_tokens", "token_budget", "network_depth", "search_scope", "agent_role", "environment", "temperature"]
)
def test_behavioural_keys_are_not_mistaken_for_authority(key: str) -> None:
    assert forbidden_term(key) is None


def test_normalize_key() -> None:
    assert normalize_key("APIKey") == "api_key"
    assert normalize_key("mcp.Servers") == "mcp_servers"
    assert normalize_key("allowed-hosts") == "allowed_hosts"


def test_forbidden_key_in_parameters_is_rejected_even_if_nested() -> None:
    report = G.validate(DEFINITION, {**PARAMS, "mode": [{"credentials": "x"}]}, SCHEMA)
    assert codes(report) == {"FORBIDDEN_KEY", "INVALID_CHOICE"}
    assert any(v.path == "parameters.mode[0].credentials" for v in report.violations)


def test_tool_escalation_against_parent() -> None:
    parent = {"definition": DEFINITION, "parameters": PARAMS}
    escalated = {**DEFINITION, "tools": ["paper_search", "shell_exec"]}
    report = G.validate(escalated, PARAMS, SCHEMA, parent=parent)
    assert codes(report) == {"TOOL_ESCALATION"}
    assert "shell_exec" in report.violations[0].message
    nested = {**DEFINITION, "steps": [{"name": "x", "tool": {"name": "web_fetch"}}]}
    assert "TOOL_ESCALATION" in codes(G.validate(nested, PARAMS, SCHEMA, parent=parent))
    narrowed = {**DEFINITION, "tools": [], "steps": []}
    assert G.validate(narrowed, PARAMS, SCHEMA, parent=parent).ok
    assert G.validate(escalated, PARAMS, SCHEMA).ok  # root versions (no parent) are human-authored


def test_parameter_violations() -> None:
    report = G.validate(
        DEFINITION,
        {"temperature": 1.7, "depth": 3, "mode": "c", "surprise": 1},
        SCHEMA,
    )
    assert codes(report) == {"OUT_OF_BOUNDS", "INVALID_CHOICE", "UNKNOWN_PARAMETER"}
    missing = G.validate(DEFINITION, {"temperature": 0.5, "mode": "a"}, SCHEMA)
    assert codes(missing) == {"MISSING_PARAMETER"}  # depth has no default; retries does
    assert missing.violations[0].path == "parameters.depth"


def test_immutable_parameter_cannot_differ_from_parent() -> None:
    parent = {"definition": DEFINITION, "parameters": PARAMS}
    report = G.validate(DEFINITION, {**PARAMS, "depth": 4}, SCHEMA, parent=parent)
    assert codes(report) == {"IMMUTABLE_CHANGED"}


def test_definition_keys_outside_schema_are_rejected() -> None:
    report = G.validate({**DEFINITION, "extras": {"x": 1}}, PARAMS, SCHEMA)
    assert codes(report) == {"DEFINITION_KEY_NOT_ALLOWED"}
    empty_schema: dict[str, Any] = {"parameters": {}}
    assert codes(G.validate({"steps": []}, {}, empty_schema)) == {"DEFINITION_KEY_NOT_ALLOWED"}


def test_structural_and_encoding_attacks() -> None:
    homoglyph = {"steps": [{"pеrmissions": ["admin"]}]}  # Cyrillic "е"
    assert "INVALID_KEY" in codes(G.validate(homoglyph, PARAMS, SCHEMA))
    deep: dict = {}
    cursor = deep
    for _ in range(40):
        cursor["steps"] = {}
        cursor = cursor["steps"]
    assert "TOO_DEEP" in codes(G.validate({"steps": deep}, PARAMS, SCHEMA))
    assert "NOT_FINITE" in codes(G.validate({"steps": [float("nan")]}, PARAMS, SCHEMA))
    assert "INVALID_VALUE" in codes(G.validate({"steps": [{1, 2}]}, PARAMS, SCHEMA))
    assert "INVALID_STRUCTURE" in codes(G.validate(["not", "a", "dict"], PARAMS, SCHEMA))  # type: ignore[arg-type]
    assert "INVALID_SCHEMA" in codes(G.validate({}, {}, {"parameters": {"x": {"type": "nope"}}}))
    big = {"steps": list(range(50))}
    assert "TOO_LARGE" in codes(StrategyGuardrails(max_nodes=20).validate(big, PARAMS, SCHEMA))


def test_schema_declaring_authority_is_rejected() -> None:
    bad = {"parameters": {"autonomy_level": {"type": "int", "min": 0, "max": 5}}, "definition_keys": ["policy"]}
    report = G.validate_schema(bad)
    assert [v.path for v in report.violations] == ["schema.definition_keys[0]", "schema.parameters.autonomy_level"]
    with pytest.raises(GuardrailViolation):
        CandidateGenerator(bad)


def test_assert_safe_raises_with_report_and_report_is_sorted() -> None:
    with pytest.raises(GuardrailViolation) as exc_info:
        G.assert_safe({"steps": [{"sudo": True}], "zzz": 1}, {**PARAMS, "temperature": 3.0}, SCHEMA)
    report = exc_info.value.report
    assert not report.ok
    paths = [v.path for v in report.violations]
    assert paths == sorted(paths)
    assert "FORBIDDEN_KEY" in str(exc_info.value)
    G.assert_safe(DEFINITION, PARAMS, SCHEMA)  # no exception
