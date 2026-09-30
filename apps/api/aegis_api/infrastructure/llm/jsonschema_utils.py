"""JSON-schema helpers for structured outputs (provider-friendly schemas and validation)."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import jsonschema

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)
MAX_DEPTH = 32


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local ``#/$defs/...`` references so providers with partial JSON-schema support accept it."""
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            return {}
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith(("#/$defs/", "#/definitions/")):
                target = defs.get(ref.split("/")[-1], {})
                merged = {**copy.deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged, depth + 1)
            return {k: resolve(v, depth + 1) for k, v in node.items() if k not in ("$defs", "definitions")}
        if isinstance(node, list):
            return [resolve(v, depth + 1) for v in node]
        return node

    resolved: dict[str, Any] = resolve(schema)
    return resolved


def parse_json_output(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    match = _FENCE.match(candidate)
    if match:
        candidate = match.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"[:300]
        for e in validator.iter_errors(instance)
    ][:20]
