"""Upload scanning.

* ``clamav``: streams bytes to clamd over TCP (``INSTREAM``) and reports ``clean`` / ``infected`` / ``error``.
* ``none``: no scanner configured — status is reported honestly as ``not_scanned`` (never as ``clean``).

Independently of the scanner, ``sniff`` rejects executable/binary payloads masquerading as documents.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from aegis_api.config import get_settings

CHUNK = 64 * 1024
_EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"#!")


@dataclass(frozen=True)
class ScanResult:
    status: str  # clean | infected | not_scanned | error
    detail: str = ""


def looks_executable(data: bytes) -> bool:
    return data[:4].startswith(_EXECUTABLE_MAGIC)


def scan_bytes(data: bytes, *, timeout: float = 30.0) -> ScanResult:
    settings = get_settings()
    if settings.malware_scanner != "clamav" or not settings.clamav_host:
        return ScanResult("not_scanned", "no malware scanner configured")
    try:
        with socket.create_connection((settings.clamav_host, settings.clamav_port), timeout=timeout) as sock:
            sock.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), CHUNK):
                chunk = data[i : i + CHUNK]
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
            sock.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                part = sock.recv(4096)
                if not part:
                    break
                reply += part
    except OSError as exc:
        return ScanResult("error", f"scanner unavailable: {type(exc).__name__}")
    text = reply.rstrip(b"\0").decode("utf-8", errors="replace")
    if text.endswith("OK"):
        return ScanResult("clean", "clamav: OK")
    if "FOUND" in text:
        return ScanResult("infected", text.split(":", 1)[-1].strip()[:200])
    return ScanResult("error", text[:200])
