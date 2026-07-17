"""Application entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn

from debuginfod.benchmark_store import BenchmarkStore
from debuginfod.config import parse_args
from debuginfod.database_factory import open_database
from debuginfod.db import Database
from debuginfod.delta_store import DeltaStore
from debuginfod.indexer import Indexer
from debuginfod.metrics import MetricsCollector
from debuginfod.quik_indexer import QuikIndexer
from debuginfod.scan_runner import ScanRunner
from debuginfod.webapi import create_app


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> None:
    _args, settings = parse_args(argv)
    _setup_logging(settings.log_level)

    logger = logging.getLogger(__name__)
    logger.info("Starting debuginfod-python on port %d", settings.port)

    db = open_database(settings)
    store = DeltaStore(
        db=db,
        blob_dir=settings.blob_dir,
        reconstruct_cache_dir=settings.reconstruct_cache_dir,
        xdelta3_path=settings.xdelta3_path,
        delta_min_ratio=settings.delta_min_ratio,
    )
    store.verify_xdelta3()

    scan_paths = list(settings.scan_paths)
    if settings.dedup_enabled and settings.work_path not in scan_paths:
        scan_paths.append(settings.work_path)

    quik: QuikIndexer | None = None
    if settings.dedup_enabled and settings.dedup_projects:
        quik = QuikIndexer(
            db=db,
            store=store,
            input_path=settings.input_path,
            work_path=settings.work_path,
            dedup_projects=list(settings.dedup_projects),
            seven_zip_path=settings.seven_zip_path,
            xdelta3_path=settings.xdelta3_path,
            lzma_enabled=settings.delta_lzma,
            remove_original_after_dedup=settings.remove_original_after_dedup,
        )

    metrics = MetricsCollector()
    indexer = Indexer(db=db, store=store, scan_paths=scan_paths, quik_indexer=quik)
    scan_runner = ScanRunner(
        indexer=indexer,
        interval_sec=settings.rescan_interval_sec,
        metrics=metrics,
    )
    if settings.scan_enabled:
        scan_runner.start()

    benchmark_store = BenchmarkStore()
    py_url = f"http://localhost:{settings.port}"

    app = create_app(
        db=db,
        store=store,
        scan_runner=scan_runner,
        metadata_maxtime_sec=settings.metadata_maxtime_sec,
        metadata_page_size=settings.metadata_page_size,
        admin_key=settings.admin_key,
        ui_enabled=settings.ui_enabled,
        metrics=metrics,
        blob_dir=settings.blob_dir,
        reconstruct_cache_dir=settings.reconstruct_cache_dir,
        benchmark_store=benchmark_store,
        benchmark_go_url=settings.benchmark_go_url,
        benchmark_py_url=py_url,
        benchmark_testdata=settings.benchmark_testdata,
        benchmark_go_admin_key=settings.benchmark_go_admin_key,
        benchmark_py_admin_key=settings.benchmark_py_admin_key,
        scan_paths=settings.scan_paths,
    )

    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)
    finally:
        if settings.scan_enabled and scan_runner is not None:
            scan_runner.stop()
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
