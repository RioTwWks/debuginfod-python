"""Tests for dedup fast-path skip and background runner."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from debuginfod.dedup.pipeline import BackfillResult, PipelineOptions, run_ingest_all
from debuginfod.dedup.service import DedupConfig, DedupService
from debuginfod.dedup_runner import DedupRunner
from debuginfod.db import Database
from debuginfod.db_dedup import DedupFileRecord
from debuginfod.indexer import ScanStats


def test_run_ingest_all_skips_when_no_work(tmp_path) -> None:
    db = Database(str(tmp_path / "skip.db"))
    project_id = db.ensure_dedup_project("qt")
    build_id = db.upsert_dedup_build_dir(project_id, str(tmp_path / "build"), 1)
    file_path = tmp_path / "libfoo.debug"
    file_path.write_bytes(b"x")
    file_id = db.upsert_dedup_file(
        DedupFileRecord(
            id=0,
            build_dir_id=build_id,
            project_name="qt",
            file_path=str(file_path),
            filename="libfoo.debug",
            file_stem="libfoo",
            version="1",
            file_build_num=1,
            original_size=1,
        )
    )
    db.mark_dedup_file_done(file_id, "full", None, "", "sha", 1)

    opts = PipelineOptions(
        db=db,
        scan_paths=[str(tmp_path)],
        xdelta=MagicMock(),
        preprocessor=MagicMock(),
        objcopy_zstd=MagicMock(),
    )
    result = run_ingest_all(opts, scan_indexed=0)
    assert result.files_registered == 0
    assert result.groups_processed == 0
    assert int(result.dedup_status.get("done", 0)) == 1


def test_dedup_runner_schedules_background(tmp_path) -> None:
    db = Database(str(tmp_path / "runner.db"))
    cfg = DedupConfig(enabled=True, projects=[])
    service = DedupService(db, cfg, [tmp_path])
    stop = threading.Event()
    runner = DedupRunner(service, stop)

    service.run_ingest_after_scan = MagicMock(  # type: ignore[method-assign]
        return_value=BackfillResult(dedup_status={"done": 0})
    )

    runner.schedule_after_scan(ScanStats(files_indexed=0, files_skipped=10))
    assert runner.in_progress
    runner._thread.join(timeout=5)
    assert not runner.in_progress
    service.run_ingest_after_scan.assert_called_once()
    assert service.run_ingest_after_scan.call_args.kwargs["scan_indexed"] == 0
