"""Recovery playbook and lesson extraction for classified failures.

Recovery actions are *proposals*. Actions that increase resource consumption, change the environment or
touch policy are flagged ``requires_approval``; the application layer routes them through the policy engine
before anything is retried. Nothing here grants permissions or loosens limits by itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.lab.enums import FailureType
from engines.lab.failures.classifier import FailureClassification


@dataclass
class RecoveryAction:
    kind: str
    description: str
    spec_patch: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    auto_applicable: bool = False
    retry: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "spec_patch": self.spec_patch,
            "requires_approval": self.requires_approval,
            "auto_applicable": self.auto_applicable,
            "retry": self.retry,
        }


@dataclass(frozen=True)
class RecoveryLimits:
    max_timeout_seconds: int = 6 * 3600
    max_memory_mb: int = 32_768
    default_timeout_seconds: int = 600
    default_memory_mb: int = 1024
    max_seeds: int = 20


def propose_recovery(
    classification: FailureClassification, spec: dict[str, Any], limits: RecoveryLimits | None = None
) -> list[RecoveryAction]:
    """Deterministic recovery proposals for a failure, given the experiment spec (as a dict)."""
    limits = limits or RecoveryLimits()
    resources = dict(spec.get("resources") or {})
    rule = classification.rule_id
    ftype = classification.failure_type
    actions: list[RecoveryAction] = []

    if rule == "resource.timeout":
        current = int(resources.get("timeout_seconds", limits.default_timeout_seconds))
        new = min(current * 2, limits.max_timeout_seconds)
        if new > current:
            actions.append(
                RecoveryAction(
                    kind="increase_timeout",
                    description=f"Double the wall-clock timeout ({current}s → {new}s)",
                    spec_patch={"resources": {**resources, "timeout_seconds": new}},
                    requires_approval=new > limits.default_timeout_seconds * 4,
                    auto_applicable=True,
                )
            )
        actions.append(
            RecoveryAction(
                kind="reduce_workload",
                description="Reduce problem size / iterations for a pilot run",
                requires_approval=False,
                auto_applicable=False,
            )
        )
    elif rule == "resource.oom":
        current = int(resources.get("memory_mb", limits.default_memory_mb))
        new = min(current * 2, limits.max_memory_mb)
        if new > current:
            actions.append(
                RecoveryAction(
                    kind="increase_memory",
                    description=f"Double the memory limit ({current}MB → {new}MB)",
                    spec_patch={"resources": {**resources, "memory_mb": new}},
                    requires_approval=new > limits.default_memory_mb * 4,
                    auto_applicable=True,
                )
            )
        params = dict(spec.get("parameters") or {})
        for key in ("batch_size", "chunk_size", "n_samples"):
            if isinstance(params.get(key), int) and params[key] > 1:
                actions.append(
                    RecoveryAction(
                        kind="reduce_parameter",
                        description=f"Halve '{key}' to reduce peak memory",
                        spec_patch={"parameters": {**params, key: max(1, params[key] // 2)}},
                        auto_applicable=True,
                    )
                )
                break
    elif rule == "code.dependency":
        actions.append(
            RecoveryAction(
                kind="change_environment",
                description="Select an execution environment that provides the missing package (network is denied at runtime)",
                requires_approval=True,
            )
        )
    elif ftype == FailureType.CODE_FAILURE:
        actions.append(
            RecoveryAction(
                kind="regenerate_code",
                description="Regenerate the code with the error trace as feedback (CodingAgent)",
                auto_applicable=True,
            )
        )
    elif ftype == FailureType.DATA_FAILURE:
        actions.append(
            RecoveryAction(
                kind="verify_dataset",
                description="Verify the dataset version, split mounts and schema before retrying",
                retry=False,
            )
        )
    elif ftype == FailureType.MODEL_FAILURE:
        params = dict(spec.get("parameters") or {})
        lr = params.get("learning_rate")
        if isinstance(lr, int | float) and lr > 0:
            actions.append(
                RecoveryAction(
                    kind="reduce_learning_rate",
                    description=f"Reduce learning_rate {lr} → {lr / 10}",
                    spec_patch={"parameters": {**params, "learning_rate": lr / 10}},
                    auto_applicable=True,
                )
            )
        actions.append(RecoveryAction(kind="add_gradient_clipping", description="Add gradient clipping / NaN guards"))
    elif rule == "statistical.insufficient":
        seeds = list(spec.get("seeds") or [])
        want = min(max(len(seeds) * 2, 3), limits.max_seeds)
        extra = [s for s in range(1, 10_000) if s not in seeds][: max(want - len(seeds), 0)]
        actions.append(
            RecoveryAction(
                kind="add_seeds",
                description=f"Increase repetitions to {want} seeds",
                spec_patch={"seeds": seeds + extra},
                auto_applicable=True,
            )
        )
    elif ftype == FailureType.STATISTICAL_FAILURE:
        actions.append(
            RecoveryAction(
                kind="power_analysis",
                description="Effect not significant: run a power analysis before adding seeds; do not p-hack",
                retry=False,
            )
        )
    elif ftype == FailureType.REPRODUCIBILITY_FAILURE:
        actions.append(
            RecoveryAction(
                kind="investigate_nondeterminism",
                description="Pin all seeds, disable nondeterministic kernels, compare environment digests",
                retry=False,
            )
        )
    elif ftype == FailureType.HYPOTHESIS_FAILURE:
        actions.append(
            RecoveryAction(
                kind="record_negative_result",
                description="Record the negative result; refine or reject the hypothesis (not a bug to retry)",
                retry=False,
            )
        )
    elif ftype == FailureType.POLICY_FAILURE:
        actions.append(
            RecoveryAction(
                kind="request_approval",
                description="Request human approval or adjust the plan to stay within policy",
                requires_approval=True,
                retry=False,
            )
        )
    elif ftype == FailureType.TOOL_FAILURE:
        actions.append(
            RecoveryAction(
                kind="retry_with_backoff", description="Retry the tool call with backoff", auto_applicable=True
            )
        )
    elif ftype == FailureType.EVALUATION_FAILURE:
        actions.append(
            RecoveryAction(
                kind="check_outputs",
                description="Ensure the experiment writes the outputs the harness expects",
                auto_applicable=False,
            )
        )
    elif ftype == FailureType.EXPERIMENT_DESIGN_FAILURE:
        actions.append(
            RecoveryAction(
                kind="redesign",
                description="Revise the experiment design to address validation issues",
                retry=False,
            )
        )
    elif ftype == FailureType.STRATEGY_FAILURE:
        actions.append(
            RecoveryAction(kind="retire_or_mutate", description="Retire the strategy or mutate from a surviving parent")
        )
    return actions


def extract_lesson(
    classification: FailureClassification,
    *,
    context: str,
    recovery: RecoveryAction | None = None,
    resolved: bool | None = None,
) -> str:
    """A compact, factual lesson suitable for failure memory (no speculation beyond the evidence)."""
    base = f"In {context}: {classification.failure_type.value.replace('_', ' ')} — {classification.root_cause}."
    if recovery is None:
        return base
    outcome = {True: "resolved it", False: "did not resolve it", None: "has not been tested yet"}[resolved]
    return f"{base} Recovery '{recovery.kind}' ({recovery.description}) {outcome}."
