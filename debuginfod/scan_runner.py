"""Background periodic rescan."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from debuginfod.indexer import Indexer, ScanStats
from debuginfod.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class ScanRunner:
    """Run indexer scans on interval and support manual trigger."""

    def __init__(
        self,
        indexer: Indexer,
        interval_sec: int,
        on_complete: Callable[[ScanStats], None] | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.indexer = indexer
        self.interval_sec = interval_sec
        self.on_complete = on_complete
        self.metrics = metrics
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = False
        self._lock = threading.Lock()
        self._last_stats: ScanStats | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def last_stats(self) -> ScanStats | None:
        return self._last_stats

    def run_once(self) -> ScanStats:
        logger.info("Starting scan")
        started = time.perf_counter()
        stats = self.indexer.scan()
        duration = time.perf_counter() - started
        with self._lock:
            self._last_stats = stats
            self._ready = True
        if self.metrics is not None:
            self.metrics.record_scan(
                indexed=stats.files_indexed,
                skipped=stats.files_skipped,
                errors=stats.errors,
                duration_sec=duration,
            )
        logger.info(
            "Scan complete: indexed=%d skipped=%d errors=%d",
            stats.files_indexed,
            stats.files_skipped,
            stats.errors,
        )
        if self.on_complete:
            self.on_complete(stats)
        return stats

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scan-runner")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        self.run_once()
        while not self._stop.wait(self.interval_sec):
            self.run_once()
