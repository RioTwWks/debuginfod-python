"""Tests for singleton dedup base lookup."""

from __future__ import annotations

from pathlib import Path

from debuginfod.db import Database, DedupFileRecord
from debuginfod.dedup.group_base import DedupNotFoundError, find_group_base


def _insert_dedup_file(
    db: Database,
    *,
    project: str,
    file_stem: str,
    storage_kind: str = "base",
    status: str = "done",
) -> DedupFileRecord:
    project_id = db.ensure_dedup_project(project)
    build_dir_id = db.upsert_dedup_build_dir(project_id, f"/tmp/{project}/build_1", 1)
    record = DedupFileRecord(
        id=0,
        build_dir_id=build_dir_id,
        project_name=project,
        file_path=f"/tmp/{project}/build_1/{file_stem}.debug",
        filename=f"{file_stem}.debug",
        file_stem=file_stem,
        version="1",
        file_build_num=1,
    )
    file_id = db.upsert_dedup_file(record)
    if status == "done":
        db.mark_dedup_file_done(file_id, storage_kind, None, "", "sha", 100)
    return db.get_dedup_file_by_id(file_id)  # type: ignore[return-value]


def test_find_group_base_matches_normalized_project(tmp_path: Path) -> None:
    db = Database(tmp_path / "group-base.sqlite")
    base = _insert_dedup_file(db, project="Released/Qt/5.15", file_stem="libfoo")
    target = DedupFileRecord(
        id=99,
        build_dir_id=base.build_dir_id,
        project_name="Released/Qt/5.15.2",
        file_path="/tmp/target/libfoo.debug",
        filename="libfoo.debug",
        file_stem="libfoo",
        version="1",
        file_build_num=2,
    )
    found = find_group_base(db, target)
    assert found.id == base.id


def test_find_group_base_missing_raises(tmp_path: Path) -> None:
    db = Database(tmp_path / "group-base-missing.sqlite")
    target = DedupFileRecord(
        id=1,
        build_dir_id=1,
        project_name="Released/Other",
        file_path="/tmp/other/libbar.debug",
        filename="libbar.debug",
        file_stem="libbar",
        version="1",
        file_build_num=1,
    )
    try:
        find_group_base(db, target)
    except DedupNotFoundError:
        return
    raise AssertionError("expected DedupNotFoundError")
