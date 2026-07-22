"""Dedup transient failure and group retry tests."""

from __future__ import annotations

from debuginfod.dedup.pipeline import expand_dedup_groups_with_bases, is_transient_dedup_error
from debuginfod.db import Database
from debuginfod.db_dedup import DedupFileRecord


def test_is_transient_dedup_error() -> None:
    assert is_transient_dedup_error("memory limit exceeded during subprocess")
    assert is_transient_dedup_error("dedup stopped (memory pressure)")
    assert not is_transient_dedup_error("sha256 mismatch after restore")


def test_reset_transient_dedup_errors(tmp_path) -> None:
    db = Database(str(tmp_path / "dedup.db"))
    project_id = db.ensure_dedup_project("qt")
    build_id = db.upsert_dedup_build_dir(project_id, str(tmp_path / "build"), 1)
    file_id = db.upsert_dedup_file(
        DedupFileRecord(
            id=0,
            build_dir_id=build_id,
            project_name="qt",
            file_path=str(tmp_path / "libfoo.debug"),
            filename="libfoo.debug",
            file_stem="libfoo",
            version="1",
            file_build_num=2,
            original_size=1024,
        )
    )
    db.mark_dedup_file_error(file_id, "memory limit exceeded during subprocess")
    assert db.reset_transient_dedup_errors() == 1
    record = db.get_dedup_file_by_id(file_id)
    assert record is not None
    assert record.status == "pending"
    assert record.error_msg == ""


def test_expand_dedup_groups_with_bases(tmp_path) -> None:
    db = Database(str(tmp_path / "groups.db"))
    project_id = db.ensure_dedup_project("qt")
    build_id = db.upsert_dedup_build_dir(project_id, str(tmp_path / "build"), 1)
    base_path = tmp_path / "libfoo.debug"
    delta_path = tmp_path / "libfoo2.debug"
    base_path.write_bytes(b"base")
    delta_path.write_bytes(b"delta")

    base_id = db.upsert_dedup_file(
        DedupFileRecord(
            id=0,
            build_dir_id=build_id,
            project_name="qt",
            file_path=str(base_path),
            filename="libfoo.debug",
            file_stem="libfoo",
            version="1",
            file_build_num=1,
            original_size=4,
        )
    )
    db.mark_dedup_file_done(base_id, "base", None, "", "sha", 4)

    delta_id = db.upsert_dedup_file(
        DedupFileRecord(
            id=0,
            build_dir_id=build_id,
            project_name="qt",
            file_path=str(delta_path),
            filename="libfoo2.debug",
            file_stem="libfoo",
            version="2",
            file_build_num=2,
            original_size=5,
        )
    )
    pending = db.get_dedup_file_by_id(delta_id)
    assert pending is not None

    groups = expand_dedup_groups_with_bases(
        db,
        {"qt|libfoo": [pending]},
    )
    members = groups["qt|libfoo"]
    assert len(members) == 2
    assert members[0].storage_kind == "base"
    assert members[1].id == delta_id
