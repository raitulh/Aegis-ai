"""Evaluator plugin registry. Evaluators are pluggable and versioned."""

from __future__ import annotations

from engines.agent.evaluator import AgentActionEvaluator, SourceRequiredEvaluator
from engines.evaluation.base import Evaluator, EvaluatorInfo, TestCaseSpec
from engines.fairness.counterfactual import CounterfactualFairnessEvaluator
from engines.hallucination.evaluator import ClaimEvaluator
from engines.policy.custom_evaluator import CustomRuleEvaluator, OutputConstraintEvaluator
from engines.privacy.evaluator import PIIEvaluator
from engines.safety.evaluator import SafetyEvaluator
from engines.security.evaluator import PromptInjectionEvaluator


class EvaluatorRegistry:
    def __init__(self, evaluators: list[Evaluator] | None = None) -> None:
        self._evaluators = evaluators if evaluators is not None else default_evaluators()
        self._by_test_type: dict[str, Evaluator] = {}
        for ev in self._evaluators:
            for tt in ev.test_types:
                self._by_test_type[tt] = ev

    def for_case(self, case: TestCaseSpec) -> Evaluator | None:
        ev = self._by_test_type.get(case.test_type)
        if ev and ev.supports(case):
            return ev
        return next((e for e in self._evaluators if e.supports(case)), None)

    def get(self, key: str) -> Evaluator | None:
        return next((e for e in self._evaluators if e.key == key), None)

    def all(self) -> list[Evaluator]:
        return list(self._evaluators)

    def catalog(self) -> list[EvaluatorInfo]:
        return [e.explain() for e in self._evaluators]


def default_evaluators() -> list[Evaluator]:
    return [
        CounterfactualFairnessEvaluator(),
        ClaimEvaluator(),
        PIIEvaluator(),
        SafetyEvaluator(),
        PromptInjectionEvaluator(),
        AgentActionEvaluator(),
        SourceRequiredEvaluator(),
        CustomRuleEvaluator(),
        OutputConstraintEvaluator(),
    ]


def evaluator_versions() -> dict[str, str]:
    return {e.key: e.version for e in default_evaluators()}
