"""Browse tree for .debug files (debuginfod-go internal/storage/ui_tree.go parity)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from debuginfod.db import Database, DedupFileRecord
from debuginfod.elfcomment import from_path_or_empty
from debuginfod.webui.search import (
    UIArtifactRow,
    _row_from_record,
    artifact_display_path,
    enrich_artifact_row,
    relative_to_scan_roots,
)

UI_NO_COMMIT_LABEL = "(no commit)"


def file_git_commit(file: UITreeFile) -> str:
    if commit := file.git_commit.strip():
        return commit
    if file.comment:
        if commit := str(file.comment.get("git_commit") or "").strip():
            return commit
    return ""


@dataclass
class UITreeFile:
    filename: str
    relative_path: str
    project: str = ""
    buildid: str = ""
    dedup_id: int = 0
    source: str = ""
    type: str = "debuginfo"
    git_commit: str = ""
    comment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "filename": self.filename,
            "relative_path": self.relative_path,
            "type": self.type,
        }
        if self.project:
            payload["project"] = self.project
        if self.buildid:
            payload["buildid"] = self.buildid
        if self.dedup_id:
            payload["dedup_id"] = self.dedup_id
        if self.source:
            payload["source"] = self.source
        if self.git_commit:
            payload["git_commit"] = self.git_commit
        if self.comment:
            payload["comment"] = self.comment
        return payload


@dataclass
class UITreeNode:
    name: str
    path: str
    files: list[UITreeFile] = field(default_factory=list)
    children: list["UITreeNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "path": self.path}
        if self.files:
            payload["files"] = [f.to_dict() for f in self.files]
        if self.children:
            payload["children"] = [c.to_dict() for c in self.children]
        return payload


def ui_project_from_relative_path(rel: str) -> str:
    rel = rel.replace("\\", "/").strip("/")
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] in {"Released", "Unsorted"}:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if parts else rel


def ui_commit_key(file: UITreeFile) -> str:
    if commit := file_git_commit(file):
        return commit
    return UI_NO_COMMIT_LABEL


def ui_commit_label(commit: str) -> str:
    if commit == UI_NO_COMMIT_LABEL:
        return commit
    if len(commit) > 16:
        return commit[:12] + "…"
    return commit


def is_debug_ui_file(row: UIArtifactRow) -> bool:
    if row.type == "debuginfo":
        return True
    name = (row.filename or Path(row.file).name).lower()
    return name.endswith(".debug")


def is_simple_search_query(query: str) -> bool:
    query = query.strip()
    if not query:
        return False
    return not any(ch in query for ch in "*?[")


def matches_unified_query(query: str, *, relative_path: str, filename: str, git_commit: str, buildid: str, raw_buildid: str) -> bool:
    q = query.strip()
    if not q:
        return True
    if any(ch in q for ch in "*?["):
        return (
            fnmatch.fnmatch(relative_path, q)
            or fnmatch.fnmatchcase(relative_path, q)
            or fnmatch.fnmatch(filename, q)
            or fnmatch.fnmatch(git_commit, q)
            or fnmatch.fnmatch(buildid, q)
            or fnmatch.fnmatch(raw_buildid, q)
        )
    lower = q.lower()
    return (
        lower in relative_path.lower()
        or lower in filename.lower()
        or lower in git_commit.lower()
        or lower in buildid.lower()
        or lower in raw_buildid.lower()
    )


def _normalize_browse_path(path: str) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path))


def _artifact_abs_path(row: UIArtifactRow) -> str:
    return row.file_path or row.file or ""


def _resolve_git_commit(row: UIArtifactRow, *, elf_fallback: bool = False) -> str:
    if row.git_commit:
        return row.git_commit
    if row.comment:
        commit = str(row.comment.get("git_commit") or "").strip()
        if commit:
            return commit
    if not elf_fallback or row.archive_path or not row.file_path:
        return ""
    return from_path_or_empty(row.file_path)


def artifact_record_to_ui_tree_file(
    row: UIArtifactRow,
    scan_roots: list[Path],
    *,
    enrich_comment: bool = False,
    resolve_commit: bool = False,
) -> UITreeFile:
    enrich_artifact_row(row, scan_roots, with_comment=enrich_comment)
    rel = row.relative_path or artifact_display_path(row, scan_roots)
    git_commit = (
        _resolve_git_commit(row, elf_fallback=enrich_comment)
        if resolve_commit
        else (row.git_commit or "")
    )
    if not git_commit and enrich_comment and row.comment:
        git_commit = str(row.comment.get("git_commit") or "").strip()
    return UITreeFile(
        filename=row.filename or Path(row.file).name,
        relative_path=rel,
        project=ui_project_from_relative_path(rel),
        buildid=row.buildid,
        source="artifact",
        type=row.type or "debuginfo",
        git_commit=git_commit,
        comment=row.comment,
    )


def dedup_file_to_ui_tree_file(record: DedupFileRecord, scan_roots: list[Path]) -> UITreeFile:
    rel = relative_to_scan_roots(record.file_path, scan_roots)
    return UITreeFile(
        filename=record.filename,
        relative_path=rel,
        project=record.project_name,
        dedup_id=record.id,
        source="dedup",
        type="debuginfo",
        git_commit=record.commit_tag,
    )


def browse_files_for_ui(
    db: Database,
    scan_roots: list[Path],
    query: str,
    limit: int,
) -> tuple[list[UITreeFile], bool]:
    if limit > 50000:
        limit = 50000

    query = query.strip()
    enrich_comment = bool(query)
    simple_query = is_simple_search_query(query)

    artifacts: list[UITreeFile] = []
    indexed_paths: set[str] = set()

    for record in db.search_debug_artifacts_for_ui(query):
        row = _row_from_record(record)
        if not is_debug_ui_file(row):
            continue
        tree_file = artifact_record_to_ui_tree_file(
            row,
            scan_roots,
            enrich_comment=enrich_comment,
            resolve_commit=enrich_comment,
        )
        if query and not matches_unified_query(
            query,
            relative_path=tree_file.relative_path,
            filename=tree_file.filename,
            git_commit=tree_file.git_commit,
            buildid=tree_file.buildid,
            raw_buildid=row.raw_buildid,
        ):
            continue
        abs_path = _normalize_browse_path(_artifact_abs_path(row))
        if abs_path:
            indexed_paths.add(abs_path)
        artifacts.append(tree_file)

    dedup_files = _search_dedup_files_for_ui(db, scan_roots, query, indexed_paths, simple_query)
    files = artifacts + dedup_files
    files.sort(key=lambda f: (f.relative_path, f.filename))

    complete = True
    if limit > 0 and len(files) > limit:
        files = files[:limit]
        complete = False
    return files, complete


def _search_dedup_files_for_ui(
    db: Database,
    scan_roots: list[Path],
    query: str,
    skip_paths: set[str],
    simple_query: bool,
) -> list[UITreeFile]:
    out: list[UITreeFile] = []
    for record in db.search_dedup_files_for_ui(query, simple_query=simple_query):
        norm = _normalize_browse_path(record.file_path)
        if norm in skip_paths:
            continue
        rel = relative_to_scan_roots(record.file_path, scan_roots)
        tree_file = dedup_file_to_ui_tree_file(record, scan_roots)
        if query and not matches_unified_query(
            query,
            relative_path=rel,
            filename=record.filename,
            git_commit=record.commit_tag,
            buildid="",
            raw_buildid="",
        ):
            continue
        out.append(tree_file)
    return out


def build_ui_tree_from_files(files: list[UITreeFile]) -> list[UITreeNode]:
    commits: dict[str, list[UITreeFile]] = {}
    for file in files:
        key = ui_commit_key(file)
        commits.setdefault(key, []).append(file)

    names = sorted(commits)
    if UI_NO_COMMIT_LABEL in names:
        names.remove(UI_NO_COMMIT_LABEL)
        names.append(UI_NO_COMMIT_LABEL)

    out: list[UITreeNode] = []
    for commit in names:
        out.append(
            UITreeNode(
                name=ui_commit_label(commit),
                path=commit,
                files=_sort_ui_tree_files_by_path(commits[commit]),
            )
        )
    return out


def _sort_ui_tree_files_by_path(files: list[UITreeFile]) -> list[UITreeFile]:
    if not files:
        return []
    return sorted(files, key=lambda f: (f.relative_path, f.filename))


def browse_for_ui(
    db: Database,
    scan_roots: list[Path],
    query: str,
    limit: int,
) -> dict[str, Any]:
    files, complete = browse_files_for_ui(db, scan_roots, query, limit)
    projects = build_ui_tree_from_files(files)
    return {
        "query": query,
        "projects": [p.to_dict() for p in projects],
        "count": len(files),
        "limit": limit,
        "complete": complete,
    }
