"""Restore dedup files to cache for HTTP serving."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from debuginfod.db import Database
from debuginfod.dedup.copy import file_sha256
from debuginfod.dedup.preprocess import decompress_debug_sections
from debuginfod.dedup.xdelta import Xdelta


class RestoreOptions:
    def __init__(
        self,
        xdelta: Xdelta | None = None,
        objcopy: str = "objcopy",
        compress_base: bool = True,
    ) -> None:
        self.xdelta = xdelta or Xdelta()
        self.objcopy = objcopy
        self.compress_base = compress_base


def restore_to_cache(
    db: Database,
    opts: RestoreOptions,
    cache_dir: str | Path,
    file_path: str,
) -> str:
    path = str(Path(file_path).resolve())
    record = db.get_dedup_file_by_path(path)
    if record is None:
        if Path(path).is_file():
            return path
        raise FileNotFoundError(path)

    if record.storage_kind in {"full", "base"}:
        if Path(path).is_file():
            return path
        raise FileNotFoundError(path)

    if record.storage_kind == "delta":
        return _restore_delta(db, opts, cache_dir, record)

    if Path(path).is_file():
        return path
    raise FileNotFoundError(path)


def _restore_delta(
    db: Database,
    opts: RestoreOptions,
    cache_dir: str | Path,
    record,
) -> str:
    if not record.base_file_id:
        raise RuntimeError(f"delta without base_file_id: {record.file_path}")

    base = db.get_dedup_file_by_id(record.base_file_id)
    if base is None:
        raise RuntimeError("base file record missing")
    if not Path(base.file_path).is_file():
        raise FileNotFoundError(base.file_path)
    if not record.delta_path or not Path(record.delta_path).is_file():
        raise FileNotFoundError(record.delta_path)

    out_dir = Path(cache_dir) / "dedup-restored"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_name = f"{record.id}-{record.file_build_num}-{Path(record.file_path).name}"
    out_path = out_dir / cache_name

    if out_path.is_file() and record.original_size > 0 and record.sha256:
        if out_path.stat().st_size == record.original_size:
            if file_sha256(out_path) == record.sha256:
                return str(out_path)

    base_for_decode = base.file_path
    tmp_base: str | None = None
    if opts.compress_base:
        fd, tmp_base = tempfile.mkstemp(prefix="dedup-base-", dir=out_dir)
        os.close(fd)
        try:
            decompress_debug_sections(opts.objcopy, base.file_path, tmp_base)
            base_for_decode = tmp_base
            opts.xdelta.decode(base_for_decode, record.delta_path, out_path)
        finally:
            if tmp_base:
                Path(tmp_base).unlink(missing_ok=True)
    else:
        opts.xdelta.decode(base_for_decode, record.delta_path, out_path)

    if record.sha256:
        got = file_sha256(out_path)
        if got != record.sha256:
            out_path.unlink(missing_ok=True)
            raise RuntimeError("restored sha256 mismatch")
    return str(out_path)
