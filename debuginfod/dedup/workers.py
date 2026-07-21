"""Parallel group processing for dedup."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from debuginfod.dedup.pipeline import PipelineOptions, mark_singleton_full, process_group

logger = logging.getLogger(__name__)


def process_groups(
    opts: PipelineOptions,
    groups: dict[str, list],
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

    if opts.dry_run or opts.workers <= 1 or len(jobs) <= 1:
        return _run_sequential(opts, jobs)

    compressed = skipped = errors = bytes_before = bytes_after = 0
    workers = max(1, opts.workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_group_job, opts, job) for job in jobs]
        for fut in as_completed(futures):
            c, s, e, bb, ba = fut.result()
            compressed += c
            skipped += s
            errors += e
            bytes_before += bb
            bytes_after += ba
    return compressed, skipped, errors, bytes_before, bytes_after


def _run_sequential(opts: PipelineOptions, jobs: list[list]) -> tuple[int, int, int, int, int]:
    compressed = skipped = errors = bytes_before = bytes_after = 0
    for job in jobs:
        c, s, e, bb, ba = _run_group_job(opts, job)
        compressed += c
        skipped += s
        errors += e
        bytes_before += bb
        bytes_after += ba
    return compressed, skipped, errors, bytes_before, bytes_after


def _run_group_job(opts: PipelineOptions, group: list) -> tuple[int, int, int, int, int]:
    if len(group) == 1:
        if not opts.dry_run:
            try:
                mark_singleton_full(opts, group[0])
            except Exception as exc:
                opts.db.mark_dedup_file_error(group[0].id, str(exc))
                return 0, 0, 1, 0, 0
        return 0, 1, 0, group[0].original_size, group[0].original_size

    if opts.dry_run:
        bb = sum(f.original_size for f in group)
        return len(group) - 1, 1, 0, bb, 0

    c, bb, ba, err = process_group(opts, group)
    errors = 1 if err else 0
    return c, 1, errors, bb, ba
