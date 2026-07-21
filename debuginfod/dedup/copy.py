"""File copy helpers for dedup pipeline."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path


def copy_file_atomic(src: str | Path, dst: str | Path) -> None:
    src_path = Path(src)
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst_path.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(src_path, tmp_path)
        os.replace(tmp_path, dst_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()
