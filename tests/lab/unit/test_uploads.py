"""Lab upload validation (magic sniffing, archive safety, limits) and the ClamAV INSTREAM adapter."""

from __future__ import annotations

import hashlib
import io
import json
import socket
import stat
import struct
import tarfile
import threading
import zipfile
from collections.abc import Iterator

import pytest

from aegis_api.errors import PayloadTooLarge, ValidationFailed
from aegis_api.lab.data.scanning import (
    ClamAVScanner,
    MalwareDetected,
    NoopScanner,
    ScannerUnavailable,
    ScanResult,
    enforce_scan,
    parse_clamd_reply,
    scan_bytes,
    scan_file,
)
from aegis_api.lab.data.uploads import DATASET_KINDS, parse_npy_header, validate_lab_upload

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def _npy(shape: tuple[int, ...] = (3, 2), descr: str = "<f8", payload: bytes | None = None) -> bytes:
    header = f"{{'descr': '{descr}', 'fortran_order': False, 'shape': {shape!r}, }}"
    header_bytes = header.encode("latin-1")
    pad = 64 - ((10 + len(header_bytes) + 1) % 64)
    header_bytes += b" " * pad + b"\n"
    body = payload if payload is not None else b"\x00" * 8 * (shape[0] * (shape[1] if len(shape) > 1 else 1))
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header_bytes)) + header_bytes + body


def _zip(entries: dict[str, bytes], *, symlink: str | None = None, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


def _tgz(members: list[tarfile.TarInfo | tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for member in members:
            if isinstance(member, tarfile.TarInfo):
                tf.addfile(member)
            else:
                name, data = member
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def validate(name: str, data: bytes, content_type: str | None = None, **kw):
    return validate_lab_upload(name, content_type, io.BytesIO(data), **kw)


class NonSeekable(io.RawIOBase):
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readinto(self, b):  # type: ignore[no-untyped-def]
        chunk = self._buf.read(len(b))
        b[: len(chunk)] = chunk
        return len(chunk)


# -- accepted files ---------------------------------------------------------------------------------
VALID_FILES = [
    ("data.csv", b"a,b\n1,2\n", "text/csv", "csv"),
    ("data.tsv", b"a\tb\n1\t2\n", None, "tsv"),
    ("notes.txt", "héllo wörld\n#!/not/a/script".encode(), "text/plain", "text"),
    ("README.md", b"#!/usr/bin/env hi\n# Title\n", "text/markdown", "markdown"),
    ("doc.json", b'{"a": [1, 2, {"b": null}]}', "application/json", "json"),
    ("rows.jsonl", b'{"a": 1}\n\n{"a": 2}\n', "application/x-ndjson", "jsonl"),
    (
        "nb.ipynb",
        json.dumps({"nbformat": 4, "nbformat_minor": 5, "cells": [], "metadata": {}}).encode(),
        None,
        "notebook",
    ),
    ("paper.pdf", b"%PDF-1.7\n1 0 obj\n", "application/pdf", "pdf"),
    ("table.parquet", b"PAR1" + b"\x00" * 16 + b"PAR1", None, "parquet"),
    ("array.npy", _npy(), None, "npy"),
    ("arrays.npz", _zip({"x.npy": _npy(), "y.npy": _npy((4,), "<i8")}), "application/zip", "npz"),
    ("bundle.zip", _zip({"a/b.csv": b"x,y\n1,2\n", "c.txt": b"hello"}), "application/zip", "zip"),
    ("bundle.tar.gz", _tgz([("dir/a.csv", b"x\n1\n"), ("b.txt", b"hello")]), "application/gzip", "tar_gz"),
    ("bundle.tgz", _tgz([("a.csv", b"x\n1\n")]), None, "tar_gz"),
    ("plot.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png", "png"),
    ("photo.JPG", b"\xff\xd8\xff\xe0" + b"\x00" * 32, "image/jpeg", "jpeg"),
]


@pytest.mark.parametrize(("name", "data", "ctype", "kind"), VALID_FILES)
def test_valid_uploads_are_accepted(name, data, ctype, kind):
    with validate(name, data, ctype) as upload:
        assert upload.kind == kind
        assert upload.size == len(data)
        assert upload.sha256 == hashlib.sha256(data).hexdigest()
        assert upload.open().read() == data
        assert upload.filename == upload.filename.strip()


def test_filename_is_sanitized_and_extension_detected():
    with validate("../../etc/My Data.CSV", b"a\n1\n") as upload:
        assert upload.filename == "My_Data.csv"
        assert upload.extension == ".csv"
        assert upload.content_type == "text/csv"


def test_non_seekable_streams_are_spooled():
    data = b"a,b\n" + b"1,2\n" * 100_000
    upload = validate_lab_upload("big.csv", "text/csv", NonSeekable(data))  # type: ignore[arg-type]
    try:
        assert upload.owns_file
        assert upload.size == len(data)
        assert upload.open().read() == data
    finally:
        upload.close()


def test_generic_content_type_is_accepted_and_canonicalized():
    with validate("x.jsonl", b'{"a":1}\n', "application/octet-stream") as upload:
        assert upload.content_type == "application/x-ndjson"


# -- rejections -------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "data", "ctype"),
    [
        ("paper.pdf", b"not a pdf", None),
        ("table.parquet", b"PAR1" + b"\x00" * 16 + b"XXXX", None),
        ("table.parquet", b"PAR1PAR1", None),
        ("array.npy", b"\x93NUMPX" + b"\x00" * 20, None),
        ("plot.png", b"GIF89a" + b"\x00" * 20, None),
        ("photo.jpg", b"\x89PNG\r\n\x1a\n", None),
        ("bundle.zip", b"PK\x05\x06" + b"\x00" * 18, None),  # empty archive
        ("bundle.tgz", b"not gzip", None),
        ("doc.json", b'{"a": ', None),
        ("rows.jsonl", b'{"a": 1}\n{oops}\n', None),
        ("nb.ipynb", json.dumps({"cells": []}).encode(), None),  # no nbformat
        ("nb.ipynb", json.dumps({"nbformat": "4", "cells": []}).encode(), None),
        ("nb.ipynb", b"[1, 2]", None),
        ("data.csv", b"\xff\xfe\x00a,b", None),  # invalid UTF-8 / NUL
        ("data.csv", b"a,b\n\xc3\x28,1\n", None),  # invalid UTF-8 sequence
        ("notes.txt", b"hello\x00world", None),
        ("data.csv", b"\n\n", None),  # no header
    ],
)
def test_content_mismatches_are_rejected(name, data, ctype):
    with pytest.raises(ValidationFailed):
        validate(name, data, ctype)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("data.csv", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" + b"\x00" * 60),  # PE executable disguised as CSV
        ("array.npy", b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 60),
        ("paper.pdf", b"\xcf\xfa\xed\xfe" + b"\x00" * 60),  # Mach-O
        ("doc.json", b"#!/bin/sh\nrm -rf /\n"),
        ("data.csv", b"#!/usr/bin/env python\nimport os\n"),
        ("table.parquet", b"#!/bin/sh\nPAR1"),
    ],
)
def test_executables_and_scripts_are_rejected(name, data):
    with pytest.raises(ValidationFailed) as info:
        validate(name, data)
    assert info.value.code == "invalid_file_content"


def test_mz_prefix_in_plain_text_is_not_mistaken_for_an_executable():
    with validate("codes.csv", b"MZ_code,value\nA,1\n") as upload:
        assert upload.kind == "csv"


@pytest.mark.parametrize("name", ["tool.exe", "script.sh", "run.py", "page.html", "noext", "evil.csv.exe"])
def test_unsupported_extensions_are_rejected(name):
    with pytest.raises(ValidationFailed) as info:
        validate(name, b"hello")
    assert info.value.code == "unsupported_file_type"


def test_declared_content_type_must_match_extension():
    with pytest.raises(ValidationFailed):
        validate("data.csv", b"a\n1\n", "image/png")
    with pytest.raises(ValidationFailed):
        validate("plot.png", b"\x89PNG\r\n\x1a\n", "text/html")


def test_allowed_kinds_restrict_types():
    with pytest.raises(ValidationFailed):
        validate("paper.pdf", b"%PDF-1.4", allowed_kinds=DATASET_KINDS)
    with validate("data.csv", b"a\n1\n", allowed_kinds=DATASET_KINDS) as upload:
        assert upload.kind == "csv"


def test_empty_and_oversized_uploads():
    with pytest.raises(ValidationFailed):
        validate("data.csv", b"")
    with pytest.raises(PayloadTooLarge):
        validate("data.csv", b"a\n" + b"1\n" * 100, max_bytes=50)
    with pytest.raises(PayloadTooLarge):
        validate_lab_upload("data.csv", None, NonSeekable(b"a\n" + b"1\n" * 100), max_bytes=50)  # type: ignore[arg-type]


# -- archives ---------------------------------------------------------------------------------------
def test_zip_bomb_high_ratio_is_rejected():
    bomb = _zip({"zeros.bin": b"\x00" * (20 * 1024 * 1024)})
    assert len(bomb) < 100_000
    with pytest.raises(ValidationFailed) as info:
        validate("bundle.zip", bomb)
    assert "ratio" in info.value.message


def test_zip_with_too_many_entries_is_rejected():
    many = _zip({f"f{i}.txt": b"x" for i in range(5001)}, compression=zipfile.ZIP_STORED)
    with pytest.raises(ValidationFailed) as info:
        validate("bundle.zip", many)
    assert "5000" in info.value.message


@pytest.mark.parametrize(
    "entry", ["../evil.txt", "a/../../evil.txt", "/etc/passwd", "C:/Windows/x.txt", "a\\..\\..\\x"]
)
def test_zip_with_path_traversal_is_rejected(entry):
    with pytest.raises(ValidationFailed):
        validate("bundle.zip", _zip({entry: b"x"}))


def test_zip_with_symlink_is_rejected():
    with pytest.raises(ValidationFailed) as info:
        validate("bundle.zip", _zip({"ok.txt": b"x"}, symlink="link"))
    assert "symbolic" in info.value.message


def test_zip_with_executables_is_rejected():
    with pytest.raises(ValidationFailed):
        validate("bundle.zip", _zip({"setup.exe": b"hello"}))
    with pytest.raises(ValidationFailed):
        validate("bundle.zip", _zip({"innocent.bin": b"\x7fELF\x02\x01\x01" + b"\x00" * 64}))


def test_zip_with_encrypted_entry_is_rejected():
    data = bytearray(_zip({"a.txt": b"secret"}, compression=zipfile.ZIP_STORED))
    # Set the "encrypted" general-purpose flag in the local header and the central directory.
    data[6] |= 0x1
    central = data.find(b"PK\x01\x02")
    data[central + 8] |= 0x1
    with pytest.raises(ValidationFailed):
        validate("bundle.zip", bytes(data))


def test_npz_must_contain_only_safe_arrays():
    with pytest.raises(ValidationFailed):
        validate("arrays.npz", _zip({"x.npy": _npy(), "notes.txt": b"hi"}))
    with pytest.raises(ValidationFailed) as info:
        validate("arrays.npz", _zip({"x.npy": _npy((2,), "|O", payload=b"\x80\x04N.")}))
    assert "pickled" in info.value.message


def test_npy_object_dtype_is_rejected():
    with pytest.raises(ValidationFailed):
        validate("array.npy", _npy((2,), "|O", payload=b"\x80\x04N."))


def test_npy_header_parser():
    header = parse_npy_header(_npy((5, 3), "<f4"))
    assert header["shape"] == (5, 3)
    for bad in [b"\x93NUMPY\x09\x00\x00\x00", b"\x93NUMPY\x01\x00\xff\xff" + b"x" * 10]:
        with pytest.raises(ValidationFailed):
            parse_npy_header(bad)
    with pytest.raises(ValidationFailed):
        parse_npy_header(_npy().replace(b"'shape'", b"'shxpe'"))
    with pytest.raises(ValidationFailed):
        parse_npy_header(_npy().replace(b"(3, 2)", b"__import__('os')".ljust(6)))


def test_tar_with_symlink_is_rejected():
    link = tarfile.TarInfo("link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    with pytest.raises(ValidationFailed) as info:
        validate("bundle.tar.gz", _tgz([("a.txt", b"x"), link]))
    assert "regular files" in info.value.message


def test_tar_with_hardlink_and_devices_is_rejected():
    hard = tarfile.TarInfo("hard")
    hard.type = tarfile.LNKTYPE
    hard.linkname = "a.txt"
    device = tarfile.TarInfo("dev")
    device.type = tarfile.CHRTYPE
    for member in (hard, device):
        with pytest.raises(ValidationFailed):
            validate("bundle.tgz", _tgz([("a.txt", b"x"), member]))


@pytest.mark.parametrize("name", ["../evil.txt", "/abs.txt", "a/../../b.txt"])
def test_tar_with_path_traversal_is_rejected(name):
    with pytest.raises(ValidationFailed):
        validate("bundle.tgz", _tgz([(name, b"x")]))


def test_tar_bomb_high_ratio_is_rejected():
    with pytest.raises(ValidationFailed):
        validate("bundle.tgz", _tgz([("zeros.bin", b"\x00" * (20 * 1024 * 1024))]))


def test_tar_with_executable_member_is_rejected():
    with pytest.raises(ValidationFailed):
        validate("bundle.tgz", _tgz([("tool", b"MZ\x90\x00" + b"\x00" * 64)]))


def test_corrupt_archives_are_rejected():
    with pytest.raises(ValidationFailed):
        validate("bundle.zip", b"PK\x03\x04" + b"garbage" * 10)
    with pytest.raises(ValidationFailed):
        validate("bundle.tgz", b"\x1f\x8b\x08\x00" + b"garbage" * 10)


# -- malware scanning -------------------------------------------------------------------------------
class FakeClamd:
    """A clamd INSTREAM speaker for tests: verifies framing and answers OK / FOUND / ERROR."""

    def __init__(self, *, mode: str = "auto", max_stream: int | None = None) -> None:
        self.mode = mode
        self.max_stream = max_stream
        self.received: list[bytes] = []
        self.commands: list[bytes] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _recv_exact(self, conn: socket.socket, n: int) -> bytes:
        data = b""
        while len(data) < n:
            part = conn.recv(n - len(data))
            if not part:
                raise ConnectionError("closed")
            data += part
        return data

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    command = b""
                    while not command.endswith(b"\0"):
                        command += self._recv_exact(conn, 1)
                    self.commands.append(command)
                    body = b""
                    while True:
                        (length,) = struct.unpack(">I", self._recv_exact(conn, 4))
                        if length == 0:
                            break
                        body += self._recv_exact(conn, length)
                        if self.max_stream is not None and len(body) > self.max_stream:
                            conn.sendall(b"INSTREAM size limit exceeded. ERROR\0")
                            break
                    else:  # pragma: no cover
                        pass
                    if self.max_stream is not None and len(body) > self.max_stream:
                        continue
                    self.received.append(body)
                    if self.mode == "error":
                        conn.sendall(b"stream: Can't allocate memory ERROR\0")
                    elif self.mode == "garbage":
                        conn.sendall(b"???\0")
                    elif EICAR in body:
                        conn.sendall(b"stream: Eicar-Test-Signature FOUND\0")
                    else:
                        conn.sendall(b"stream: OK\0")
                except (ConnectionError, OSError, struct.error):
                    continue

    def close(self) -> None:
        self._stop = True
        self.sock.close()


@pytest.fixture
def clamd() -> Iterator[FakeClamd]:
    server = FakeClamd()
    yield server
    server.close()


def test_clamav_clean_and_framing(clamd):
    scanner = ClamAVScanner("127.0.0.1", clamd.port, chunk_size=1024)
    data = b"a,b\n" + b"1,2\n" * 2000
    result = scanner.scan_stream([data[:10], data[10:]])
    assert result == ScanResult(status="clean", scanner="clamav")
    assert clamd.commands[-1] == b"zINSTREAM\0"
    assert clamd.received[-1] == data


def test_clamav_detects_eicar(clamd):
    scanner = ClamAVScanner("127.0.0.1", clamd.port)
    result = scan_bytes(b"prefix " + EICAR, scanner)
    assert result.status == "infected"
    assert result.signature == "Eicar-Test-Signature"
    with pytest.raises(MalwareDetected) as info:
        enforce_scan(result, fail_closed=False)
    assert info.value.code == "malware_detected"
    assert info.value.status_code == 422


def test_clamav_error_reply():
    server = FakeClamd(mode="error")
    try:
        result = ClamAVScanner("127.0.0.1", server.port).scan_stream([b"data"])
        assert result.status == "error"
        enforce_scan(result, fail_closed=False)  # platform objects: recorded, not rejected
        with pytest.raises(ScannerUnavailable):
            enforce_scan(result, fail_closed=True)  # user uploads fail closed
    finally:
        server.close()


def test_clamav_stream_limit_reply():
    server = FakeClamd(max_stream=10)
    try:
        result = ClamAVScanner("127.0.0.1", server.port, chunk_size=1024).scan_stream([b"x" * 50_000])
        assert result.status == "error"
    finally:
        server.close()


def test_clamav_unreachable_is_an_error_not_clean():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    result = ClamAVScanner("127.0.0.1", port, connect_timeout=0.5).scan_stream([b"x"])
    assert result.status == "error"


def test_scan_file_rewinds(clamd):
    fh = io.BytesIO(b"hello world")
    fh.seek(5)
    result = scan_file(fh, ClamAVScanner("127.0.0.1", clamd.port))
    assert result.status == "clean"
    assert fh.tell() == 0
    assert clamd.received[-1] == b"hello world"


def test_noop_scanner_never_reports_clean():
    assert NoopScanner().scan_stream([EICAR]).status == "not_scanned"
    assert scan_bytes(EICAR, NoopScanner()).status == "not_scanned"


@pytest.mark.parametrize(
    ("reply", "status", "signature"),
    [
        ("stream: OK", "clean", None),
        ("stream: Win.Test.EICAR_HDB-1 FOUND", "infected", "Win.Test.EICAR_HDB-1"),
        ("INSTREAM size limit exceeded. ERROR", "error", None),
        ("", "error", None),
        ("weird", "error", None),
    ],
)
def test_parse_clamd_reply(reply, status, signature):
    result = parse_clamd_reply(reply)
    assert result.status == status
    assert result.signature == signature
