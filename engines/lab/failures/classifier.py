"""Deterministic failure classification.

Failures are classified by ordered, explainable rules over observable signals (exit status, OOM/timeout
flags, exception type, log lines, evaluator verdicts, policy denials). A model may later *assist* the
diagnosis (FailureAnalyzerAgent), but the classification recorded with every failure always comes from these
rules and carries the rule id and the evidence lines that triggered it.

``signature`` is a normalized fingerprint of the failure (type + canonicalized error line) used to detect
recurrences and retrieve similar historical failures.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from engines.lab.enums import FailureType


class FailureSignal(BaseModel):
    stage: str = Field(
        default="execution",
        description="execution|evaluation|reproduction|tool|model|policy|design|data|verification|strategy",
    )
    status: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    oom_killed: bool = False
    error_type: str | None = None
    error_message: str | None = None
    stderr_tail: str = ""
    stdout_tail: str = ""
    evaluator_verdicts: dict[str, str] = Field(
        default_factory=dict, description="evaluator key → pass|fail|inconclusive"
    )
    evaluator_warnings: list[str] = Field(default_factory=list)
    validation_codes: list[str] = Field(default_factory=list)
    policy_denied: bool = False
    tool_name: str | None = None
    provider_error: str | None = None
    hypothesis_outcome: str | None = None
    reproduction_relative_difference: float | None = None
    context: dict[str, Any] = Field(default_factory=dict)


@dataclass
class FailureClassification:
    failure_type: FailureType
    confidence: float
    rule_id: str
    root_cause: str
    evidence_lines: list[str] = field(default_factory=list)
    signature: str = ""
    secondary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_type": str(self.failure_type),
            "confidence": self.confidence,
            "rule_id": self.rule_id,
            "root_cause": self.root_cause,
            "evidence_lines": self.evidence_lines,
            "signature": self.signature,
            "secondary": self.secondary,
        }


_Rule = tuple[str, FailureType, float, Callable[[FailureSignal, str], str | None]]

_PY_EXC = re.compile(r"^(?P<type>[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Warning))(: (?P<msg>.*))?$", re.M)


def _search(pattern: str, text: str, flags: int = re.I | re.M) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(0).strip()[:300] if match else None


def _log(signal: FailureSignal) -> str:
    return "\n".join(
        filter(None, [signal.error_type or "", signal.error_message or "", signal.stderr_tail, signal.stdout_tail])
    )


RULES: list[_Rule] = [
    (
        "policy.denied",
        FailureType.POLICY_FAILURE,
        0.98,
        lambda s, t: "policy denied the action" if s.policy_denied or s.stage == "policy" else None,
    ),
    (
        "resource.timeout",
        FailureType.RESOURCE_FAILURE,
        0.97,
        lambda s, t: "wall-clock timeout exceeded" if s.timed_out or s.status == "timed_out" else None,
    ),
    (
        "resource.oom",
        FailureType.RESOURCE_FAILURE,
        0.96,
        lambda s, t: (
            "out of memory"
            if s.oom_killed or s.exit_code == 137
            else _search(r"\b(MemoryError|CUDA out of memory|Cannot allocate memory|OOMKilled)\b", t)
        ),
    ),
    (
        "resource.disk",
        FailureType.RESOURCE_FAILURE,
        0.9,
        lambda s, t: _search(r"No space left on device|Disk quota exceeded", t),
    ),
    (
        "design.validation",
        FailureType.EXPERIMENT_DESIGN_FAILURE,
        0.95,
        lambda s, t: (
            ("design validation failed: " + ", ".join(sorted(set(s.validation_codes)))) if s.validation_codes else None
        ),
    ),
    (
        "data.missing_file",
        FailureType.DATA_FAILURE,
        0.88,
        lambda s, t: _search(
            r"(FileNotFoundError|No such file or directory).*(\.csv|\.parquet|\.json|\.npy|\.pt|data|dataset)", t
        ),
    ),
    (
        "data.schema",
        FailureType.DATA_FAILURE,
        0.8,
        lambda s, t: _search(
            r"(KeyError: ['\"]\w+['\"]|ParserError|UnicodeDecodeError|could not convert string to float|Length mismatch|ValueError: Input contains NaN)",
            t,
        ),
    ),
    (
        "code.dependency",
        FailureType.CODE_FAILURE,
        0.93,
        lambda s, t: _search(r"(ModuleNotFoundError|ImportError): .*", t),
    ),
    (
        "code.syntax",
        FailureType.CODE_FAILURE,
        0.95,
        lambda s, t: _search(r"(SyntaxError|IndentationError|TabError)\b.*", t),
    ),
    (
        "model.numerical",
        FailureType.MODEL_FAILURE,
        0.82,
        lambda s, t: _search(
            r"(loss (is|became) nan|nan loss|diverg(ed|ence)|inf(inite)? (loss|gradient)|gradient explosion)", t
        ),
    ),
    (
        "model.provider",
        FailureType.TOOL_FAILURE,
        0.85,
        lambda s, t: f"model provider error: {s.provider_error[:200]}" if s.provider_error else None,
    ),
    (
        "tool.error",
        FailureType.TOOL_FAILURE,
        0.85,
        lambda s, t: f"tool '{s.tool_name}' failed" if s.stage == "tool" else None,
    ),
    (
        "code.runtime",
        FailureType.CODE_FAILURE,
        0.75,
        lambda s, t: _search(
            r"(TypeError|AttributeError|NameError|IndexError|ZeroDivisionError|AssertionError|RuntimeError|ValueError|NotImplementedError)\b.*",
            t,
        ),
    ),
    (
        "reproducibility.mismatch",
        FailureType.REPRODUCIBILITY_FAILURE,
        0.9,
        lambda s, t: (
            (
                f"reproduction differed by {s.reproduction_relative_difference:.2%}"
                if s.reproduction_relative_difference is not None
                else "reproduction did not match"
            )
            if s.stage == "reproduction"
            and (s.evaluator_verdicts.get("reproduction") == "fail" or s.reproduction_relative_difference is not None)
            else None
        ),
    ),
    (
        "evaluation.harness",
        FailureType.EVALUATION_FAILURE,
        0.85,
        lambda s, t: "evaluation harness failed" if s.stage == "evaluation" and s.exit_code not in (None, 0) else None,
    ),
    (
        "statistical.insufficient",
        FailureType.STATISTICAL_FAILURE,
        0.88,
        lambda s, t: (
            "insufficient samples for the statistical plan"
            if s.evaluator_verdicts.get("statistical") == "inconclusive"
            or any("min_seeds" in w for w in s.evaluator_warnings)
            else None
        ),
    ),
    (
        "statistical.not_significant",
        FailureType.STATISTICAL_FAILURE,
        0.7,
        lambda s, t: (
            "effect not statistically significant"
            if s.evaluator_verdicts.get("statistical") == "fail" and s.evaluator_verdicts.get("benchmark") != "fail"
            else None
        ),
    ),
    (
        "hypothesis.refuted",
        FailureType.HYPOTHESIS_FAILURE,
        0.8,
        lambda s, t: (
            "the measured outcome contradicts the hypothesis"
            if s.hypothesis_outcome == "rejected"
            or (
                s.evaluator_verdicts.get("benchmark") == "fail"
                and s.evaluator_verdicts.get("statistical") in ("fail", None)
            )
            else None
        ),
    ),
    (
        "evaluation.metric_missing",
        FailureType.EVALUATION_FAILURE,
        0.75,
        lambda s, t: (
            "declared metrics were not produced"
            if any("not produced" in w or "not found" in w for w in s.evaluator_warnings)
            else None
        ),
    ),
    (
        "strategy.underperformed",
        FailureType.STRATEGY_FAILURE,
        0.7,
        lambda s, t: "strategy failed to improve on its parent" if s.stage == "strategy" else None,
    ),
    (
        "code.nonzero_exit",
        FailureType.CODE_FAILURE,
        0.55,
        lambda s, t: f"process exited with code {s.exit_code}" if s.exit_code not in (None, 0) else None,
    ),
]


_NORMALIZERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
    (re.compile(r"0x[0-9a-f]+", re.I), "<hex>"),
    (re.compile(r"(/[\w.\-]+)+"), "<path>"),
    (re.compile(r"line \d+"), "line <n>"),
    (re.compile(r"\d+(\.\d+)?"), "<n>"),
    (re.compile(r"'[^']{1,80}'"), "'<s>'"),
    (re.compile(r"\s+"), " "),
]


def normalize_error_line(line: str) -> str:
    out = line.strip()
    for pattern, repl in _NORMALIZERS:
        out = pattern.sub(repl, out)
    return out[:240]


def failure_signature(failure_type: str, key_line: str) -> str:
    digest = hashlib.sha256(f"{failure_type}|{normalize_error_line(key_line)}".encode()).hexdigest()
    return f"{failure_type}:{digest[:20]}"


def _last_exception_line(text: str) -> str | None:
    matches = list(_PY_EXC.finditer(text))
    return matches[-1].group(0).strip()[:300] if matches else None


class FailureClassifier:
    """Rule-based classifier; returns the best rule match and any secondary matches."""

    def __init__(self, rules: list[_Rule] | None = None) -> None:
        self.rules = rules or RULES

    def classify(self, signal: FailureSignal) -> FailureClassification:
        text = _log(signal)
        matches: list[tuple[str, FailureType, float, str]] = []
        for rule_id, ftype, confidence, predicate in self.rules:
            hit = predicate(signal, text)
            if hit:
                matches.append((rule_id, ftype, confidence, hit))
        if not matches:
            key_line = _last_exception_line(text) or (signal.error_message or signal.status or "unknown failure")
            ftype = FailureType.TOOL_FAILURE if signal.stage == "tool" else FailureType.CODE_FAILURE
            return FailureClassification(
                failure_type=ftype,
                confidence=0.3,
                rule_id="fallback.unclassified",
                root_cause="No classification rule matched; manual or model-assisted diagnosis required",
                evidence_lines=[key_line] if key_line else [],
                signature=failure_signature(ftype, key_line),
            )
        rule_id, ftype, confidence, hit = matches[0]
        key_line = _last_exception_line(text) or hit
        evidence = [hit]
        if key_line and key_line != hit:
            evidence.append(key_line)
        return FailureClassification(
            failure_type=ftype,
            confidence=confidence,
            rule_id=rule_id,
            root_cause=hit,
            evidence_lines=evidence,
            signature=failure_signature(ftype, key_line),
            secondary=[f"{r}:{t}" for r, t, _, _ in matches[1:4]],
        )
