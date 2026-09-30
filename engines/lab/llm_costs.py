"""LLM cost math (pure).

Costs are computed only from a *configured* price and the token counts the provider reported — they are
never invented. When no price is configured the cost is ``None`` and the basis says so; callers record
``cost_estimated=True`` in that case.

Token semantics (normalised by the provider adapters before they reach this module):

* ``input_tokens`` — every prompt-side token billed at the input rate, *including* cached tokens
  (Gemini's ``promptTokenCount`` already includes ``cachedContentTokenCount``);
* ``cached_tokens`` — the subset of ``input_tokens`` served from the provider's context cache, billed at
  ``cached_input_per_mtok`` when a cached price is configured (otherwise at the normal input price);
* ``output_tokens`` — visible output tokens;
* ``thinking_tokens`` — reasoning ("thought") tokens, billed at the **output** price.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MTOK = Decimal(1_000_000)
USD_QUANTUM = Decimal("0.000001")  # matches Numeric(18, 6) money columns

BASIS_NO_PRICE = "no price configured"
BASIS_LOCAL = "local model — no provider charge"
BASIS_CONFIGURED = "configured price × reported tokens"
BASIS_NO_USAGE = "provider did not report token usage"


@dataclass(frozen=True)
class Price:
    """USD per one million tokens."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None = None
    source: str = "settings"

    def as_dict(self) -> dict[str, str | None]:
        return {
            "input_per_mtok": str(self.input_per_mtok),
            "output_per_mtok": str(self.output_per_mtok),
            "cached_input_per_mtok": str(self.cached_input_per_mtok) if self.cached_input_per_mtok is not None else None,
            "source": self.source,
        }


@dataclass(frozen=True)
class TokenCounts:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thinking_tokens: int = 0


@dataclass(frozen=True)
class CostResult:
    usd: Decimal | None
    estimated: bool
    basis: str


def to_decimal(value: Any) -> Decimal | None:
    """Parse a non-negative price. Returns ``None`` for missing/invalid/negative values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result


def price_from_mapping(data: Mapping[str, Any] | None, *, source: str = "settings") -> Price | None:
    """Build a :class:`Price` from ``{"input_per_mtok", "output_per_mtok", "cached_input_per_mtok"?}``.

    Both the input and the output price are required; a partial price is treated as "no price".
    """
    if not data:
        return None
    input_price = to_decimal(data.get("input_per_mtok"))
    output_price = to_decimal(data.get("output_per_mtok"))
    if input_price is None or output_price is None:
        return None
    return Price(
        input_per_mtok=input_price,
        output_per_mtok=output_price,
        cached_input_per_mtok=to_decimal(data.get("cached_input_per_mtok")),
        source=source,
    )


def quantize_usd(value: Decimal) -> Decimal:
    return value.quantize(USD_QUANTUM, rounding=ROUND_HALF_UP)


def compute_cost(price: Price | None, usage: TokenCounts, *, local: bool = False) -> CostResult:
    """Exact cost of one call from reported token counts.

    ``local`` providers (models the organization runs itself) cost nothing at the provider level.
    """
    if local:
        return CostResult(usd=Decimal("0"), estimated=False, basis=BASIS_LOCAL)
    if price is None:
        return CostResult(usd=None, estimated=True, basis=BASIS_NO_PRICE)
    input_tokens = max(int(usage.input_tokens or 0), 0)
    cached = min(max(int(usage.cached_tokens or 0), 0), input_tokens)
    output_tokens = max(int(usage.output_tokens or 0), 0)
    thinking = max(int(usage.thinking_tokens or 0), 0)
    if input_tokens == 0 and output_tokens == 0 and thinking == 0:
        return CostResult(usd=Decimal("0"), estimated=True, basis=BASIS_NO_USAGE)
    cached_price = price.cached_input_per_mtok if price.cached_input_per_mtok is not None else price.input_per_mtok
    total = (
        Decimal(input_tokens - cached) * price.input_per_mtok
        + Decimal(cached) * cached_price
        + Decimal(output_tokens + thinking) * price.output_per_mtok
    ) / MTOK
    return CostResult(usd=quantize_usd(total), estimated=True, basis=BASIS_CONFIGURED)


def estimate_cost(price: Price | None, input_tokens: int, output_tokens: int, *, local: bool = False) -> Decimal | None:
    """Pre-call estimate (no cache discount). ``None`` when the price is unknown."""
    if local:
        return Decimal("0")
    if price is None:
        return None
    total = (
        Decimal(max(input_tokens, 0)) * price.input_per_mtok + Decimal(max(output_tokens, 0)) * price.output_per_mtok
    ) / MTOK
    return quantize_usd(total)


def estimate_tokens_from_chars(chars: int) -> int:
    """Deterministic, provider-independent token estimate (~4 characters per token, rounded up)."""
    return max((max(chars, 0) + 3) // 4, 1)
