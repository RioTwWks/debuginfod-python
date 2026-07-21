"""xdelta3 CLI wrapper."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debuginfod.memlimit import MemoryGovernor

logger = logging.getLogger(__name__)


class Xdelta:
    def __init__(self, bin_path: str = "xdelta3") -> None:
        self.bin = bin_path or "xdelta3"

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def encode(
        self,
        base_path: str | Path,
        target_path: str | Path,
        delta_path: str | Path,
        *,
        memory_governor: MemoryGovernor | None = None,
        stop_event: object | None = None,
    ) -> None:
        from debuginfod.memlimit import run_subprocess_monitored

        delta = Path(delta_path)
        delta.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-e", "-s", str(base_path), str(target_path), str(delta)]
        result = run_subprocess_monitored(
            cmd,
            memory_governor=memory_governor,
            stop_event=stop_event,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[:512]
            raise RuntimeError(f"xdelta3 encode failed: {stderr}")

    def decode(
        self,
        base_path: str | Path,
        delta_path: str | Path,
        out_path: str | Path,
        *,
        memory_governor: MemoryGovernor | None = None,
        stop_event: object | None = None,
    ) -> None:
        from debuginfod.memlimit import run_subprocess_monitored

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-d", "-s", str(base_path), str(delta_path), str(out)]
        result = run_subprocess_monitored(
            cmd,
            memory_governor=memory_governor,
            stop_event=stop_event,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[:512]
            raise RuntimeError(f"xdelta3 decode failed: {stderr}")


def delta_path_for(file_path: str | Path) -> str:
    return str(file_path) + ".xdelta"
