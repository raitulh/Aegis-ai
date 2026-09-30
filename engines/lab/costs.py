"""Compute and storage cost estimation. Like LLM pricing, costs are only produced when prices are configured
(``COMPUTE_PRICING_JSON``); otherwise the cost is ``None`` with an explanatory basis — never invented."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CostEstimate:
    usd: float | None
    basis: str


def compute_cost(
    pricing: dict[str, Any],
    *,
    seconds: float,
    cpu: float,
    memory_mb: int,
    gpu_type: str | None = None,
    gpu_count: int = 0,
) -> CostEstimate:
    """Cost of a compute job: core-hours + GB-hours + GPU-hours at configured rates."""
    if seconds <= 0:
        return CostEstimate(usd=0.0, basis="no runtime")
    cpu_rate = pricing.get("cpu_core_hour")
    mem_rate = pricing.get("memory_gb_hour")
    if cpu_rate is None and mem_rate is None and not pricing.get("gpu_hour"):
        return CostEstimate(usd=None, basis="no compute price configured (COMPUTE_PRICING_JSON)")
    hours = seconds / 3600.0
    usd = hours * float(cpu) * float(cpu_rate or 0.0) + hours * (memory_mb / 1024.0) * float(mem_rate or 0.0)
    basis = "configured cpu/memory rates × runtime"
    if gpu_count:
        gpu_rates = pricing.get("gpu_hour") or {}
        rate = gpu_rates.get(gpu_type or "default") if isinstance(gpu_rates, dict) else None
        if rate is None:
            return CostEstimate(usd=None, basis=f"no GPU price configured for '{gpu_type}'")
        usd += hours * gpu_count * float(rate)
        basis += " + GPU rate"
    return CostEstimate(usd=round(usd, 6), basis=basis)


def storage_cost(pricing: dict[str, Any], *, size_bytes: int, days: float = 30.0) -> CostEstimate:
    rate = pricing.get("storage_gb_month")
    if rate is None:
        return CostEstimate(usd=None, basis="no storage price configured")
    gb = size_bytes / 1024**3
    return CostEstimate(usd=round(gb * float(rate) * (days / 30.0), 6), basis="configured GB-month rate")


def estimate_compute_seconds_cost(
    pricing: dict[str, Any], resources: dict[str, Any], timeout_seconds: float
) -> CostEstimate:
    """Upper-bound estimate for a job before it runs (assumes it uses its whole timeout)."""
    return compute_cost(
        pricing,
        seconds=timeout_seconds,
        cpu=float(resources.get("cpu", 1)),
        memory_mb=int(resources.get("memory_mb", 1024)),
        gpu_type=resources.get("gpu_type"),
        gpu_count=int(resources.get("gpu_count", 0) or 0),
    )
