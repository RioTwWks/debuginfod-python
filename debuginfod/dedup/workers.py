"""Parallel group processing for dedup."""

from __future__ import annotations

import logging
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from debuginfod.dedup.group_base import DedupNotFoundError, find_group_base
from debuginfod.dedup.pipeline import (
    PipelineOptions,
    compress_one,
    mark_singleton_full,
    process_group,
    _group_peak_bytes,
)
from debuginfod.memlimit import MemoryGovernor

logger = logging.getLogger(__name__)


def _stopped(stop_event: object | None) -> bool:
    return stop_event is not None and getattr(stop_event, "is_set", lambda: False)()


def _largest_file_bytes(group: list) -> int:
    if not group:
        return 0
    return max(int(getattr(f, "original_size", 0) or 0) for f in group)


def process_groups(
    opts: PipelineOptions,
    groups: dict[str, list],
    *,
    memory_governor: MemoryGovernor | None = None,
    stop_event: object | None = None,
) -> tuple[int, int, int, int, int]:
    if not opts.dry_run:
        if not opts.xdelta.available():
            logger.error("xdelta3 not found")
            return 0, 0, len(groups), 0, 0
        if opts.preprocessor.name() != "none" and not opts.preprocessor.available():
            logger.error("dedup preprocessor not available: %s", opts.preprocessor.name())
            return 0, 0, len(groups), 0, 0

    jobs: list[list] = []
    for group in groups.values():
        if not group:
            continue
        sorted_group = sorted(group, key=lambda f: (f.file_build_num, f.file_path))
        jobs.append(sorted_group)

    if not jobs:
        return 0, 0, 0, 0, 0

    largest = max(_largest_file_bytes(job) for job in jobs)
    largest_peak = max((_group_peak_bytes(job, opts) for job in jobs), default=0)
    peak_factor = opts.dedup_peak_factor
    workers = opts.workers
    serial_bytes = max(0, opts.dedup_serial_above_mb) * 1024 * 1024
    if serial_bytes > 0 and largest > serial_bytes:
        if workers > 1:
            logger.info(
                "Dedup forced serial (largest file %.1f MiB > %d MiB threshold)",
                largest / (1024 * 1024),
                opts.dedup_serial_above_mb,
            )
        workers = 1
    elif memory_governor is not None:
        workers = memory_governor.effective_workers(
            opts.workers, largest_peak, peak_factor=1.0
        )
        if workers < opts.workers:
            logger.info(
                "Dedup parallelism reduced %d -> %d (largest file %.1f MiB, peak=%.1fx)",
                opts.workers,
                workers,
                largest / (1024 * 1024),
                peak_factor,
            )

    if opts.dry_run or workers <= 1 or len(jobs) <= 1:
        return _run_sequential(opts, jobs, memory_governor=memory_governor, stop_event=stop_event)

    compressed = skipped = errors = bytes_before = bytes_after = 0
    pending: dict = {}
    job_iter = iter(jobs)

    with ThreadPoolExecutor(max_workers=workers) as pool:

        def submit_next() -> bool:
            if _stopped(stop_event):
                return False
            try:
                job = next(job_iter)
            except StopIteration:
                return False
            peak_bytes = _group_peak_bytes(job, opts)
            if memory_governor is not None:
                if not memory_governor.wait_for_peak_bytes(peak_bytes, stop_event):
                    return False
            pending[pool.submit(_run_group_job, opts, job, memory_governor, stop_event)] = job
            return True

        for _ in range(min(workers, len(jobs))):
            if not submit_next():
                break

        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                pending.pop(fut)
                c, s, e, bb, ba = fut.result()
                compressed += c
                skipped += s
                errors += e
                bytes_before += bb
                bytes_after += ba
                if _stopped(stop_event):
                    for other in pending:
                        other.cancel()
                    pending.clear()
                    break
                submit_next()

    return compressed, skipped, errors, bytes_before, bytes_after


def _run_sequential(
    opts: PipelineOptions,
    jobs: list[list],
    *,
    memory_governor: MemoryGovernor | None = None,
    stop_event: object | None = None,
) -> tuple[int, int, int, int, int]:
    compressed = skipped = errors = bytes_before = bytes_after = 0
    for job in jobs:
        if _stopped(stop_event):
            break
        peak_bytes = _group_peak_bytes(job, opts)
        if memory_governor is not None:
            if not memory_governor.wait_for_peak_bytes(peak_bytes, stop_event):
                break
        c, s, e, bb, ba = _run_group_job(opts, job, memory_governor, stop_event)
        compressed += c
        skipped += s
        errors += e
        bytes_before += bb
        bytes_after += ba
    return compressed, skipped, errors, bytes_before, bytes_after


def _run_group_job(
    opts: PipelineOptions,
    group: list,
    memory_governor: MemoryGovernor | None = None,
    stop_event: object | None = None,
) -> tuple[int, int, int, int, int]:
    if len(group) == 1:
        singleton = group[0]
        if not opts.dry_run:
            try:
                existing = find_group_base(opts.db, singleton)
            except DedupNotFoundError:
                try:
                    mark_singleton_full(opts, singleton)
                except Exception as exc:
                    opts.db.mark_dedup_file_error(singleton.id, str(exc))
                    return 0, 0, 1, 0, 0
                return 0, 1, 0, singleton.original_size, singleton.original_size
            except Exception as exc:
                opts.db.mark_dedup_file_error(singleton.id, str(exc))
                return 0, 0, 1, 0, 0
            try:
                delta_size = compress_one(
                    opts,
                    existing,
                    singleton,
                    memory_governor=memory_governor,
                    stop_event=stop_event,
                )
            except Exception as exc:
                opts.db.mark_dedup_file_error(singleton.id, str(exc))
                return 0, 0, 1, 0, 0
            return 1, 1, 0, singleton.original_size, delta_size
        try:
            find_group_base(opts.db, singleton)
            return 1, 1, 0, singleton.original_size, 0
        except DedupNotFoundError:
            return 0, 1, 0, singleton.original_size, singleton.original_size

    if opts.dry_run:
        bb = sum(f.original_size for f in group)
        return len(group) - 1, 1, 0, bb, 0

    peak_bytes = _group_peak_bytes(group, opts)
    budget = None
    if memory_governor is not None:
        budget = memory_governor.acquire_peak_budget(peak_bytes, stop_event)
        if budget is None:
            return 0, 0, 1, sum(f.original_size for f in group), 0

    try:
        with budget or _null_context():
            c, bb, ba, err = process_group(opts, group, memory_governor=memory_governor, stop_event=stop_event)
    finally:
        pass

    errors = 1 if err else 0
    return c, 1, errors, bb, ba


class _null_context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        return None
