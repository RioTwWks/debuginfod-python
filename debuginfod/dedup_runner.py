"""Background dedup worker — scan returns immediately while dedup runs async."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debuginfod.dedup.pipeline import BackfillResult
    from debuginfod.dedup.service import DedupService
    from debuginfod.indexer import ScanStats

logger = logging.getLogger(__name__)


class DedupRunner:
    """Run dedup ingest on a daemon thread so HTTP/UI stay responsive."""

    def __init__(self, service: "DedupService", stop_event: threading.Event) -> None:
        self.service = service
        self._stop = stop_event
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._in_progress = False
        self._last_result: BackfillResult | None = None

    @property
    def in_progress(self) -> bool:
        with self._lock:
            return self._in_progress

    @property
    def last_result(self) -> "BackfillResult | None":
        return self._last_result

    def schedule_after_scan(self, scan_stats: "ScanStats") -> None:
        """Queue dedup after scan; returns immediately."""
        if not self.service.enabled():
            return
        with self._lock:
            if self._in_progress:
                logger.info("Dedup already in progress, skipping new schedule")
                return
            self._in_progress = True
            self._thread = threading.Thread(
                target=self._run,
                args=(scan_stats,),
                daemon=True,
                name="dedup-runner",
            )
            self._thread.start()

    def _run(self, scan_stats: "ScanStats") -> None:
        try:
            logger.info(
                "Background dedup started (scan indexed=%d skipped=%d)",
                scan_stats.files_indexed,
                scan_stats.files_skipped,
            )
            result = self.service.run_ingest_after_scan(
                stop_event=self._stop,
                scan_indexed=scan_stats.files_indexed,
            )
            self._last_result = result
        except Exception:
            logger.exception("Background dedup failed")
        finally:
            with self._lock:
                self._in_progress = False
            logger.info("Background dedup finished")
