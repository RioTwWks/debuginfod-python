"""ELF preprocess before xdelta (objcopy + dwz)."""

from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from debuginfod.memlimit import MemoryGovernor

logger = logging.getLogger(__name__)


class Preprocessor(ABC):
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def apply_in_place(
        self,
        path: str | Path,
        *,
        memory_governor: MemoryGovernor | None = None,
        stop_event: object | None = None,
    ) -> None: ...


class NoPreprocessor(Preprocessor):
    def name(self) -> str:
        return "none"

    def available(self) -> bool:
        return True

    def apply_in_place(
        self,
        path: str | Path,
        *,
        memory_governor: MemoryGovernor | None = None,
        stop_event: object | None = None,
    ) -> None:
        return None


class DecompressDwzPreprocessor(Preprocessor):
    def __init__(self, dwz: str = "dwz", objcopy: str = "objcopy") -> None:
        self.dwz = dwz or "dwz"
        self.objcopy = objcopy or "objcopy"

    def name(self) -> str:
        return "decompress-dwz"

    def available(self) -> bool:
        return shutil.which(self.dwz) is not None and shutil.which(self.objcopy) is not None

    def apply_in_place(
        self,
        path: str | Path,
        *,
        memory_governor: MemoryGovernor | None = None,
        stop_event: object | None = None,
    ) -> None:
        from debuginfod.memlimit import run_subprocess_monitored

        p = str(path)
        result = run_subprocess_monitored(
            [self.objcopy, "--decompress-debug-sections", p],
            memory_governor=memory_governor,
            stop_event=stop_event,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[:512])
        result = run_subprocess_monitored(
            [self.dwz, p],
            memory_governor=memory_governor,
            stop_event=stop_event,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[:512])


class ObjcopyZstd:
    def __init__(self, objcopy: str = "objcopy") -> None:
        self.bin = objcopy or "objcopy"

    def available(self) -> bool:
        return shutil.which(self.bin) is not None

    def compress_in_place(
        self,
        path: str | Path,
        *,
        memory_governor: MemoryGovernor | None = None,
        stop_event: object | None = None,
    ) -> int:
        from debuginfod.memlimit import run_subprocess_monitored

        p = str(path)
        result = run_subprocess_monitored(
            [self.bin, "--compress-debug-sections=zstd", p],
            memory_governor=memory_governor,
            stop_event=stop_event,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[:512])
        return Path(p).stat().st_size


def decompress_debug_sections(objcopy: str, src_path: str | Path, dst_path: str | Path) -> None:
    from debuginfod.dedup.copy import copy_file_atomic

    copy_file_atomic(src_path, dst_path)
    bin_path = objcopy or "objcopy"
    from debuginfod.memlimit import run_subprocess_monitored

    result = run_subprocess_monitored(
        [bin_path, "--decompress-debug-sections", str(dst_path)],
    )
    if result.returncode != 0:
        Path(dst_path).unlink(missing_ok=True)
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[:512])


def resolve_preprocessor(strategy: str, dwz: str = "", objcopy: str = "") -> Preprocessor:
    if strategy in {"xdelta", "none"}:
        return NoPreprocessor()
    return DecompressDwzPreprocessor(dwz=dwz, objcopy=objcopy)
