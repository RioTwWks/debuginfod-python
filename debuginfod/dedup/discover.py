"""Discover build_* directories and register .debug files."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from debuginfod.db import Database, DedupFileRecord
from debuginfod.debugfilename.parse import metadata_from_name, parse_build_dir
from debuginfod import buildid as buildid_mod

logger = logging.getLogger(__name__)


def _project_filter_set(projects: list[str]) -> set[str] | None:
    if not projects:
        return None
    allowed = {p.strip().replace("\\", "/") for p in projects if p.strip()}
    return allowed or None


def _matches_project(project_name: str, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    return project_name.replace("\\", "/") in allowed


def _project_name_for_build_dir(scan_root: Path, build_dir: Path) -> str:
    parent = build_dir.parent
    try:
        rel = parent.relative_to(scan_root)
        if str(rel) == ".":
            return scan_root.name
        return rel.as_posix()
    except ValueError:
        return parent.name


def discover(db: Database, scan_roots: list[str | Path], project_filter: list[str] | None = None) -> int:
    """Recursively find build_* dirs and register .debug files."""
    allowed = _project_filter_set(project_filter or [])
    registered = 0
    for root in scan_roots:
        root_abs = Path(root).resolve()
        if not root_abs.is_dir():
            logger.warning("dedup discover: missing root %s", root_abs)
            continue
        registered += _discover_under_root(db, root_abs, allowed)
    return registered


def _discover_under_root(db: Database, root_abs: Path, allowed: set[str] | None) -> int:
    registered = 0
    for dirpath, dirnames, _filenames in os.walk(root_abs):
        current = Path(dirpath)
        for name in list(dirnames):
            if not name.startswith("build_"):
                continue
            build_path = current / name
            project_name = _project_name_for_build_dir(root_abs, build_path)
            if not _matches_project(project_name, allowed):
                dirnames.remove(name)
                continue
            try:
                dir_num = parse_build_dir(name)
            except Exception as exc:
                logger.debug("dedup skip dir %s: %s", build_path, exc)
                dirnames.remove(name)
                continue

            project_id = db.ensure_dedup_project(project_name)
            build_dir_id = db.upsert_dedup_build_dir(project_id, str(build_path.resolve()), dir_num)
            registered += _register_debug_files(db, build_dir_id, build_path, project_name)
            dirnames.remove(name)
    return registered


def _register_debug_files(
    db: Database,
    build_dir_id: int,
    dir_path: Path,
    project_name: str,
) -> int:
    count = 0
    for path in dir_path.rglob("*"):
        if not path.is_file():
            if path.is_dir() and path != dir_path and path.name.startswith("build_"):
                continue
            continue
        name = path.name
        lower = name.lower()
        if not lower.endswith(".debug"):
            continue
        if lower.endswith(".xdelta") or lower.endswith(".zst"):
            continue
        try:
            meta = metadata_from_name(name)
        except Exception as exc:
            logger.debug("dedup skip file %s: %s", path, exc)
            continue

        commit_tag = ""
        try:
            from debuginfod.elfcomment import from_path as comment_from_path

            commit_tag = comment_from_path(path)
        except Exception:
            logger.debug("dedup no commit tag for %s", path)

        size = path.stat().st_size
        db.upsert_dedup_file(
            DedupFileRecord(
                id=0,
                build_dir_id=build_dir_id,
                project_name=project_name,
                file_path=str(path.resolve()),
                filename=meta.filename,
                file_stem=meta.stem,
                version=meta.version,
                file_build_num=meta.build_num,
                commit_tag=commit_tag,
                original_size=size,
            )
        )
        count += 1
    return count
