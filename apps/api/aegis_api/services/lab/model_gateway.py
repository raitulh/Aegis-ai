"""ModelGateway: the only path from lab code to a model provider.

Per call:
1. *Short transaction*: load the organization's model catalogue overrides and routing policy (external
   providers require ``allow_external_models`` consent), check budgets via the policy engine, route.
2. *No transaction*: call the provider with bounded retries (only retryable error kinds); on exhausted
   transient failures, fail over to the next eligible candidate chosen by the same router/policy.
3. Validate structured output against the JSON schema; one repair round-trip on invalid output, otherwise the
   call fails (outputs are never silently "fixed").
4. *Short transaction*: record usage (tokens, latency, cost only if priced, retries, prompt hash, routing
   reason) for success and failure alike.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import or_, select

from aegis_api.config import get_settings
from aegis_api.db.session import session_scope
from aegis_api.errors import BudgetExceeded, PolicyDenied, ServiceUnavailable
from aegis_api.infrastructure.llm.base import LLMError, LLMErrorKind, call_with_retry
from aegis_api.infrastructure.llm.jsonschema_utils import inline_refs, parse_json_output, validate
from aegis_api.infrastructure.llm.router import ProviderRegistry, get_registry
from aegis_api.infrastructure.llm.schemas import LLMRequest, LLMResponse, Turn
from aegis_api.infrastructure.observability import metrics
from aegis_api.models import Project
from aegis_api.models.lab import Mission, ModelConfig
from aegis_api.services import quota_service
from aegis_api.services.lab import policy as lab_policy
from aegis_api.services.lab import usage
from aegis_api.services.lab.common import SYSTEM_ACTOR, Actor
from engines.lab.budgets import Spend
from engines.lab.enums import ModelTier
from engines.lab.routing import ModelCandidate, ModelRouter, RouteDecision, RoutingPolicy

log = structlog.get_logger("aegis.lab.gateway")

MAX_FAILOVERS = 2


@dataclass
class CallContext:
    organization_id: uuid.UUID
    project_id: uuid.UUID | None = None
    mission_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    research_task_id: uuid.UUID | None = None
    actor: Actor = SYSTEM_ACTOR
    request_id: str | None = None
    pinned: dict[str, str] = field(default_factory=dict)
    enforce_budget: bool = True


@dataclass
class GatewayResult:
    response: LLMResponse
    decision: RouteDecision
    candidate: ModelCandidate
    cost_usd: float | None
    cost_basis: str
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def parsed(self) -> dict[str, Any] | None:
        return self.response.parsed


class StructuredOutputError(Exception):
    def __init__(self, message: str, errors: list[str], response: LLMResponse | None = None) -> None:
        super().__init__(message)
        self.errors = errors
        self.response = response


@dataclass
class _Plan:
    registry: ProviderRegistry
    router: ModelRouter
    policy: RoutingPolicy
    decision: RouteDecision


class ModelGateway:
    def __init__(self, registry_factory: Any = None) -> None:
        self._registry_factory = registry_factory or get_registry

    # --- routing ---------------------------------------------------------------------------------------
    def _plan(
        self,
        ctx: CallContext,
        task_type: str,
        *,
        complexity: float,
        latency_budget_ms: int | None,
        cost_budget_usd: float | None,
        required_features: frozenset[str],
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> _Plan:
        with session_scope(ctx.organization_id) as db:
            overrides = [
                {
                    "provider": m.provider,
                    "model": m.model,
                    "tier": m.tier,
                    "features": list(m.features or []) or None,
                    "input_per_mtok": m.input_per_mtok,
                    "output_per_mtok": m.output_per_mtok,
                    "typical_latency_ms": m.typical_latency_ms,
                    "is_agent": m.is_agent,
                    "enabled": m.enabled,
                }
                for m in db.scalars(
                    select(ModelConfig)
                    .where(
                        or_(ModelConfig.organization_id.is_(None), ModelConfig.organization_id == ctx.organization_id)
                    )
                    .order_by(ModelConfig.organization_id.is_not(None))
                ).all()
            ]
            quota = quota_service.get_quota(db, ctx.organization_id)
            mission = db.get(Mission, ctx.mission_id) if ctx.mission_id else None
            project = db.get(Project, ctx.project_id) if ctx.project_id else None
            pinned = dict((mission.config or {}).get("model_pins", {})) if mission else {}
            pinned.update(ctx.pinned)
            routing_policy = RoutingPolicy(
                allow_external=bool(quota.allow_external_models),
                pinned=pinned,
                max_tier=ModelTier((mission.config or {})["max_model_tier"])
                if mission and (mission.config or {}).get("max_model_tier")
                else None,
            )
            registry = self._registry_factory([o for o in overrides if o["enabled"] is not None])
            router = ModelRouter(registry.available_candidates())
            decision = router.route(
                task_type,
                complexity,
                latency_budget_ms,
                cost_budget_usd,
                routing_policy,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                required_features=required_features,
            )
            if not decision.ok:
                reasons = "; ".join(f"{k}: {why}" for k, why in decision.rejected[:6]) or "no models configured"
                hint = (
                    " External providers need the organization's data-processing consent (allow_external_models)."
                    if not quota.allow_external_models
                    else ""
                )
                raise ServiceUnavailable(f"{decision.reason}. {reasons}.{hint}", code="no_eligible_model")
            if ctx.enforce_budget:
                est_tokens = decision.estimated_input_tokens + decision.estimated_output_tokens
                estimate = Spend(
                    total_cost=decision.estimated_cost_usd or 0.0,
                    llm_cost=decision.estimated_cost_usd or 0.0,
                    llm_tokens=est_tokens,
                )
                budget = usage.combined_check(
                    db, ctx.organization_id, mission=mission, project=project, estimate=estimate
                )
                result = lab_policy.evaluate(
                    db,
                    organization_id=ctx.organization_id,
                    action="model.call",
                    facts=lab_policy.base_facts(
                        db,
                        ctx.organization_id,
                        ctx.actor,
                        mission=mission,
                        extra={
                            "budget": budget,
                            "model": {"provider": decision.candidate.provider, "model": decision.candidate.model}
                            if decision.candidate
                            else {},
                            "task_type": task_type,
                            "estimated_cost": decision.estimated_cost_usd or 0.0,
                        },
                    ),
                    actor=ctx.actor,
                    resource_type="model_call",
                )
                if result.denied:
                    raise PolicyDenied("; ".join(result.reasons), details=result.to_dict())
                if result.needs_approval:
                    raise BudgetExceeded(
                        "Model call blocked: " + "; ".join(result.reasons),
                        details={"policy": result.to_dict(), "budget": budget},
                    )
            return _Plan(registry=registry, router=router, policy=routing_policy, decision=decision)

    # --- calls -----------------------------------------------------------------------------------------
    def generate(
        self,
        ctx: CallContext,
        request: LLMRequest,
        *,
        complexity: float = 0.5,
        latency_budget_ms: int | None = None,
        cost_budget_usd: float | None = None,
        required_features: frozenset[str] = frozenset(),
        prompt_hash: str | None = None,
    ) -> GatewayResult:
        settings = get_settings()
        plan = self._plan(
            ctx,
            request.task_type,
            complexity=complexity,
            latency_budget_ms=latency_budget_ms,
            cost_budget_usd=cost_budget_usd,
            required_features=required_features | (frozenset({"tools"}) if request.tools else frozenset()),
            input_tokens=None,
            output_tokens=None,
        )
        if request.response_schema is not None:
            request = request.model_copy(update={"response_schema": inline_refs(request.response_schema)})
        candidates = [plan.decision.candidate] + [
            c for c in plan.registry.available_candidates() if c.key in plan.decision.alternatives
        ]
        attempts: list[dict[str, Any]] = []
        last_error: LLMError | None = None
        for candidate in [c for c in candidates if c is not None][: MAX_FAILOVERS + 1]:
            provider = plan.registry.provider(candidate.provider)
            started = time.perf_counter()
            try:
                model_id = candidate.model

                def _call(p: Any = provider, m: str = model_id, r: LLMRequest = request) -> LLMResponse:
                    return p.generate(r, model=m)

                response, retries = call_with_retry(
                    _call, max_retries=settings.gemini_max_retries if candidate.provider == "gemini" else 2
                )
                response.retries = retries
                if request.response_schema is not None:
                    response = self._ensure_structured(provider, candidate, request, response)
                latency = int((time.perf_counter() - started) * 1000)
                cost = candidate.estimate_cost(response.usage.input_tokens, response.usage.output_tokens)
                basis = "configured per-token price" if cost is not None else "no price configured for model"
                self._record(ctx, request, candidate, plan.decision, response, latency, cost, basis, None, prompt_hash)
                attempts.append({"model": candidate.key, "outcome": "ok", "retries": retries})
                return GatewayResult(
                    response=response,
                    decision=plan.decision,
                    candidate=candidate,
                    cost_usd=cost,
                    cost_basis=basis,
                    attempts=attempts,
                )
            except StructuredOutputError as exc:
                latency = int((time.perf_counter() - started) * 1000)
                self._record(
                    ctx,
                    request,
                    candidate,
                    plan.decision,
                    exc.response,
                    latency,
                    None,
                    "",
                    "output_invalid",
                    prompt_hash,
                )
                raise
            except LLMError as exc:
                latency = int((time.perf_counter() - started) * 1000)
                self._record(ctx, request, candidate, plan.decision, None, latency, None, "", exc.code, prompt_hash)
                attempts.append({"model": candidate.key, "outcome": exc.kind.value})
                last_error = exc
                if not exc.retryable:
                    raise
                log.warning("model_failover", model=candidate.key, kind=exc.kind.value)
        assert last_error is not None
        raise last_error

    def _ensure_structured(
        self, provider: Any, candidate: ModelCandidate, request: LLMRequest, response: LLMResponse
    ) -> LLMResponse:
        if response.tool_calls:
            return response  # the caller runs the tool loop; structured output is validated on the final turn
        schema = request.response_schema or {}
        parsed = response.parsed if response.parsed is not None else parse_json_output(response.text)
        errors = ["output is not a JSON object"] if parsed is None else validate(parsed, schema)
        if not errors:
            response.parsed = parsed
            return response
        history = list(request.history) or [Turn(kind="user", text=request.input)]
        history += [
            Turn(kind="model", text=response.text[:20000]),
            Turn(
                kind="user",
                text="Your previous answer did not match the required JSON schema. Errors:\n- "
                + "\n- ".join(errors[:10])
                + "\nReturn only a corrected JSON object that satisfies the schema.",
            ),
        ]
        repair = request.model_copy(update={"history": history, "input": ""})
        fixed, _ = call_with_retry(lambda: provider.generate(repair, model=candidate.model), max_retries=1)
        parsed = fixed.parsed if fixed.parsed is not None else parse_json_output(fixed.text)
        errors = ["output is not a JSON object"] if parsed is None else validate(parsed, schema)
        fixed.usage.input_tokens += response.usage.input_tokens
        fixed.usage.output_tokens += response.usage.output_tokens
        if errors:
            raise StructuredOutputError("model output failed schema validation after one repair", errors, fixed)
        fixed.parsed = parsed
        return fixed

    def _record(
        self,
        ctx: CallContext,
        request: LLMRequest,
        candidate: ModelCandidate,
        decision: RouteDecision,
        response: LLMResponse | None,
        latency_ms: int,
        cost: float | None,
        basis: str,
        error_code: str | None,
        prompt_hash: str | None,
    ) -> None:
        outcome = "ok" if error_code is None else error_code
        metrics.LLM_REQUESTS.labels(
            provider=candidate.provider, model=candidate.model, task_type=request.task_type, outcome=outcome
        ).inc()
        metrics.LLM_LATENCY.labels(
            provider=candidate.provider, model=candidate.model, task_type=request.task_type
        ).observe(latency_ms / 1000)
        if response is not None:
            metrics.LLM_TOKENS.labels(provider=candidate.provider, model=candidate.model, direction="input").inc(
                response.usage.input_tokens
            )
            metrics.LLM_TOKENS.labels(provider=candidate.provider, model=candidate.model, direction="output").inc(
                response.usage.output_tokens
            )
        try:
            with session_scope(ctx.organization_id) as db:
                usage.record_model_usage(
                    db,
                    organization_id=ctx.organization_id,
                    provider=candidate.provider,
                    model=response.model if response else candidate.model,
                    task_type=request.task_type,
                    input_tokens=response.usage.input_tokens if response else 0,
                    output_tokens=response.usage.output_tokens if response else 0,
                    cached_tokens=response.usage.cached_tokens if response else 0,
                    thought_tokens=response.usage.thought_tokens if response else 0,
                    latency_ms=latency_ms,
                    success=error_code is None,
                    cost_usd=cost,
                    cost_basis=basis or None,
                    project_id=ctx.project_id,
                    mission_id=ctx.mission_id,
                    agent_run_id=ctx.agent_run_id,
                    research_task_id=ctx.research_task_id,
                    error_code=error_code,
                    retry_count=response.retries if response else 0,
                    prompt_hash=prompt_hash,
                    routing_reason=decision.reason,
                    request_id=ctx.request_id,
                    provider_request_id=response.interaction_id if response else None,
                )
        except Exception:  # usage recording must not mask the model result; it is logged for reconciliation
            log.exception("model_usage_record_failed", model=candidate.key)

    # --- agents (Deep Research) ------------------------------------------------------------------------
    def research_agent(self, ctx: CallContext) -> tuple[Any, ModelCandidate]:
        """Resolve the configured Deep Research agent (routing + consent + budget checks)."""
        plan = self._plan(
            ctx,
            "research",
            complexity=0.5,
            latency_budget_ms=None,
            cost_budget_usd=None,
            required_features=frozenset(),
            input_tokens=None,
            output_tokens=None,
        )
        candidate = plan.decision.candidate
        assert candidate is not None
        provider = plan.registry.provider(candidate.provider)
        if not hasattr(provider, "start_agent"):
            raise LLMError(
                f"{candidate.provider} does not support background research agents",
                kind=LLMErrorKind.NOT_SUPPORTED,
                provider=candidate.provider,
            )
        return provider, candidate

    def record_agent_usage(
        self,
        ctx: CallContext,
        candidate: ModelCandidate,
        *,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        success: bool,
        interaction_id: str | None,
        error_code: str | None = None,
    ) -> float | None:
        cost = candidate.estimate_cost(input_tokens, output_tokens)
        with session_scope(ctx.organization_id) as db:
            usage.record_model_usage(
                db,
                organization_id=ctx.organization_id,
                provider=candidate.provider,
                model=candidate.model,
                task_type="research",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                success=success,
                cost_usd=cost,
                cost_basis="configured per-token price" if cost is not None else "no price configured for agent",
                project_id=ctx.project_id,
                mission_id=ctx.mission_id,
                research_task_id=ctx.research_task_id,
                error_code=error_code,
                provider_request_id=interaction_id,
            )
        return cost

    # --- embeddings ------------------------------------------------------------------------------------
    def embed(self, ctx: CallContext, texts: list[str]) -> tuple[list[list[float]], str]:
        """Embed with the configured embedding provider; the local hashing embedder needs no consent."""
        from engines.providers.embeddings import HashEmbedder

        settings = get_settings()
        dim = settings.embedding_dim
        provider_name = settings.embedding_provider
        if provider_name == "hash" or not texts:
            embedder = HashEmbedder(dim)
            return embedder.embed(texts), embedder.name
        with session_scope(ctx.organization_id) as db:
            allowed = bool(quota_service.get_quota(db, ctx.organization_id).allow_external_models)
        if provider_name != "ollama" and not allowed:
            embedder = HashEmbedder(dim)
            return embedder.embed(texts), embedder.name
        registry = self._registry_factory([])
        model = {
            "gemini": settings.gemini_embed_model,
            "openai": settings.openai_embed_model,
            "ollama": settings.ollama_embed_model,
        }.get(provider_name, "")
        provider = registry.provider(provider_name)
        vectors, _ = call_with_retry(lambda: provider.embed(texts, model=model), max_retries=2)
        for v in vectors:
            if len(v) != dim:
                raise ServiceUnavailable(f"embedding model returned {len(v)} dims; EMBEDDING_DIM is {dim}")
        return vectors, f"{provider_name}:{model}"


_GATEWAY: ModelGateway | None = None


def get_gateway() -> ModelGateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = ModelGateway()
    return _GATEWAY
