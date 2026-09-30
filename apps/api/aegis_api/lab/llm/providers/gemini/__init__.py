"""Google Gemini: ``generateContent`` adapter and the Interactions API (incl. Deep Research) client."""

from __future__ import annotations

from aegis_api.lab.llm.providers.gemini.client import GeminiClient
from aegis_api.lab.llm.providers.gemini.interactions import (
    GeminiInteractionsClient,
    InteractionEvent,
    InteractionSnapshot,
)

__all__ = ["GeminiClient", "GeminiInteractionsClient", "InteractionEvent", "InteractionSnapshot"]
