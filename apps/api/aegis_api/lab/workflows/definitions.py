"""Registry of workflow kinds (``FLOW_KINDS``: kind → async flow function) and the generic flows.

Domain flows (``MissionWorkflow``, ``ExperimentWorkflow``, …) are registered by their owning context with
:func:`register_flow` in ``aegis_api.lab.<context>.flows`` (listed in :data:`FLOW_MODULES`, imported lazily
by the engines, the Temporal worker and the launcher). Every registered kind automatically gets a Temporal
workflow class of the same name (see ``temporal_workflows``) and runs unchanged on the local engine.

This module is pure (no IO) and safe to import inside the Temporal workflow sandbox.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from aegis_api.lab.workflows.ctx import FlowFn, WorkflowContext

log = structlog.get_logger("aegis.lab.workflows")

# Workflow kinds named by the platform contract (flows for most of them are registered by their contexts).
KNOWN_FLOW_KINDS: tuple[str, ...] = (
    "MissionWorkflow",
    "ResearchWorkflow",
    "HypothesisWorkflow",
    "ExperimentWorkflow",
    "ExperimentBatchWorkflow",
    "EvaluationWorkflow",
    "VerificationWorkflow",
    "EvolutionWorkflow",
    "DiscoveryWorkflow",
    "ReportWorkflow",
    "DatasetProcessingWorkflow",
    "ArtifactProcessingWorkflow",
    "AgentRunWorkflow",
    "ExecutionJobWorkflow",
)

# Modules that register flows (``register_flow``); missing modules are tolerated.
FLOW_MODULES: tuple[str, ...] = tuple(
    f"aegis_api.lab.{context}.flows"
    for context in (
        "missions",
        "research",
        "knowledge",
        "hypotheses",
        "experiments",
        "evaluation",
        "failures",
        "strategies",
        "verification",
        "data",
        "agents",
        "execution",
    )
)

# Kinds double as Temporal workflow type names and generated class names: identifier-like, ≤ 48 chars.
_KIND_RE = re.compile(r"^[A-Z][A-Za-z0-9_]{0,47}$")


class UnknownFlowKind(LookupError):
    """No flow is registered for the requested workflow kind."""


@dataclass(frozen=True)
class FlowOptions:
    """Per-kind execution options shared by both engines."""

    execution_timeout_seconds: int | None = None  # whole-run deadline → TIMED_OUT
    description: str = ""


FLOW_KINDS: dict[str, FlowFn] = {}
FLOW_OPTIONS: dict[str, FlowOptions] = {}


def validate_kind(kind: str) -> str:
    if not isinstance(kind, str) or not _KIND_RE.match(kind):
        raise ValueError(f"Invalid workflow kind {kind!r}: an identifier starting with a capital, ≤ 48 chars")
    return kind


def register_flow(
    kind: str,
    fn: FlowFn,
    *,
    execution_timeout_seconds: int | None = None,
    description: str = "",
    replace: bool = False,
) -> FlowFn:
    """Register ``fn`` as the flow for ``kind`` (idempotent for the same function)."""
    validate_kind(kind)
    if not callable(fn):
        raise TypeError("flow must be an async callable (ctx, input) -> dict")
    existing = FLOW_KINDS.get(kind)
    if existing is not None and existing is not fn and not replace:
        raise ValueError(f"Workflow kind '{kind}' is already registered")
    if execution_timeout_seconds is not None and execution_timeout_seconds <= 0:
        raise ValueError("execution_timeout_seconds must be positive")
    FLOW_KINDS[kind] = fn
    FLOW_OPTIONS[kind] = FlowOptions(execution_timeout_seconds=execution_timeout_seconds, description=description)
    return fn


def flow(kind: str, **options: Any) -> Callable[[FlowFn], FlowFn]:
    """Decorator form of :func:`register_flow`."""

    def decorator(fn: FlowFn) -> FlowFn:
        return register_flow(kind, fn, **options)

    return decorator


def load_flows() -> dict[str, FlowFn]:
    """Import every module in :data:`FLOW_MODULES` (missing modules are skipped)."""
    for module in FLOW_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise
    return FLOW_KINDS


def has_flow(kind: str) -> bool:
    if kind in FLOW_KINDS:
        return True
    load_flows()
    return kind in FLOW_KINDS


def get_flow(kind: str) -> FlowFn:
    fn = FLOW_KINDS.get(kind)
    if fn is None:
        load_flows()
        fn = FLOW_KINDS.get(kind)
    if fn is None:
        raise UnknownFlowKind(f"No flow is registered for workflow kind '{kind}'")
    return fn


def flow_options(kind: str) -> FlowOptions:
    return FLOW_OPTIONS.get(kind, FlowOptions())


# ------------------------------------------------------------------------------------------------
# Generic flows
# ------------------------------------------------------------------------------------------------
def _require_id(data: Mapping[str, Any], key: str, kind: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{kind} input requires '{key}'")
    return value


async def execution_job_flow(ctx: WorkflowContext, input: dict[str, Any]) -> dict[str, Any]:
    """Run one submitted compute job on the execution task queue and return its ``JobResult``."""
    job_id = _require_id(input, "job_id", "ExecutionJobWorkflow")
    return await ctx.activity("execution.run_job", {"job_id": job_id}, step_key="execution.run_job")


_AGENT_RUN_KEYS = ("role", "project_id", "mission_id", "input", "parent_run_id")


async def agent_run_flow(ctx: WorkflowContext, input: dict[str, Any]) -> dict[str, Any]:
    """Execute (or resume) one persisted agent run; ``agents.run`` is idempotent per ``agent_run_id``."""
    body: dict[str, Any] = {"agent_run_id": _require_id(input, "agent_run_id", "AgentRunWorkflow")}
    for key in _AGENT_RUN_KEYS:
        if input.get(key) is not None:
            body[key] = input[key]
    return await ctx.activity("agents.run", body, step_key="agents.run")


register_flow(
    "ExecutionJobWorkflow",
    execution_job_flow,
    description="Runs a submitted sandboxed compute job (execution.run_job).",
)
register_flow(
    "AgentRunWorkflow",
    agent_run_flow,
    description="Executes a persisted agent run (agents.run).",
)
