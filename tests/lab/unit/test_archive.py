"""Safe tar extraction/building: traversal, links, devices and budget attacks are refused."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest

from aegis_api.lab.execution.archive import (
    ArchiveError,
    ArchiveLimitExceeded,
    ChunkReader,
    ExtractLimits,
    TarBuilder,
    UnsafeArchiveMember,
    normalize_relative_path,
    safe_extract,
    stream_budget,
)

LIMITS = ExtractLimits(max_total_bytes=1024 * 1024, max_files=50)


def _tar(build: Callable[[tarfile.TarFile], None], *, gz: bool = False) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz" if gz else "w", format=tarfile.PAX_FORMAT) as tar:
        build(tar)
    buf.seek(0)
    return buf


def _file(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _special(tar: tarfile.TarFile, name: str, kind: bytes, **attrs: object) -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    for key, value in attrs.items():
        setattr(info, key, value)
    tar.addfile(info)


def test_extracts_regular_files_with_checksums(tmp_path: Path) -> None:
    def build(tar: tarfile.TarFile) -> None:
        _special(tar, "output", tarfile.DIRTYPE)
        _file(tar, "output/metrics.json", b'{"acc": 0.9}')
        _file(tar, "output/plots/a.csv", b"x,y\n1,2\n")

    report = safe_extract(_tar(build), tmp_path / "out", LIMITS, strip_prefix="output")
    files = report.by_path()
    assert set(files) == {"metrics.json", "plots/a.csv"}
    assert files["metrics.json"].sha256 == hashlib.sha256(b'{"acc": 0.9}').hexdigest()
    assert (tmp_path / "out" / "plots" / "a.csv").read_bytes() == b"x,y\n1,2\n"
    assert report.total_bytes == len(b'{"acc": 0.9}') + len(b"x,y\n1,2\n")
    assert report.rejected == []


def test_gzip_streams_are_supported(tmp_path: Path) -> None:
    report = safe_extract(_tar(lambda t: _file(t, "a.txt", b"hi"), gz=True), tmp_path, LIMITS)
    assert report.by_path()["a.txt"].size == 2


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("../escape.txt", "parent directory"),
        ("a/../../escape.txt", "parent directory"),
        ("/etc/cron.d/evil", "absolute"),
        ("a\\..\\b", "backslash"),
        ("/".join(["d"] * 40) + "/f", "too deep"),
    ],
)
def test_traversal_names_are_rejected(tmp_path: Path, name: str, reason: str) -> None:
    archive = _tar(lambda t: _file(t, name, b"pwned"))
    with pytest.raises(UnsafeArchiveMember, match=reason):
        safe_extract(archive, tmp_path / "dest", LIMITS)
    assert not (tmp_path / "escape.txt").exists()
    lenient = safe_extract(_tar(lambda t: _file(t, name, b"pwned")), tmp_path / "dest2", LIMITS, strict=False)
    assert lenient.files == [] and lenient.rejected and reason in lenient.rejected[0].reason


@pytest.mark.parametrize(
    ("kind", "attrs", "reason"),
    [
        (tarfile.SYMTYPE, {"linkname": "/etc/passwd"}, "symbolic link"),
        (tarfile.LNKTYPE, {"linkname": "/etc/passwd"}, "hard link"),
        (tarfile.CHRTYPE, {"devmajor": 1, "devminor": 3}, "device"),
        (tarfile.BLKTYPE, {"devmajor": 8, "devminor": 0}, "device"),
        (tarfile.FIFOTYPE, {}, "fifo"),
    ],
)
def test_links_devices_and_fifos_are_rejected(
    tmp_path: Path, kind: bytes, attrs: dict[str, object], reason: str
) -> None:
    with pytest.raises(UnsafeArchiveMember, match=reason):
        safe_extract(_tar(lambda t: _special(t, "output/x", kind, **attrs)), tmp_path / "d", LIMITS)


def test_symlink_is_never_followed_even_when_later_members_use_it(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "passwd").write_text("root:x:0:0")

    def build(tar: tarfile.TarFile) -> None:
        _special(tar, "output/link", tarfile.SYMTYPE, linkname=str(victim))
        _file(tar, "output/link/passwd", b"overwritten")
        _special(tar, "output/passwd", tarfile.SYMTYPE, linkname="/etc/passwd")
        _file(tar, "output/ok.txt", b"fine")

    dest = tmp_path / "dest"
    report = safe_extract(_tar(build), dest, LIMITS, strip_prefix="output", strict=False)
    assert (victim / "passwd").read_text() == "root:x:0:0"
    assert {r.reason for r in report.rejected} == {"symbolic link"}
    assert set(report.by_path()) == {"link/passwd", "ok.txt"}
    assert not (dest / "link").is_symlink() and (dest / "link" / "passwd").read_bytes() == b"overwritten"
    assert not any(p.is_symlink() for p in dest.rglob("*"))


def test_total_size_budget(tmp_path: Path) -> None:
    limits = ExtractLimits(max_total_bytes=10, max_files=10)
    with pytest.raises(ArchiveLimitExceeded):
        safe_extract(_tar(lambda t: _file(t, "big.bin", b"x" * 11)), tmp_path / "a", limits)
    with pytest.raises(ArchiveLimitExceeded):

        def two(tar: tarfile.TarFile) -> None:
            _file(tar, "a.bin", b"x" * 6)
            _file(tar, "b.bin", b"x" * 6)

        safe_extract(_tar(two), tmp_path / "b", limits, strict=False)
    per_file = ExtractLimits(max_total_bytes=100, max_files=10, max_member_bytes=5)
    with pytest.raises(ArchiveLimitExceeded):
        safe_extract(_tar(lambda t: _file(t, "a.bin", b"x" * 6)), tmp_path / "c", per_file)


def test_file_and_entry_count_budgets(tmp_path: Path) -> None:
    def many(tar: tarfile.TarFile) -> None:
        for i in range(6):
            _file(tar, f"f{i}.txt", b"1")

    with pytest.raises(ArchiveLimitExceeded, match="more than 5 files"):
        safe_extract(_tar(many), tmp_path / "a", ExtractLimits(max_total_bytes=100, max_files=5))

    def dirs(tar: tarfile.TarFile) -> None:
        for i in range(30):
            _special(tar, f"d{i}", tarfile.DIRTYPE)

    with pytest.raises(ArchiveLimitExceeded, match="entries"):
        safe_extract(_tar(dirs), tmp_path / "b", ExtractLimits(max_total_bytes=100, max_files=5, max_entries=20))


def test_prefix_duplicates_and_garbage(tmp_path: Path) -> None:
    with pytest.raises(UnsafeArchiveMember, match="outside"):
        safe_extract(_tar(lambda t: _file(t, "etc/passwd", b"x")), tmp_path / "a", LIMITS, strip_prefix="output")

    def dup(tar: tarfile.TarFile) -> None:
        _file(tar, "a.txt", b"1")
        _file(tar, "a.txt", b"2")

    with pytest.raises(UnsafeArchiveMember, match="duplicate"):
        safe_extract(_tar(dup), tmp_path / "b", LIMITS)
    with pytest.raises(ArchiveError):
        safe_extract(io.BytesIO(b"definitely not a tar archive" * 100), tmp_path / "c", LIMITS)
    with pytest.raises(ArchiveError):
        safe_extract(io.BytesIO(gzip.compress(b"\x00garbage" * 10)), tmp_path / "d", LIMITS)


def test_chunk_reader_enforces_stream_budget(tmp_path: Path) -> None:
    data = _tar(lambda t: _file(t, "a.bin", b"x" * 4096)).getvalue()
    chunks = [data[i : i + 1000] for i in range(0, len(data), 1000)]
    reader = io.BufferedReader(ChunkReader(chunks, max_bytes=len(data)))
    assert safe_extract(reader, tmp_path / "ok", LIMITS).by_path()["a.bin"].size == 4096
    small = io.BufferedReader(ChunkReader(chunks, max_bytes=2048))
    with pytest.raises(ArchiveLimitExceeded):
        safe_extract(small, tmp_path / "no", LIMITS)
    assert stream_budget(LIMITS) > LIMITS.max_total_bytes


def test_tar_builder_is_deterministic_and_safe(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1\n")
    (tmp_path / "src" / "main.py").write_text("print(1)\n")
    (tmp_path / "src" / "link.py").symlink_to("/etc/passwd")

    def build() -> bytes:
        buf = io.BytesIO()
        with TarBuilder(buf, max_total_bytes=10_000) as tar:
            tar.add_dir("output", mode=0o755, uid=65534, gid=65534)
            tar.add_bytes("input/params.json", b"{}")
            tar.add_tree("code", tmp_path / "src")
        return buf.getvalue()

    first, second = build(), build()
    assert first == second
    with tarfile.open(fileobj=io.BytesIO(first)) as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert set(members) == {
        "output",
        "input",
        "input/params.json",
        "code",
        "code/main.py",
        "code/pkg",
        "code/pkg/mod.py",
    }
    assert members["output"].uid == 65534 and members["input/params.json"].uid == 0
    assert members["input/params.json"].mode == 0o444 and members["input"].mode == 0o555
    assert all(m.mtime == 0 for m in members.values())
    buf = io.BytesIO()
    with TarBuilder(buf, max_total_bytes=4) as tar:
        with pytest.raises(UnsafeArchiveMember):
            tar.add_bytes("../x", b"1")
        with pytest.raises(ArchiveLimitExceeded):
            tar.add_bytes("input/big", b"12345")
        tar.add_bytes("input/a", b"1")
        with pytest.raises(ValueError, match="duplicate"):
            tar.add_bytes("input/a", b"1")


def test_normalize_relative_path() -> None:
    assert normalize_relative_path("./input//data.csv", allowed_roots=("input", "code")) == "input/data.csv"
    for bad in ("../x", "/abs", "output/x", "input/../../x", "input/.done", ""):
        with pytest.raises(ValueError):
            normalize_relative_path(bad, allowed_roots=("input", "code"))


# -- output interpretation ------------------------------------------------------------------------------
def test_metrics_json_parsing_is_strict_and_labelled_self_reported() -> None:
    from aegis_api.lab.execution.outputs import parse_metrics

    as_dict = parse_metrics(b'{"accuracy": 0.9, "loss": 1, "flag": true, "bad": "x", "inf": 1e999}')
    assert as_dict["source"] == "self_reported"
    assert as_dict["values"] == {"accuracy": 0.9, "loss": 1.0}
    assert len(as_dict["errors"]) == 3  # bool, string and non-finite values are rejected, never coerced
    as_list = parse_metrics(
        b'[{"name": "acc", "value": 0.5, "step": 1}, {"name": "acc", "value": 0.7, "step": 2},'
        b' {"name": "../etc", "value": 1}, {"name": "acc", "value": 0.1, "step": -1}, 7]'
    )
    assert [item["value"] for item in as_list["items"]] == [0.5, 0.7]
    assert as_list["values"] == {"acc": 0.7} and len(as_list["errors"]) == 3
    capped = parse_metrics(json.dumps({f"m{i}": i for i in range(1500)}).encode(), max_metrics=1000)
    assert len(capped["items"]) == 1000 and any("more than 1000" in e for e in capped["errors"])
    assert parse_metrics(b"not json")["errors"] and parse_metrics(b'"scalar"')["items"] == []
    assert parse_metrics(b"[" + b'{"name": "x", "value": NaN},' * 3 + b'{"name": "y", "value": 2}]')["values"] == {
        "y": 2.0
    }


def test_output_kinds_and_mime_types() -> None:
    from aegis_api.lab.execution.outputs import artifact_kind_for, mime_type_for

    assert artifact_kind_for("result.csv") == "csv" and artifact_kind_for("plots/a.PNG") == "plot"
    assert artifact_kind_for("model.safetensors") == "checkpoint" and artifact_kind_for("notes.bin") == "output"
    assert artifact_kind_for("metrics.json") == "json" and artifact_kind_for("run.log") == "log"
    assert mime_type_for("a.csv") == "text/csv" and mime_type_for("a.log") == "text/plain"
    assert mime_type_for("weird.zzz") == "application/octet-stream"
