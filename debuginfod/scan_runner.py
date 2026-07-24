"""Background periodic rescan."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from debuginfod.indexer import Indexer, ScanStats
from debuginfod.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class ScanRunner:
    """Run indexer scans on interval and support manual trigger."""

    def __init__(
        self,
        indexer: Indexer | None,
        interval_sec: int,
        on_complete: Callable[[ScanStats], None] | None = None,
        metrics: MetricsCollector | None = None,
        stop_event: threading.Event | None = None,
        dedup_runner: object | None = None,
    ) -> None:
        self.interval_sec = interval_sec
        self.on_complete = on_complete
        self.metrics = metrics
        self.dedup_runner = dedup_runner
        self._stop = stop_event or threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = False
        self._scanning = False
        self._lock = threading.Lock()
        self._last_stats: ScanStats | None = None
        self._indexer: Indexer | None = None
        if indexer is not None:
            self.indexer = indexer

    @property
    def indexer(self) -> Indexer:
        if self._indexer is None:
            raise RuntimeError("scan runner indexer not configured")
        return self._indexer

    @indexer.setter
    def indexer(self, value: Indexer) -> None:
        self._indexer = value
        value.bind_stop_event(self._stop)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def scanning(self) -> bool:
        return self._scanning

    @property
    def stop_event(self) -> threading.Event:
        return self._stop

    @property
    def dedup_in_progress(self) -> bool:
        runner = self.dedup_runner
        if runner is None:
            return False
        return bool(getattr(runner, "in_progress", False))

    @property
    def last_stats(self) -> ScanStats | None:
        return self._last_stats

    def request_stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._indexer is not None:
            self._indexer.request_stop()
        logger.info("Scan stop requested")

    def trigger(self) -> None:
        """Request out-of-band scan (non-blocking, Go ScanTrigger parity)."""
        if self._stop.is_set():
            return
        with self._lock:
            if self._scanning:
                logger.info("Scan already in progress, skipping trigger")
                return
        threading.Thread(target=self.run_once, daemon=True, name="scan-trigger").start()

    def run_once(self) -> ScanStats:
        if self._stop.is_set():
            return ScanStats(cancelled=True)

        with self._lock:
            if self._scanning:
                logger.info("Scan already in progress, skipping")
                return self._last_stats or ScanStats()
            self._scanning = True

        logger.info("Starting scan")
        started = time.perf_counter()
        if self.metrics is not None:
            self.metrics.begin_scan("indexing")
        try:
            stats = self.indexer.scan()
        except Exception:
            if self.metrics is not None:
                self.metrics.end_scan()
            raise
        finally:
            with self._lock:
                self._scanning = False

        duration = time.perf_counter() - started
        finished_at = datetime.now(timezone.utc)

        with self._lock:
            self._last_stats = stats
            if not stats.cancelled:
                self._ready = True

        if stats.cancelled:
            if self.metrics is not None:
                self.metrics.end_scan()
        elif (
            self.dedup_runner is not None
            and getattr(getattr(self.dedup_runner, "service", None), "enabled", lambda: False)()
        ):
            if self.metrics is not None:
                self.metrics.set_scan_phase("dedup")
            self.dedup_runner.schedule_after_scan(stats, metrics=self.metrics)
        elif self.metrics is not None:
            self.metrics.end_scan()

        if self.metrics is not None and not stats.cancelled:
            self.metrics.record_scan(
                indexed=stats.files_indexed,
                skipped=stats.files_skipped,
                errors=stats.errors,
                duration_sec=duration,
                finished_at=finished_at,
            )

        if not stats.cancelled:
            try:
                counts = self.indexer.db.count_stats()
                storage = self.indexer.db.get_stats()
                self.indexer.db.insert_scan_run(
                    {
                        "finished_at": finished_at.replace(microsecond=0).isoformat(),
                        "duration_ms": int(duration * 1000),
                        "indexed": stats.files_indexed,
                        "skipped": stats.files_skipped,
                        "errors": stats.errors,
                        "artifacts_total": counts.artifacts_total,
                        "scanned_files": counts.scanned_files_total,
                        "bytes_on_disk": int(storage.get("bytes_on_disk", 0)),
                    }
                )
            except Exception:
                logger.exception("Failed to record scan run history")

        if stats.cancelled:
            logger.info(
                "Scan cancelled: indexed=%d skipped=%d errors=%d",
                stats.files_indexed,
                stats.files_skipped,
                stats.errors,
            )
        else:
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

    def stop(self, timeout: float = 1.0, force_exit: bool = True) -> None:
        """Request scan stop and wait briefly for the background thread."""
        self.request_stop()
        if self._thread is None:
            return
        try:
            self._thread.join(timeout=timeout)
        except KeyboardInterrupt:
            logger.warning("Interrupted while waiting for scan thread")
            if force_exit:
                os._exit(130)
            raise
        if self._thread.is_alive():
            logger.warning("Scan thread still running after %.1fs", timeout)
            if force_exit:
                os._exit(130)

    def _loop(self) -> None:
        if not self._stop.is_set():
            self.run_once()
        while not self._stop.wait(self.interval_sec):
            if self._stop.is_set():
                break
            self.run_once()
