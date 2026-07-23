"""Post-scan dedup ingest hook."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debuginfod.dedup_runner import DedupRunner
    from debuginfod.indexer import ScanStats

logger = logging.getLogger(__name__)


class DedupScanHook:
    def __init__(self, runner: "DedupRunner | None") -> None:
        self.runner = runner

    def schedule_ingest_after_scan(self, scan_stats: "ScanStats") -> None:
        if self.runner is None:
            return
        try:
            self.runner.schedule_after_scan(scan_stats)
        except Exception:
            logger.exception("dedup schedule after scan failed")
