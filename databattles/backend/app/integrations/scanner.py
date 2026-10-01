"""Malware scanning adapter for uploaded files.

The default scanner records `skipped`. When CLAMAV_HOST is configured, files are streamed to a
clamd daemon (INSTREAM protocol) and rejected if a signature matches.
"""

from __future__ import annotations

import os
import socket
import struct
from typing import Protocol


class FileScanner(Protocol):
    def scan(self, path: str) -> str: ...  # "clean" | "infected" | "skipped" | "error"


class NoopScanner:
    def scan(self, path: str) -> str:
        return "skipped"


class ClamdScanner:
    def __init__(self, host: str, port: int = 3310, timeout: float = 30.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, path: str) -> str:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock, open(path, "rb") as fh:
                sock.sendall(b"zINSTREAM\0")
                while chunk := fh.read(64 * 1024):
                    sock.sendall(struct.pack("!L", len(chunk)) + chunk)
                sock.sendall(struct.pack("!L", 0))
                reply = sock.recv(4096).decode(errors="ignore")
        except OSError:
            return "error"
        return "infected" if "FOUND" in reply else "clean"


def get_scanner() -> FileScanner:
    host = os.environ.get("CLAMAV_HOST")
    return ClamdScanner(host, int(os.environ.get("CLAMAV_PORT", "3310"))) if host else NoopScanner()
