"""Index worker behavior tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from debuginfod.index_worker import process_elf_path


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required")
def test_debuginfo_skips_dwarf_sources(tmp_path: Path) -> None:
    """`.debug` artifacts index build-id only; skip pyelftools DWARF walk."""
    src = tmp_path / "t.c"
    src.write_text("int main(void) { return 0; }\n")
    binary = tmp_path / "demo"
    debug_elf = tmp_path / "libdemo.debug"
    subprocess.run(
        ["gcc", "-g", "-O0", "-Wl,--build-id=sha1", "-o", str(binary), str(src)],
        check=True,
    )
    shutil.copy(binary, debug_elf)
    result = process_elf_path(str(debug_elf))
    assert result.indexed
    assert result.artifact is not None
    assert result.artifact.artifact_type == "debuginfo"
    assert result.sources == []
