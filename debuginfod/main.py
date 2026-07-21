"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn

from debuginfod.benchmark_store import BenchmarkStore
from debuginfod.config import parse_args
from debuginfod.database_factory import open_database
from debuginfod.dedup.scan_hook import DedupScanHook
from debuginfod.dedup.service import DedupConfig, DedupService
from debuginfod.indexer import Indexer
from debuginfod.memlimit import MemoryGovernor, clamp_memory_limits
from debuginfod.metrics import MetricsCollector
from debuginfod.scan_runner import ScanRunner
from debuginfod.shutdown import install_scan_shutdown_handlers
from debuginfod.webapi import create_app


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_memory_governor(settings: object) -> tuple[MemoryGovernor, list[str]]:
    limits, notes = clamp_memory_limits(
        getattr(settings, "memory_max_ram_mb", 0),
        getattr(settings, "memory_max_swap_mb", 0),
        getattr(settings, "memory_min_available_mb", 0),
        getattr(settings, "memory_dedup_peak_factor", 3.0),
        getattr(settings, "memory_dedup_peak_factor_decompress", 10.0),
        getattr(settings, "memory_max_system_ram_pct", 75),
    )
    return MemoryGovernor(limits), notes


def main(argv: list[str] | None = None) -> None:
    _args, settings = parse_args(argv)
    _setup_logging(settings.log_level)

    logger = logging.getLogger(__name__)
    logger.info("Starting debuginfod-python on port %d", settings.port)

    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DEBUGINFOD_SCAN_DWARF_MAX_MB"] = str(settings.scan_dwarf_max_mb)

    memory_governor, limit_notes = _build_memory_governor(settings)
    for note in limit_notes:
        logger.info("Memory limit adjust: %s", note)
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
        dedup_peak_factor=settings.memory_dedup_peak_factor,
        dedup_serial_above_mb=settings.memory_dedup_serial_above_mb,
        dedup_max_file_mb=settings.memory_dedup_max_file_mb,
    )
    dedup_service: DedupService | None = None
    dedup_hook: DedupScanHook | None = None
    if dedup_cfg.enabled:
        dedup_service = DedupService(
            db,
            dedup_cfg,
            settings.scan_paths,
            memory_governor=memory_governor,
        )
        dedup_hook = DedupScanHook(dedup_service)
        logger.info(
            "Dedup enabled: projects=%s workers=%d strategy=%s",
            dedup_cfg.projects or "*",
            dedup_cfg.workers,
            dedup_cfg.strategy,
        )

    if memory_governor.limits.enabled:
        eff_rss = memory_governor.limits.max_rss_bytes // (1024 * 1024)
        logger.info(
            "Memory limits: effective_max_rss=%d MiB max_swap=%d MiB (tree+delta) "
            "min_available=%d MiB dedup_peak=%.1fx decompress_peak=%.1fx "
            "serial_above=%d MiB dedup_max_file=%d MiB dwarf_max=%d MiB; swap baseline=%.1f MiB",
            eff_rss,
            settings.memory_max_swap_mb,
            settings.memory_min_available_mb,
            settings.memory_dedup_peak_factor,
            settings.memory_dedup_peak_factor_decompress,
            settings.memory_dedup_serial_above_mb,
            settings.memory_dedup_max_file_mb,
            settings.scan_dwarf_max_mb,
            memory_governor.baseline_system_swap_bytes / (1024 * 1024),
        )
    else:
        logger.info(
            "Memory limits disabled (set DEBUGINFOD_MEMORY_MAX_RAM_MB / "
            "DEBUGINFOD_MEMORY_MAX_SWAP_MB to throttle); dwarf_max=%d MiB",
            settings.scan_dwarf_max_mb,
        )

    scan_mode = "thread pool (memory limits)" if memory_governor.limits.enabled else "process pool"
    logger.info(
        "Scan workers=%d (%s); dedup workers=%d",
        settings.scan_workers,
        scan_mode,
        settings.dedup_workers if dedup_cfg.enabled else 0,
    )

    metrics = MetricsCollector()
    indexer = Indexer(
        db=db,
        scan_paths=settings.scan_paths,
        workers=settings.scan_workers,
        dedup_hook=dedup_hook,
        memory_governor=memory_governor,
    )
    scan_runner = ScanRunner(
        indexer=indexer,
        interval_sec=settings.rescan_interval_sec,
        metrics=metrics,
    )
    if settings.scan_enabled:
        install_scan_shutdown_handlers(scan_runner)
        scan_runner.start()

    benchmark_store = BenchmarkStore()
    py_url = f"http://localhost:{settings.port}"

    @asynccontextmanager
    async def lifespan(_app):  # type: ignore[no-untyped-def]
        if settings.scan_enabled:
            install_scan_shutdown_handlers(scan_runner)
        yield
        if settings.scan_enabled:
            logger.info("Application shutdown: stopping scan")
            scan_runner.stop(timeout=0.5, force_exit=False)

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
        lifespan=lifespan,
    )

    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        if settings.scan_enabled:
            scan_runner.request_stop()
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
