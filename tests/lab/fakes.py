"""Test doubles for the LLM gateway — use ONLY in tests.

Later workstreams (agents, research, hypotheses, verification, …) test code that calls
``aegis_api.lab.llm.gateway.get_gateway().generate(...)`` without any network access like this::

    from tests.lab.fakes import install_fake_llm, tool_call_response

    def test_generates_hypotheses(lab, monkeypatch):
        fake = install_fake_llm(
            monkeypatch,
            [
                {"hypotheses": [{"statement": "…"}]},        # dict  → JSON text (validated/parsed by the gateway)
                "a plain-text answer",                        # str   → text
                lambda request: {"echo": request.task_type},  # callable(LLMRequest) → str | dict | LLMResponse
                tool_call_response("search", {"q": "x"}),     # a function-call turn
                LLMTransientError("boom"),                    # an exception instance is raised by the provider
            ],
        )
        ...  # code under test
        assert fake.requests[0].task_type == "hypothesis_generation"
        assert fake.calls == 1

What you get:

* ``get_gateway()`` returns a fresh :class:`~aegis_api.lab.llm.gateway.LLMGateway` whose only provider is the
  fake (kind ``"fake"``, model ``"fake-model"`` on every tier) for the duration of the test (``monkeypatch``
  restores the original). The real gateway pipeline still runs: routing, consent, structured-output validation
  with one repair attempt, retries (without sleeping) and ``model_usage`` recording (provider ``"fake"``), so an
  organization (e.g. the ``lab`` fixture) and the test database are required.
* The fake is a *local* provider, so no external-processing consent is needed. Pass ``external=True`` to make it
  behave like a hosted provider (then the organization must have ``allow_external_llm``).
* Responses are consumed in order. When they run out the fake raises ``AssertionError`` (unexpected extra call)
  unless ``repeat_last=True``.
* ``fake.requests`` holds every :class:`LLMRequest` received (including repair calls), ``fake.models`` the model
  ids requested.

The ``fake_llm`` fixture wraps the helper: ``fake = fake_llm(["…"])``. Import it into a test module (or a conftest)
with ``from tests.lab.fakes import fake_llm  # noqa: F401``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest

from aegis_api.lab.llm import gateway as gateway_module
from aegis_api.lab.llm.base import ProviderCapabilities
from aegis_api.lab.llm.gateway import LLMGateway
from aegis_api.lab.llm.schemas import LLMRequest, LLMResponse, StreamChunk, ToolCall, Usage

FAKE_KIND = "fake"
FAKE_MODEL = "fake-model"

FakeItem = str | dict[str, Any] | LLMResponse | BaseException | Callable[[LLMRequest], Any]


def tool_call_response(
    name: str, arguments: dict[str, Any] | None = None, *, call_id: str | None = None
) -> LLMResponse:
    """A scripted function-call turn (the gateway returns it without structured-output validation)."""
    call = ToolCall(id=call_id or f"call_{name}", name=name, arguments=arguments or {})
    return LLMResponse(
        text="",
        tool_calls=[call],
        provider=FAKE_KIND,
        model=FAKE_MODEL,
        finish_reason="tool_calls",
        raw_assistant_turn={
            "role": "model",
            "parts": [{"functionCall": {"id": call.id, "name": name, "args": call.arguments}}],
        },
    )


class FakeLLMProvider:
    """Scripted in-process provider implementing the ``LLMProvider`` protocol."""

    def __init__(
        self,
        responses: Sequence[FakeItem] = (),
        *,
        kind: str = FAKE_KIND,
        external: bool = False,
        repeat_last: bool = False,
        capabilities: ProviderCapabilities | None = None,
    ) -> None:
        self.kind = kind
        self.capabilities = capabilities or ProviderCapabilities(
            structured_output=True,
            tools=True,
            search=True,
            code_execution=True,
            streaming=True,
            background=True,
            local=not external,
        )
        self._responses: list[FakeItem] = list(responses)
        self._index = 0
        self.repeat_last = repeat_last
        self.requests: list[LLMRequest] = []
        self.models: list[str] = []
        self.closed = False
        self.gateway: LLMGateway | None = None

    @property
    def calls(self) -> int:
        return len(self.requests)

    def add(self, *items: FakeItem) -> None:
        self._responses.extend(items)

    def _next(self, request: LLMRequest) -> FakeItem:
        if self._index < len(self._responses):
            item = self._responses[self._index]
            self._index += 1
            return item
        if self.repeat_last and self._responses:
            return self._responses[-1]
        raise AssertionError(
            f"FakeLLMProvider: no scripted response left for task '{request.task_type}' (call #{self.calls})"
        )

    @staticmethod
    def _usage(request: LLMRequest, text: str) -> Usage:
        return Usage(input_tokens=max(request.prompt_chars() // 4, 1), output_tokens=max(len(text) // 4, 1))

    def _to_response(self, request: LLMRequest, model: str, item: Any) -> LLMResponse:
        if callable(item) and not isinstance(item, LLMResponse | BaseException):
            item = item(request)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, LLMResponse):
            usage = item.usage if item.usage.total_tokens else self._usage(request, item.text)
            return item.model_copy(update={"provider": self.kind, "model": model, "usage": usage})
        text = json.dumps(item) if isinstance(item, dict | list) else str(item)
        return LLMResponse(
            text=text,
            usage=self._usage(request, text),
            provider=self.kind,
            model=model,
            model_version=f"{model}-test",
            request_id=f"fake-{self.calls}",
            finish_reason="stop",
            raw_assistant_turn={"role": "model", "parts": [{"text": text}]},
        )

    def generate(self, request: LLMRequest, *, model: str) -> LLMResponse:
        self.requests.append(request)
        self.models.append(model)
        return self._to_response(request, model, self._next(request))

    def stream(self, request: LLMRequest, *, model: str) -> Iterator[StreamChunk]:
        response = self.generate(request, model=model)
        for start in range(0, len(response.text), 16):
            yield StreamChunk(text_delta=response.text[start : start + 16], provider=self.kind, model=model)
        yield StreamChunk(
            done=True,
            usage=response.usage,
            finish_reason=response.finish_reason,
            provider=self.kind,
            model=model,
            model_version=response.model_version,
            request_id=response.request_id,
        )

    def close(self) -> None:
        self.closed = True


def make_fake_gateway(fake: FakeLLMProvider, *, model: str = FAKE_MODEL) -> LLMGateway:
    """A gateway whose only provider is ``fake`` (no settings providers, no backoff sleeping)."""
    gateway = LLMGateway(include_settings_providers=False, sleep=lambda _seconds: None, rng=lambda: 0.0)
    gateway.register_provider_factory(
        fake.kind,
        lambda: fake,
        capabilities=fake.capabilities,
        models={"fast": model, "default": model, "reasoning": model},
        priority=0,
    )
    return gateway


def install_fake_llm(
    monkeypatch: pytest.MonkeyPatch,
    responses: Sequence[FakeItem] = (),
    *,
    external: bool = False,
    repeat_last: bool = False,
    model: str = FAKE_MODEL,
    capabilities: ProviderCapabilities | None = None,
) -> FakeLLMProvider:
    """Route every ``get_gateway()`` call to a scripted fake provider for the rest of the test."""
    fake = FakeLLMProvider(responses, external=external, repeat_last=repeat_last, capabilities=capabilities)
    gateway = make_fake_gateway(fake, model=model)
    monkeypatch.setattr(gateway_module, "_GATEWAY", gateway)
    fake.gateway = gateway
    return fake


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeLLMProvider]:
    """Factory fixture: ``fake = fake_llm([...], external=False, repeat_last=False)``."""

    def _install(responses: Sequence[FakeItem] = (), **kwargs: Any) -> FakeLLMProvider:
        return install_fake_llm(monkeypatch, responses, **kwargs)

    return _install
