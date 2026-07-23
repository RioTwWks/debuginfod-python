"""Tests for logging configuration."""

from __future__ import annotations

import logging

from debuginfod.logging_config import setup_logging


def test_setup_logging_with_file(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    setup_logging("debug", log_dir)
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    assert any(
        h.__class__.__name__ == "TimedRotatingFileHandler" for h in root.handlers
    )
    assert (log_dir / "debuginfod.log").is_file()

    logging.getLogger("test.logging.emit").info("hello")
    assert (log_dir / "debuginfod.log").stat().st_size > 0
