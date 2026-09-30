"""Object keys: tenant-scoped construction, sanitization and rejection of traversal attempts."""

from __future__ import annotations

import uuid

import pytest

from aegis_api.lab.storage.base import InvalidObjectKey
from aegis_api.lab.storage.keys import object_key, sanitize_part, split_relative_path, validate_key

ORG = uuid.UUID("11111111-1111-4111-8111-111111111111")
PROJECT = uuid.UUID("22222222-2222-4222-8222-222222222222")
PREFIX = f"org/{ORG}/proj/{PROJECT}"


def test_object_key_shape():
    key = object_key(ORG, PROJECT, "artifacts", uuid.UUID(int=5), "v1", "results.csv")
    assert key == f"{PREFIX}/artifacts/{uuid.UUID(int=5)}/v1/results.csv"
    assert validate_key(key) == key
    assert validate_key(key, organization_id=ORG, project_id=PROJECT) == key


def test_object_key_accepts_string_uuids():
    assert object_key(str(ORG), str(PROJECT), "x").startswith(PREFIX)


@pytest.mark.parametrize("bad", ["not-a-uuid", "", "../../etc"])
def test_object_key_requires_uuid_tenant_ids(bad):
    with pytest.raises(InvalidObjectKey):
        object_key(bad, PROJECT, "x")
    with pytest.raises(InvalidObjectKey):
        object_key(ORG, bad, "x")


def test_object_key_requires_a_part():
    with pytest.raises(InvalidObjectKey):
        object_key(ORG, PROJECT)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report final.pdf", "report_final.pdf"),
        ("../../etc/passwd", "_.._etc_passwd"),
        ("a/b\\c", "a_b_c"),
        ("%2e%2e", "_2e_2e"),
        (".hidden", "hidden"),
        ("données.csv", "donn_es.csv"),
        ("x" * 500, "x" * 200),
    ],
)
def test_sanitize_part_maps_to_one_safe_segment(raw, expected):
    part = sanitize_part(raw)
    assert part == expected
    assert "/" not in part and "\\" not in part
    validate_key(f"{PREFIX}/{part}")


@pytest.mark.parametrize("raw", ["", ".", "..", "...", "／", "．．", "\u2025", "___", "\x00"])
def test_sanitize_part_rejects_parts_with_nothing_safe_left(raw):
    with pytest.raises(InvalidObjectKey):
        sanitize_part(raw)


def test_object_key_never_creates_extra_segments():
    key = object_key(ORG, PROJECT, "a/../../b")
    assert key.count("/") == PREFIX.count("/") + 1


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "/abs/path",
        f"/{PREFIX}/x",
        f"{PREFIX}/",
        f"{PREFIX}//x",
        f"{PREFIX}/../x",
        f"{PREFIX}/./x",
        f"{PREFIX}/..",
        f"{PREFIX}/%2e%2e/x",
        f"{PREFIX}/a%2Fb",
        f"{PREFIX}\\..\\x",
        f"{PREFIX}/.hidden",
        f"{PREFIX}/a b",
        f"{PREFIX}/caf\u00e9",
        f"{PREFIX}/\uff0e\uff0e/x",  # fullwidth dots
        f"{PREFIX}/\u2025/x",  # two-dot leader
        f"{PREFIX}/x\x00y",
        f"{PREFIX}/" + "x" * 201,
        "/".join([PREFIX, *["d"] * 40]),
        "a/" * 600 + "x",
        "C:/windows/system32",
    ],
)
def test_validate_key_rejects_traversal_and_tricks(bad):
    with pytest.raises(InvalidObjectKey):
        validate_key(bad)


def test_validate_key_enforces_tenant_prefix():
    other_org = uuid.uuid4()
    key = object_key(other_org, PROJECT, "x")
    with pytest.raises(InvalidObjectKey):
        validate_key(key, organization_id=ORG)
    with pytest.raises(InvalidObjectKey):
        validate_key(object_key(ORG, uuid.uuid4(), "x"), organization_id=ORG, project_id=PROJECT)
    with pytest.raises(InvalidObjectKey):
        validate_key(PREFIX, organization_id=ORG, project_id=PROJECT)  # the prefix itself is not an object
    assert validate_key(object_key(ORG, uuid.uuid4(), "x"), organization_id=ORG)


def test_validate_key_rejects_non_strings():
    with pytest.raises(InvalidObjectKey):
        validate_key(None)  # type: ignore[arg-type]


def test_split_relative_path():
    assert split_relative_path("results/metrics.json") == ["results", "metrics.json"]
    for bad in ["/etc/passwd", "../x", "a/../b", "a//b", "a\\b", "C:/x", "a/./b", "%2e%2e/x", "caf\u00e9", ""]:
        with pytest.raises(InvalidObjectKey):
            split_relative_path(bad)
    with pytest.raises(InvalidObjectKey):
        split_relative_path("/".join(["d"] * 20))
