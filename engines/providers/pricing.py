"""Cost estimation. Costs are only produced when a price is configured for provider:model — never invented."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostEstimate:
    usd: float | None
    estimated: bool
    basis: str


def estimate_cost(
    pricing: dict[str, dict[str, float]],
    provider: str,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> CostEstimate:
    if provider in ("ollama", "demo"):
        return CostEstimate(usd=0.0, estimated=False, basis="local/simulated model — no provider charge")
    if input_tokens is None and output_tokens is None:
        return CostEstimate(usd=None, estimated=True, basis="provider did not report token usage")
    price = pricing.get(f"{provider}:{model}") or pricing.get(provider)
    if not price:
        return CostEstimate(usd=None, estimated=True, basis="no price configured (MODEL_PRICING_JSON)")
    usd = (input_tokens or 0) / 1e6 * float(price.get("input_per_mtok", 0)) + (output_tokens or 0) / 1e6 * float(
        price.get("output_per_mtok", 0)
    )
    return CostEstimate(usd=round(usd, 6), estimated=True, basis="configured price × reported tokens")
