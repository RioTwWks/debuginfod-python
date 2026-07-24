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


@dataclass
class ScanProgress:
    running: bool = False
    phase: str = "idle"
    started_at: datetime | None = None
    indexed: int = 0
    skipped: int = 0
    errors: int = 0
    current_path: str = ""
    dedup_groups_total: int = 0
    dedup_groups_processed: int = 0
    dedup_files_compressed: int = 0
    dedup_files_skipped: int = 0
    dedup_errors: int = 0
    dedup_bytes_before: int = 0
    dedup_bytes_after: int = 0


class MetricsCollector:
    """Collect uptime, HTTP request count, scan progress, and last scan statistics."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._lock = threading.Lock()
        self._http_requests = 0
        self._last_scan = ScanMetrics()
        self._ready = False
        self._progress = ScanProgress()
        self._last_path_update = 0.0

    def record_http(self) -> None:
        with self._lock:
            self._http_requests += 1

    def record_scan(
        self,
        indexed: int,
        skipped: int,
        errors: int,
        duration_sec: float,
        finished_at: datetime | None = None,
    ) -> None:
        with self._lock:
            self._last_scan = ScanMetrics(
                duration_ms=int(duration_sec * 1000),
                indexed=indexed,
                skipped=skipped,
                errors=errors,
                finished_at=finished_at or datetime.now(timezone.utc),
            )
            self._ready = True

    def mark_ready(self) -> None:
        with self._lock:
            self._ready = True

    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def begin_scan(self, phase: str = "indexing") -> None:
        with self._lock:
            self._progress = ScanProgress(
                running=True,
                phase=phase,
                started_at=datetime.now(timezone.utc),
            )
            self._last_path_update = 0.0

    def set_scan_phase(self, phase: str) -> None:
        with self._lock:
            if self._progress.running:
                self._progress.phase = phase

    def end_scan(self) -> None:
        with self._lock:
            self._progress.running = False
            self._progress.phase = "idle"
            self._progress.current_path = ""

    def update_indexing_progress(self, indexed: int, skipped: int, errors: int) -> None:
        with self._lock:
            if not self._progress.running:
                return
            self._progress.indexed = indexed
            self._progress.skipped = skipped
            self._progress.errors = errors

    def set_scan_current_path(self, path: str) -> None:
        now = time.monotonic()
        with self._lock:
            if not self._progress.running:
                return
            if self._last_path_update and now - self._last_path_update < 0.5:
                return
            self._last_path_update = now
            self._progress.current_path = path

    def set_dedup_groups_total(self, total: int) -> None:
        with self._lock:
            self._progress.dedup_groups_total = total

    def update_dedup_progress(
        self,
        groups_done: int,
        compressed: int,
        skipped: int,
        errors: int,
        bytes_before: int,
        bytes_after: int,
    ) -> None:
        with self._lock:
            if not self._progress.running:
                return
            self._progress.dedup_groups_processed = groups_done
            self._progress.dedup_files_compressed = compressed
            self._progress.dedup_files_skipped = skipped
            self._progress.dedup_errors = errors
            self._progress.dedup_bytes_before = bytes_before
            self._progress.dedup_bytes_after = bytes_after

    def scan_progress(self) -> ScanProgress:
        with self._lock:
            return ScanProgress(
                running=self._progress.running,
                phase=self._progress.phase,
                started_at=self._progress.started_at,
                indexed=self._progress.indexed,
                skipped=self._progress.skipped,
                errors=self._progress.errors,
                current_path=self._progress.current_path,
                dedup_groups_total=self._progress.dedup_groups_total,
                dedup_groups_processed=self._progress.dedup_groups_processed,
                dedup_files_compressed=self._progress.dedup_files_compressed,
                dedup_files_skipped=self._progress.dedup_files_skipped,
                dedup_errors=self._progress.dedup_errors,
                dedup_bytes_before=self._progress.dedup_bytes_before,
                dedup_bytes_after=self._progress.dedup_bytes_after,
            )

    def uptime_seconds(self) -> int:
        return int(time.monotonic() - self._started)

    def http_requests(self) -> int:
        with self._lock:
            return self._http_requests

    def last_scan(self) -> ScanMetrics:
        with self._lock:
            return self._last_scan
