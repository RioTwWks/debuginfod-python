"""Application entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn

from debuginfod.benchmark_store import BenchmarkStore
from debuginfod.config import parse_args
from debuginfod.database_factory import open_database
from debuginfod.dedup.scan_hook import DedupScanHook
from debuginfod.dedup.service import DedupConfig, DedupService
from debuginfod.indexer import Indexer
from debuginfod.metrics import MetricsCollector
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

    settings.cache_dir.mkdir(parents=True, exist_ok=True)

    db = open_database(settings)
    dedup_cfg = DedupConfig(
        enabled=settings.dedup_enabled,
        projects=list(settings.dedup_projects),
        workers=settings.dedup_workers,
        strategy=settings.dedup_strategy,
        compress_base=settings.dedup_compress_base,
        xdelta_path=settings.xdelta3_path,
        dwz_path=settings.dwz_path,
        objcopy_path=settings.objcopy_path,
    )
    dedup_service: DedupService | None = None
    dedup_hook: DedupScanHook | None = None
    if dedup_cfg.enabled:
        dedup_service = DedupService(db, dedup_cfg, settings.scan_paths)
        dedup_hook = DedupScanHook(dedup_service)
        logger.info(
            "Dedup enabled: projects=%s workers=%d strategy=%s",
            dedup_cfg.projects or "*",
            dedup_cfg.workers,
            dedup_cfg.strategy,
        )

    metrics = MetricsCollector()
    indexer = Indexer(
        db=db,
        scan_paths=settings.scan_paths,
        workers=settings.scan_workers,
        dedup_hook=dedup_hook,
    )
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
        scan_runner=scan_runner,
        cache_dir=settings.cache_dir,
        dedup_restorer=dedup_service,
        scan_enabled=settings.scan_enabled,
        dedup_enabled=dedup_cfg.enabled,
        metadata_maxtime_sec=settings.metadata_maxtime_sec,
        metadata_page_size=settings.metadata_page_size,
        admin_key=settings.admin_key,
        ui_enabled=settings.ui_enabled,
        metrics=metrics,
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
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        if settings.scan_enabled and scan_runner is not None:
            scan_runner.stop(timeout=2.0)
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
