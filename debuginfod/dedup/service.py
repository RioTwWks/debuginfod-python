"""Dedup service facade (debuginfod-go/internal/dedup/service.go)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from debuginfod.db import Database
from debuginfod.dedup.pipeline import BackfillResult, PipelineOptions, run_ingest_all
from debuginfod.dedup.preprocess import ObjcopyZstd, resolve_preprocessor
from debuginfod.dedup.restore import RestoreOptions, restore_to_cache
from debuginfod.dedup.xdelta import Xdelta
from debuginfod.memlimit import MemoryGovernor, dedup_peak_factor_for_strategy

logger = logging.getLogger(__name__)


@dataclass
class DedupConfig:
    enabled: bool = False
    projects: list[str] = field(default_factory=list)
    workers: int = 4
    strategy: str = "xdelta-decompress-dwz"
    compress_base: bool = True
    xdelta_path: str = "xdelta3"
    dwz_path: str = "dwz"
    objcopy_path: str = "objcopy"
    dedup_peak_factor: float = 3.0
    dedup_serial_above_mb: int = 64
    dedup_max_file_mb: int = 256


class DedupService:
    def __init__(
        self,
        db: Database,
        cfg: DedupConfig,
        scan_paths: list[str | Path],
        memory_governor: MemoryGovernor | None = None,
    ) -> None:
        self.db = db
        self.cfg = cfg
        self.scan_paths = [str(p) for p in scan_paths]
        self._memory_governor = memory_governor
        self._xdelta = Xdelta(cfg.xdelta_path)
        self._preprocessor = resolve_preprocessor(cfg.strategy, cfg.dwz_path, cfg.objcopy_path)
        self._objcopy_zstd = ObjcopyZstd(cfg.objcopy_path)
        self._restore_opts = RestoreOptions(
            xdelta=self._xdelta,
            objcopy=cfg.objcopy_path,
            compress_base=cfg.compress_base,
        )

    def enabled(self) -> bool:
        return self.cfg.enabled

    def _pipeline_opts(self, dry_run: bool = False, stop_event: object | None = None) -> PipelineOptions:
        peak_factor = self.cfg.dedup_peak_factor
        if self._memory_governor is not None:
            peak_factor = dedup_peak_factor_for_strategy(
                self.cfg.strategy,
                self._memory_governor.limits,
            )
        return PipelineOptions(
            db=self.db,
            scan_paths=self.scan_paths,
            xdelta=self._xdelta,
            preprocessor=self._preprocessor,
            objcopy_zstd=self._objcopy_zstd,
            compress_base=self.cfg.compress_base,
            projects=self.cfg.projects,
            workers=self.cfg.workers,
            dry_run=dry_run,
            memory_governor=self._memory_governor,
            stop_event=stop_event,
            dedup_strategy=self.cfg.strategy,
            dedup_peak_factor=peak_factor,
            dedup_serial_above_mb=self.cfg.dedup_serial_above_mb,
            dedup_max_file_mb=self.cfg.dedup_max_file_mb,
        )

    def restore_to_cache(self, cache_dir: str | Path, file_path: str) -> str:
        return restore_to_cache(self.db, self._restore_opts, cache_dir, file_path)

    def run_ingest_after_scan(self, stop_event: object | None = None) -> BackfillResult:
        started = datetime.now(timezone.utc)
        result = run_ingest_all(self._pipeline_opts(stop_event=stop_event))
        self._record_run(started, result)
        logger.info(
            "dedup ingest: registered=%d compressed=%d errors=%d bytes_before=%d bytes_after=%d",
            result.files_registered,
            result.files_compressed,
            result.errors,
            result.bytes_before,
            result.bytes_after,
        )
        return result

    def run_backfill(self, project: str = "", batch: int = 50, dry_run: bool = False) -> BackfillResult:
        started = datetime.now(timezone.utc)
        opts = self._pipeline_opts(dry_run=dry_run)
        if project:
            opts.projects = [project]
        result = run_ingest_all(opts)
        result.build_dirs_processed = batch
        if not dry_run:
            self._record_run(started, result, project=project, dry_run=dry_run)
        return result

    def _record_run(
        self,
        started: datetime,
        result: BackfillResult,
        project: str = "",
        dry_run: bool = False,
    ) -> None:
        finished = datetime.now(timezone.utc)
        self.db.insert_dedup_run(
            {
                "finished_at": finished.replace(microsecond=0).isoformat(),
                "duration_ms": int((finished - started).total_seconds() * 1000),
                "project": project,
                "dry_run": dry_run,
                "build_dirs_processed": result.build_dirs_processed,
                "files_registered": result.files_registered,
                "files_compressed": result.files_compressed,
                "files_skipped": result.files_skipped,
                "errors": result.errors,
                "bytes_before": result.bytes_before,
                "bytes_after": result.bytes_after,
            }
        )
