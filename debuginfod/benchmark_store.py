"""In-memory store for benchmark reports."""

from __future__ import annotations

import threading
from typing import Any


class BenchmarkStore:
    """Keep last benchmark report and short history for Web UI."""

    def __init__(self, history_limit: int = 20) -> None:
        self._history_limit = history_limit
        self._lock = threading.Lock()
        self._last: dict[str, Any] | None = None
        self._history: list[dict[str, Any]] = []

    def save(self, report: dict[str, Any]) -> None:
        with self._lock:
            self._last = report
            self._history.insert(0, report)
            self._history = self._history[: self._history_limit]

    def last(self) -> dict[str, Any] | None:
        with self._lock:
            return self._last

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)
