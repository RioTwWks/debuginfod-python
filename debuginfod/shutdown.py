"""Shutdown signal helpers."""

from __future__ import annotations

import logging
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debuginfod.scan_runner import ScanRunner

logger = logging.getLogger(__name__)


def install_scan_shutdown_handlers(runner: ScanRunner | None) -> None:
    """Stop background scan as early as possible on SIGINT/SIGTERM."""
    if runner is None:
        return

    previous: dict[int, signal.Handlers] = {
        signal.SIGINT: signal.getsignal(signal.SIGINT),
        signal.SIGTERM: signal.getsignal(signal.SIGTERM),
    }

    def _handler(signum: int, frame) -> None:  # type: ignore[no-untyped-def]
        logger.info("Signal %s received, stopping background scan", signum)
        runner.request_stop()
        previous_handler = previous.get(signum)
        if callable(previous_handler):
            previous_handler(signum, frame)
        elif previous_handler == signal.SIG_DFL:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
