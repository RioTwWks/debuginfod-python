"""Dedup database tables and operations (debuginfod-go/internal/storage/dedup.go)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEDUP_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS dedup_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dedup_build_dirs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    dir_path TEXT NOT NULL UNIQUE,
    dir_build_num INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error_msg TEXT NOT NULL DEFAULT '',
    processed_at INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES dedup_projects(id)
);
CREATE INDEX IF NOT EXISTS idx_dedup_build_dirs_status ON dedup_build_dirs(status);

CREATE TABLE IF NOT EXISTS dedup_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    build_dir_id INTEGER NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    file_stem TEXT NOT NULL,
    version TEXT NOT NULL,
    file_build_num INTEGER NOT NULL,
    commit_tag TEXT NOT NULL DEFAULT '',
    storage_kind TEXT NOT NULL DEFAULT 'full',
    base_file_id INTEGER,
    delta_path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    original_size INTEGER NOT NULL DEFAULT 0,
    compressed_size INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error_msg TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (build_dir_id) REFERENCES dedup_build_dirs(id),
    FOREIGN KEY (base_file_id) REFERENCES dedup_files(id)
);
CREATE INDEX IF NOT EXISTS idx_dedup_files_status ON dedup_files(status);
CREATE INDEX IF NOT EXISTS idx_dedup_files_group ON dedup_files(file_stem, version, commit_tag);

CREATE TABLE IF NOT EXISTS dedup_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dedup_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finished_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    project TEXT NOT NULL DEFAULT '',
    dry_run INTEGER NOT NULL DEFAULT 0,
    build_dirs_processed INTEGER NOT NULL DEFAULT 0,
    files_registered INTEGER NOT NULL DEFAULT 0,
    files_compressed INTEGER NOT NULL DEFAULT 0,
    files_dedup_ref INTEGER NOT NULL DEFAULT 0,
    files_skipped INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    bytes_before INTEGER NOT NULL DEFAULT 0,
    bytes_after INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class DedupFileRecord:
    id: int
    build_dir_id: int
    project_name: str
    file_path: str
    filename: str
    file_stem: str
    version: str
    file_build_num: int
    commit_tag: str = ""
    storage_kind: str = "full"
    base_file_id: int | None = None
    delta_path: str = ""
    sha256: str = ""
    original_size: int = 0
    compressed_size: int = 0
    status: str = "pending"
    error_msg: str = ""


@dataclass
class DedupProjectTotals:
    name: str
    file_count: int
    bytes_before: int
    bytes_after: int
    build_dirs: int = 0
    files_base: int = 0
    files_delta: int = 0
    files_full: int = 0
    bytes_saved: int = 0
    saved_percent: float = 0.0


def _empty_dedup_totals() -> dict[str, Any]:
    return {
        "files_done": 0,
        "files_base": 0,
        "files_delta": 0,
        "files_full": 0,
        "files_compressed": 0,
        "files_cas_ref": 0,
        "bytes_original": 0,
        "bytes_on_disk": 0,
        "bytes_saved": 0,
        "saved_percent": 0.0,
    }


class DedupDbMixin:
    """Mixin for Database — dedup CRUD."""

    def _migrate_dedup(self) -> None:
        if self._dialect == "postgresql":
            from debuginfod.pg_schema import DEDUP_POSTGRES_SCHEMA

            for statement in DEDUP_POSTGRES_SCHEMA.split(";"):
                sql = statement.strip()
                if sql:
                    self._conn.execute(sql)
            return
        self._conn.executescript(DEDUP_SCHEMA_SQLITE)

    def ensure_dedup_project(self, name: str) -> int:
        self._execute("INSERT OR IGNORE INTO dedup_projects (name) VALUES (?)", (name,))
        row = self._execute("SELECT id FROM dedup_projects WHERE name = ?", (name,)).fetchone()
        return int(row["id"] if isinstance(row, dict) else row[0])

    def upsert_dedup_build_dir(self, project_id: int, dir_path: str, dir_build_num: int) -> int:
        self._execute(
            """
            INSERT INTO dedup_build_dirs (project_id, dir_path, dir_build_num, status)
            VALUES (?, ?, ?, 'pending')
            ON CONFLICT(dir_path) DO UPDATE SET dir_build_num = excluded.dir_build_num
            """,
            (project_id, dir_path, dir_build_num),
        )
        row = self._execute("SELECT id FROM dedup_build_dirs WHERE dir_path = ?", (dir_path,)).fetchone()
        return int(row["id"] if isinstance(row, dict) else row[0])

    def upsert_dedup_file(self, record: DedupFileRecord) -> int:
        self._execute(
            """
            INSERT INTO dedup_files (
                build_dir_id, file_path, filename, file_stem, version,
                file_build_num, commit_tag, original_size, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(file_path) DO UPDATE SET
                commit_tag = excluded.commit_tag,
                original_size = excluded.original_size
            """,
            (
                record.build_dir_id,
                record.file_path,
                record.filename,
                record.file_stem,
                record.version,
                record.file_build_num,
                record.commit_tag,
                record.original_size,
            ),
        )
        row = self._execute("SELECT id FROM dedup_files WHERE file_path = ?", (record.file_path,)).fetchone()
        return int(row["id"] if isinstance(row, dict) else row[0])

    def list_dedup_files_for_browse(self) -> list[DedupFileRecord]:
        rows = self._execute(
            """
            SELECT f.id, f.build_dir_id, p.name AS project_name, f.file_path, f.filename,
                f.file_stem, f.version, f.file_build_num, f.commit_tag,
                f.storage_kind, f.base_file_id, f.delta_path, f.sha256,
                f.original_size, f.compressed_size, f.status, f.error_msg
            FROM dedup_files f
            JOIN dedup_build_dirs b ON b.id = f.build_dir_id
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE f.status != 'error'
            ORDER BY f.file_path
            """
        ).fetchall()
        return [self._row_to_dedup_file(r) for r in rows]

    def list_all_pending_dedup_files(self) -> list[DedupFileRecord]:
        rows = self._execute(
            """
            SELECT f.id, f.build_dir_id, p.name AS project_name, f.file_path, f.filename,
                f.file_stem, f.version, f.file_build_num, f.commit_tag,
                f.storage_kind, f.base_file_id, f.delta_path, f.sha256,
                f.original_size, f.compressed_size, f.status, f.error_msg
            FROM dedup_files f
            JOIN dedup_build_dirs b ON b.id = f.build_dir_id
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE f.status = 'pending'
            ORDER BY p.name, f.file_build_num
            """
        ).fetchall()
        return [self._row_to_dedup_file(r) for r in rows]

    def list_dedup_bases_by_stem(self, file_stem: str, limit: int = 32) -> list[DedupFileRecord]:
        """Return done base files for file_stem (newest build first)."""
        if limit <= 0:
            limit = 32
        rows = self._execute(
            """
            SELECT f.id, f.build_dir_id, p.name AS project_name, f.file_path, f.filename,
                f.file_stem, f.version, f.file_build_num, f.commit_tag,
                f.storage_kind, f.base_file_id, f.delta_path, f.sha256,
                f.original_size, f.compressed_size, f.status, f.error_msg
            FROM dedup_files f
            JOIN dedup_build_dirs b ON b.id = f.build_dir_id
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE f.file_stem = ? AND f.storage_kind = 'base' AND f.status = 'done'
            ORDER BY f.file_build_num DESC, f.id DESC
            LIMIT ?
            """,
            (file_stem, limit),
        ).fetchall()
        return [self._row_to_dedup_file(r) for r in rows]

    def search_dedup_files_for_ui(
        self,
        query: str,
        *,
        simple_query: bool,
    ) -> list[DedupFileRecord]:
        """List dedup files for browse (optional SQL prefilter for simple queries)."""
        params: list[Any] = []
        extra_where = ""
        if simple_query:
            pattern = f"%{query.strip().lower()}%"
            extra_where = """
            AND (
                LOWER(f.file_path) LIKE ? OR
                LOWER(f.filename) LIKE ? OR
                LOWER(f.commit_tag) LIKE ?
            )
            """
            params = [pattern, pattern, pattern]
        rows = self._execute(
            f"""
            SELECT f.id, f.build_dir_id, p.name AS project_name, f.file_path, f.filename,
                f.file_stem, f.version, f.file_build_num, f.commit_tag,
                f.storage_kind, f.base_file_id, f.delta_path, f.sha256,
                f.original_size, f.compressed_size, f.status, f.error_msg
            FROM dedup_files f
            JOIN dedup_build_dirs b ON b.id = f.build_dir_id
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE f.status != 'error'
            {extra_where}
            ORDER BY f.file_path
            """,
            tuple(params),
        ).fetchall()
        return [self._row_to_dedup_file(r) for r in rows]

    def get_dedup_group_base(self, project_name: str, file_stem: str) -> DedupFileRecord | None:
        """Earliest done base/full file for a dedup group (retry after partial failure)."""
        rows = self._execute(
            """
            SELECT f.id, f.build_dir_id, p.name AS project_name, f.file_path, f.filename,
                f.file_stem, f.version, f.file_build_num, f.commit_tag,
                f.storage_kind, f.base_file_id, f.delta_path, f.sha256,
                f.original_size, f.compressed_size, f.status, f.error_msg
            FROM dedup_files f
            JOIN dedup_build_dirs b ON b.id = f.build_dir_id
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE p.name = ? AND f.file_stem = ? AND f.status = 'done'
              AND f.storage_kind IN ('base', 'full')
            ORDER BY f.file_build_num, f.file_path
            LIMIT 1
            """,
            (project_name, file_stem),
        ).fetchall()
        if not rows:
            return None
        return self._row_to_dedup_file(rows[0])

    def get_dedup_file_by_path(self, file_path: str) -> DedupFileRecord | None:
        rows = self._execute(
            """
            SELECT f.id, f.build_dir_id, p.name AS project_name, f.file_path, f.filename,
                f.file_stem, f.version, f.file_build_num, f.commit_tag,
                f.storage_kind, f.base_file_id, f.delta_path, f.sha256,
                f.original_size, f.compressed_size, f.status, f.error_msg
            FROM dedup_files f
            JOIN dedup_build_dirs b ON b.id = f.build_dir_id
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE f.file_path = ?
            """,
            (file_path,),
        ).fetchall()
        if not rows:
            return None
        return self._row_to_dedup_file(rows[0])

    def get_dedup_file_by_id(self, file_id: int) -> DedupFileRecord | None:
        rows = self._execute(
            """
            SELECT f.id, f.build_dir_id, p.name AS project_name, f.file_path, f.filename,
                f.file_stem, f.version, f.file_build_num, f.commit_tag,
                f.storage_kind, f.base_file_id, f.delta_path, f.sha256,
                f.original_size, f.compressed_size, f.status, f.error_msg
            FROM dedup_files f
            JOIN dedup_build_dirs b ON b.id = f.build_dir_id
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE f.id = ?
            """,
            (file_id,),
        ).fetchall()
        if not rows:
            return None
        return self._row_to_dedup_file(rows[0])

    def mark_dedup_file_done(
        self,
        file_id: int,
        storage_kind: str,
        base_file_id: int | None,
        delta_path: str,
        sha256: str,
        compressed_size: int,
    ) -> None:
        self._execute(
            """
            UPDATE dedup_files SET
                storage_kind = ?, base_file_id = ?, delta_path = ?,
                sha256 = ?, compressed_size = ?, status = 'done', error_msg = ''
            WHERE id = ?
            """,
            (storage_kind, base_file_id, delta_path, sha256, compressed_size, file_id),
        )

    def update_dedup_file_compressed_size(self, file_id: int, compressed_size: int) -> None:
        self._execute(
            "UPDATE dedup_files SET compressed_size = ? WHERE id = ?",
            (compressed_size, file_id),
        )

    def mark_dedup_file_error(self, file_id: int, message: str) -> None:
        self._execute(
            "UPDATE dedup_files SET status = 'error', error_msg = ? WHERE id = ?",
            (message[:500], file_id),
        )

    def count_dedup_files_by_status(self) -> dict[str, int]:
        rows = self._execute(
            "SELECT status, COUNT(*) AS cnt FROM dedup_files GROUP BY status"
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            if isinstance(row, dict):
                counts[str(row["status"])] = int(row["cnt"] or 0)
            else:
                counts[str(row[0])] = int(row[1] or 0)
        return counts

    def reset_transient_dedup_errors(self) -> int:
        """Re-queue files that failed due to transient memory pressure."""
        patterns = (
            "%memory limit exceeded%",
            "%dedup stopped%",
            "%stopped during subprocess%",
        )
        clauses = " OR ".join("error_msg LIKE ?" for _ in patterns)
        row = self._execute(
            f"SELECT COUNT(*) AS cnt FROM dedup_files WHERE status = 'error' AND ({clauses})",
            patterns,
        ).fetchone()
        count = int(row["cnt"] if isinstance(row, dict) else row[0])
        if count <= 0:
            return 0
        self._execute(
            f"""
            UPDATE dedup_files
            SET status = 'pending', error_msg = ''
            WHERE status = 'error' AND ({clauses})
            """,
            patterns,
        )
        return count

    def finish_build_dir_if_done(self, build_dir_id: int) -> None:
        row = self._execute(
            "SELECT COUNT(*) AS cnt FROM dedup_files WHERE build_dir_id = ? AND status = 'pending'",
            (build_dir_id,),
        ).fetchone()
        pending = row["cnt"] if isinstance(row, dict) else row[0]
        if pending == 0:
            self._execute(
                "UPDATE dedup_build_dirs SET status = 'done', processed_at = strftime('%s','now') WHERE id = ?",
                (build_dir_id,),
            )

    def insert_dedup_run(self, payload: dict[str, Any]) -> None:
        self._execute(
            """
            INSERT INTO dedup_runs (
                finished_at, duration_ms, project, dry_run, build_dirs_processed,
                files_registered, files_compressed, files_dedup_ref, files_skipped,
                errors, bytes_before, bytes_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("finished_at", ""),
                payload.get("duration_ms", 0),
                payload.get("project", ""),
                1 if payload.get("dry_run") else 0,
                payload.get("build_dirs_processed", 0),
                payload.get("files_registered", 0),
                payload.get("files_compressed", 0),
                payload.get("files_dedup_ref", 0),
                payload.get("files_skipped", 0),
                payload.get("errors", 0),
                payload.get("bytes_before", 0),
                payload.get("bytes_after", 0),
            ),
        )

    def list_dedup_projects(self) -> list[DedupProjectTotals]:
        rows = self._execute(
            """
            SELECT p.name,
                   COUNT(f.id) AS file_count,
                   COALESCE(SUM(f.original_size), 0) AS bytes_before,
                   COALESCE(SUM(CASE WHEN f.compressed_size > 0 THEN f.compressed_size
                               WHEN f.storage_kind IN ('full','base') THEN f.original_size
                               ELSE 0 END), 0) AS bytes_after
            FROM dedup_projects p
            LEFT JOIN dedup_build_dirs b ON b.project_id = p.id
            LEFT JOIN dedup_files f ON f.build_dir_id = b.id AND f.status = 'done'
            GROUP BY p.name
            ORDER BY p.name
            """
        ).fetchall()
        result: list[DedupProjectTotals] = []
        for row in rows:
            if isinstance(row, dict):
                result.append(
                    DedupProjectTotals(
                        name=row["name"],
                        file_count=int(row["file_count"] or 0),
                        bytes_before=int(row["bytes_before"] or 0),
                        bytes_after=int(row["bytes_after"] or 0),
                    )
                )
            else:
                result.append(
                    DedupProjectTotals(
                        name=row[0],
                        file_count=int(row[1] or 0),
                        bytes_before=int(row[2] or 0),
                        bytes_after=int(row[3] or 0),
                    )
                )
        return result

    def dedup_stats(self) -> dict[str, Any]:
        totals = self.dedup_storage_totals()
        return {
            "total_files": totals["files_done"],
            "delta_files": totals["files_delta"],
            "base_files": totals["files_base"],
            "bytes_before": totals["bytes_original"],
            "bytes_after": totals["bytes_on_disk"],
            "bytes_saved": totals["bytes_saved"],
        }

    def dedup_storage_totals(self) -> dict[str, Any]:
        row = self._execute(
            """
            SELECT
                SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS files_done,
                SUM(CASE WHEN status = 'done' AND storage_kind = 'base' THEN 1 ELSE 0 END) AS files_base,
                SUM(CASE WHEN status = 'done' AND storage_kind = 'delta' THEN 1 ELSE 0 END) AS files_delta,
                SUM(CASE WHEN status = 'done' AND storage_kind = 'full' THEN 1 ELSE 0 END) AS files_full,
                SUM(CASE WHEN status = 'done' THEN original_size ELSE 0 END) AS bytes_original,
                SUM(
                    CASE WHEN status = 'done' THEN
                        CASE
                            WHEN storage_kind = 'delta' THEN
                                CASE WHEN compressed_size > 0 THEN compressed_size ELSE 0 END
                            WHEN storage_kind IN ('base', 'full') THEN
                                CASE
                                    WHEN compressed_size > 0 THEN compressed_size
                                    WHEN original_size > 0 THEN original_size
                                    ELSE 0
                                END
                            ELSE 0
                        END
                    ELSE 0
                END) AS bytes_on_disk
            FROM dedup_files
            """
        ).fetchone()
        if row is None:
            return _empty_dedup_totals()

        if isinstance(row, dict):
            files_done = int(row.get("files_done") or 0)
            files_base = int(row.get("files_base") or 0)
            files_delta = int(row.get("files_delta") or 0)
            files_full = int(row.get("files_full") or 0)
            bytes_original = int(row.get("bytes_original") or 0)
            bytes_on_disk = int(row.get("bytes_on_disk") or 0)
        else:
            files_done = int(row[0] or 0)
            files_base = int(row[1] or 0)
            files_delta = int(row[2] or 0)
            files_full = int(row[3] or 0)
            bytes_original = int(row[4] or 0)
            bytes_on_disk = int(row[5] or 0)

        bytes_saved = max(0, bytes_original - bytes_on_disk)
        saved_percent = (bytes_saved / bytes_original * 100.0) if bytes_original else 0.0
        return {
            "files_done": files_done,
            "files_base": files_base,
            "files_delta": files_delta,
            "files_full": files_full,
            "files_compressed": 0,
            "files_cas_ref": 0,
            "bytes_original": bytes_original,
            "bytes_on_disk": bytes_on_disk,
            "bytes_saved": bytes_saved,
            "saved_percent": saved_percent,
        }

    def dedup_totals_by_project(self) -> list[dict[str, Any]]:
        rows = self._execute("SELECT name FROM dedup_projects ORDER BY name").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            name = row["name"] if isinstance(row, dict) else row[0]
            result.append(self._dedup_totals_for_project(str(name)))
        return result

    def _dedup_totals_for_project(self, project_name: str) -> dict[str, Any]:
        build_dirs_row = self._execute(
            """
            SELECT COUNT(*) AS cnt FROM dedup_build_dirs b
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE p.name = ?
            """,
            (project_name,),
        ).fetchone()
        if build_dirs_row is None:
            build_dirs = 0
        elif isinstance(build_dirs_row, dict):
            build_dirs = int(build_dirs_row.get("cnt", 0))
        else:
            build_dirs = int(build_dirs_row[0])

        row = self._execute(
            """
            SELECT
                SUM(CASE WHEN f.status = 'done' THEN 1 ELSE 0 END) AS files_done,
                SUM(CASE WHEN f.status = 'done' AND f.storage_kind = 'base' THEN 1 ELSE 0 END) AS files_base,
                SUM(CASE WHEN f.status = 'done' AND f.storage_kind = 'delta' THEN 1 ELSE 0 END) AS files_delta,
                SUM(CASE WHEN f.status = 'done' AND f.storage_kind = 'full' THEN 1 ELSE 0 END) AS files_full,
                SUM(CASE WHEN f.status = 'done' THEN f.original_size ELSE 0 END) AS bytes_original,
                SUM(
                    CASE WHEN f.status = 'done' THEN
                        CASE
                            WHEN f.storage_kind = 'delta' THEN
                                CASE WHEN f.compressed_size > 0 THEN f.compressed_size ELSE 0 END
                            WHEN f.storage_kind IN ('base', 'full') THEN
                                CASE
                                    WHEN f.compressed_size > 0 THEN f.compressed_size
                                    WHEN f.original_size > 0 THEN f.original_size
                                    ELSE 0
                                END
                            ELSE 0
                        END
                    ELSE 0
                END) AS bytes_on_disk
            FROM dedup_files f
            JOIN dedup_build_dirs b ON b.id = f.build_dir_id
            JOIN dedup_projects p ON p.id = b.project_id
            WHERE p.name = ?
            """,
            (project_name,),
        ).fetchone()

        if row is None:
            totals = _empty_dedup_totals()
        elif isinstance(row, dict):
            totals = {
                "files_done": int(row.get("files_done") or 0),
                "files_base": int(row.get("files_base") or 0),
                "files_delta": int(row.get("files_delta") or 0),
                "files_full": int(row.get("files_full") or 0),
                "bytes_original": int(row.get("bytes_original") or 0),
                "bytes_on_disk": int(row.get("bytes_on_disk") or 0),
            }
        else:
            totals = {
                "files_done": int(row[0] or 0),
                "files_base": int(row[1] or 0),
                "files_delta": int(row[2] or 0),
                "files_full": int(row[3] or 0),
                "bytes_original": int(row[4] or 0),
                "bytes_on_disk": int(row[5] or 0),
            }

        bytes_original = totals["bytes_original"]
        bytes_on_disk = totals["bytes_on_disk"]
        bytes_saved = max(0, bytes_original - bytes_on_disk)
        saved_percent = (bytes_saved / bytes_original * 100.0) if bytes_original else 0.0
        return {
            "project": project_name,
            "build_dirs": build_dirs,
            "files_done": totals["files_done"],
            "files_base": totals["files_base"],
            "files_delta": totals["files_delta"],
            "files_full": totals["files_full"],
            "bytes_original": bytes_original,
            "bytes_on_disk": bytes_on_disk,
            "bytes_saved": bytes_saved,
            "saved_percent": saved_percent,
        }

    def list_dedup_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        rows = self._execute(
            """
            SELECT id, finished_at, duration_ms, project, dry_run,
                build_dirs_processed, files_registered, files_compressed,
                files_dedup_ref, files_skipped, errors, bytes_before, bytes_after
            FROM dedup_runs
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                payload = dict(row)
            else:
                payload = {
                    "id": row[0],
                    "finished_at": row[1],
                    "duration_ms": row[2],
                    "project": row[3],
                    "dry_run": row[4],
                    "build_dirs_processed": row[5],
                    "files_registered": row[6],
                    "files_compressed": row[7],
                    "files_dedup_ref": row[8],
                    "files_skipped": row[9],
                    "errors": row[10],
                    "bytes_before": row[11],
                    "bytes_after": row[12],
                }
            before = int(payload.get("bytes_before") or 0)
            after = int(payload.get("bytes_after") or 0)
            saved = max(0, before - after)
            payload["bytes_saved"] = saved
            payload["saved_percent"] = (saved / before * 100.0) if before else 0.0
            payload["dry_run"] = bool(payload.get("dry_run"))
            result.append(payload)
        return result

    @staticmethod
    def _row_to_dedup_file(row: Any) -> DedupFileRecord:
        if isinstance(row, dict):
            keys = row
        else:
            keys = {
                "id": row[0],
                "build_dir_id": row[1],
                "project_name": row[2],
                "file_path": row[3],
                "filename": row[4],
                "file_stem": row[5],
                "version": row[6],
                "file_build_num": row[7],
                "commit_tag": row[8],
                "storage_kind": row[9],
                "base_file_id": row[10],
                "delta_path": row[11],
                "sha256": row[12],
                "original_size": row[13],
                "compressed_size": row[14],
                "status": row[15],
                "error_msg": row[16],
            }
        base_id = keys.get("base_file_id")
        return DedupFileRecord(
            id=int(keys["id"]),
            build_dir_id=int(keys["build_dir_id"]),
            project_name=str(keys["project_name"]),
            file_path=str(keys["file_path"]),
            filename=str(keys["filename"]),
            file_stem=str(keys["file_stem"]),
            version=str(keys["version"] or ""),
            file_build_num=int(keys["file_build_num"] or 0),
            commit_tag=str(keys["commit_tag"] or ""),
            storage_kind=str(keys["storage_kind"] or "full"),
            base_file_id=int(base_id) if base_id else None,
            delta_path=str(keys["delta_path"] or ""),
            sha256=str(keys["sha256"] or ""),
            original_size=int(keys["original_size"] or 0),
            compressed_size=int(keys["compressed_size"] or 0),
            status=str(keys["status"] or "pending"),
            error_msg=str(keys["error_msg"] or ""),
        )


def _dedup_file_bytes_on_disk(
    kind: str,
    file_path: str,
    delta_path: str,
    orig_size: int,
    comp_size: int,
) -> int:
    if kind == "delta":
        if comp_size > 0:
            return comp_size
        if delta_path:
            try:
                return Path(delta_path).stat().st_size
            except OSError:
                return 0
        return 0
    if kind in {"base", "full"}:
        if comp_size > 0:
            return comp_size
        if orig_size > 0:
            return orig_size
        if file_path:
            try:
                return Path(file_path).stat().st_size
            except OSError:
                pass
        return 0
    return 0
