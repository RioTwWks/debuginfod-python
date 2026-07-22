"""Filesystem scanner — metadata index only (debuginfod-go parity)."""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import sys
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from debuginfod import buildid
from debuginfod.db import Database
from debuginfod.index_worker import IndexWorkerResult, process_elf_path
from debuginfod.memlimit import MemoryGovernor, release_heap

logger = logging.getLogger(__name__)


def _main_script_path() -> str | None:
    main_mod = sys.modules.get("__main__")
    if main_mod is None:
        return None
    main_file = getattr(main_mod, "__file__", None)
    if not main_file or main_file in {"<stdin>", "-"}:
        return None
    return main_file


def _create_scan_executor(
    workers: int,
    use_process_pool: bool,
    memory_governor: MemoryGovernor | None = None,
) -> ProcessPoolExecutor | ThreadPoolExecutor:
    """Process pool isolates RAM per worker; prefer fork on Linux."""
    if not use_process_pool or _main_script_path() is None:
        if use_process_pool:
            logger.debug("Process pool unavailable for this entrypoint; using threads")
        return ThreadPoolExecutor(max_workers=workers)

    if sys.platform == "linux":
        methods = ("fork", "forkserver", "spawn")
    else:
        methods = ("spawn", "forkserver")

    for method in methods:
        try:
            ctx = mp.get_context(method)
            return ProcessPoolExecutor(
                max_workers=workers,
                mp_context=ctx,
                max_tasks_per_child=1,
            )
        except (ValueError, OSError):
            continue

    logger.warning("Process pool unavailable; falling back to thread pool")
    return ThreadPoolExecutor(max_workers=workers)


@dataclass
class ScanStats:
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    errors: int = 0
    artifacts_added: int = 0
    dedup_files_registered: int = 0
    dedup_files_compressed: int = 0
    dedup_errors: int = 0
    cancelled: bool = False


class Indexer:
    """Walk scan paths and index ELF artifacts by file path (no blob storage)."""

    def __init__(
        self,
        db: Database,
        scan_paths: list[Path],
        workers: int = 4,
        dedup_hook: object | None = None,
        stop_event: threading.Event | None = None,
        use_process_pool: bool = True,
        memory_governor: MemoryGovernor | None = None,
    ) -> None:
        self.db = db
        self.scan_paths = [p.resolve() for p in scan_paths]
        self.workers = max(1, workers)
        self.dedup_hook = dedup_hook
        self._stop = stop_event or threading.Event()
        self._use_process_pool = use_process_pool
        self._memory = memory_governor
        self._executor: ProcessPoolExecutor | ThreadPoolExecutor | None = None
        self._executor_lock = threading.Lock()

    def bind_stop_event(self, stop_event: threading.Event) -> None:
        self._stop = stop_event

    def request_stop(self) -> None:
        self._stop.set()
        with self._executor_lock:
            pool = self._executor
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def scan(self) -> ScanStats:
        stats = ScanStats()
        if self._stop.is_set():
            stats.cancelled = True
            return stats

        scan_workers = self.workers
        if self._memory is not None and self._memory.limits.enabled:
            capped = self._memory.effective_scan_workers(self.workers)
            if capped < scan_workers:
                logger.info(
                    "Scan workers capped %d -> %d (memory limits)",
                    scan_workers,
                    capped,
                )
            scan_workers = capped

        batch_size = max(scan_workers * 2, 8)
        batch: list[Path] = []
        pool: ProcessPoolExecutor | ThreadPoolExecutor | None = None

        try:
            pool = _create_scan_executor(scan_workers, self._use_process_pool, self._memory)
            with self._executor_lock:
                self._executor = pool

            for path in self._iter_elf_jobs(stats):
                if self._stop.is_set():
                    stats.cancelled = True
                    return stats
                batch.append(path)
                if len(batch) >= batch_size:
                    self._index_batch(pool, batch, stats)
                    batch.clear()
                    if self._stop.is_set():
                        stats.cancelled = True
                        return stats

            if batch and not self._stop.is_set():
                self._index_batch(pool, batch, stats)

            if self._stop.is_set():
                stats.cancelled = True
                return stats
        finally:
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
                with self._executor_lock:
                    if self._executor is pool:
                        self._executor = None
                pool = None
                if self._memory is not None and self._memory.limits.enabled:
                    logger.info("Scan pool stopped; releasing memory before dedup")
                    release_heap()
                    self._memory.prepare_for_heavy_work()

        if self.dedup_hook is not None and not self._stop.is_set():
            try:
                self.dedup_hook.run_ingest_after_scan(stop_event=self._stop)
                dedup = self.db.dedup_stats()
                stats.dedup_files_registered = int(dedup.get("total_files", 0))
                stats.dedup_files_compressed = int(dedup.get("delta_files", 0))
            except Exception:
                stats.dedup_errors += 1
                logger.exception("Dedup ingest after scan failed")

        return stats

    def _iter_elf_jobs(self, stats: ScanStats) -> Iterator[Path]:
        for root in self.scan_paths:
            if self._stop.is_set():
                return
            if not root.exists():
                logger.warning("Scan path does not exist: %s", root)
                continue
            if root.is_file():
                if buildid.is_elf(root):
                    stats.files_seen += 1
                    if self._should_scan(root):
                        yield root.resolve()
                    else:
                        stats.files_skipped += 1
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                if self._stop.is_set():
                    return
                for name in filenames:
                    path = Path(dirpath) / name
                    stats.files_seen += 1
                    if not buildid.is_elf(path):
                        continue
                    if not self._should_scan(path):
                        stats.files_skipped += 1
                        continue
                    yield path.resolve()

    def _index_batch(
        self,
        pool: ProcessPoolExecutor | ThreadPoolExecutor,
        jobs: list[Path],
        stats: ScanStats,
    ) -> None:
        if not jobs or self._stop.is_set():
            return

        pending: dict[Future[IndexWorkerResult], Path] = {}
        job_iter = iter(jobs)

        def submit_next() -> bool:
            if self._stop.is_set():
                return False
            try:
                path = next(job_iter)
            except StopIteration:
                return False
            if self._memory is not None and not self._memory.wait_for_headroom(
                self._stop,
                for_scan=True,
            ):
                return False
            pending[pool.submit(process_elf_path, str(path))] = path
            return True

        max_in_flight = min(len(jobs), getattr(pool, "_max_workers", self.workers))

        for _ in range(max_in_flight):
            if not submit_next():
                break

        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                path = pending.pop(future)
                try:
                    result = future.result()
                    self._apply_worker_result(path, result, stats)
                except Exception:
                    stats.errors += 1
                    logger.exception("Failed to index %s", path)
                if self._stop.is_set():
                    for other in pending:
                        other.cancel()
                    pending.clear()
                    break
                submit_next()

    def _apply_worker_result(self, path: Path, result: IndexWorkerResult, stats: ScanStats) -> None:
        if result.error:
            stats.errors += 1
            logger.error("Failed to index %s: %s", path, result.error)
            return

        if not result.indexed:
            if result.mark_kind:
                self._mark_scanned(path, result.mark_kind)
            stats.files_skipped += 1
            if result.mark_kind == "no_build_id":
                logger.debug("skip elf without build-id: %s", path)
            return

        if result.artifact is None:
            stats.files_skipped += 1
            return

        with self.db.transaction():
            self.db.upsert_artifact(result.artifact)
            self._mark_scanned(path, result.mark_kind or "elf")
            for source in result.sources:
                if self._stop.is_set():
                    break
                src_path = Path(source.file_path)
                if not self._should_scan(src_path):
                    continue
                self.db.upsert_source(source)
                self._mark_scanned(src_path, "source")

        stats.files_indexed += 1
        stats.artifacts_added += 1
        logger.debug(
            "Indexed %s build_id=%s type=%s",
            path,
            result.artifact.build_id[:12],
            result.artifact.artifact_type,
        )

    def _should_scan(self, path: Path) -> bool:
        if self._stop.is_set():
            return False
        try:
            st = path.stat()
        except OSError:
            return False
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        return self.db.needs_scan(str(path.resolve()), mtime_ns, st.st_size)

    def _mark_scanned(self, path: Path, kind: str) -> None:
        st = path.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        self.db.mark_scanned(str(path.resolve()), mtime_ns, st.st_size, kind)
