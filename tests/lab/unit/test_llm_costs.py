"""LLM cost math: cached tokens, thinking tokens, missing prices, local models, price lookup precedence."""

from __future__ import annotations

from decimal import Decimal

from engines.lab.llm_costs import (
    BASIS_CONFIGURED,
    BASIS_LOCAL,
    BASIS_NO_PRICE,
    Price,
    TokenCounts,
    compute_cost,
    estimate_cost,
    estimate_tokens_from_chars,
    price_from_mapping,
)

PRICE = Price(Decimal("1.00"), Decimal("2.00"), Decimal("0.25"))


def test_basic_cost():
    result = compute_cost(
        Price(Decimal("1"), Decimal("2")), TokenCounts(input_tokens=1_000_000, output_tokens=1_000_000)
    )
    assert result.usd == Decimal("3.000000")
    assert result.estimated is True and result.basis == BASIS_CONFIGURED


def test_cached_tokens_use_cached_price():
    result = compute_cost(PRICE, TokenCounts(input_tokens=1000, cached_tokens=400, output_tokens=500))
    # 600 × $1 + 400 × $0.25 + 500 × $2 per million
    assert result.usd == Decimal("0.001700")


def test_cached_tokens_without_cached_price_bill_at_input_price():
    price = Price(Decimal("1"), Decimal("2"))
    result = compute_cost(price, TokenCounts(input_tokens=1000, cached_tokens=400, output_tokens=0))
    assert result.usd == Decimal("0.001000")


def test_cached_tokens_are_clamped_to_input():
    result = compute_cost(PRICE, TokenCounts(input_tokens=100, cached_tokens=5000))
    assert result.usd == Decimal("0.000025")


def test_thinking_tokens_billed_as_output():
    without = compute_cost(PRICE, TokenCounts(input_tokens=0, output_tokens=1000))
    with_thinking = compute_cost(PRICE, TokenCounts(input_tokens=0, output_tokens=1000, thinking_tokens=3000))
    assert without.usd == Decimal("0.002000")
    assert with_thinking.usd == Decimal("0.008000")


def test_no_price_means_unknown_cost():
    result = compute_cost(None, TokenCounts(input_tokens=10, output_tokens=10))
    assert result.usd is None and result.estimated is True and result.basis == BASIS_NO_PRICE


def test_local_models_cost_zero():
    result = compute_cost(None, TokenCounts(input_tokens=10, output_tokens=10), local=True)
    assert result.usd == Decimal("0") and result.estimated is False and result.basis == BASIS_LOCAL


def test_quantized_to_micro_dollars():
    result = compute_cost(Price(Decimal("0.3"), Decimal("2.5")), TokenCounts(input_tokens=7, output_tokens=3))
    assert result.usd == Decimal("0.000010")  # 2.1e-6 + 7.5e-6 = 9.6e-6 → 0.000010
    assert result.usd is not None and result.usd.as_tuple().exponent == -6


def test_price_parsing():
    assert price_from_mapping({"input_per_mtok": 0.1, "output_per_mtok": "0.4"}) == Price(
        Decimal("0.1"), Decimal("0.4"), None, "settings"
    )
    assert price_from_mapping({"input_per_mtok": 1}) is None
    assert price_from_mapping({"input_per_mtok": -1, "output_per_mtok": 1}) is None
    assert price_from_mapping({"input_per_mtok": "nan", "output_per_mtok": 1}) is None
    assert price_from_mapping(None) is None


def test_estimates():
    assert estimate_cost(PRICE, 1_000_000, 500_000) == Decimal("2.000000")
    assert estimate_cost(None, 10, 10) is None
    assert estimate_cost(None, 10, 10, local=True) == Decimal("0")
    assert estimate_tokens_from_chars(0) == 1
    assert estimate_tokens_from_chars(9) == 3


def test_price_lookup_precedence():
    import uuid

    from aegis_api.lab.llm.costs import ModelConfigView, resolve_price

    org = uuid.uuid4()
    platform_row = ModelConfigView(
        id="p",
        organization_id=None,
        provider_kind="gemini",
        model="g",
        tier="default",
        input_per_mtok_usd=Decimal("1"),
        output_per_mtok_usd=Decimal("2"),
    )
    org_row = ModelConfigView(
        id="o",
        organization_id=org,
        provider_kind="gemini",
        model="g",
        tier="default",
        input_per_mtok_usd=Decimal("5"),
        output_per_mtok_usd=Decimal("6"),
    )
    pricing = {
        "gemini:g": {"input_per_mtok": 9, "output_per_mtok": 9},
        "gemini:g-001": {"input_per_mtok": 7, "output_per_mtok": 7},
    }
    assert resolve_price("gemini", "g", configs=[platform_row, org_row], pricing=pricing).input_per_mtok == Decimal("5")
    assert resolve_price("gemini", "g", configs=[platform_row], pricing=pricing).source == "platform"
    assert resolve_price("gemini", "g", configs=[], pricing=pricing).input_per_mtok == Decimal("9")
    assert resolve_price("gemini", "alias", model_version="g-001", pricing=pricing).input_per_mtok == Decimal("7")
    assert resolve_price("gemini", "unknown", pricing=pricing) is None
    assert (
        resolve_price("gemini", "unknown", pricing={"gemini": {"input_per_mtok": 1, "output_per_mtok": 1}}) is not None
    )
