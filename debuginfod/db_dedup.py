"""Dedup database tables and operations (debuginfod-go/internal/storage/dedup.go)."""

from __future__ import annotations

from dataclasses import dataclass
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
        row = self._execute(
            """
            SELECT
                COUNT(*) AS total_files,
                SUM(CASE WHEN storage_kind = 'delta' THEN 1 ELSE 0 END) AS delta_files,
                SUM(CASE WHEN storage_kind IN ('base','full') THEN 1 ELSE 0 END) AS base_files,
                COALESCE(SUM(original_size), 0) AS bytes_before,
                COALESCE(SUM(CASE WHEN compressed_size > 0 THEN compressed_size
                            WHEN storage_kind IN ('full','base') THEN original_size ELSE 0 END), 0) AS bytes_after
            FROM dedup_files WHERE status = 'done'
            """
        ).fetchone()
        if row is None:
            return {"total_files": 0, "delta_files": 0, "bytes_saved": 0}
        if isinstance(row, dict):
            before = int(row["bytes_before"] or 0)
            after = int(row["bytes_after"] or 0)
            return {
                "total_files": int(row["total_files"] or 0),
                "delta_files": int(row["delta_files"] or 0),
                "base_files": int(row["base_files"] or 0),
                "bytes_before": before,
                "bytes_after": after,
                "bytes_saved": max(0, before - after),
            }
        before = int(row[4] or 0)
        after = int(row[5] if len(row) > 5 else 0)
        return {
            "total_files": int(row[0] or 0),
            "delta_files": int(row[1] or 0),
            "bytes_before": before,
            "bytes_after": after,
            "bytes_saved": max(0, before - after),
        }

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
