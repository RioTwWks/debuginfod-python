"""SQLite storage for debuginfod metadata and blob index."""

from __future__ import annotations

import fnmatch
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator, Literal

from debuginfod.db_dedup import DedupDbMixin, DedupFileRecord, DedupProjectTotals
from debuginfod.db_history import ScanHistoryMixin

StorageKind = Literal["full", "delta"]


@dataclass(frozen=True)
class BlobRecord:
    content_hash: str
    storage_kind: StorageKind
    stored_path: str
    original_size: int
    stored_size: int
    base_hash: str = ""


@dataclass(frozen=True)
class ArtifactRecord:
    build_id: str
    artifact_type: str
    file_path: str = ""
    archive_path: str = ""
    member_path: str = ""
    build_id_kind: str = "gnu"
    raw_build_id: str = ""
    mtime_ns: int = 0
    # legacy blob fields (optional)
    content_hash: str = ""
    storage_kind: str = ""
    family_key: str = ""
    base_build_id: str = ""
    original_size: int = 0
    stored_size: int = 0


@dataclass(frozen=True)
class SourceRecord:
    build_id: str
    source_path: str
    file_path: str = ""
    archive_path: str = ""
    member_path: str = ""
    mtime_ns: int = 0
    content_hash: str = ""
    storage_kind: str = "full"


@dataclass(frozen=True)
class CountStats:
    artifacts_total: int
    artifacts_executable: int
    artifacts_debuginfo: int
    sources_total: int
    scanned_files_total: int


@dataclass(frozen=True)
class MetadataResult:
    buildid: str
    type: str
    file: str
    archive: str = ""
    buildid_kind: str = ""
    raw_buildid: str = ""
    storage_kind: str = ""
    content_hash: str = ""
    compression_ratio: float = 1.0


class Database(ScanHistoryMixin, DedupDbMixin):
    """SQLite or PostgreSQL metadata store."""

    def __init__(self, db_path: Path, database_url: str = "") -> None:
        self.db_path = db_path
        self._dialect = "sqlite"
        url = database_url.strip()
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "PostgreSQL requires psycopg: pip install 'debuginfod-python[postgres]'"
                ) from exc
            self._conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
            self._dialect = "postgresql"
        else:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._migrate()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        if self._dialect == "postgresql":
            sql = sql.replace("?", "%s")
        with self._lock:
            return self._conn.execute(sql, params)

    def _migrate(self) -> None:
        if self._dialect == "postgresql":
            self._migrate_postgres()
            return
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS blobs (
                content_hash TEXT PRIMARY KEY,
                storage_kind TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                original_size INTEGER NOT NULL,
                stored_size INTEGER NOT NULL,
                base_hash TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                build_id TEXT NOT NULL,
                type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                storage_kind TEXT NOT NULL,
                build_id_kind TEXT NOT NULL DEFAULT 'gnu',
                raw_build_id TEXT NOT NULL DEFAULT '',
                family_key TEXT NOT NULL DEFAULT '',
                base_build_id TEXT NOT NULL DEFAULT '',
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (build_id, type),
                FOREIGN KEY (content_hash) REFERENCES blobs(content_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_build_id ON artifacts(build_id);
            CREATE INDEX IF NOT EXISTS idx_artifacts_family ON artifacts(family_key);

            CREATE TABLE IF NOT EXISTS sources (
                build_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                file_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                storage_kind TEXT NOT NULL DEFAULT 'full',
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (build_id, source_path)
            );
            CREATE INDEX IF NOT EXISTS idx_sources_build_id ON sources(build_id);

            CREATE TABLE IF NOT EXISTS families (
                family_key TEXT PRIMARY KEY,
                latest_content_hash TEXT NOT NULL,
                latest_build_id TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scanned_files (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                kind TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS storage_stats (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                name TEXT PRIMARY KEY,
                dedup_enabled INTEGER NOT NULL DEFAULT 1,
                input_subpath TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS build_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                batch_name TEXT NOT NULL,
                directory TEXT NOT NULL,
                build_number INTEGER NOT NULL,
                commit_tag_id TEXT NOT NULL DEFAULT '',
                is_master INTEGER NOT NULL DEFAULT 0,
                indexed_at TEXT NOT NULL DEFAULT '',
                UNIQUE(project_name, batch_name)
            );
            CREATE INDEX IF NOT EXISTS idx_build_batches_project ON build_batches(project_name);

            CREATE TABLE IF NOT EXISTS dedup_manifest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                batch_name TEXT NOT NULL,
                file_mask TEXT NOT NULL,
                master_build_number INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                master_hash TEXT NOT NULL,
                verify_ok INTEGER NOT NULL DEFAULT 1,
                UNIQUE(project_name, batch_name, file_mask)
            );
            """
        )
        self._ensure_artifact_columns()
        self._ensure_source_columns()
        self._migrate_scan_history()
        self._migrate_dedup()
        self._conn.commit()

    def _migrate_postgres(self) -> None:
        from debuginfod.pg_schema import POSTGRES_SCHEMA

        for statement in POSTGRES_SCHEMA.split(";"):
            sql = statement.strip()
            if sql:
                self._conn.execute(sql)
        self._migrate_scan_history()
        self._migrate_dedup()
        self._conn.commit()

    def _ensure_artifact_columns(self) -> None:
        if self._dialect == "postgresql":
            return
        """Add Quik columns to artifacts on existing databases."""
        rows = self._execute("PRAGMA table_info(artifacts)").fetchall()
        existing = {row[1] for row in rows}
        additions = {
            "archive_path": "TEXT NOT NULL DEFAULT ''",
            "member_path": "TEXT NOT NULL DEFAULT ''",
            "content_hash": "TEXT NOT NULL DEFAULT ''",
            "storage_kind": "TEXT NOT NULL DEFAULT ''",
            "family_key": "TEXT NOT NULL DEFAULT ''",
            "base_build_id": "TEXT NOT NULL DEFAULT ''",
        }
        for column, typedef in additions.items():
            if column not in existing:
                self._execute(f"ALTER TABLE artifacts ADD COLUMN {column} {typedef}")

    def _ensure_source_columns(self) -> None:
        if self._dialect == "postgresql":
            return
        rows = self._execute("PRAGMA table_info(sources)").fetchall()
        existing = {row[1] for row in rows}
        additions = {
            "archive_path": "TEXT NOT NULL DEFAULT ''",
            "member_path": "TEXT NOT NULL DEFAULT ''",
        }
        for column, typedef in additions.items():
            if column not in existing:
                self._execute(f"ALTER TABLE sources ADD COLUMN {column} {typedef}")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def needs_scan(self, path: str, mtime_ns: int, size: int) -> bool:
        row = self._execute(
            "SELECT mtime_ns, size FROM scanned_files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return True
        return row["mtime_ns"] != mtime_ns or row["size"] != size

    def mark_scanned(self, path: str, mtime_ns: int, size: int, kind: str) -> None:
        self._execute(
            """
            INSERT INTO scanned_files (path, mtime_ns, size, kind)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                mtime_ns = excluded.mtime_ns,
                size = excluded.size,
                kind = excluded.kind
            """,
            (path, mtime_ns, size, kind),
        )

    def get_blob(self, content_hash: str) -> BlobRecord | None:
        row = self._execute(
            "SELECT * FROM blobs WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if row is None:
            return None
        return BlobRecord(
            content_hash=row["content_hash"],
            storage_kind=row["storage_kind"],
            stored_path=row["stored_path"],
            original_size=row["original_size"],
            stored_size=row["stored_size"],
            base_hash=row["base_hash"] or "",
        )

    def upsert_blob(self, blob: BlobRecord) -> None:
        self._execute(
            """
            INSERT INTO blobs (content_hash, storage_kind, stored_path, original_size, stored_size, base_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                storage_kind = excluded.storage_kind,
                stored_path = excluded.stored_path,
                original_size = excluded.original_size,
                stored_size = excluded.stored_size,
                base_hash = excluded.base_hash
            """,
            (
                blob.content_hash,
                blob.storage_kind,
                blob.stored_path,
                blob.original_size,
                blob.stored_size,
                blob.base_hash,
            ),
        )

    def upsert_artifact(self, record: ArtifactRecord) -> None:
        self._execute(
            """
            INSERT INTO artifacts (
                build_id, type, file_path, archive_path, member_path,
                build_id_kind, raw_build_id, mtime_ns,
                content_hash, storage_kind, family_key, base_build_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(build_id, type) DO UPDATE SET
                file_path = excluded.file_path,
                archive_path = excluded.archive_path,
                member_path = excluded.member_path,
                build_id_kind = excluded.build_id_kind,
                raw_build_id = excluded.raw_build_id,
                mtime_ns = excluded.mtime_ns,
                content_hash = excluded.content_hash,
                storage_kind = excluded.storage_kind,
                family_key = excluded.family_key,
                base_build_id = excluded.base_build_id
            WHERE excluded.mtime_ns >= artifacts.mtime_ns
            """,
            (
                record.build_id,
                record.artifact_type,
                record.file_path,
                record.archive_path,
                record.member_path,
                record.build_id_kind,
                record.raw_build_id,
                record.mtime_ns,
                record.content_hash,
                record.storage_kind,
                record.family_key,
                record.base_build_id,
            ),
        )

    def upsert_source(self, record: SourceRecord) -> None:
        self._execute(
            """
            INSERT INTO sources (
                build_id, source_path, file_path, archive_path, member_path, mtime_ns,
                content_hash, storage_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(build_id, source_path) DO UPDATE SET
                file_path = excluded.file_path,
                archive_path = excluded.archive_path,
                member_path = excluded.member_path,
                mtime_ns = excluded.mtime_ns,
                content_hash = excluded.content_hash,
                storage_kind = excluded.storage_kind
            WHERE excluded.mtime_ns >= sources.mtime_ns
            """,
            (
                record.build_id,
                record.source_path,
                record.file_path,
                record.archive_path,
                record.member_path,
                record.mtime_ns,
                record.content_hash,
                record.storage_kind,
            ),
        )

    def get_family_latest(self, family_key: str) -> tuple[str, str] | None:
        row = self._execute(
            "SELECT latest_content_hash, latest_build_id FROM families WHERE family_key = ?",
            (family_key,),
        ).fetchone()
        if row is None:
            return None
        return row["latest_content_hash"], row["latest_build_id"]

    def set_family_latest(self, family_key: str, content_hash: str, build_id: str) -> None:
        self._execute(
            """
            INSERT INTO families (family_key, latest_content_hash, latest_build_id)
            VALUES (?, ?, ?)
            ON CONFLICT(family_key) DO UPDATE SET
                latest_content_hash = excluded.latest_content_hash,
                latest_build_id = excluded.latest_build_id
            """,
            (family_key, content_hash, build_id),
        )

    def get_artifact(self, build_id: str, artifact_type: str) -> ArtifactRecord | None:
        row = self._execute(
            "SELECT * FROM artifacts WHERE build_id = ? AND type = ?",
            (build_id, artifact_type),
        ).fetchone()
        if row is None:
            return None
        keys = row.keys() if hasattr(row, "keys") else row
        return ArtifactRecord(
            build_id=row["build_id"],
            artifact_type=row["type"],
            file_path=row["file_path"] or "",
            archive_path=row["archive_path"] if "archive_path" in keys else "",
            member_path=row["member_path"] if "member_path" in keys else "",
            build_id_kind=row["build_id_kind"],
            raw_build_id=row["raw_build_id"],
            mtime_ns=row["mtime_ns"],
            content_hash=row["content_hash"] if "content_hash" in keys else "",
            storage_kind=row["storage_kind"] if "storage_kind" in keys else "",
            family_key=row["family_key"] if "family_key" in keys else "",
            base_build_id=row["base_build_id"] if "base_build_id" in keys else "",
        )

    def get_source(self, build_id: str, source_path: str) -> SourceRecord | None:
        row = self._execute(
            "SELECT * FROM sources WHERE build_id = ? AND source_path = ?",
            (build_id, source_path),
        ).fetchone()
        if row is None:
            return None
        return SourceRecord(
            build_id=row["build_id"],
            source_path=row["source_path"],
            file_path=row["file_path"],
            content_hash=row["content_hash"],
            storage_kind=row["storage_kind"],
            mtime_ns=row["mtime_ns"],
        )

    def get_source_by_suffix(self, source_path: str) -> SourceRecord | None:
        row = self._execute(
            """
            SELECT * FROM sources
            WHERE source_path = ? OR source_path LIKE '%' || ?
            ORDER BY length(source_path) ASC
            LIMIT 1
            """,
            (source_path, source_path),
        ).fetchone()
        if row is None:
            return None
        return SourceRecord(
            build_id=row["build_id"],
            source_path=row["source_path"],
            file_path=row["file_path"],
            content_hash=row["content_hash"],
            storage_kind=row["storage_kind"],
            mtime_ns=row["mtime_ns"],
        )

    def increment_stat(self, key: str, delta: int = 1) -> None:
        self._execute(
            """
            INSERT INTO storage_stats (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = value + excluded.value
            """,
            (key, delta),
        )

    def count_stats(self) -> CountStats:
        """Aggregate DB counters for Web UI."""
        artifacts_total = self._execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        artifacts_executable = self._execute(
            "SELECT COUNT(*) FROM artifacts WHERE type = 'executable'"
        ).fetchone()[0]
        artifacts_debuginfo = self._execute(
            "SELECT COUNT(*) FROM artifacts WHERE type = 'debuginfo'"
        ).fetchone()[0]
        sources_total = self._execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        scanned_files_total = self._execute("SELECT COUNT(*) FROM scanned_files").fetchone()[0]
        return CountStats(
            artifacts_total=artifacts_total,
            artifacts_executable=artifacts_executable,
            artifacts_debuginfo=artifacts_debuginfo,
            sources_total=sources_total,
            scanned_files_total=scanned_files_total,
        )

    def search_buildid_for_ui(self, query: str, limit: int = 50) -> list[MetadataResult]:
        """Search artifacts by build-id prefix for Web UI."""
        limit = max(1, min(limit, 200))
        normalized = query.strip().lower()
        if normalized.startswith("0x"):
            normalized = normalized[2:]

        if not normalized:
            rows = self._execute(
                """
                SELECT build_id, type, file_path, build_id_kind, raw_build_id
                FROM artifacts
                ORDER BY build_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            pattern = normalized + "%"
            rows = self._execute(
                """
                SELECT build_id, type, file_path, build_id_kind, raw_build_id
                FROM artifacts
                WHERE build_id LIKE ? OR lower(raw_build_id) LIKE ?
                ORDER BY build_id
                LIMIT ?
                """,
                (pattern, pattern, limit),
            ).fetchall()

        return [
            MetadataResult(
                buildid=row["build_id"],
                type=row["type"],
                file=row["file_path"],
                buildid_kind=row["build_id_kind"] or "",
                raw_buildid=row["raw_build_id"] or "",
            )
            for row in rows
        ]

    def list_artifact_records(self) -> list[ArtifactRecord]:
        rows = self._execute(
            """
            SELECT build_id, type, file_path, archive_path, member_path,
                   build_id_kind, raw_build_id, mtime_ns
            FROM artifacts
            ORDER BY file_path, type
            """
        ).fetchall()
        out: list[ArtifactRecord] = []
        for row in rows:
            out.append(
                ArtifactRecord(
                    build_id=row["build_id"],
                    artifact_type=row["type"],
                    file_path=row["file_path"] or "",
                    archive_path=row["archive_path"] or "",
                    member_path=row["member_path"] or "",
                    build_id_kind=row["build_id_kind"] or "gnu",
                    raw_build_id=row["raw_build_id"] or "",
                    mtime_ns=int(row["mtime_ns"] or 0),
                )
            )
        return out

    def artifact_mtime_map(self) -> dict[tuple[str, str], int]:
        rows = self._execute(
            "SELECT build_id, type, mtime_ns FROM artifacts"
        ).fetchall()
        return {(row["build_id"], row["type"]): int(row["mtime_ns"] or 0) for row in rows}

    def list_sources_for_buildid_ui(
        self,
        build_id: str,
        scan_roots: list[Path],
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        from datetime import datetime, timezone

        from debuginfod.webui.search import relative_to_scan_roots

        total = self._execute(
            "SELECT COUNT(*) FROM sources WHERE build_id = ?",
            (build_id,),
        ).fetchone()[0]
        rows = self._execute(
            """
            SELECT source_path, file_path, archive_path, member_path, mtime_ns
            FROM sources
            WHERE build_id = ?
            ORDER BY source_path
            LIMIT ?
            """,
            (build_id, max(1, min(limit, 200))),
        ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            file_path = (row["file_path"] or "").replace("\\", "/")
            source_path = (row["source_path"] or "").replace("\\", "/")
            archive_path = (row["archive_path"] or "").replace("\\", "/")
            member_path = (row["member_path"] or "").replace("\\", "/")
            mtime_ns = int(row["mtime_ns"] or 0)
            payload: dict[str, Any] = {
                "source_path": source_path,
                "file_path": file_path,
            }
            if archive_path:
                payload["archive_path"] = archive_path
                payload["member_path"] = member_path
                payload["archive_rel"] = relative_to_scan_roots(archive_path, scan_roots)
                payload["relative_path"] = f"{payload['archive_rel']} → {member_path}"
            else:
                payload["relative_path"] = relative_to_scan_roots(file_path, scan_roots)
            if mtime_ns > 0:
                payload["mtime_ns"] = mtime_ns
                payload["mtime"] = datetime.fromtimestamp(
                    mtime_ns / 1_000_000_000,
                    tz=timezone.utc,
                ).isoformat()
            out.append(payload)
        return out, int(total)

    def search_metadata_ui(
        self,
        key: str,
        value: str,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[MetadataResult], bool, int]:
        """Search artifacts for Web UI glob/file modes."""
        limit = max(1, min(limit, 200))
        results, complete, next_offset = self.search_metadata(key, value, offset, limit)
        ui_results = [
            MetadataResult(
                buildid=r.buildid,
                type=r.type,
                file=r.file,
                archive=r.archive,
                buildid_kind=r.buildid_kind,
                raw_buildid=r.raw_buildid,
            )
            for r in results
        ]
        return ui_results, complete, next_offset

    def get_stats(self) -> dict[str, Any]:
        artifact_count = self._execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        source_count = self._execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        scanned_count = self._execute("SELECT COUNT(*) FROM scanned_files").fetchone()[0]

        bytes_on_disk = 0
        rows = self._execute(
            "SELECT DISTINCT file_path FROM artifacts WHERE file_path != ''"
        ).fetchall()
        seen: set[str] = set()
        for row in rows:
            path = row["file_path"] if isinstance(row, dict) else row[0]
            if path in seen:
                continue
            seen.add(path)
            try:
                bytes_on_disk += Path(path).stat().st_size
            except OSError:
                continue

        dedup = self.dedup_stats()
        bytes_before = int(dedup.get("bytes_before", 0))
        bytes_after = int(dedup.get("bytes_after", 0))
        bytes_saved = int(dedup.get("bytes_saved", 0))

        return {
            "artifact_count": artifact_count,
            "source_count": source_count,
            "scanned_files_total": scanned_count,
            "bytes_on_disk": bytes_on_disk,
            "dedup": dedup,
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
            "bytes_saved": bytes_saved,
            "compression_ratio": (bytes_after / bytes_before) if bytes_before else 1.0,
        }

    def search_metadata(
        self,
        key: str,
        value: str,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[MetadataResult], bool, int]:
        """Search artifacts for /metadata endpoint."""
        rows = self._execute(
            """
            SELECT build_id, type, file_path, archive_path, build_id_kind, raw_build_id
            FROM artifacts
            ORDER BY build_id, type
            """
        ).fetchall()

        matches: list[MetadataResult] = []
        for row in rows:
            file_path = row["file_path"] or ""
            archive = row["archive_path"] if "archive_path" in row.keys() else ""
            if key == "file" and file_path != value:
                continue
            if key == "glob" and not fnmatch.fnmatch(file_path, value):
                continue
            if key == "buildid":
                from debuginfod.buildid import match_build_id_query

                if not match_build_id_query(value, row["build_id"], row["raw_build_id"] or ""):
                    continue

            matches.append(
                MetadataResult(
                    buildid=row["build_id"],
                    type=row["type"],
                    file=file_path,
                    archive=archive or "",
                    buildid_kind=row["build_id_kind"] or "",
                    raw_buildid=row["raw_build_id"] or "",
                )
            )

        page = matches[offset : offset + limit] if limit > 0 else matches[offset:]
        next_offset = offset + len(page)
        complete = next_offset >= len(matches)
        return page, complete, next_offset if not complete else 0

    def is_ready(self) -> bool:
        row = self._execute("SELECT COUNT(*) FROM scanned_files").fetchone()
        return bool(row and row[0] > 0)

    def upsert_project(self, name: str, dedup_enabled: bool = True, input_subpath: str = "") -> None:
        self._execute(
            """
            INSERT INTO projects (name, dedup_enabled, input_subpath)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                dedup_enabled = excluded.dedup_enabled,
                input_subpath = excluded.input_subpath
            """,
            (name, 1 if dedup_enabled else 0, input_subpath),
        )

    def list_projects(self) -> list[dict[str, Any]]:
        return [
            {
                "name": p.name,
                "dedup_enabled": True,
                "batch_count": 0,
                "artifact_count": p.file_count,
                "bytes_before": p.bytes_before,
                "bytes_after": p.bytes_after,
                "bytes_saved": max(0, p.bytes_before - p.bytes_after),
            }
            for p in self.list_dedup_projects()
        ]

    def list_batches(self, project_name: str) -> list[dict[str, Any]]:
        rows = self._execute(
            """
            SELECT batch_name, directory, build_number, commit_tag_id, is_master, indexed_at
            FROM build_batches
            WHERE project_name = ?
            ORDER BY build_number
            """,
            (project_name,),
        ).fetchall()
        return [
            {
                "batch_name": row["batch_name"],
                "directory": row["directory"],
                "build_number": row["build_number"],
                "commit_tag_id": row["commit_tag_id"],
                "is_master": bool(row["is_master"]),
                "indexed_at": row["indexed_at"],
            }
            for row in rows
        ]

    def upsert_build_batch(
        self,
        project_name: str,
        batch_name: str,
        directory: str,
        build_number: int,
        commit_tag_id: str,
        is_master: bool,
        indexed_at: str,
    ) -> None:
        self._execute(
            """
            INSERT INTO build_batches (
                project_name, batch_name, directory, build_number,
                commit_tag_id, is_master, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_name, batch_name) DO UPDATE SET
                directory = excluded.directory,
                build_number = excluded.build_number,
                commit_tag_id = excluded.commit_tag_id,
                is_master = excluded.is_master,
                indexed_at = excluded.indexed_at
            """,
            (
                project_name,
                batch_name,
                directory,
                build_number,
                commit_tag_id,
                1 if is_master else 0,
                indexed_at,
            ),
        )

    def upsert_dedup_manifest(
        self,
        project_name: str,
        batch_name: str,
        file_mask: str,
        master_build_number: int,
        content_hash: str,
        master_hash: str,
        verify_ok: bool,
    ) -> None:
        self._execute(
            """
            INSERT INTO dedup_manifest (
                project_name, batch_name, file_mask, master_build_number,
                content_hash, master_hash, verify_ok
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_name, batch_name, file_mask) DO UPDATE SET
                master_build_number = excluded.master_build_number,
                content_hash = excluded.content_hash,
                master_hash = excluded.master_hash,
                verify_ok = excluded.verify_ok
            """,
            (
                project_name,
                batch_name,
                file_mask,
                master_build_number,
                content_hash,
                master_hash,
                1 if verify_ok else 0,
            ),
        )
