"""Unpack Quik *.7zip.debug archives."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class ArchiveError(RuntimeError):
    """7z extraction failed."""


def find_seven_zip(seven_zip_path: str = "") -> str:
    if seven_zip_path:
        return seven_zip_path
    for candidate in ("7z", "7za", "7zz"):
        if shutil.which(candidate):
            return candidate
    raise ArchiveError("7z/7za not found; install p7zip-full or p7zip")


def extract_7zip_debug(archive: Path, dest_dir: Path, seven_zip_path: str = "") -> Path:
    """Extract one *.7zip.debug archive into dest_dir (in-place sibling)."""
    if not archive.is_file():
        raise ArchiveError(f"archive not found: {archive}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    tool = find_seven_zip(seven_zip_path)
    cmd = [tool, "x", f"-o{dest_dir}", str(archive), "-y"]
    try:
        result = subprocess.run(cmd, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise ArchiveError(f"{tool} not found") from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise ArchiveError(f"7z failed ({cmd}): {stderr}")

    logger.info("Extracted %s -> %s", archive.name, dest_dir)
    return dest_dir


def unzip_batch_directory(batch_dir: Path, seven_zip_path: str = "") -> int:
    """Extract all *.7zip.debug under batch_dir; remove archives after success."""
    count = 0
    for archive in sorted(batch_dir.rglob("*.7zip.debug")):
        extract_7zip_debug(archive, archive.parent, seven_zip_path)
        archive.unlink(missing_ok=True)
        count += 1
    return count
