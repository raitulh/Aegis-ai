"""Compute cost engine: reserved-resource pricing, estimates and rounding."""

from __future__ import annotations

from decimal import Decimal

import pytest

from engines.lab.compute_cost import BASIS_MAX_CHARS, ComputePrices, compute_cost, cost_breakdown, estimate_cost
from engines.lab.sandbox import ResourceRequest

PRICES = ComputePrices.from_values(0.10, 0.02, {"nvidia-a100": 3.2})


def test_cost_formula_cpu_memory_gpu() -> None:
    usd, basis = compute_cost(
        wall_seconds=1800, cpu=2, memory_mb=4096, gpu_type="nvidia-a100", gpu_count=1, prices=PRICES
    )
    # 2 vCPU × 0.5 h × 0.10 + 4 GB × 0.5 h × 0.02 + 1 GPU × 0.5 h × 3.2 = 0.10 + 0.04 + 1.60
    assert usd == Decimal("1.740000")
    assert isinstance(usd, Decimal)
    assert "cpu 2@0.1" in basis and "mem 4GB@0.02" in basis and "gpu 1xnvidia-a100@3.2" in basis


def test_breakdown_parts_and_rounding() -> None:
    b = cost_breakdown(wall_seconds=1, cpu=1, memory_mb=1024, gpu_type=None, gpu_count=0, prices=PRICES)
    assert b.cpu_usd == Decimal("0.000028")  # 0.10 / 3600 = 0.0000277… → half-up
    assert b.memory_usd == Decimal("0.000006")
    assert b.gpu_usd == Decimal("0")
    assert b.usd == Decimal("0.000033")  # rounded once, on the exact sum
    assert b.usd.as_tuple().exponent == -6


def test_zero_prices_and_negative_durations() -> None:
    free = ComputePrices()
    assert compute_cost(wall_seconds=3600, cpu=4, memory_mb=8192, gpu_type=None, gpu_count=0, prices=free)[0] == 0
    assert compute_cost(wall_seconds=-50, cpu=4, memory_mb=8192, gpu_type=None, gpu_count=0, prices=PRICES)[0] == 0


def test_unpriced_gpu_is_reported_not_guessed() -> None:
    b = cost_breakdown(wall_seconds=3600, cpu=1, memory_mb=1024, gpu_type="nvidia-h100", gpu_count=2, prices=PRICES)
    assert b.unpriced_gpu_type == "nvidia-h100"
    assert b.gpu_usd == 0
    assert "unpriced" in b.basis
    assert b.usd == Decimal("0.120000")


def test_estimate_uses_timeout_as_worst_case() -> None:
    resources = ResourceRequest(cpu=1, memory_mb=2048)
    usd, basis = estimate_cost(resources, 7200, PRICES)
    assert usd == Decimal("0.280000")  # 1 × 2 h × 0.10 + 2 GB × 2 h × 0.02
    assert basis.startswith("estimate 2h")


def test_basis_is_bounded_and_prices_validated() -> None:
    long_type = "x" * 48
    prices = ComputePrices.from_values(1, 1, {long_type: 1})
    _, basis = compute_cost(
        wall_seconds=12345.678, cpu=3.75, memory_mb=123456, gpu_type=long_type, gpu_count=8, prices=prices
    )
    assert len(basis) <= BASIS_MAX_CHARS
    with pytest.raises(ValueError):
        ComputePrices.from_values(-1, 0, {})
    with pytest.raises(ValueError):
        ComputePrices.from_values(0, 0, {"a100": -3})
    assert ComputePrices.from_values("0.5", Decimal("0.25"), {"t4": "0.35"}).gpu_per_hour["t4"] == Decimal("0.35")
