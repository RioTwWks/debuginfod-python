"""Post-scan dedup ingest hook."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debuginfod.dedup.service import DedupService

logger = logging.getLogger(__name__)


class DedupScanHook:
    def __init__(self, service: DedupService | None) -> None:
        self.service = service

    def run_ingest_after_scan(self, stop_event: object | None = None) -> None:
        if self.service is None or not self.service.enabled():
            return
        try:
            self.service.run_ingest_after_scan(stop_event=stop_event)
        except Exception:
            logger.exception("dedup ingest after scan failed")
