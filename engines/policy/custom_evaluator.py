"""Custom-rule and output-constraint evaluators built on the deterministic rule engine."""

from __future__ import annotations

from typing import Any

from engines.common.types import Category, EvidenceKind, ResultStatus, Severity, TestType
from engines.evaluation.base import (
    ArtifactSpec,
    EvaluationContext,
    EvaluationOutcome,
    Evaluator,
    EvaluatorInfo,
    SystemInvocation,
    TestCaseSpec,
)
from engines.policy.rules import evaluate_condition
from engines.privacy.detectors import redact


def _fact(inv: SystemInvocation) -> dict[str, Any]:
    return {
        "output": inv.output,
        "output_length": len(inv.output),
        "prompt": inv.prompt,
        "tool_calls": [
            {"name": c.name, "arguments": c.arguments, "approved": c.approved_by_human} for c in inv.tool_calls
        ],
        "tool_names": [c.name for c in inv.tool_calls],
        "tool_count": len(inv.tool_calls),
        "retrieved_count": len(inv.retrieved),
        "has_output": bool(inv.output.strip()),
        "attributes": inv.attributes,
    }


class CustomRuleEvaluator(Evaluator):
    key = "policy.custom_rule"
    name = "Custom Policy Rule"
    version = "1.0.0"
    category = Category.POLICY
    kind = "rule"
    test_types = frozenset({TestType.CUSTOM_RULE})

    def run(self, ctx: EvaluationContext, case: TestCaseSpec, invocations: list[SystemInvocation]) -> EvaluationOutcome:
        usable = [inv for inv in invocations if inv.ok]
        if not usable:
            return EvaluationOutcome(status=ResultStatus.ERROR, summary="No successful system response", confidence=0.0)
        control = ctx.control(case.control_ref)
        condition = (control.condition if control else None) or case.params.get("condition")
        if not condition:
            return EvaluationOutcome(
                status=ResultStatus.SKIPPED, summary="No rule condition configured for this control", confidence=0.0
            )
        # A rule condition describes the VIOLATION state (true = violated).
        violated = [inv for inv in usable if evaluate_condition(condition, _fact(inv))]
        observed = {"responses": len(usable), "violations": len(violated), "condition": condition}
        if violated:
            inv = violated[0]
            return EvaluationOutcome(
                status=ResultStatus.FAILED,
                score=1 - len(violated) / len(usable),
                severity=(control.severity if control else None) or case.severity_hint or Severity.MEDIUM,
                confidence=0.9,
                summary=f"Custom rule '{case.control_ref or case.name}' matched a violation in {len(violated)}/{len(usable)} response(s).",
                observed=observed,
                group=case.control_ref or "custom_rule",
                artifacts=[
                    ArtifactSpec(
                        kind=EvidenceKind.CONFIGURATION, title="Rule condition", content={"condition": condition}
                    ),
                    ArtifactSpec(
                        kind=EvidenceKind.MODEL_OUTPUT,
                        title="Response matching the rule",
                        content={"output": redact(inv.output), "tool_calls": [c.model_dump() for c in inv.tool_calls]},
                    ),
                ],
            )
        return EvaluationOutcome(
            status=ResultStatus.PASSED,
            score=1.0,
            confidence=0.9,
            summary="No response matched the rule's violation condition.",
            observed=observed,
            group="compliant",
        )

    def explain(self) -> EvaluatorInfo:
        return self.info(
            methodology="Evaluates an organization-defined boolean condition (safe operators only) over observed response facts. The condition describes the violation state; a match fails the control.",
            limitations="Only fields exposed in the fact model can be referenced. No code execution or model judgment.",
        )


class OutputConstraintEvaluator(Evaluator):
    key = "policy.output_constraint"
    name = "Output Constraint"
    version = "1.0.0"
    category = Category.POLICY
    kind = "deterministic"
    test_types = frozenset({TestType.OUTPUT_CONSTRAINT})

    def run(self, ctx: EvaluationContext, case: TestCaseSpec, invocations: list[SystemInvocation]) -> EvaluationOutcome:
        usable = [inv for inv in invocations if inv.ok]
        if not usable:
            return EvaluationOutcome(status=ResultStatus.ERROR, summary="No successful system response", confidence=0.0)
        control = ctx.control(case.control_ref)
        threshold = (control.threshold if control else {}) or {}
        forbidden = [
            p.lower() for p in (threshold.get("forbidden_phrases") or case.params.get("forbidden_phrases") or [])
        ]
        required = [p.lower() for p in (threshold.get("required_phrases") or case.params.get("required_phrases") or [])]
        max_len = threshold.get("max_length") or case.params.get("max_length")
        failures: list[tuple[SystemInvocation, str]] = []
        for inv in usable:
            low = inv.output.lower()
            for phrase in forbidden:
                if phrase in low:
                    failures.append((inv, f"contains forbidden phrase '{phrase}'"))
                    break
            else:
                missing = [p for p in required if p not in low]
                if missing:
                    failures.append((inv, f"missing required phrase(s): {', '.join(missing)}"))
                elif max_len and len(inv.output) > int(max_len):
                    failures.append((inv, f"output length {len(inv.output)} exceeds {max_len}"))
        observed = {
            "responses": len(usable),
            "failures": len(failures),
            "forbidden": forbidden,
            "required": required,
            "max_length": max_len,
        }
        if failures:
            inv, reason = failures[0]
            return EvaluationOutcome(
                status=ResultStatus.FAILED,
                score=1 - len(failures) / len(usable),
                severity=(control.severity if control else None) or Severity.MEDIUM,
                confidence=0.95,
                summary=f"Output constraint violated: {reason} ({len(failures)}/{len(usable)}).",
                observed=observed,
                group="output_constraint",
                artifacts=[
                    ArtifactSpec(
                        kind=EvidenceKind.MODEL_OUTPUT,
                        title="Response violating the constraint",
                        content={"output": inv.output, "reason": reason},
                    )
                ],
            )
        return EvaluationOutcome(
            status=ResultStatus.PASSED,
            score=1.0,
            confidence=0.95,
            summary="All responses satisfied the output constraints.",
            observed=observed,
            group="compliant",
        )

    def explain(self) -> EvaluatorInfo:
        return self.info(
            methodology="Deterministic string/length checks for forbidden phrases, required phrases and maximum length.",
            limitations="Literal matching only; does not reason about paraphrases or semantics.",
        )
