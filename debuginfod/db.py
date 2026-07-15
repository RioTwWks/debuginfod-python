"""SQLite storage for debuginfod metadata and blob index."""

from __future__ import annotations

import fnmatch
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator, Literal

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
    file_path: str
    content_hash: str
    storage_kind: StorageKind
    build_id_kind: str = "gnu"
    raw_build_id: str = ""
    family_key: str = ""
    mtime_ns: int = 0
    original_size: int = 0
    stored_size: int = 0
    base_build_id: str = ""


@dataclass(frozen=True)
class SourceRecord:
    build_id: str
    source_path: str
    file_path: str
    content_hash: str
    storage_kind: StorageKind
    mtime_ns: int = 0


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


class Database:
    """SQLite-backed metadata store."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
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
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def needs_scan(self, path: str, mtime_ns: int, size: int) -> bool:
        row = self._conn.execute(
            "SELECT mtime_ns, size FROM scanned_files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return True
        return row["mtime_ns"] != mtime_ns or row["size"] != size

    def mark_scanned(self, path: str, mtime_ns: int, size: int, kind: str) -> None:
        self._conn.execute(
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
        row = self._conn.execute(
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
        self._conn.execute(
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
        self._conn.execute(
            """
            INSERT INTO artifacts (
                build_id, type, file_path, content_hash, storage_kind,
                build_id_kind, raw_build_id, family_key, base_build_id, mtime_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(build_id, type) DO UPDATE SET
                file_path = excluded.file_path,
                content_hash = excluded.content_hash,
                storage_kind = excluded.storage_kind,
                build_id_kind = excluded.build_id_kind,
                raw_build_id = excluded.raw_build_id,
                family_key = excluded.family_key,
                base_build_id = excluded.base_build_id,
                mtime_ns = excluded.mtime_ns
            WHERE excluded.mtime_ns >= artifacts.mtime_ns
            """,
            (
                record.build_id,
                record.artifact_type,
                record.file_path,
                record.content_hash,
                record.storage_kind,
                record.build_id_kind,
                record.raw_build_id,
                record.family_key,
                record.base_build_id,
                record.mtime_ns,
            ),
        )

    def upsert_source(self, record: SourceRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO sources (build_id, source_path, file_path, content_hash, storage_kind, mtime_ns)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(build_id, source_path) DO UPDATE SET
                file_path = excluded.file_path,
                content_hash = excluded.content_hash,
                storage_kind = excluded.storage_kind,
                mtime_ns = excluded.mtime_ns
            WHERE excluded.mtime_ns >= sources.mtime_ns
            """,
            (
                record.build_id,
                record.source_path,
                record.file_path,
                record.content_hash,
                record.storage_kind,
                record.mtime_ns,
            ),
        )

    def get_family_latest(self, family_key: str) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT latest_content_hash, latest_build_id FROM families WHERE family_key = ?",
            (family_key,),
        ).fetchone()
        if row is None:
            return None
        return row["latest_content_hash"], row["latest_build_id"]

    def set_family_latest(self, family_key: str, content_hash: str, build_id: str) -> None:
        self._conn.execute(
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
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE build_id = ? AND type = ?",
            (build_id, artifact_type),
        ).fetchone()
        if row is None:
            return None
        blob = self.get_blob(row["content_hash"])
        original_size = blob.original_size if blob else 0
        stored_size = blob.stored_size if blob else 0
        return ArtifactRecord(
            build_id=row["build_id"],
            artifact_type=row["type"],
            file_path=row["file_path"],
            content_hash=row["content_hash"],
            storage_kind=row["storage_kind"],
            build_id_kind=row["build_id_kind"],
            raw_build_id=row["raw_build_id"],
            family_key=row["family_key"],
            mtime_ns=row["mtime_ns"],
            original_size=original_size,
            stored_size=stored_size,
            base_build_id=row["base_build_id"] or "",
        )

    def get_source(self, build_id: str, source_path: str) -> SourceRecord | None:
        row = self._conn.execute(
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
        row = self._conn.execute(
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
        self._conn.execute(
            """
            INSERT INTO storage_stats (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = value + excluded.value
            """,
            (key, delta),
        )

    def get_stats(self) -> dict[str, Any]:
        rows = self._conn.execute("SELECT key, value FROM storage_stats").fetchall()
        stats = {row["key"]: row["value"] for row in rows}

        blob_rows = self._conn.execute(
            """
            SELECT storage_kind, COUNT(*) AS cnt,
                   SUM(original_size) AS orig,
                   SUM(stored_size) AS stored
            FROM blobs GROUP BY storage_kind
            """
        ).fetchall()
        by_kind: dict[str, dict[str, int]] = {}
        for row in blob_rows:
            by_kind[row["storage_kind"]] = {
                "count": row["cnt"],
                "original_bytes": row["orig"] or 0,
                "stored_bytes": row["stored"] or 0,
            }

        artifact_count = self._conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        source_count = self._conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

        total_original = sum(v["original_bytes"] for v in by_kind.values())
        total_stored = sum(v["stored_bytes"] for v in by_kind.values())
        savings = total_original - total_stored

        return {
            "counters": stats,
            "blobs": by_kind,
            "artifact_count": artifact_count,
            "source_count": source_count,
            "total_original_bytes": total_original,
            "total_stored_bytes": total_stored,
            "bytes_saved": savings,
            "compression_ratio": (total_stored / total_original) if total_original else 1.0,
        }

    def search_metadata(
        self,
        key: str,
        value: str,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[MetadataResult], bool, int]:
        """Search artifacts for /metadata endpoint."""
        rows = self._conn.execute(
            """
            SELECT a.*, b.original_size, b.stored_size
            FROM artifacts a
            JOIN blobs b ON a.content_hash = b.content_hash
            ORDER BY a.build_id, a.type
            """
        ).fetchall()

        matches: list[MetadataResult] = []
        for row in rows:
            file_path = row["file_path"]
            archive = ""
            if key == "file" and file_path != value:
                continue
            if key == "glob" and not fnmatch.fnmatch(file_path, value):
                continue
            if key == "buildid":
                from debuginfod.buildid import match_build_id_query

                if not match_build_id_query(value, row["build_id"], row["raw_build_id"] or ""):
                    continue

            orig = row["original_size"] or 1
            stored = row["stored_size"] or orig
            matches.append(
                MetadataResult(
                    buildid=row["build_id"],
                    type=row["type"],
                    file=file_path,
                    archive=archive,
                    buildid_kind=row["build_id_kind"] or "",
                    raw_buildid=row["raw_build_id"] or "",
                    storage_kind=row["storage_kind"],
                    content_hash=row["content_hash"],
                    compression_ratio=stored / orig,
                )
            )

        page = matches[offset : offset + limit] if limit > 0 else matches[offset:]
        next_offset = offset + len(page)
        complete = next_offset >= len(matches)
        return page, complete, next_offset if not complete else 0

    def is_ready(self) -> bool:
        row = self._conn.execute("SELECT COUNT(*) FROM scanned_files").fetchone()
        return bool(row and row[0] > 0)
