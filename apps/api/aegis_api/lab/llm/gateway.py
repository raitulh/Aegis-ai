"""The LLM gateway: the only way lab code calls a language model.

``get_gateway().generate(request, ctx)`` (synchronous) does, in order:

1. rate limiting (``model`` tier, per organization);
2. a short tenant transaction that reads the organization's data-processing consent and model configuration,
   routes the request (:class:`~aegis_api.lab.llm.routing.ModelRouter`) and runs the mission budget pre-check
   (``aegis_api.lab.governance.budgets.check_budget`` when that module is installed) — external providers are
   only eligible when ``allows_external_llm`` is true, otherwise only local models;
3. the provider call **outside** any transaction, with bounded exponential backoff and full jitter on
   transient errors only, falling back to the next candidate on transient/unavailable/credential errors (never
   on validation or policy errors);
4. structured output validation (``response_model.model_validate_json`` or JSON Schema) with exactly one repair
   attempt, then :class:`~aegis_api.lab.llm.errors.LLMOutputInvalid`;
5. one ``model_usage`` row per provider call (success *and* failure) in its own short transaction, plus metrics
   and an ``llm.generate`` span (provider, model, task type — never prompt text).

Callers must not hold a database transaction open while calling the gateway.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import jsonschema
import structlog
from pydantic import ValidationError

from aegis_api.config import get_settings
from aegis_api.errors import NotFound
from aegis_api.lab.core.actor import Actor
from aegis_api.lab.core.deps import check_rate, tenant_uow
from aegis_api.lab.core.errors import BudgetExceeded, ModelUnavailable
from aegis_api.lab.core.org_settings import allows_external_llm
from aegis_api.lab.llm.base import BUILTIN_CAPABILITIES, LLMProvider, ProviderCapabilities
from aegis_api.lab.llm.costs import ModelConfigView, call_cost, load_configs, resolve_price
from aegis_api.lab.llm.errors import (
    LLMError,
    LLMOutputInvalid,
    LLMUnavailableError,
    LLMValidationError,
    error_class_of,
    error_code,
)
from aegis_api.lab.llm.providers import SETTINGS_FACTORIES, build_provider, is_configured, settings_fingerprint
from aegis_api.lab.llm.routing import ModelRouter, ProviderProfile, settings_profiles, to_route_decision
from aegis_api.lab.llm.schemas import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    ModelCatalogOut,
    ProviderStatusOut,
    RouteDecision,
    RoutePreviewRequest,
    StreamChunk,
    TierModelOut,
    Usage,
)
from aegis_api.lab.models import Mission
from aegis_api.lab.observability import metrics
from aegis_api.lab.observability.tracing import current_trace_id, span
from aegis_api.lab.usage.recorder import record_model_usage
from engines.lab.llm_costs import CostResult, estimate_tokens_from_chars
from engines.lab.routing import TASK_TIERS, TIER_ORDER, RankedCandidate, RoutingDecision, RoutingPolicy

log = structlog.get_logger("aegis.lab.llm")

ProviderFactory = Callable[[], LLMProvider | None]

BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 20.0
MAX_RETRY_AFTER_SECONDS = 60.0
REPAIR_OUTPUT_CHARS = 8000
REPAIR_ERROR_CHARS = 1500
DEFAULT_ESTIMATED_OUTPUT_TOKENS = 2048
REPAIR_INSTRUCTION = (
    "Your previous response could not be used because it is not valid JSON for the required schema.\n"
    "Validation errors:\n{errors}\n"
    "Respond again with ONLY a corrected JSON value that satisfies the schema — no prose, no code fences."
)
_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n(.*?)\n?```\s*$", re.S)


def provider_wide_failure(exc: LLMError) -> bool:
    """Failures that would repeat for every model of the same provider (skip its other candidates)."""
    return isinstance(exc, LLMUnavailableError) or exc.code == "llm_auth_failed"


@dataclass(frozen=True)
class LLMCallContext:
    """Who/what a model call is for (attribution in ``model_usage``, budgets, tracing)."""

    organization_id: uuid.UUID
    project_id: uuid.UUID | None = None
    mission_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    research_task_id: uuid.UUID | None = None
    actor: Actor | None = None
    trace_id: str | None = None

    @classmethod
    def for_actor(cls, actor: Actor, **kwargs: Any) -> LLMCallContext:
        kwargs.setdefault("agent_run_id", actor.agent_run_id)
        kwargs.setdefault("trace_id", actor.trace_id)
        return cls(organization_id=actor.organization_id, actor=actor, **kwargs)

    @property
    def effective_trace_id(self) -> str | None:
        return self.trace_id or (self.actor.trace_id if self.actor else None) or current_trace_id()


@dataclass
class _Registration:
    kind: str
    factory: ProviderFactory
    capabilities: ProviderCapabilities
    models: dict[str, str] | None = None  # None → the settings tier mapping (built-in providers)
    priority: int | None = None
    builtin: bool = False
    instance: LLMProvider | None = None
    instance_key: tuple[object, ...] | None = None


@dataclass
class _Attempt:
    provider: str
    model: str
    success: bool
    retry_count: int
    latency_ms: int
    route_reason: str
    usage: Usage = field(default_factory=Usage)
    cost: CostResult | None = None
    model_version: str | None = None
    request_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class _Plan:
    decision: RoutingDecision
    configs: list[ModelConfigView]
    allow_external: bool


class LLMGateway:
    """Process-wide model gateway. Providers are built lazily from settings (and rebuilt when settings change)."""

    def __init__(
        self,
        *,
        include_settings_providers: bool = True,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._lock = threading.RLock()
        self._registrations: dict[str, _Registration] = {}
        self._overrides: dict[str, LLMProvider] = {}
        self._sleep = sleep
        self._rng = rng
        if include_settings_providers:
            for kind in SETTINGS_FACTORIES:
                self._registrations[kind] = _Registration(
                    kind=kind,
                    factory=lambda k=kind: build_provider(k),  # type: ignore[misc]
                    capabilities=BUILTIN_CAPABILITIES[kind],
                    builtin=True,
                )

    # -- provider registry ---------------------------------------------------------------------
    def register_provider_factory(
        self,
        kind: str,
        factory: ProviderFactory,
        *,
        capabilities: ProviderCapabilities | None = None,
        models: dict[str, str] | None = None,
        priority: int | None = None,
    ) -> None:
        """Register (or replace) a provider kind. ``models`` maps tiers to model ids; built-in kinds default to
        the settings mapping. Capabilities default to the built-in profile of ``kind``."""
        caps = capabilities or BUILTIN_CAPABILITIES.get(kind)
        if caps is None:
            raise ValueError(f"capabilities are required for provider kind '{kind}'")
        with self._lock:
            old = self._registrations.get(kind)
            if old is not None and old.instance is not None:
                old.instance.close()
            self._registrations[kind] = _Registration(
                kind=kind,
                factory=factory,
                capabilities=caps,
                models=dict(models) if models is not None else None,
                priority=priority,
                builtin=False,
            )

    def unregister_provider(self, kind: str) -> None:
        with self._lock:
            registration = self._registrations.pop(kind, None)
            self._overrides.pop(kind, None)
        if registration is not None and registration.instance is not None:
            registration.instance.close()

    def set_provider_override(self, kind: str, provider: LLMProvider | None) -> None:
        """Use ``provider`` for ``kind`` instead of building one from settings (``None`` removes the override)."""
        with self._lock:
            if provider is None:
                self._overrides.pop(kind, None)
                return
            if kind not in self._registrations:
                self._registrations[kind] = _Registration(
                    kind=kind, factory=lambda: None, capabilities=provider.capabilities, builtin=False
                )
            self._overrides[kind] = provider

    def reset(self) -> None:
        """Close cached provider instances (they are rebuilt on next use)."""
        with self._lock:
            for registration in self._registrations.values():
                if registration.instance is not None:
                    registration.instance.close()
                registration.instance = None
                registration.instance_key = None

    def _is_available(self, registration: _Registration) -> bool:
        if registration.kind in self._overrides:
            return True
        if registration.builtin:
            return is_configured(registration.kind)
        return True

    def _provider(self, kind: str) -> LLMProvider | None:
        with self._lock:
            override = self._overrides.get(kind)
            if override is not None:
                return override
            registration = self._registrations.get(kind)
            if registration is None or not self._is_available(registration):
                return None
            key = settings_fingerprint(kind, get_settings()) if registration.builtin else ()
            if registration.instance is None or registration.instance_key != key:
                if registration.instance is not None:
                    registration.instance.close()
                registration.instance = registration.factory()
                registration.instance_key = key
            return registration.instance

    def profiles(self) -> list[ProviderProfile]:
        """Routing profiles of every usable provider kind (no credentials)."""
        settings = get_settings()
        profiles: list[ProviderProfile] = []
        with self._lock:
            registrations = list(self._registrations.values())
            available = [r for r in registrations if self._is_available(r)]
        for registration in available:
            if registration.models is None and registration.kind in BUILTIN_CAPABILITIES:
                base = settings_profiles([registration.kind], settings)[0]
                profiles.append(
                    ProviderProfile(
                        kind=base.kind,
                        capabilities=registration.capabilities,
                        models=base.models,
                        priority=registration.priority if registration.priority is not None else base.priority,
                    )
                )
            else:
                profiles.append(
                    ProviderProfile(
                        kind=registration.kind,
                        capabilities=registration.capabilities,
                        models=registration.models or {},
                        priority=registration.priority if registration.priority is not None else 100,
                    )
                )
        return profiles

    def known_kinds(self) -> list[str]:
        with self._lock:
            return list(self._registrations)

    # -- queries -------------------------------------------------------------------------------
    def available_providers(self, organization_id: uuid.UUID) -> list[str]:
        """Provider kinds usable for this organization right now (configured and permitted by consent)."""
        with tenant_uow(organization_id) as db:
            allow_external = allows_external_llm(db, organization_id)
        return [p.kind for p in self.profiles() if p.local or allow_external]

    def route_preview(self, organization_id: uuid.UUID, preview: RoutePreviewRequest) -> RouteDecision:
        """Dry-run routing (no provider call, no usage)."""
        with tenant_uow(organization_id) as db:
            allow_external = allows_external_llm(db, organization_id)
            configs = load_configs(db, organization_id, enabled_only=True)
        router = ModelRouter(self.profiles(), configs)
        return router.route(
            preview.task_type.value,
            preview.complexity,
            preview.latency_budget_ms,
            preview.cost_budget_usd,
            RoutingPolicy(allow_external=allow_external),
            tier=preview.tier,
            required_capabilities=frozenset(preview.required_capabilities),
            provider=preview.provider,
            model=preview.model,
            estimated_input_tokens=preview.estimated_input_tokens,
            estimated_output_tokens=preview.estimated_output_tokens,
        )

    def catalog(self, organization_id: uuid.UUID) -> ModelCatalogOut:
        settings = get_settings()
        with tenant_uow(organization_id) as db:
            allow_external = allows_external_llm(db, organization_id)
            configs = load_configs(db, organization_id, enabled_only=True)
        profiles = {p.kind: p for p in self.profiles()}
        router = ModelRouter(list(profiles.values()), configs)
        providers: list[ProviderStatusOut] = []
        for kind in self.known_kinds():
            with self._lock:
                registration = self._registrations.get(kind)
            if registration is None:
                continue
            configured = kind in profiles
            caps = registration.capabilities
            providers.append(
                ProviderStatusOut(
                    kind=kind,
                    configured=configured,
                    local=caps.local,
                    usable=configured and (caps.local or allow_external),
                    capabilities=caps.as_out(),
                )
            )
        tiers: dict[str, list[TierModelOut]] = {tier.value: [] for tier in TIER_ORDER}
        source_rank = {"org": 0, "platform": 1, "settings": 2}
        for candidate in sorted(
            router.candidates, key=lambda c: (c.priority, source_rank.get(c.source, 9), c.provider, c.model)
        ):
            if candidate.tier in tiers:
                tiers[candidate.tier].append(
                    TierModelOut(
                        provider=candidate.provider,
                        model=candidate.model,
                        source=candidate.source,
                        priority=candidate.priority,
                        local=candidate.local,
                        price_configured=candidate.price is not None,
                    )
                )
        return ModelCatalogOut(
            default_provider=settings.llm_default_provider,
            external_processing_allowed=allow_external,
            providers=providers,
            tiers=tiers,
            deep_research_agent=settings.gemini_deep_research_agent if "gemini" in profiles else None,
            task_tiers={task: tier.value for task, tier in TASK_TIERS.items()},
        )

    # -- planning ------------------------------------------------------------------------------
    @staticmethod
    def _estimated_tokens(request: LLMRequest) -> tuple[int, int]:
        chars = request.prompt_chars() + sum(len(json.dumps(t, default=str)) for t in request.previous_turns)
        output = request.max_output_tokens or min(get_settings().llm_max_output_tokens, DEFAULT_ESTIMATED_OUTPUT_TOKENS)
        return estimate_tokens_from_chars(chars), output

    def _plan(
        self, request: LLMRequest, ctx: LLMCallContext, *, extra_capabilities: frozenset[str] = frozenset()
    ) -> _Plan:
        input_tokens, output_tokens = self._estimated_tokens(request)
        profiles = self.profiles()
        with tenant_uow(ctx.organization_id) as db:
            allow_external = allows_external_llm(db, ctx.organization_id)
            configs = load_configs(db, ctx.organization_id, enabled_only=True)
            router = ModelRouter(profiles, configs)
            decision = router.decide(
                request.task_type.value,
                request.complexity,
                request.latency_budget_ms,
                request.cost_budget_usd,
                RoutingPolicy(allow_external=allow_external),
                tier=request.tier,
                required_capabilities=request.required_capabilities() | extra_capabilities,
                provider=request.provider,
                model=request.model,
                estimated_input_tokens=input_tokens,
                estimated_output_tokens=output_tokens,
            )
            if decision.primary is None:
                raise ModelUnavailable(
                    self._unavailable_message(request, decision, profiles, allow_external),
                    details=to_route_decision(request.task_type.value, decision).model_dump(mode="json"),
                )
            if ctx.mission_id is not None:
                self._check_budget(db, ctx, decision.primary.estimated_cost_usd or Decimal("0"))
        return _Plan(decision=decision, configs=configs, allow_external=allow_external)

    @staticmethod
    def _unavailable_message(
        request: LLMRequest, decision: RoutingDecision, profiles: list[ProviderProfile], allow_external: bool
    ) -> str:
        task = request.task_type.value
        if not profiles:
            return (
                f"No model provider is configured for task '{task}'. Configure a provider (e.g. GEMINI_API_KEY) or a "
                "local model server (OLLAMA_BASE_URL)."
            )
        if not allow_external and not any(p.local for p in profiles):
            external = ", ".join(sorted(p.kind for p in profiles))
            return (
                f"No model is available for task '{task}': the configured provider(s) ({external}) process data "
                "outside your organization, which requires consent to external data processing (organization "
                "settings → data_processing.allow_external_llm). Enable that consent or configure a local model "
                "provider (OLLAMA_BASE_URL)."
            )
        return f"No configured model satisfies this request ({decision.reason})."

    @staticmethod
    def _check_budget(db: Any, ctx: LLMCallContext, estimated_usd: Decimal) -> None:
        try:
            from aegis_api.lab.governance.budgets import check_budget
        except ImportError:  # governance context not installed in this deployment
            return
        mission = db.get(Mission, ctx.mission_id)
        if mission is None or mission.organization_id != ctx.organization_id:
            raise NotFound("Mission not found")
        result = check_budget(db, mission, "llm", estimated_usd=estimated_usd)
        if not getattr(result, "ok", True):
            metrics.BUDGET_EXCEEDED.labels("llm").inc()
            remaining = getattr(result, "remaining_usd", None)
            raise BudgetExceeded(
                getattr(result, "reason", None) or "The mission's LLM budget would be exceeded",
                details={
                    "mission_id": str(ctx.mission_id),
                    "estimated_usd": str(estimated_usd),
                    "remaining_usd": str(remaining) if remaining is not None else None,
                },
            )

    # -- execution -----------------------------------------------------------------------------
    def _max_retries(self, kind: str) -> int:
        settings = get_settings()
        return max(settings.gemini_max_retries if kind == "gemini" else settings.llm_max_retries, 0)

    def _backoff(self, retry: int, retry_after: float | None) -> float:
        delay = self._rng() * min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2**retry))
        if retry_after:
            delay = max(delay, min(retry_after, MAX_RETRY_AFTER_SECONDS))
        return delay

    @staticmethod
    def _call_request(request: LLMRequest, ranked: RankedCandidate) -> LLMRequest:
        candidate = ranked.candidate
        # The model configuration's limit (or LLM_MAX_OUTPUT_TOKENS) caps what a request may ask for.
        cap = candidate.max_output_tokens or get_settings().llm_max_output_tokens
        max_tokens = min(request.max_output_tokens or cap, cap)
        temperature = request.temperature if request.temperature is not None else candidate.temperature
        return request.model_copy(update={"max_output_tokens": max_tokens, "temperature": temperature})

    def _cost(self, plan: _Plan, ranked: RankedCandidate, response: LLMResponse) -> CostResult:
        candidate = ranked.candidate
        price = candidate.price or resolve_price(
            candidate.provider, candidate.model, configs=plan.configs, model_version=response.model_version
        )
        return call_cost(price, response.usage, local=candidate.local)

    def generate(self, request: LLMRequest, ctx: LLMCallContext) -> LLMResponse:
        """Route and execute one model request (see module docstring). Blocking; never hold a DB transaction."""
        check_rate("model", f"org:{ctx.organization_id}")
        attempts: list[_Attempt] = []
        with span("llm.generate", task_type=request.task_type.value) as current_span:
            try:
                plan = self._plan(request, ctx)
                response = self._execute(request, plan, attempts)
                current_span.set_attribute("llm.provider", response.provider)
                current_span.set_attribute("llm.model", response.model)
                current_span.set_attribute("llm.retry_count", response.retry_count)
                return response
            finally:
                if attempts:
                    last = attempts[-1]
                    current_span.set_attribute("llm.provider", last.provider)
                    current_span.set_attribute("llm.model", last.model)
                    current_span.set_attribute("llm.attempts", len(attempts))
                    self._record(ctx, request, attempts)

    def _execute(self, request: LLMRequest, plan: _Plan, attempts: list[_Attempt]) -> LLMResponse:
        decision = plan.decision
        errors: list[LLMError] = []
        dead: set[str] = set()  # providers that failed as a whole (credentials, unreachable)
        for position, ranked in enumerate(decision.ranked):
            candidate = ranked.candidate
            if candidate.provider in dead:
                continue
            cause = errors[-1].code if errors else "unavailable"
            route_reason = (
                decision.reason if position == 0 else f"fallback #{position} after {cause}; {decision.reason}"
            )
            provider = self._provider(candidate.provider)
            if provider is None:
                dead.add(candidate.provider)
                errors.append(
                    LLMUnavailableError(
                        f"{candidate.provider} is not configured",
                        code="llm_not_configured",
                        provider=candidate.provider,
                    )
                )
                continue
            call_request = self._call_request(request, ranked)
            retries = 0
            max_retries = self._max_retries(candidate.provider)
            while True:
                try:
                    response = self._attempt(provider, call_request, ranked, retries, route_reason, attempts, plan)
                except LLMError as exc:
                    if exc.retryable and retries < max_retries:
                        self._sleep(self._backoff(retries, exc.retry_after))
                        retries += 1
                        continue
                    errors.append(exc)
                    if provider_wide_failure(exc):
                        dead.add(candidate.provider)
                    if exc.fallback:
                        break
                    raise
                response = self._structured(
                    provider, request, call_request, ranked, response, retries, route_reason, attempts, plan
                )
                response.retry_count = retries
                response.route_reason = route_reason
                return response
        if errors and all(isinstance(e, LLMUnavailableError) for e in errors):
            raise ModelUnavailable(
                "No model provider could be reached: " + "; ".join(e.message for e in errors[:4]),
                details={"candidates": [r.candidate.key for r in decision.ranked]},
            )
        raise errors[-1]

    def _attempt(
        self,
        provider: LLMProvider,
        call_request: LLMRequest,
        ranked: RankedCandidate,
        retries: int,
        route_reason: str,
        attempts: list[_Attempt],
        plan: _Plan,
    ) -> LLMResponse:
        candidate = ranked.candidate
        task = call_request.task_type.value
        started = time.perf_counter()
        try:
            response = provider.generate(call_request, model=candidate.model)
        except Exception as exc:
            elapsed = time.perf_counter() - started
            metrics.LLM_LATENCY.labels(candidate.provider, task).observe(elapsed)
            metrics.LLM_ERRORS.labels(candidate.provider, error_class_of(exc)).inc()
            attempts.append(
                _Attempt(
                    provider=candidate.provider,
                    model=candidate.model,
                    success=False,
                    retry_count=retries,
                    latency_ms=int(elapsed * 1000),
                    route_reason=route_reason,
                    cost=CostResult(usd=None, estimated=True, basis="call failed — no tokens reported"),
                    error_code=error_code(exc),
                )
            )
            log.warning(
                "llm_call_failed",
                provider=candidate.provider,
                model=candidate.model,
                task_type=task,
                error_class=error_class_of(exc),
                error_code=error_code(exc),
                retry=retries,
            )
            raise
        elapsed = time.perf_counter() - started
        metrics.LLM_LATENCY.labels(candidate.provider, task).observe(elapsed)
        cost = self._cost(plan, ranked, response)
        response.latency_ms = response.latency_ms or int(elapsed * 1000)
        response.cost_usd = cost.usd
        response.cost_estimated = cost.estimated
        response.cost_basis = cost.basis
        attempts.append(
            _Attempt(
                provider=candidate.provider,
                model=candidate.model,
                success=True,
                retry_count=retries,
                latency_ms=int(elapsed * 1000),
                route_reason=route_reason,
                usage=response.usage,
                cost=cost,
                model_version=response.model_version,
                request_id=response.request_id,
            )
        )
        return response

    # -- structured output ---------------------------------------------------------------------
    @staticmethod
    def _json_text(text: str) -> str:
        match = _FENCE.match(text)
        return match.group(1) if match else text.strip()

    @classmethod
    def validate_structured(cls, request: LLMRequest, text: str) -> tuple[dict[str, Any] | None, str | None]:
        """(parsed object, None) or (None, human-readable validation errors)."""
        raw = cls._json_text(text)
        if request.response_model is not None:
            try:
                model = request.response_model.model_validate_json(raw)
            except ValidationError as exc:
                lines = [
                    f"- {'.'.join(str(p) for p in err.get('loc', ())) or '(root)'}: {err.get('msg')}"
                    for err in exc.errors()[:10]
                ]
                return None, "\n".join(lines)[:REPAIR_ERROR_CHARS]
            dumped = model.model_dump(mode="json")
            return (dumped if isinstance(dumped, dict) else {"value": dumped}), None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"- invalid JSON: {exc.msg} at position {exc.pos}"
        schema = request.response_schema or {}
        try:
            validator = jsonschema.Draft202012Validator(schema)
            problems = sorted(validator.iter_errors(value), key=lambda e: list(e.path))[:10]
        except jsonschema.SchemaError as exc:
            raise LLMValidationError(f"response_schema is not a valid JSON Schema: {exc.message}") from None
        if problems:
            lines = [f"- {'.'.join(str(p) for p in err.path) or '(root)'}: {err.message}" for err in problems]
            return None, "\n".join(lines)[:REPAIR_ERROR_CHARS]
        if not isinstance(value, dict):
            return {"value": value}, None
        return value, None

    def _structured(
        self,
        provider: LLMProvider,
        request: LLMRequest,
        call_request: LLMRequest,
        ranked: RankedCandidate,
        response: LLMResponse,
        retries: int,
        route_reason: str,
        attempts: list[_Attempt],
        plan: _Plan,
    ) -> LLMResponse:
        if not request.wants_structured_output or response.tool_calls:
            return response
        if response.finish_reason == "max_tokens":
            raise LLMOutputInvalid(
                "The model output was truncated at max_output_tokens before the JSON was complete",
                code="llm_output_truncated",
                provider=response.provider,
            )
        parsed, problems = self.validate_structured(request, response.text)
        if problems is None:
            response.parsed = parsed
            return response
        repair_request = call_request.model_copy(
            update={
                "messages": [
                    *call_request.messages,
                    LLMMessage(role="assistant", content=response.text[:REPAIR_OUTPUT_CHARS]),
                    LLMMessage(role="user", content=REPAIR_INSTRUCTION.format(errors=problems)),
                ]
            }
        )
        repaired = self._attempt(provider, repair_request, ranked, retries, f"repair; {route_reason}", attempts, plan)
        parsed, problems = self.validate_structured(request, repaired.text)
        if problems is None and repaired.finish_reason != "max_tokens":
            total_cost = (
                (response.cost_usd or Decimal("0")) + (repaired.cost_usd or Decimal("0"))
                if response.cost_usd is not None or repaired.cost_usd is not None
                else None
            )
            return repaired.model_copy(
                update={
                    "parsed": parsed,
                    "usage": response.usage + repaired.usage,
                    "cost_usd": total_cost,
                    "latency_ms": response.latency_ms + repaired.latency_ms,
                    "metadata": {**repaired.metadata, "repair_attempted": True},
                }
            )
        raise LLMOutputInvalid(
            "The model output did not match the required schema after one repair attempt",
            provider=response.provider,
            details={"errors": (problems or "output truncated").splitlines()[:10]},
        )

    # -- usage ---------------------------------------------------------------------------------
    def _record(self, ctx: LLMCallContext, request: LLMRequest, attempts: list[_Attempt]) -> None:
        trace_id = ctx.effective_trace_id
        try:
            with tenant_uow(ctx.organization_id) as db:
                for attempt in attempts:
                    cost = attempt.cost or CostResult(
                        usd=None, estimated=True, basis="call failed — no tokens reported"
                    )
                    record_model_usage(
                        db,
                        organization_id=ctx.organization_id,
                        provider=attempt.provider,
                        model=attempt.model,
                        task_type=request.task_type.value,
                        input_tokens=attempt.usage.input_tokens,
                        output_tokens=attempt.usage.output_tokens,
                        cached_tokens=attempt.usage.cached_tokens,
                        thinking_tokens=attempt.usage.thinking_tokens,
                        latency_ms=attempt.latency_ms,
                        cost_usd=cost.usd,
                        cost_estimated=cost.estimated,
                        cost_basis=cost.basis,
                        success=attempt.success,
                        error_code=attempt.error_code,
                        retry_count=attempt.retry_count,
                        model_version=attempt.model_version,
                        request_id=attempt.request_id,
                        trace_id=trace_id,
                        project_id=ctx.project_id,
                        mission_id=ctx.mission_id,
                        agent_run_id=ctx.agent_run_id,
                        research_task_id=ctx.research_task_id,
                        route_reason=attempt.route_reason,
                    )
        except Exception:  # never lose a (paid) model result because the ledger write failed
            log.exception("llm_usage_record_failed", attempts=len(attempts), task_type=request.task_type.value)

    # -- streaming -----------------------------------------------------------------------------
    def stream(self, request: LLMRequest, ctx: LLMCallContext) -> Iterator[StreamChunk]:
        """Stream plain-text output. Retries/fallbacks apply only until the first chunk arrives; usage is recorded
        when the stream ends (or is abandoned). Structured output and function calling are not streamed."""
        if request.wants_structured_output or request.tools:
            raise LLMValidationError("Streaming supports plain text output only", code="llm_unsupported_feature")
        check_rate("model", f"org:{ctx.organization_id}")
        plan = self._plan(request, ctx, extra_capabilities=frozenset({"streaming"}))
        return self._stream(request, ctx, plan)

    def _stream(self, request: LLMRequest, ctx: LLMCallContext, plan: _Plan) -> Iterator[StreamChunk]:
        attempts: list[_Attempt] = []
        errors: list[LLMError] = []
        dead: set[str] = set()
        try:
            for position, ranked in enumerate(plan.decision.ranked):
                candidate = ranked.candidate
                if candidate.provider in dead:
                    continue
                route_reason = (
                    plan.decision.reason if position == 0 else f"fallback #{position}; {plan.decision.reason}"
                )
                provider = self._provider(candidate.provider)
                if provider is None:
                    continue
                call_request = self._call_request(request, ranked)
                retries = 0
                while True:
                    started = time.perf_counter()
                    iterator = provider.stream(call_request, model=candidate.model)
                    try:
                        first = next(iterator)
                    except StopIteration:
                        first = StreamChunk(
                            done=True, usage=Usage(), provider=candidate.provider, model=candidate.model
                        )
                    except LLMError as exc:
                        attempts.append(
                            _Attempt(
                                provider=candidate.provider,
                                model=candidate.model,
                                success=False,
                                retry_count=retries,
                                latency_ms=int((time.perf_counter() - started) * 1000),
                                route_reason=route_reason,
                                error_code=error_code(exc),
                            )
                        )
                        metrics.LLM_ERRORS.labels(candidate.provider, exc.error_class).inc()
                        if exc.retryable and retries < self._max_retries(candidate.provider):
                            self._sleep(self._backoff(retries, exc.retry_after))
                            retries += 1
                            continue
                        errors.append(exc)
                        if provider_wide_failure(exc):
                            dead.add(candidate.provider)
                        if exc.fallback:
                            break
                        raise
                    yield from self._drain(
                        first, iterator, ranked, plan, attempts, retries, route_reason, started, request.task_type.value
                    )
                    return
            if errors:
                raise errors[-1]
            raise ModelUnavailable("No model provider could be reached for streaming")
        finally:
            if attempts:
                self._record(ctx, request, attempts)

    def _drain(
        self,
        first: StreamChunk,
        iterator: Iterator[StreamChunk],
        ranked: RankedCandidate,
        plan: _Plan,
        attempts: list[_Attempt],
        retries: int,
        route_reason: str,
        started: float,
        task: str,
    ) -> Iterator[StreamChunk]:
        candidate = ranked.candidate
        attempt = _Attempt(
            provider=candidate.provider,
            model=candidate.model,
            success=False,
            retry_count=retries,
            latency_ms=0,
            route_reason=route_reason,
            error_code="stream_incomplete",
        )
        attempts.append(attempt)
        chunk: StreamChunk | None = first
        try:
            while chunk is not None:
                if chunk.done:
                    usage = chunk.usage or Usage()
                    probe = LLMResponse(
                        provider=candidate.provider,
                        model=candidate.model,
                        usage=usage,
                        model_version=chunk.model_version,
                    )
                    cost = self._cost(plan, ranked, probe)
                    attempt.success = True
                    attempt.error_code = None
                    attempt.usage = usage
                    attempt.cost = cost
                    attempt.model_version = chunk.model_version
                    attempt.request_id = chunk.request_id
                    attempt.latency_ms = int((time.perf_counter() - started) * 1000)
                    metrics.LLM_LATENCY.labels(candidate.provider, task).observe(time.perf_counter() - started)
                    yield chunk.model_copy(update={"cost_usd": cost.usd})
                    return
                yield chunk
                chunk = next(iterator, None)
        except LLMError as exc:
            attempt.error_code = error_code(exc)
            metrics.LLM_ERRORS.labels(candidate.provider, exc.error_class).inc()
            raise
        finally:
            if not attempt.success:
                attempt.latency_ms = int((time.perf_counter() - started) * 1000)


_GATEWAY: LLMGateway | None = None
_GATEWAY_LOCK = threading.Lock()


def get_gateway() -> LLMGateway:
    """The process-wide gateway (tests replace it with ``tests.lab.fakes.install_fake_llm``)."""
    global _GATEWAY
    if _GATEWAY is None:
        with _GATEWAY_LOCK:
            if _GATEWAY is None:
                _GATEWAY = LLMGateway()
    return _GATEWAY
