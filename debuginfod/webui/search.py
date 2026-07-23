"""Web UI search helpers (debuginfod-go internal/webui parity)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from debuginfod.db import Database, MetadataResult, ArtifactRecord


@dataclass
class UIArtifactRow:
    buildid: str
    type: str
    file: str
    archive: str = ""
    archive_path: str = ""
    member_path: str = ""
    file_path: str = ""
    buildid_kind: str = ""
    raw_buildid: str = ""
    relative_path: str = ""
    filename: str = ""
    directory: str = ""
    archive_rel: str = ""
    mtime_ns: int = 0
    mtime: str = ""
    comment: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    sources_count: int = 0


def relative_to_scan_roots(abs_path: str, scan_roots: list[Path]) -> str:
    """Return path relative to the nearest scan root."""
    path = Path(abs_path).as_posix()
    if not path:
        return ""
    best = ""
    for root in scan_roots:
        try:
            root_abs = root.resolve().as_posix().rstrip("/")
        except OSError:
            continue
        if path == root_abs:
            return ""
        prefix = root_abs + "/"
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix) :]
        if not best or len(rel) < len(best):
            best = rel
    return best or path


def artifact_display_path(record: UIArtifactRow, scan_roots: list[Path]) -> str:
    if record.archive:
        arch_rel = relative_to_scan_roots(record.archive, scan_roots)
        if arch_rel != record.archive:
            return f"{arch_rel} → {record.file}"
        return f"{record.archive} → {record.file}"
    return relative_to_scan_roots(record.file, scan_roots)


def _artifact_filename(record: UIArtifactRow) -> str:
    return Path(record.file).name if record.file else ""


def _artifact_directory(rel_path: str) -> str:
    if "/" in rel_path:
        return rel_path.rsplit("/", 1)[0]
    return ""


def _mtime_iso(mtime_ns: int) -> str:
    if mtime_ns <= 0:
        return ""
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def metadata_to_ui_row(record: MetadataResult) -> UIArtifactRow:
    archive_path = record.archive or ""
    return UIArtifactRow(
        buildid=record.buildid,
        type=record.type,
        file=record.file,
        archive=archive_path,
        archive_path=archive_path,
        file_path=record.file,
        buildid_kind=record.buildid_kind,
        raw_buildid=record.raw_buildid,
    )


def _row_from_metadata(record: MetadataResult, mtime_ns: int = 0) -> UIArtifactRow:
    archive_path = record.archive or ""
    return UIArtifactRow(
        buildid=record.buildid,
        type=record.type,
        file=record.file,
        archive=archive_path,
        archive_path=archive_path,
        file_path=record.file,
        buildid_kind=record.buildid_kind,
        raw_buildid=record.raw_buildid,
        mtime_ns=mtime_ns,
    )


def _row_from_record(record: ArtifactRecord) -> UIArtifactRow:
    display_file = record.member_path or record.file_path
    return UIArtifactRow(
        buildid=record.build_id,
        type=record.artifact_type,
        file=display_file,
        archive=record.archive_path,
        archive_path=record.archive_path,
        member_path=record.member_path,
        file_path=record.file_path,
        buildid_kind=record.build_id_kind,
        raw_buildid=record.raw_build_id,
        mtime_ns=record.mtime_ns,
    )


def _iter_ui_artifacts(db: Database) -> list[UIArtifactRow]:
    return [_row_from_record(record) for record in db.list_artifact_records()]


def enrich_artifact_row(row: UIArtifactRow, scan_roots: list[Path], *, with_comment: bool = False) -> None:
    row.relative_path = artifact_display_path(row, scan_roots)
    row.filename = _artifact_filename(row)
    row.directory = _artifact_directory(row.relative_path)
    if row.archive_path:
        row.archive_rel = relative_to_scan_roots(row.archive_path, scan_roots)
    row.mtime = _mtime_iso(row.mtime_ns)
    if with_comment and not row.archive_path and row.file_path:
        from debuginfod.elfcomment import info_from_path

        row.comment = info_from_path(row.file_path)


def artifact_disk_path(row: UIArtifactRow) -> str:
    if row.archive_path or row.archive:
        return ""
    return row.file_path or row.file


def match_path_query(query: str, relative_path: str) -> bool:
    query = query.strip()
    rel = relative_path.replace("\\", "/")
    if not query:
        return True
    q = query.replace("\\", "/")
    if any(ch in q for ch in "*?["):
        return fnmatch.fnmatch(rel, q) or fnmatch.fnmatchcase(rel, q)
    lower_q = q.lower()
    lower_rel = rel.lower()
    return lower_q in lower_rel or lower_rel.endswith(lower_q)


def match_name_query(query: str, filename: str) -> bool:
    query = query.strip()
    if not query:
        return False
    q = query.replace("\\", "/")
    if any(ch in q for ch in "*?["):
        return fnmatch.fnmatch(filename, q)
    lower_q = q.lower()
    lower_name = filename.lower()
    return lower_q in lower_name or lower_name == lower_q


def _primary_type(types: list[str]) -> str:
    if "debuginfo" in types:
        return "debuginfo"
    return types[0] if types else "executable"


def _artifact_to_dict(row: UIArtifactRow) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "buildid": row.buildid,
        "type": row.type,
        "file": row.file,
    }
    if row.archive:
        payload["archive"] = row.archive
    if row.archive_path:
        payload["archive_path"] = row.archive_path
    if row.member_path:
        payload["member_path"] = row.member_path
    if row.file_path:
        payload["file_path"] = row.file_path
    if row.buildid_kind:
        payload["buildid_kind"] = row.buildid_kind
    if row.raw_buildid:
        payload["raw_buildid"] = row.raw_buildid
    if row.relative_path:
        payload["relative_path"] = row.relative_path
    if row.filename:
        payload["filename"] = row.filename
    if row.directory:
        payload["directory"] = row.directory
    if row.archive_rel:
        payload["archive_rel"] = row.archive_rel
    if row.mtime_ns:
        payload["mtime_ns"] = row.mtime_ns
    if row.mtime:
        payload["mtime"] = row.mtime
    if row.comment:
        payload["comment"] = row.comment
    if row.sources:
        payload["sources"] = row.sources
    if row.sources_count:
        payload["sources_count"] = row.sources_count
    return payload


def _grouped_to_dict(
    buildid: str,
    types: list[str],
    entries: list[UIArtifactRow],
    *,
    buildid_kind: str,
    raw_buildid: str,
    sources: list[dict[str, Any]],
    sources_count: int,
) -> dict[str, Any]:
    by_type: dict[str, str] = {}
    by_type_rel: dict[str, str] = {}
    for entry in entries:
        label = entry.file
        if entry.archive_path or entry.archive:
            arch = entry.archive_path or entry.archive
            label = f"{arch} → {entry.file}"
        by_type[entry.type] = label
        by_type_rel[entry.type] = entry.relative_path

    primary = _primary_type(types)
    relative_path = by_type_rel.get(primary, "")
    file_label = by_type.get(primary, "")
    if not relative_path:
        for artifact_type in types:
            relative_path = by_type_rel.get(artifact_type, "")
            file_label = by_type.get(artifact_type, "")
            if relative_path:
                break

    filename = Path(relative_path).name if relative_path else ""
    directory = _artifact_directory(relative_path)

    return {
        "buildid": buildid,
        "types": types,
        "type": primary,
        "file": file_label,
        "relative_path": relative_path,
        "filename": filename,
        "directory": directory,
        "buildid_kind": buildid_kind,
        "raw_buildid": raw_buildid,
        "by_type": by_type,
        "by_type_rel": by_type_rel,
        "entries": [_artifact_to_dict(entry) for entry in entries],
        "sources": sources,
        "sources_count": sources_count,
    }


def enrich_flat_results(
    db: Database,
    rows: list[UIArtifactRow],
    scan_roots: list[Path],
    *,
    with_comment: bool = False,
    source_limit: int = 0,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        enrich_artifact_row(row, scan_roots, with_comment=with_comment)
        if source_limit > 0:
            sources, count = db.list_sources_for_buildid_ui(
                row.buildid,
                scan_roots,
                limit=source_limit,
            )
            row.sources = sources
            row.sources_count = count
        else:
            row.sources = []
            row.sources_count = db.count_sources_for_buildid(row.buildid)
        out.append(_artifact_to_dict(row))
    return out


def search_buildid_grouped(
    db: Database,
    query: str,
    limit: int,
    scan_roots: list[Path],
    *,
    include_details: bool = False,
) -> list[dict[str, Any]]:
    raw_limit = min(limit * 4, 200)
    records = db.search_buildid_for_ui(query, raw_limit)

    groups: dict[str, list[UIArtifactRow]] = {}
    order: list[str] = []
    meta: dict[str, tuple[str, str]] = {}

    for record in records:
        row = _row_from_metadata(record, record.mtime_ns)
        enrich_artifact_row(row, scan_roots, with_comment=include_details)
        if record.buildid not in groups:
            groups[record.buildid] = []
            order.append(record.buildid)
        groups[record.buildid].append(row)
        kind = record.buildid_kind or meta.get(record.buildid, ("", ""))[0]
        raw = record.raw_buildid or meta.get(record.buildid, ("", ""))[1]
        meta[record.buildid] = (kind, raw)

    out: list[dict[str, Any]] = []
    for build_id in order:
        entries = groups[build_id]
        types = sorted({entry.type for entry in entries})
        kind, raw = meta.get(build_id, ("", ""))
        sources: list[dict[str, Any]] = []
        count = db.count_sources_for_buildid(build_id)
        if include_details and count > 0:
            sources, count = db.list_sources_for_buildid_ui(build_id, scan_roots, limit=20)
        if include_details:
            for entry in entries:
                enrich_artifact_row(entry, scan_roots, with_comment=True)
        out.append(
            _grouped_to_dict(
                build_id,
                types,
                entries,
                buildid_kind=kind,
                raw_buildid=raw,
                sources=sources,
                sources_count=count,
            )
        )
        if len(out) >= limit:
            break
    return out


def artifact_detail_for_ui(
    db: Database,
    build_id: str,
    scan_roots: list[Path],
) -> dict[str, Any] | None:
    """Full artifact detail for one build-id (lazy UI expand)."""
    records = db.list_artifacts_for_buildid(build_id)
    if not records:
        return None
    entries: list[UIArtifactRow] = []
    for record in records:
        row = _row_from_record(record)
        enrich_artifact_row(row, scan_roots, with_comment=True)
        entries.append(row)
    types = sorted({entry.type for entry in entries})
    kind = entries[0].buildid_kind if entries else ""
    raw = entries[0].raw_buildid if entries else ""
    sources, count = db.list_sources_for_buildid_ui(build_id, scan_roots, limit=20)
    return _grouped_to_dict(
        build_id,
        types,
        entries,
        buildid_kind=kind,
        raw_buildid=raw,
        sources=sources,
        sources_count=count,
    )


def search_path_for_ui(
    db: Database,
    scan_roots: list[Path],
    query: str,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], bool, int]:
    query = query.strip()
    has_glob = any(ch in query for ch in "*?[")

    if not has_glob:
        if query:
            page_records, has_more = db.list_artifact_records_page(
                offset,
                limit,
                path_substring=query.replace("\\", "/"),
            )
        else:
            page_records, has_more = db.list_artifact_records_page(offset, limit)
        rows = [_row_from_record(record) for record in page_records]
        enriched = enrich_flat_results(db, rows, scan_roots, with_comment=True, source_limit=20)
        next_offset = offset + len(page_records)
        return enriched, not has_more, next_offset if has_more else 0

    matches: list[UIArtifactRow] = []
    for row in _iter_ui_artifacts(db):
        enrich_artifact_row(row, scan_roots)
        if match_path_query(query, row.relative_path):
            matches.append(row)

    page = matches[offset : offset + limit] if limit > 0 else matches[offset:]
    next_offset = offset + len(page)
    complete = next_offset >= len(matches)
    enriched = enrich_flat_results(db, page, scan_roots, with_comment=True, source_limit=20)
    return enriched, complete, next_offset if not complete else 0


def search_name_for_ui(
    db: Database,
    scan_roots: list[Path],
    query: str,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], bool, int]:
    matches: list[UIArtifactRow] = []
    for row in _iter_ui_artifacts(db):
        enrich_artifact_row(row, scan_roots)
        if match_name_query(query, row.filename or _artifact_filename(row)):
            matches.append(row)

    page = matches[offset : offset + limit] if limit > 0 else matches[offset:]
    next_offset = offset + len(page)
    complete = next_offset >= len(matches)
    enriched = enrich_flat_results(db, page, scan_roots, with_comment=True, source_limit=20)
    return enriched, complete, next_offset if not complete else 0
