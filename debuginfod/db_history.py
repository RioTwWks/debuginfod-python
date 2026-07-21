"""Scan run history (debuginfod-go/internal/storage/history.go)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCAN_HISTORY_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finished_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    indexed INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    artifacts_total INTEGER NOT NULL DEFAULT 0,
    scanned_files INTEGER NOT NULL DEFAULT 0,
    bytes_on_disk INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scan_runs_finished ON scan_runs(finished_at DESC);
"""

SCAN_HISTORY_POSTGRES = """
CREATE TABLE IF NOT EXISTS scan_runs (
    id SERIAL PRIMARY KEY,
    finished_at TEXT NOT NULL,
    duration_ms BIGINT NOT NULL,
    indexed INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    artifacts_total BIGINT NOT NULL DEFAULT 0,
    scanned_files BIGINT NOT NULL DEFAULT 0,
    bytes_on_disk BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scan_runs_finished ON scan_runs(finished_at DESC);
"""


@dataclass
class ScanRunRecord:
    id: int
    finished_at: str
    duration_ms: int
    indexed: int
    skipped: int
    errors: int
    artifacts_total: int = 0
    scanned_files: int = 0
    bytes_on_disk: int = 0


class ScanHistoryMixin:
    def _migrate_scan_history(self) -> None:
        if self._dialect == "postgresql":
            for statement in SCAN_HISTORY_POSTGRES.split(";"):
                sql = statement.strip()
                if sql:
                    self._execute(sql)
            return
        self._conn.executescript(SCAN_HISTORY_SCHEMA_SQLITE)

    def insert_scan_run(self, payload: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO scan_runs (
                finished_at, duration_ms, indexed, skipped, errors,
                artifacts_total, scanned_files, bytes_on_disk
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("finished_at", ""),
                payload.get("duration_ms", 0),
                payload.get("indexed", 0),
                payload.get("skipped", 0),
                payload.get("errors", 0),
                payload.get("artifacts_total", 0),
                payload.get("scanned_files", 0),
                payload.get("bytes_on_disk", 0),
            ),
        )

    def list_scan_runs(self, limit: int = 50) -> list[ScanRunRecord]:
        limit = max(1, min(limit, 200))
        rows = self._execute(
            """
            SELECT id, finished_at, duration_ms, indexed, skipped, errors,
                artifacts_total, scanned_files, bytes_on_disk
            FROM scan_runs
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result: list[ScanRunRecord] = []
        for row in rows:
            if isinstance(row, dict):
                result.append(
                    ScanRunRecord(
                        id=int(row["id"]),
                        finished_at=str(row["finished_at"]),
                        duration_ms=int(row["duration_ms"]),
                        indexed=int(row["indexed"]),
                        skipped=int(row["skipped"]),
                        errors=int(row["errors"]),
                        artifacts_total=int(row["artifacts_total"] or 0),
                        scanned_files=int(row["scanned_files"] or 0),
                        bytes_on_disk=int(row["bytes_on_disk"] or 0),
                    )
                )
            else:
                result.append(
                    ScanRunRecord(
                        id=int(row[0]),
                        finished_at=str(row[1]),
                        duration_ms=int(row[2]),
                        indexed=int(row[3]),
                        skipped=int(row[4]),
                        errors=int(row[5]),
                        artifacts_total=int(row[6] or 0),
                        scanned_files=int(row[7] or 0),
                        bytes_on_disk=int(row[8] or 0),
                    )
                )
        return result

    def index_summary(self) -> dict[str, int]:
        counts = self.count_stats()
        storage = self.get_stats()
        return {
            "artifacts_total": counts.artifacts_total,
            "artifacts_executable": counts.artifacts_executable,
            "artifacts_debuginfo": counts.artifacts_debuginfo,
            "scanned_files_total": counts.scanned_files_total,
            "bytes_on_disk": int(storage.get("bytes_on_disk", 0)),
        }
