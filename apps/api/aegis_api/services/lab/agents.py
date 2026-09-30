"""Agent runtime: persisted, observable, schema-validated agent runs.

State machine (persisted on ``lab.agent_runs`` after every transition, each in its own short transaction):
    created → planning → executing ⇄ waiting_tool → evaluating → completed | failed | cancelled

An agent run gets: a versioned prompt built from segregated sections (system policy / role instructions /
mission instructions / fenced untrusted data), a model routed by the ModelGateway, the tools allowed for its
role ∩ the mission's allowlist ∩ its agent-version configuration, a step budget and a deadline. Its final
output must validate against the role's output schema — invalid output fails the run; it is never patched.
Agents act on behalf of the principal that launched the mission and can never change autonomy or permissions
(there is no tool for either, and the policy engine denies both for non-human actors).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select

from aegis_api.config import get_settings
from aegis_api.db.base import utcnow
from aegis_api.db.session import session_scope
from aegis_api.errors import AppError, ValidationFailed
from aegis_api.infrastructure.llm.base import LLMError
from aegis_api.infrastructure.llm.schemas import LLMRequest, ToolCallRequest, ToolResultMessage, Turn
from aegis_api.infrastructure.observability import metrics
from aegis_api.models.lab import Agent, AgentMessageRecord, AgentRun, AgentVersion, Mission
from aegis_api.security.context import Principal
from aegis_api.services import audit_log
from aegis_api.services.lab import events, prompts
from aegis_api.services.lab.common import Actor, sha256_json
from aegis_api.services.lab.model_gateway import CallContext, StructuredOutputError, get_gateway
from aegis_api.services.lab.tools import ToolContext, get_broker
from engines.lab.agents.roles import AgentRoleSpec, spec_for
from engines.lab.agents.schemas import json_schema
from engines.lab.enums import AgentRunStatus, LabEventType
from engines.lab.prompts.registry import UntrustedBlock, build_prompt
from engines.lab.security.messages import MessageRejected, build_message, verify
from engines.lab.security.prompt_injection import wrap_untrusted
from engines.lab.state_machines import AGENT_RUN

log = structlog.get_logger("aegis.lab.agents")

# Agent-version configuration keys an organization may set (all *narrowing*).
AGENT_CONFIG_KEYS = frozenset(
    {"tools", "max_steps", "timeout_seconds", "model_pins", "max_model_tier", "temperature", "max_output_tokens"}
)


class AgentRunFailed(Exception):
    def __init__(self, message: str, *, run_id: uuid.UUID, code: str = "agent_failed", retryable: bool = False) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.code = code
        self.retryable = retryable


class AgentCancelled(Exception):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__("agent run cancelled")
        self.run_id = run_id


@dataclass
class AgentTask:
    organization_id: uuid.UUID
    role: str
    variables: dict[str, Any]
    mission_instructions: str
    actor: Actor
    project_id: uuid.UUID | None = None
    mission_id: uuid.UUID | None = None
    workflow_run_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None
    purpose: str | None = None
    research_data: list[UntrustedBlock] = field(default_factory=list)
    complexity: float = 0.5
    input_summary: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None


@dataclass
class AgentResult:
    run_id: uuid.UUID
    output: dict[str, Any]
    provider: str
    model: str
    cost_usd: float | None
    input_tokens: int
    output_tokens: int
    steps: int
    tools_used: list[str]
    source_ids: list[str]
    injection_score: float
    prompt_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "output": self.output,
            "provider": self.provider,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "steps": self.steps,
            "tools_used": self.tools_used,
            "source_ids": self.source_ids,
            "injection_score": self.injection_score,
            "prompt_hash": self.prompt_hash,
        }


@dataclass
class _Resolved:
    spec: AgentRoleSpec
    agent_id: uuid.UUID | None
    agent_version_id: uuid.UUID | None
    config: dict[str, Any]
    tools: frozenset[str]
    max_steps: int
    timeout_seconds: int


def _resolve(db: Any, task: AgentTask, mission: Mission | None) -> _Resolved:
    spec = spec_for(task.role)
    config: dict[str, Any] = {}
    agent_id = version_id = None
    stmt = select(Agent).where(
        Agent.organization_id == task.organization_id, Agent.role == spec.role.value, Agent.status == "active"
    )
    agents = db.scalars(stmt).all()
    agent = next((a for a in agents if a.project_id == task.project_id), None) or next(
        (a for a in agents if a.project_id is None), None
    )
    if agent is not None and agent.current_version_id:
        version = db.get(AgentVersion, agent.current_version_id)
        if version is not None:
            config = {k: v for k, v in (version.config or {}).items() if k in AGENT_CONFIG_KEYS}
            agent_id, version_id = agent.id, version.id
    tools = set(spec.default_tools)
    if "tools" in config:
        tools &= set(config["tools"])  # agent versions can only narrow the role's tools
    if mission is not None and mission.allowed_tools:
        tools &= set(mission.allowed_tools)
    settings = get_settings()
    max_steps = min(int(config.get("max_steps", spec.max_steps)), spec.max_steps, settings.agent_max_steps)
    timeout = min(int(config.get("timeout_seconds", spec.timeout_seconds)), spec.timeout_seconds)
    return _Resolved(spec, agent_id, version_id, config, frozenset(tools), max(1, max_steps), max(30, timeout))


def _transition(run_id: uuid.UUID, organization_id: uuid.UUID, target: str, **fields: Any) -> None:
    with session_scope(organization_id) as db:
        run = db.get(AgentRun, run_id)
        assert run is not None
        run.status = AGENT_RUN.ensure(run.status, target)
        for k, v in fields.items():
            setattr(run, k, v)


def _mission_cancelled(organization_id: uuid.UUID, mission_id: uuid.UUID | None) -> bool:
    if mission_id is None:
        return False
    with session_scope(organization_id) as db:
        mission = db.get(Mission, mission_id)
        return bool(mission and (mission.cancel_requested or mission.status in ("cancelled", "paused")))


class AgentRuntime:
    def run(self, task: AgentTask) -> AgentResult:
        started = time.perf_counter()
        with session_scope(task.organization_id) as db:
            mission = db.get(Mission, task.mission_id) if task.mission_id else None
            resolved = _resolve(db, task, mission)
            template = prompts.resolve(db, task.organization_id, resolved.spec.prompt)
            system_policy = prompts.resolve(db, task.organization_id, "system.policy")
            try:
                built = build_prompt(
                    template=template,
                    variables=task.variables,
                    mission_instructions=task.mission_instructions,
                    research_data=task.research_data,
                    system_policy=system_policy,
                )
            except ValueError as exc:
                raise ValidationFailed(f"prompt build failed for {task.role}: {exc}") from exc
            settings = get_settings()
            model_params: dict[str, Any] = {
                "temperature": resolved.config.get("temperature"),
                "max_output_tokens": min(
                    int(resolved.config.get("max_output_tokens", settings.llm_max_output_tokens)),
                    settings.llm_max_output_tokens,
                ),
            }
            run = AgentRun(
                organization_id=task.organization_id,
                project_id=task.project_id,
                mission_id=task.mission_id,
                agent_id=resolved.agent_id,
                agent_version_id=resolved.agent_version_id,
                parent_run_id=task.parent_run_id,
                workflow_run_id=task.workflow_run_id,
                role=resolved.spec.role.value,
                purpose=(task.purpose or "")[:64] or None,
                status=AgentRunStatus.CREATED,
                input={
                    "variables": {k: (str(v)[:2000]) for k, v in task.variables.items()},
                    "research_blocks": [{"source": b.source, "source_id": b.source_id} for b in task.research_data],
                    **task.input_summary,
                },
                prompt_ref=built.template_ref,
                prompt_template_sha256=built.template_sha256,
                prompt_hash=built.prompt_hash,
                model_params=model_params,
                tools_used=[],
                injection_score=built.max_injection_score,
                trace_id=task.trace_id,
                started_at=utcnow(),
                deadline_at=utcnow() + timedelta(seconds=resolved.timeout_seconds),
            )
            db.add(run)
            db.flush()
            run_id = run.id
            if task.mission_id:
                events.emit(
                    db,
                    organization_id=task.organization_id,
                    mission_id=task.mission_id,
                    project_id=task.project_id,
                    event_type=LabEventType.AGENT_STARTED,
                    message=f"{resolved.spec.role.value} agent started"
                    + (f" ({task.purpose})" if task.purpose else ""),
                    data={"agent_run_id": str(run_id), "role": resolved.spec.role.value, "prompt": built.template_ref},
                    actor=task.actor,
                    trace_id=task.trace_id,
                )
        agent_actor = Actor.agent(run_id, resolved.spec.role.value)
        tool_ctx = ToolContext(
            organization_id=task.organization_id,
            project_id=task.project_id,
            mission_id=task.mission_id,
            agent_run_id=run_id,
            role=resolved.spec.role.value,
            actor=agent_actor,
            allowed_tools=resolved.tools,
            trace_id=task.trace_id,
        )
        broker = get_broker()
        tool_specs = broker.tools_for(tool_ctx) if resolved.tools else []
        gateway = get_gateway()
        call_ctx = CallContext(
            organization_id=task.organization_id,
            project_id=task.project_id,
            mission_id=task.mission_id,
            agent_run_id=run_id,
            actor=agent_actor,
            pinned=dict(resolved.config.get("model_pins") or {}),
        )
        schema = json_schema(resolved.spec.output_model)
        history: list[Turn] = [Turn(kind="user", text=built.input_text)]
        totals = {"input": 0, "output": 0, "cost": 0.0, "priced": True}
        tools_used: list[str] = []
        injection = built.max_injection_score
        provider = model = ""
        steps = 0
        deadline = time.monotonic() + resolved.timeout_seconds
        try:
            _transition(run_id, task.organization_id, AgentRunStatus.PLANNING)
            _transition(run_id, task.organization_id, AgentRunStatus.EXECUTING)
            final: dict[str, Any] | None = None
            while final is None:
                if _mission_cancelled(task.organization_id, task.mission_id):
                    raise AgentCancelled(run_id)
                if time.monotonic() > deadline:
                    raise AgentRunFailed(
                        f"agent exceeded its {resolved.timeout_seconds}s deadline", run_id=run_id, code="timeout"
                    )
                steps += 1
                offer_tools = bool(tool_specs) and steps < resolved.max_steps
                request = LLMRequest(
                    task_type=resolved.spec.task_class.value,
                    system=built.system_instruction,
                    input="",
                    history=history,
                    response_schema=schema,
                    tools=[t.definition() for t in tool_specs] if offer_tools else [],
                    temperature=model_params["temperature"],
                    max_output_tokens=int(model_params["max_output_tokens"]),
                    metadata={"agent_run_id": str(run_id), "role": resolved.spec.role.value},
                )
                result = gateway.generate(call_ctx, request, complexity=task.complexity, prompt_hash=built.prompt_hash)
                provider, model = result.candidate.provider, result.response.model
                totals["input"] += result.response.usage.input_tokens
                totals["output"] += result.response.usage.output_tokens
                if result.cost_usd is None:
                    totals["priced"] = False
                else:
                    totals["cost"] += result.cost_usd
                if result.response.tool_calls and offer_tools:
                    _transition(run_id, task.organization_id, AgentRunStatus.WAITING_TOOL, steps=steps)
                    for call in result.response.tool_calls[:4]:
                        message, tool_injection = self._tool(broker, tool_ctx, call)
                        history.append(Turn(kind="tool_call", tool_call=call))
                        history.append(Turn(kind="tool_result", tool_result=message))
                        tools_used.append(call.name)
                        injection = max(injection, tool_injection)
                    _transition(
                        run_id,
                        task.organization_id,
                        AgentRunStatus.EXECUTING,
                        tools_used=sorted(set(tools_used)),
                    )
                    continue
                if result.response.tool_calls and not offer_tools:
                    history.append(
                        Turn(kind="user", text="Tool budget exhausted. Answer now with the final JSON object only.")
                    )
                    if steps >= resolved.max_steps + 1:
                        raise AgentRunFailed("agent did not produce a final answer", run_id=run_id, code="no_answer")
                    continue
                final = result.parsed
            _transition(run_id, task.organization_id, AgentRunStatus.EVALUATING, steps=steps)
            try:
                output = resolved.spec.output_model.model_validate(final).model_dump(mode="json")
            except ValidationError as exc:
                raise AgentRunFailed(
                    f"output failed validation: {exc.errors()[:3]}", run_id=run_id, code="output_invalid"
                ) from exc
        except AgentCancelled:
            self._fail(task, run_id, AgentRunStatus.CANCELLED, "cancelled", totals, steps, tools_used, provider, model)
            raise
        except AgentRunFailed as exc:
            self._fail(task, run_id, AgentRunStatus.FAILED, str(exc), totals, steps, tools_used, provider, model)
            raise
        except StructuredOutputError as exc:
            self._fail(
                task,
                run_id,
                AgentRunStatus.FAILED,
                f"invalid structured output: {exc.errors[:3]}",
                totals,
                steps,
                tools_used,
                provider,
                model,
            )
            raise AgentRunFailed(str(exc), run_id=run_id, code="output_invalid") from exc
        except LLMError as exc:
            self._fail(
                task,
                run_id,
                AgentRunStatus.FAILED,
                f"{exc.kind.value}: {exc}",
                totals,
                steps,
                tools_used,
                provider,
                model,
            )
            raise AgentRunFailed(str(exc), run_id=run_id, code=exc.code, retryable=exc.retryable) from exc
        except AppError as exc:
            self._fail(task, run_id, AgentRunStatus.FAILED, exc.message, totals, steps, tools_used, provider, model)
            raise AgentRunFailed(exc.message, run_id=run_id, code=exc.code) from exc
        cost = round(totals["cost"], 6) if totals["priced"] else None
        with session_scope(task.organization_id) as db:
            done = db.get(AgentRun, run_id)
            assert done is not None
            done.status = AGENT_RUN.ensure(done.status, AgentRunStatus.COMPLETED)
            done.output = output
            done.output_valid = True
            done.provider, done.model = provider, model
            done.steps = steps
            done.tools_used = sorted(set(tools_used))
            done.input_tokens, done.output_tokens = int(totals["input"]), int(totals["output"])
            done.cost_usd = cost
            done.injection_score = injection
            done.completed_at = utcnow()
            if task.mission_id:
                events.emit(
                    db,
                    organization_id=task.organization_id,
                    mission_id=task.mission_id,
                    project_id=task.project_id,
                    event_type=LabEventType.AGENT_COMPLETED,
                    message=f"{resolved.spec.role.value} agent completed in {steps} step(s)",
                    data={
                        "agent_run_id": str(run_id),
                        "role": resolved.spec.role.value,
                        "model": model,
                        "tokens": int(totals["input"] + totals["output"]),
                        "cost_usd": cost,
                    },
                    actor=agent_actor,
                    trace_id=task.trace_id,
                )
        metrics.AGENT_DURATION.labels(role=resolved.spec.role.value, status="completed").observe(
            time.perf_counter() - started
        )
        return AgentResult(
            run_id=run_id,
            output=output,
            provider=provider,
            model=model,
            cost_usd=cost,
            input_tokens=int(totals["input"]),
            output_tokens=int(totals["output"]),
            steps=steps,
            tools_used=sorted(set(tools_used)),
            source_ids=list(dict.fromkeys(tool_ctx.source_ids)),
            injection_score=injection,
            prompt_hash=built.prompt_hash,
        )

    @staticmethod
    def _tool(broker: Any, ctx: ToolContext, call: ToolCallRequest) -> tuple[ToolResultMessage, float]:
        outcome = broker.invoke(ctx, call.name, call.arguments)
        fenced, _ = wrap_untrusted(
            outcome.text, source=f"tool:{call.name}", source_id=str(outcome.record_id), kind="tool_output"
        )
        message = ToolResultMessage(call_id=call.id, name=call.name, result=fenced, is_error=not outcome.ok)
        return message, outcome.injection_score

    @staticmethod
    def _fail(
        task: AgentTask,
        run_id: uuid.UUID,
        status: str,
        error: str,
        totals: dict[str, Any],
        steps: int,
        tools_used: list[str],
        provider: str,
        model: str,
    ) -> None:
        try:
            with session_scope(task.organization_id) as db:
                run = db.get(AgentRun, run_id)
                if run is None:
                    return
                if not AGENT_RUN.is_terminal(run.status):
                    run.status = AGENT_RUN.ensure(run.status, status)
                run.error = error[:4000]
                run.output_valid = False if status == AgentRunStatus.FAILED else None
                run.steps = steps
                run.tools_used = sorted(set(tools_used))
                run.provider, run.model = provider or None, model or None
                run.input_tokens, run.output_tokens = int(totals["input"]), int(totals["output"])
                run.cost_usd = round(totals["cost"], 6) if totals["priced"] and totals["cost"] else None
                run.completed_at = utcnow()
                if task.mission_id:
                    events.emit(
                        db,
                        organization_id=task.organization_id,
                        mission_id=task.mission_id,
                        project_id=task.project_id,
                        event_type=LabEventType.AGENT_FAILED,
                        message=f"{run.role} agent {status}: {error[:300]}",
                        data={"agent_run_id": str(run_id), "role": run.role, "error": error[:500]},
                        level="warning" if status == AgentRunStatus.CANCELLED else "error",
                        trace_id=task.trace_id,
                    )
        except Exception:
            log.exception("agent_fail_record_failed", run_id=str(run_id))
        metrics.AGENT_DURATION.labels(role=task.role, status=status).observe(0)


def handoff(
    *,
    organization_id: uuid.UUID,
    mission_id: uuid.UUID,
    sender_run_id: uuid.UUID,
    sender_role: str,
    receiver_role: str,
    message_type: str,
    payload: dict[str, Any],
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Signed inter-agent message: built, signed, verified and recorded before the receiver sees the payload.
    Verification binds mission, organization and sender role; tampered/forged messages are rejected."""
    key = get_settings().effective_agent_message_key
    message_id = uuid.uuid4().hex
    message = build_message(
        key=key,
        message_id=message_id,
        sender_agent_id=str(sender_run_id),
        sender_role=sender_role,
        receiver_agent_id=f"role:{receiver_role}",
        mission_id=str(mission_id),
        trace_id=trace_id or uuid.uuid4().hex,
        organization_id=str(organization_id),
        message_type=message_type,
        payload=payload,
    )
    raw = message.model_dump(mode="json")
    accepted, reason = True, None
    try:
        verify(
            raw,
            key,
            expected_receiver=f"role:{receiver_role}",
            expected_mission=str(mission_id),
            expected_organization=str(organization_id),
            known_sender_roles={str(sender_run_id): sender_role},
        )
    except MessageRejected as exc:
        accepted, reason = False, str(exc)
    with session_scope(organization_id) as db:
        db.add(
            AgentMessageRecord(
                organization_id=organization_id,
                mission_id=mission_id,
                message_id=message_id,
                sender_run_id=sender_run_id,
                message_type=message_type[:24],
                envelope={k: v for k, v in raw.items() if k != "payload"} | {"payload_sha256": sha256_json(payload)},
                accepted=accepted,
                rejection_reason=reason,
            )
        )
    if not accepted:
        raise ValidationFailed(f"inter-agent message rejected: {reason}")
    return {"message_id": message_id, "payload": payload}


# --- agent definitions (versioned configuration) --------------------------------------------------------------


def create_agent(
    db: Any,
    principal: Principal,
    *,
    role: str,
    name: str,
    description: str | None,
    config: dict[str, Any],
    project_id: uuid.UUID | None = None,
) -> tuple[Agent, AgentVersion]:
    spec = spec_for(role)
    _validate_config(spec, config)
    agent = Agent(
        organization_id=principal.organization_id,
        project_id=project_id,
        role=spec.role.value,
        name=name[:120],
        description=description,
        status="active",
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(agent)
    db.flush()
    version = new_version(db, principal, agent, config=config, change_note="initial version")
    return agent, version


def new_version(
    db: Any, principal: Principal, agent: Agent, *, config: dict[str, Any], change_note: str | None
) -> AgentVersion:
    spec = spec_for(agent.role)
    _validate_config(spec, config)
    number = 1 + (
        db.scalar(
            select(AgentVersion.version)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version.desc())
            .limit(1)
        )
        or 0
    )
    version = AgentVersion(
        organization_id=agent.organization_id,
        agent_id=agent.id,
        version=number,
        config=config,
        config_sha256=sha256_json(config),
        change_note=change_note,
        created_by_id=principal.user_id if principal.is_human else None,
    )
    db.add(version)
    db.flush()
    agent.current_version_id = version.id
    audit_log.record(
        db,
        organization_id=agent.organization_id,
        action="lab.agent.version_created",
        resource_type="agent",
        resource_id=agent.id,
        principal=principal,
        after={"version": number, "config_sha256": version.config_sha256},
    )
    return version


def _validate_config(spec: AgentRoleSpec, config: dict[str, Any]) -> None:
    unknown = set(config) - AGENT_CONFIG_KEYS
    if unknown:
        raise ValidationFailed(f"unsupported agent config keys: {', '.join(sorted(unknown))}")
    tools = config.get("tools")
    if tools is not None:
        extra = set(tools) - set(spec.default_tools)
        if extra:
            raise ValidationFailed(
                f"agent versions can only narrow the {spec.role.value} role's tools; not allowed: {sorted(extra)}"
            )
    if int(config.get("max_steps", 1)) < 1 or int(config.get("timeout_seconds", 60)) < 30:
        raise ValidationFailed("max_steps must be ≥ 1 and timeout_seconds ≥ 30")


_RUNTIME = AgentRuntime()


def get_runtime() -> AgentRuntime:
    return _RUNTIME
