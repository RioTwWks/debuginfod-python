"""Central logging setup: console + optional daily log files."""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_level(level: str) -> int:
    return getattr(logging, level.upper(), logging.INFO)


def setup_logging(level: str, log_dir: str | Path | None = None) -> None:
    """Configure root logger for console and optional daily file output.

    When ``log_dir`` is set, writes ``debuginfod.log`` with midnight rotation
    (suffix ``.YYYY-MM-DD``). Level is controlled by ``DEBUGINFOD_LOG_LEVEL``.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(_parse_level(level))

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    if not log_dir:
        return

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "debuginfod.log"

    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logging.getLogger(__name__).info("File logging enabled: %s (daily rotation)", log_path)
