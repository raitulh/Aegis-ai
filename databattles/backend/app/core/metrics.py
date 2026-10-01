"""Lightweight in-process application metrics (request counts, latency, errors).

Exposed to platform admins via the health dashboard. For production fleets,
export these to Prometheus/OpenTelemetry; the recording API stays the same.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.request_count = 0
        self.error_count = 0  # 5xx
        self.status_counts: dict[int, int] = defaultdict(int)
        self.latencies_ms: deque[float] = deque(maxlen=2000)
        self.counters: dict[str, int] = defaultdict(int)

    def record_request(self, status: int, latency_ms: float) -> None:
        with self._lock:
            self.request_count += 1
            self.status_counts[status] += 1
            if status >= 500:
                self.error_count += 1
            self.latencies_ms.append(latency_ms)

    def incr(self, name: str, value: int = 1) -> None:
        with self._lock:
            self.counters[name] += value

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            lat = sorted(self.latencies_ms)

            def pct(p: float) -> float | None:
                if not lat:
                    return None
                return round(lat[min(len(lat) - 1, int(len(lat) * p))], 1)

            return {
                "uptime_seconds": int(time.time() - self.started_at),
                "requests": self.request_count,
                "server_errors": self.error_count,
                "error_rate": round(self.error_count / self.request_count, 4) if self.request_count else 0.0,
                "latency_ms_p50": pct(0.5),
                "latency_ms_p95": pct(0.95),
                "latency_ms_p99": pct(0.99),
                "status_counts": dict(self.status_counts),
                "counters": dict(self.counters),
            }


metrics = Metrics()
