"""Compute cost model for sandboxed jobs (pure, deterministic).

Cost is charged on *reserved* resources for the wall-clock duration of the run (requests equal limits on
every backend, so reserved capacity is what the job actually occupies)::

    usd = cpu × hours × cpu_price  +  memory_GB × hours × memory_GB_price  +  gpu_count × hours × gpu_price[type]

Prices come from deployment settings (``EXECUTION_CPU_PRICE_PER_HOUR_USD``,
``EXECUTION_MEMORY_GB_PRICE_PER_HOUR_USD``, ``EXECUTION_GPU_PRICE_PER_HOUR_JSON``) and are passed in; nothing
is hard-coded here. Results are ``Decimal`` quantized to 6 places (the ledger's ``Numeric(18, 6)``). An
*estimate* is the same formula evaluated at the job's timeout (the worst case), used for budget and policy
pre-checks before a job is admitted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

_Q = Decimal("0.000001")
_SECONDS_PER_HOUR = Decimal(3600)
_MIB_PER_GB = Decimal(1024)
BASIS_MAX_CHARS = 120


def _dec(value: float | int | Decimal | str | None) -> Decimal:
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _q(value: Decimal) -> Decimal:
    return value.quantize(_Q, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ComputePrices:
    """Hourly prices in USD. ``gpu_per_hour`` maps GPU type → price per GPU-hour."""

    cpu_per_hour: Decimal = Decimal(0)
    memory_gb_per_hour: Decimal = Decimal(0)
    gpu_per_hour: Mapping[str, Decimal] = field(default_factory=dict)

    @classmethod
    def from_values(
        cls,
        cpu_per_hour: float | Decimal | str | None,
        memory_gb_per_hour: float | Decimal | str | None,
        gpu_per_hour: Mapping[str, float | Decimal | str] | None = None,
    ) -> ComputePrices:
        prices = {str(k): _dec(v) for k, v in (gpu_per_hour or {}).items()}
        for name, value in (("cpu", _dec(cpu_per_hour)), ("memory", _dec(memory_gb_per_hour)), *prices.items()):
            if value < 0:
                raise ValueError(f"negative compute price for {name}")
        return cls(cpu_per_hour=_dec(cpu_per_hour), memory_gb_per_hour=_dec(memory_gb_per_hour), gpu_per_hour=prices)


class ResourceLike(Protocol):
    @property
    def cpu(self) -> float: ...

    @property
    def memory_mb(self) -> int: ...

    @property
    def gpu_type(self) -> str | None: ...

    @property
    def gpu_count(self) -> int: ...


@dataclass(frozen=True)
class CostBreakdown:
    usd: Decimal
    basis: str
    hours: Decimal
    cpu_usd: Decimal
    memory_usd: Decimal
    gpu_usd: Decimal
    unpriced_gpu_type: str | None = None


def cost_breakdown(
    *,
    wall_seconds: float | Decimal,
    cpu: float | Decimal,
    memory_mb: int | float | Decimal,
    gpu_type: str | None,
    gpu_count: int,
    prices: ComputePrices,
    kind: str = "reserved",
) -> CostBreakdown:
    """Cost of holding ``cpu`` vCPUs, ``memory_mb`` MiB and ``gpu_count`` GPUs for ``wall_seconds``."""
    seconds = max(_dec(wall_seconds), Decimal(0))
    hours = seconds / _SECONDS_PER_HOUR
    cpu_d = max(_dec(cpu), Decimal(0))
    mem_gb = max(_dec(memory_mb), Decimal(0)) / _MIB_PER_GB
    gpus = max(int(gpu_count or 0), 0)
    cpu_usd = cpu_d * hours * prices.cpu_per_hour
    memory_usd = mem_gb * hours * prices.memory_gb_per_hour
    gpu_usd = Decimal(0)
    unpriced: str | None = None
    if gpus:
        label = gpu_type or "unspecified"
        price = prices.gpu_per_hour.get(label)
        if price is None:
            unpriced = label
        else:
            gpu_usd = Decimal(gpus) * hours * price
    total = _q(cpu_usd + memory_usd + gpu_usd)
    parts = [
        f"{kind} {_fmt(hours)}h",
        f"cpu {_fmt(cpu_d)}@{_fmt(prices.cpu_per_hour)}",
        f"mem {_fmt(mem_gb)}GB@{_fmt(prices.memory_gb_per_hour)}",
    ]
    if gpus:
        gpu_price = "unpriced" if unpriced else _fmt(prices.gpu_per_hour[gpu_type or "unspecified"])
        parts.append(f"gpu {gpus}x{gpu_type or 'unspecified'}@{gpu_price}")
    basis = "; ".join(parts)[:BASIS_MAX_CHARS]
    return CostBreakdown(
        usd=total,
        basis=basis,
        hours=hours,
        cpu_usd=_q(cpu_usd),
        memory_usd=_q(memory_usd),
        gpu_usd=_q(gpu_usd),
        unpriced_gpu_type=unpriced,
    )


def _fmt(value: Decimal) -> str:
    text = f"{value.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def compute_cost(
    *,
    wall_seconds: float | Decimal,
    cpu: float | Decimal,
    memory_mb: int | float | Decimal,
    gpu_type: str | None,
    gpu_count: int,
    prices: ComputePrices,
) -> tuple[Decimal, str]:
    """Actual cost of a finished run → ``(usd, basis)``."""
    b = cost_breakdown(
        wall_seconds=wall_seconds,
        cpu=cpu,
        memory_mb=memory_mb,
        gpu_type=gpu_type,
        gpu_count=gpu_count,
        prices=prices,
    )
    return b.usd, b.basis


def estimate_cost(resources: ResourceLike, timeout_seconds: int | float, prices: ComputePrices) -> tuple[Decimal, str]:
    """Worst-case cost if the job runs until its timeout → ``(usd, basis)``."""
    b = cost_breakdown(
        wall_seconds=timeout_seconds,
        cpu=resources.cpu,
        memory_mb=resources.memory_mb,
        gpu_type=resources.gpu_type,
        gpu_count=resources.gpu_count,
        prices=prices,
        kind="estimate",
    )
    return b.usd, b.basis
