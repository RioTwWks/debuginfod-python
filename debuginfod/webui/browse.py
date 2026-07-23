"""Browse tree for .debug files (debuginfod-go internal/storage/ui_tree.go parity)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from debuginfod.db import Database, ArtifactRecord, DedupFileRecord
from debuginfod.webui.search import (
    UIArtifactRow,
    _row_from_record,
    artifact_display_path,
    enrich_artifact_row,
    relative_to_scan_roots,
)


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


def is_debug_ui_file(row: UIArtifactRow) -> bool:
    if row.type == "debuginfo":
        return True
    name = (row.filename or Path(row.file).name).lower()
    return name.endswith(".debug")


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


def artifact_record_to_ui_tree_file(
    row: UIArtifactRow,
    scan_roots: list[Path],
    *,
    enrich_comment: bool = False,
) -> UITreeFile:
    enrich_artifact_row(row, scan_roots, with_comment=enrich_comment)
    rel = row.relative_path or artifact_display_path(row, scan_roots)
    git_commit = ""
    if row.comment:
        git_commit = str(row.comment.get("git_commit") or "")
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

    artifacts: list[UITreeFile] = []
    indexed_paths: set[str] = set()

    for record in db.list_artifact_records():
        row = _row_from_record(record)
        if not is_debug_ui_file(row):
            continue
        tree_file = artifact_record_to_ui_tree_file(row, scan_roots, enrich_comment=enrich_comment)
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

    dedup_files = _search_dedup_files_for_ui(db, scan_roots, query, indexed_paths)
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
) -> list[UITreeFile]:
    out: list[UITreeFile] = []
    for record in db.list_dedup_files_for_browse():
        if record.status == "error":
            continue
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


@dataclass
class _TreeNode:
    children: dict[str, "_TreeNode"] = field(default_factory=dict)
    files: list[UITreeFile] = field(default_factory=list)


def build_ui_tree_from_files(files: list[UITreeFile]) -> list[UITreeNode]:
    projects: dict[str, dict[str, list[UITreeFile]]] = {}

    for file in files:
        project = file.project or ui_project_from_relative_path(file.relative_path)
        rest = file.relative_path
        if rest.startswith(project):
            rest = rest[len(project) :].lstrip("/")
        dir_path = str(Path(rest).parent)
        if dir_path == ".":
            dir_path = ""
        projects.setdefault(project, {}).setdefault(dir_path, []).append(file)

    out: list[UITreeNode] = []
    for pname in sorted(projects):
        root = UITreeNode(name=pname, path=pname)
        root.children, root.files = _build_dir_children(pname, projects[pname])
        out.append(root)
    return out


def _build_dir_children(
    project: str,
    dirs: dict[str, list[UITreeFile]],
) -> tuple[list[UITreeNode], list[UITreeFile]]:
    if not dirs:
        return [], []

    root = _TreeNode()
    for dir_path, files in dirs.items():
        parts = dir_path.split("/") if dir_path else []
        cur = root
        for part in parts:
            cur = cur.children.setdefault(part, _TreeNode())
        cur.files.extend(files)

    return _tree_node_to_ui(project, root), _sort_ui_tree_files(root.files)


def _tree_node_to_ui(base: str, node: _TreeNode) -> list[UITreeNode]:
    out: list[UITreeNode] = []
    for name in sorted(node.children):
        child = node.children[name]
        path = f"{base}/{name}"
        out.append(
            UITreeNode(
                name=name,
                path=path,
                files=_sort_ui_tree_files(child.files),
                children=_tree_node_to_ui(path, child),
            )
        )
    return out


def _sort_ui_tree_files(files: list[UITreeFile]) -> list[UITreeFile]:
    if not files:
        return []
    return sorted(files, key=lambda f: f.filename)


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
