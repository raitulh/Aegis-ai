"""Container healthcheck probe (stdlib only; used by docker/Dockerfile.api and docker-compose.yml).

Usage::

    python docker/healthcheck.py                                   # API liveness: http://127.0.0.1:8000/health/live
    python docker/healthcheck.py http://127.0.0.1:9102/metrics     # a worker's Prometheus endpoint

Exits 0 when the URL answers HTTP 2xx within the timeout, 1 otherwise. Only loopback http:// URLs are accepted —
this is a local liveness probe, never an outbound request — and proxies from the environment are ignored so an
``HTTP_PROXY`` setting can never route the probe elsewhere. Replaces ``curl`` so the runtime image ships no HTTP
client binary.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:8000/health/live"
TIMEOUT_SECONDS = 4.0
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def probe(url: str, timeout: float = TIMEOUT_SECONDS) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in _LOOPBACK:
        sys.stderr.write(f"healthcheck: refusing non-loopback URL {url!r}\n")
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        # Scheme and host are validated above (loopback http only).
        with opener.open(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError) as exc:
        sys.stderr.write(f"healthcheck: {type(exc).__name__}: {exc}\n")
        return False


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else DEFAULT_URL
    return 0 if probe(url) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
