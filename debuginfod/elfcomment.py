"""Parse ELF .comment for git commit tag (debuginfod-go/pkg/elfcomment)."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from elftools.elf.elffile import ELFFile


def from_bytes(data: bytes) -> str:
    elffile = ELFFile(BytesIO(data))
    section = elffile.get_section_by_name(".comment")
    if section is None:
        return ""
    raw = section.data()
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    return _extract_tag(text)


def from_path(path: str | Path) -> str:
    return from_bytes(Path(path).read_bytes())


def _extract_tag(raw: str) -> str:
    lines = [ln.strip() for ln in raw.replace("\r\n", "\n").split("\n") if ln.strip()]
    for line in lines:
        if re.fullmatch(r"[0-9a-fA-F]{8,40}", line):
            return line
        if line.startswith(("tag:", "commit:", "refs/")):
            return line
    return lines[-1] if lines else ""
