"""Malware scanning adapters for uploaded and produced files.

``MALWARE_SCANNER`` selects the implementation:

* ``none``   — :class:`NoopScanner`; files are recorded as ``not_scanned``.
* ``clamav`` — :class:`ClamAVScanner` streams bytes to ``clamd`` over TCP using the ``zINSTREAM``
  command: each chunk is prefixed with its 4-byte big-endian length and the stream ends with a
  zero-length chunk. clamd answers ``stream: OK``, ``stream: <signature> FOUND`` or ``… ERROR``.

User uploads fail closed: an infected file is rejected with 422 ``malware_detected`` (and audited), and
when a scanner is configured but unreachable the upload is refused with 503 rather than stored
unscanned. Platform-produced objects are recorded with ``scan_status="error"`` and re-scanned by the
``data.process_artifact_version`` activity.
"""

from __future__ import annotations

import socket
import struct
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import BinaryIO, Literal, Protocol

import structlog

from aegis_api.config import get_settings
from aegis_api.errors import ServiceUnavailable, ValidationFailed

log = structlog.get_logger("aegis.lab.scanning")

ScanStatus = Literal["clean", "infected", "error", "not_scanned"]
SCAN_CHUNK_SIZE = 64 * 1024
_MAX_REPLY_BYTES = 4096


class MalwareDetected(ValidationFailed):
    """The file was rejected by the malware scanner."""

    code = "malware_detected"


class ScannerUnavailable(ServiceUnavailable):
    """The malware scanner is unavailable; the upload was not accepted."""

    code = "malware_scanner_unavailable"


@dataclass(frozen=True)
class ScanResult:
    status: ScanStatus
    scanner: str
    signature: str | None = None
    detail: str | None = None

    @property
    def infected(self) -> bool:
        return self.status == "infected"


class MalwareScanner(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    def scan_stream(self, chunks: Iterable[bytes]) -> ScanResult: ...


class NoopScanner:
    """No scanning configured: every file is ``not_scanned`` (never reported as clean)."""

    name = "none"

    @property
    def enabled(self) -> bool:
        return False

    def scan_stream(self, chunks: Iterable[bytes]) -> ScanResult:
        return ScanResult(status="not_scanned", scanner=self.name)


class ClamAVScanner:
    """clamd ``INSTREAM`` client over TCP with connect/read timeouts."""

    name = "clamav"

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = 5.0,
        timeout: float = 120.0,
        chunk_size: int = SCAN_CHUNK_SIZE,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.timeout = timeout
        self.chunk_size = max(1024, chunk_size)

    @property
    def enabled(self) -> bool:
        return True

    def _rechunk(self, chunks: Iterable[bytes]) -> Iterator[bytes]:
        for chunk in chunks:
            for start in range(0, len(chunk), self.chunk_size):
                piece = chunk[start : start + self.chunk_size]
                if piece:
                    yield piece

    def scan_stream(self, chunks: Iterable[bytes]) -> ScanResult:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        except OSError as exc:
            log.warning("clamav_unreachable", error=type(exc).__name__)
            return ScanResult(status="error", scanner=self.name, detail="scanner unreachable")
        try:
            sock.settimeout(self.timeout)
            send_error: OSError | None = None
            try:
                sock.sendall(b"zINSTREAM\0")
                for piece in self._rechunk(chunks):
                    sock.sendall(struct.pack(">I", len(piece)) + piece)
                sock.sendall(struct.pack(">I", 0))
            except OSError as exc:
                # clamd closes the stream early on StreamMaxLength; its reply explains why.
                send_error = exc
            reply = self._read_reply(sock)
            if not reply and send_error is not None:
                log.warning("clamav_stream_failed", error=type(send_error).__name__)
                return ScanResult(status="error", scanner=self.name, detail="stream interrupted")
            return parse_clamd_reply(reply, scanner=self.name)
        except OSError as exc:
            log.warning("clamav_scan_failed", error=type(exc).__name__)
            return ScanResult(status="error", scanner=self.name, detail="scanner communication failed")
        finally:
            sock.close()

    @staticmethod
    def _read_reply(sock: socket.socket) -> str:
        data = bytearray()
        while len(data) < _MAX_REPLY_BYTES:
            try:
                part = sock.recv(1024)
            except OSError:
                break
            if not part:
                break
            data.extend(part)
            if b"\0" in part or b"\n" in part:
                break
        return data.split(b"\0", 1)[0].decode("utf-8", "replace").strip()


def parse_clamd_reply(reply: str, *, scanner: str = "clamav") -> ScanResult:
    """Interpret a clamd INSTREAM reply (``stream: OK`` / ``stream: Sig FOUND`` / ``… ERROR``)."""
    text = reply.strip()
    body = text.split(":", 1)[1].strip() if text.lower().startswith("stream:") else text
    if body == "OK":
        return ScanResult(status="clean", scanner=scanner)
    if body.endswith(" FOUND"):
        signature = body[: -len(" FOUND")].strip()[:200] or "unknown"
        return ScanResult(status="infected", scanner=scanner, signature=signature)
    if body.endswith("ERROR"):
        return ScanResult(status="error", scanner=scanner, detail=body[:200])
    return ScanResult(status="error", scanner=scanner, detail="unrecognized scanner reply")


_override: MalwareScanner | None = None


def get_scanner() -> MalwareScanner:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.malware_scanner == "clamav":
        return ClamAVScanner(settings.clamav_host, settings.clamav_port)
    return NoopScanner()


def configure_scanner(scanner: MalwareScanner | None) -> None:
    """Install an explicit scanner (``None`` restores settings-based selection)."""
    global _override
    _override = scanner


def iter_file(fileobj: BinaryIO, chunk_size: int = SCAN_CHUNK_SIZE) -> Iterator[bytes]:
    while chunk := fileobj.read(chunk_size):
        yield chunk


def scan_file(fileobj: BinaryIO, scanner: MalwareScanner | None = None) -> ScanResult:
    """Scan a seekable file from the start and rewind it afterwards."""
    active = scanner or get_scanner()
    if not active.enabled:
        return ScanResult(status="not_scanned", scanner=active.name)
    fileobj.seek(0)
    try:
        return active.scan_stream(iter_file(fileobj))
    finally:
        fileobj.seek(0)


def scan_bytes(data: bytes, scanner: MalwareScanner | None = None) -> ScanResult:
    active = scanner or get_scanner()
    if not active.enabled:
        return ScanResult(status="not_scanned", scanner=active.name)
    return active.scan_stream([data])


def enforce_scan(result: ScanResult, *, fail_closed: bool) -> None:
    """Raise for infected files (always) and for scanner errors when ``fail_closed``."""
    if result.status == "infected":
        raise MalwareDetected(
            "The file was rejected by the malware scanner",
            details={"scanner": result.scanner, "signature": result.signature},
        )
    if result.status == "error" and fail_closed:
        raise ScannerUnavailable("The malware scanner is unavailable; please retry later")
