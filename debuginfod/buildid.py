"""ELF build-id extraction compatible with debuginfod protocol."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import NoteSection

NT_GNU_BUILD_ID = 3
NT_GO_BUILD_ID = 4

BuildIDKind = Literal["gnu", "go"]
ArtifactType = Literal["executable", "debuginfo"]


class BuildIDNotFoundError(Exception):
    """Raised when ELF has no GNU/Go build-id note."""


@dataclass(frozen=True)
class BuildIDResult:
    value: str
    kind: BuildIDKind
    raw: str = ""


def normalize(build_id: str) -> str:
    """Normalize build-id to lowercase hex without prefixes."""
    value = build_id.strip().lower()
    if value.startswith("0x"):
        value = value[2:]
    return value.replace("-", "")


def go_canonical_id(raw: str) -> str:
    """Convert Go build-id string to SHA-256 hex for debuginfod URLs."""
    return hashlib.sha256(raw.encode()).hexdigest()


def is_elf(path: Path) -> bool:
    """Check ELF magic bytes."""
    try:
        with path.open("rb") as fh:
            header = fh.read(4)
        return len(header) == 4 and header == b"\x7fELF"
    except OSError:
        return False


def parse_notes(data: bytes) -> BuildIDResult:
    """Parse SHT_NOTE section for GNU or Go build-id."""
    gnu: BuildIDResult | None = None
    go_result: BuildIDResult | None = None
    offset = 0

    while offset + 12 <= len(data):
        namesz = int.from_bytes(data[offset : offset + 4], "little")
        descsz = int.from_bytes(data[offset + 4 : offset + 8], "little")
        note_type = int.from_bytes(data[offset + 8 : offset + 12], "little")
        offset += 12

        name_pad = (namesz + 3) & ~3
        desc_pad = (descsz + 3) & ~3
        if offset + name_pad + descsz > len(data):
            break

        name = data[offset : offset + namesz].rstrip(b"\x00").decode("ascii", errors="replace")
        offset += name_pad
        desc = data[offset : offset + descsz]
        offset += desc_pad

        if note_type == NT_GNU_BUILD_ID and name == "GNU" and desc:
            gnu = BuildIDResult(value=desc.hex(), kind="gnu")
        elif note_type == NT_GO_BUILD_ID and name == "Go" and desc:
            raw = desc.decode("utf-8", errors="replace")
            go_result = BuildIDResult(value=go_canonical_id(raw), kind="go", raw=raw)

    if gnu is not None:
        return gnu
    if go_result is not None:
        return go_result
    raise BuildIDNotFoundError("build-id not found in notes")


def from_bytes(data: bytes) -> BuildIDResult:
    """Extract build-id from ELF bytes."""
    from io import BytesIO

    elffile = ELFFile(BytesIO(data))
    return from_elf(elffile)


def from_elf(elffile: ELFFile) -> BuildIDResult:
    """Extract build-id from an open ELFFile."""
    for section in elffile.iter_sections():
        if not isinstance(section, NoteSection):
            continue
        try:
            return parse_notes(section.data())
        except BuildIDNotFoundError:
            continue
    raise BuildIDNotFoundError("build-id not found")


def from_path(path: Path) -> BuildIDResult:
    """Extract build-id from ELF file on disk."""
    with path.open("rb") as fh:
        elffile = ELFFile(fh)
        return from_elf(elffile)


def artifact_type(path_hint: str, elffile: ELFFile) -> ArtifactType:
    """Classify ELF as executable or debuginfo."""
    base = path_hint.replace("\\", "/").lower()
    if base.endswith(".debug"):
        return "debuginfo"
    if "/.build-id/" in base:
        return "debuginfo"
    if "/usr/lib/debug/" in base:
        return "debuginfo"

    if elffile.header["e_type"] in ("ET_EXEC", "ET_DYN"):
        return "executable"
    return "debuginfo"


def match_build_id_query(query: str, indexed_id: str, raw_go_id: str = "") -> bool:
    """Check if metadata buildid query matches indexed record."""
    q = normalize(query)
    if q == normalize(indexed_id):
        return True
    if raw_go_id and q == go_canonical_id(raw_go_id):
        return True
    if raw_go_id and q == raw_go_id:
        return True
    return False


def family_key(artifact_type_name: str, file_path: str) -> str:
    """Logical family for delta chaining across rebuilds of the same binary."""
    norm = file_path.replace("\\", "/")
    norm = re.sub(
        r"\.build-id/[0-9a-fA-F]{2}/[0-9a-fA-F]+",
        ".build-id/*/*",
        norm,
    )
    # Схлопываем типичные суффиксы версий (demo_v1, demo_v2 → demo).
    norm = re.sub(r"_v\d+$", "", norm)
    norm = re.sub(r"-\d+(\.\d+)*$", "", norm)
    return f"{artifact_type_name}|{norm}"
