"""Known-answer tests for PII/secret detection."""

from __future__ import annotations

import pytest

from engines.privacy.detectors import PIIDetector, iban_valid, luhn_valid, redact

D = PIIDetector()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Contact dana.fields@example.com please", "email"),
        ("Call +1 415 555 0123 today", "phone"),
        ("Card 4111 1111 1111 1111 on file", "credit_card"),
        ("IBAN DE89 3704 0044 0532 0130 00", "iban"),
        ("key AKIAIOSFODNN7EXAMPLE rotated", "aws_access_key"),
        ("token ghp_16CharactersLongTokenValue1234567890 leaked", "github_token"),
    ],
)
def test_detects(text, expected):
    types = {m.type for m in D.detect(text)}
    assert expected in types, (text, types)


def test_luhn():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")


def test_iban():
    assert iban_valid("DE89370400440532013000")
    assert not iban_valid("DE00370400440532013000")


def test_no_false_positive_on_plain_text():
    assert D.detect("The quick brown fox jumps over the lazy dog.") == []


def test_redaction_masks_values():
    out = redact("Email me at dana.fields@example.com or 555-123-4567", detector=D)
    assert "dana.fields@example.com" not in out
    assert "EMAIL" in out
