"""Metrics collector tests."""

from __future__ import annotations

from debuginfod.metrics import MetricsCollector


def test_scan_progress_lifecycle() -> None:
    metrics = MetricsCollector()
    metrics.begin_scan("indexing")
    metrics.update_indexing_progress(3, 7, 1)
    metrics.set_scan_current_path("/tmp/example.so")

    progress = metrics.scan_progress()
    assert progress.running is True
    assert progress.phase == "indexing"
    assert progress.indexed == 3
    assert progress.skipped == 7
    assert progress.errors == 1
    assert progress.current_path == "/tmp/example.so"

    metrics.set_scan_phase("dedup")
    metrics.set_dedup_groups_total(10)
    metrics.update_dedup_progress(4, 2, 1, 0, 1000, 400)

    progress = metrics.scan_progress()
    assert progress.phase == "dedup"
    assert progress.dedup_groups_total == 10
    assert progress.dedup_groups_processed == 4
    assert progress.dedup_files_compressed == 2

    metrics.end_scan()
    assert metrics.scan_progress().running is False
