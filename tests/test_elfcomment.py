"""ELF .comment parsing edge cases."""

from __future__ import annotations

from pathlib import Path

from debuginfod.elfcomment import from_path_or_empty


def test_from_path_or_empty_invalid_elf(tmp_path: Path) -> None:
    path = tmp_path / "broken.so.debug"
    path.write_bytes(b"\x7fELF" + b"\x00" * 200)
    assert from_path_or_empty(path) == ""
