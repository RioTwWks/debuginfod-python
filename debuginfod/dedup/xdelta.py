"""xdelta3 CLI wrapper."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class Xdelta:
    def __init__(self, bin_path: str = "xdelta3") -> None:
        self.bin = bin_path or "xdelta3"

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def encode(self, base_path: str | Path, target_path: str | Path, delta_path: str | Path) -> None:
        delta = Path(delta_path)
        delta.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-e", "-s", str(base_path), str(target_path), str(delta)]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[:512]
            raise RuntimeError(f"xdelta3 encode failed: {stderr}")

    def decode(self, base_path: str | Path, delta_path: str | Path, out_path: str | Path) -> None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-d", "-s", str(base_path), str(delta_path), str(out)]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[:512]
            raise RuntimeError(f"xdelta3 decode failed: {stderr}")


def delta_path_for(file_path: str | Path) -> str:
    return str(file_path) + ".xdelta"
