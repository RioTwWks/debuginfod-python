"""Dedup pipeline: group, xdelta, verify (debuginfod-go/internal/dedup/pipeline.go)."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from debuginfod.db import Database, DedupFileRecord
from debuginfod.dedup.copy import copy_file_atomic, file_sha256
from debuginfod.dedup.discover import discover
from debuginfod.dedup.preprocess import ObjcopyZstd, Preprocessor
from debuginfod.dedup.project_group import normalize_dedup_group_project
from debuginfod.dedup.xdelta import Xdelta, delta_path_for

if TYPE_CHECKING:
    from debuginfod.memlimit import MemoryGovernor

logger = logging.getLogger(__name__)


def _uses_decompress(strategy: str) -> bool:
    return "decompress" in strategy.lower()


def _dedup_peak_bytes(path: str | Path, opts: PipelineOptions) -> int:
    from debuginfod.memlimit import estimate_decompress_peak_bytes, estimate_dedup_peak_bytes

    size = Path(path).stat().st_size
    if _uses_decompress(opts.dedup_strategy):
        limits = opts.memory_governor.limits if opts.memory_governor is not None else None
        return estimate_decompress_peak_bytes(size, limits)
    return estimate_dedup_peak_bytes(size, opts.dedup_peak_factor)


def _group_peak_bytes(group: list, opts: PipelineOptions) -> int:
    peaks: list[int] = []
    for record in group:
        path = Path(record.file_path)
        if path.is_file():
            peaks.append(_dedup_peak_bytes(path, opts))
    return max(peaks) if peaks else 0


def _file_exceeds_dedup_max(record: DedupFileRecord, opts: PipelineOptions) -> bool:
    if opts.dedup_max_file_mb <= 0:
        return False
    limit = opts.dedup_max_file_mb * 1024 * 1024
    size = int(record.original_size or 0)
    if size <= 0:
        try:
            size = Path(record.file_path).stat().st_size
        except OSError:
            return False
    return size > limit


@dataclass
class PipelineOptions:
    db: Database
    scan_paths: list[str]
    xdelta: Xdelta
    preprocessor: Preprocessor
    objcopy_zstd: ObjcopyZstd
    compress_base: bool = True
    projects: list[str] = field(default_factory=list)
    workers: int = 4
    dry_run: bool = False
    memory_governor: "MemoryGovernor | None" = None
    stop_event: object | None = None
    dedup_strategy: str = ""
    dedup_peak_factor: float = 3.0
    dedup_serial_above_mb: int = 64
    dedup_max_file_mb: int = 256


@dataclass
class BackfillResult:
    build_dirs_processed: int = 0
    files_registered: int = 0
    groups_processed: int = 0
    files_compressed: int = 0
    files_skipped: int = 0
    errors: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    dry_run: bool = False


def group_key(record: DedupFileRecord) -> str:
    project = normalize_dedup_group_project(record.project_name)
    return f"{project}|{record.file_stem}"


def group_files(files: list[DedupFileRecord]) -> dict[str, list[DedupFileRecord]]:
    groups: dict[str, list[DedupFileRecord]] = {}
    for f in files:
        key = group_key(f)
        groups.setdefault(key, []).append(f)
    return groups


def run_ingest_all(opts: PipelineOptions) -> BackfillResult:
    result = BackfillResult(dry_run=opts.dry_run)
    n, err = _safe_discover(opts)
    result.files_registered = n
    if err:
        result.errors += 1
        return result

    files = opts.db.list_all_pending_dedup_files()
    if opts.projects:
        allowed = {p.strip().replace("\\", "/") for p in opts.projects if p.strip()}
        files = [f for f in files if f.project_name.replace("\\", "/") in allowed]

    groups = group_files(files)
    result.groups_processed = len(groups)
    from debuginfod.dedup.workers import process_groups

    compressed, skipped, errors, b_before, b_after = process_groups(
        opts,
        groups,
        memory_governor=opts.memory_governor,
        stop_event=opts.stop_event,
    )
    result.files_compressed = compressed
    result.files_skipped = skipped
    result.errors = errors
    result.bytes_before = b_before
    result.bytes_after = b_after

    if not opts.dry_run:
        seen: set[int] = set()
        for f in files:
            if f.build_dir_id not in seen:
                seen.add(f.build_dir_id)
                opts.db.finish_build_dir_if_done(f.build_dir_id)
    return result


def _safe_discover(opts: PipelineOptions) -> tuple[int, Exception | None]:
    try:
        return discover(opts.db, opts.scan_paths, opts.projects), None
    except Exception as exc:
        logger.exception("dedup discover failed")
        return 0, exc


def mark_singleton_full(opts: PipelineOptions, record: DedupFileRecord) -> None:
    if not Path(record.file_path).is_file():
        raise FileNotFoundError(record.file_path)
    sha = file_sha256(record.file_path)
    opts.db.mark_dedup_file_done(record.id, "full", None, "", sha, 0)


def process_group(
    opts: PipelineOptions,
    group: list[DedupFileRecord],
    *,
    memory_governor: "MemoryGovernor | None" = None,
    stop_event: object | None = None,
) -> tuple[int, int, int, Exception | None]:
    gov = memory_governor or opts.memory_governor
    bytes_before = sum(f.original_size for f in group)
    base = group[0]
    if _file_exceeds_dedup_max(base, opts):
        msg = f"file exceeds DEBUGINFOD_DEDUP_MAX_FILE_MB={opts.dedup_max_file_mb}"
        opts.db.mark_dedup_file_error(base.id, msg)
        return 0, bytes_before, 0, RuntimeError(msg)
    if not Path(base.file_path).is_file():
        opts.db.mark_dedup_file_error(base.id, f"base missing: {base.file_path}")
        return 0, bytes_before, 0, FileNotFoundError(base.file_path)

    base_size = int(base.original_size or Path(base.file_path).stat().st_size)
    peak_bytes = _dedup_peak_bytes(base.file_path, opts)
    if gov is not None:
        if not gov.wait_for_peak_bytes(peak_bytes, stop_event):
            return 0, bytes_before, 0, RuntimeError("dedup stopped (memory pressure)")

    try:
        opts.preprocessor.apply_in_place(
            base.file_path,
            memory_governor=gov,
            stop_event=stop_event,
        )
    except Exception as exc:
        opts.db.mark_dedup_file_error(base.id, str(exc))
        return 0, bytes_before, 0, exc

    from debuginfod.memlimit import release_heap

    release_heap()
    base_sha = file_sha256(base.file_path)
    base_size = Path(base.file_path).stat().st_size
    opts.db.mark_dedup_file_done(base.id, "base", None, "", base_sha, base_size)
    bytes_after = base_size
    compressed = 0
    group_err: Exception | None = None

    for target in group[1:]:
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            group_err = RuntimeError("dedup stopped")
            break
        if _file_exceeds_dedup_max(target, opts):
            msg = f"file exceeds DEBUGINFOD_DEDUP_MAX_FILE_MB={opts.dedup_max_file_mb}"
            opts.db.mark_dedup_file_error(target.id, msg)
            group_err = RuntimeError(msg)
            continue
        try:
            delta_size = compress_one(
                opts,
                base,
                target,
                memory_governor=gov,
                stop_event=stop_event,
            )
            compressed += 1
            bytes_after += delta_size
            release_heap()
        except Exception as exc:
            logger.warning("dedup compress failed for %s: %s", target.file_path, exc)
            opts.db.mark_dedup_file_error(target.id, str(exc))
            group_err = exc

    if opts.compress_base and opts.objcopy_zstd.available():
        if gov is not None and not gov.wait_for_peak_bytes(
            _dedup_peak_bytes(base.file_path, opts), stop_event
        ):
            group_err = RuntimeError("dedup stopped (memory pressure)")
        else:
            try:
                comp_size = opts.objcopy_zstd.compress_in_place(
                    base.file_path,
                    memory_governor=gov,
                    stop_event=stop_event,
                )
                opts.db.update_dedup_file_compressed_size(base.id, comp_size)
                bytes_after = bytes_after - base_size + comp_size
                release_heap()
            except Exception as exc:
                opts.db.mark_dedup_file_error(base.id, f"compress base: {exc}")
                group_err = exc

    return compressed, bytes_before, bytes_after, group_err


def compress_one(
    opts: PipelineOptions,
    base: DedupFileRecord,
    target: DedupFileRecord,
    *,
    memory_governor: "MemoryGovernor | None" = None,
    stop_event: object | None = None,
) -> int:
    gov = memory_governor or opts.memory_governor
    if not Path(target.file_path).is_file():
        raise FileNotFoundError(target.file_path)
    if _file_exceeds_dedup_max(target, opts):
        raise RuntimeError(f"file exceeds DEBUGINFOD_DEDUP_MAX_FILE_MB={opts.dedup_max_file_mb}")

    from debuginfod.memlimit import estimate_xdelta_peak_bytes, release_heap

    peak_bytes = _dedup_peak_bytes(target.file_path, opts)
    if gov is not None and not gov.wait_for_peak_bytes(peak_bytes, stop_event):
        raise RuntimeError("dedup stopped (memory pressure)")

    work_dir = Path(tempfile.mkdtemp(prefix="dedup-prep-", dir=str(Path(target.file_path).parent)))
    try:
        prep_target = work_dir / Path(target.file_path).name
        copy_file_atomic(target.file_path, prep_target)
        opts.preprocessor.apply_in_place(
            prep_target,
            memory_governor=gov,
            stop_event=stop_event,
        )
        release_heap()
        orig_sha = file_sha256(prep_target)

        base_bytes = Path(base.file_path).stat().st_size
        prep_bytes = prep_target.stat().st_size
        xdelta_peak = estimate_xdelta_peak_bytes(base_bytes, prep_bytes)
        if gov is not None and not gov.wait_for_peak_bytes(xdelta_peak, stop_event):
            raise RuntimeError("dedup stopped before xdelta (memory pressure)")

        delta_path = delta_path_for(target.file_path)
        opts.xdelta.encode(
            base.file_path,
            prep_target,
            delta_path,
            memory_governor=gov,
            stop_event=stop_event,
        )

        tmp_restore = work_dir / "restore-verify.debug"
        opts.xdelta.decode(
            base.file_path,
            delta_path,
            tmp_restore,
            memory_governor=gov,
            stop_event=stop_event,
        )
        release_heap()
        restored_sha = file_sha256(tmp_restore)
        if restored_sha != orig_sha:
            Path(delta_path).unlink(missing_ok=True)
            raise RuntimeError("sha256 mismatch after restore")

        os.remove(target.file_path)
        delta_size = Path(delta_path).stat().st_size
        opts.db.mark_dedup_file_done(
            target.id, "delta", base.id, delta_path, orig_sha, delta_size
        )
        return delta_size
    finally:
        import shutil

        shutil.rmtree(work_dir, ignore_errors=True)
