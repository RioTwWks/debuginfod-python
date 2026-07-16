"""Runtime metrics for Web UI and monitoring."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ScanMetrics:
    duration_ms: int = 0
    indexed: int = 0
    skipped: int = 0
    errors: int = 0
    finished_at: datetime | None = None


class MetricsCollector:
    """Collect uptime, HTTP request count, and last scan statistics."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._lock = threading.Lock()
        self._http_requests = 0
        self._last_scan = ScanMetrics()

    def record_http(self) -> None:
        with self._lock:
            self._http_requests += 1

    def record_scan(
        self,
        indexed: int,
        skipped: int,
        errors: int,
        duration_sec: float,
    ) -> None:
        with self._lock:
            self._last_scan = ScanMetrics(
                duration_ms=int(duration_sec * 1000),
                indexed=indexed,
                skipped=skipped,
                errors=errors,
                finished_at=datetime.now(timezone.utc),
            )

    def uptime_seconds(self) -> int:
        return int(time.monotonic() - self._started)

    def http_requests(self) -> int:
        with self._lock:
            return self._http_requests

    def last_scan(self) -> ScanMetrics:
        with self._lock:
            return self._last_scan
