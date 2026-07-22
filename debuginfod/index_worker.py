"""CPU-bound ELF parsing for parallel indexing (runs in worker processes)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from elftools.elf.elffile import ELFFile

from debuginfod import buildid
from debuginfod.db import ArtifactRecord, SourceRecord

_DWARF_MAX_BYTES = int(os.getenv("DEBUGINFOD_SCAN_DWARF_MAX_MB", "128")) * 1024 * 1024


@dataclass
class IndexWorkerResult:
    path: str
    indexed: bool = False
    mark_kind: str = ""
    artifact: ArtifactRecord | None = None
    sources: list[SourceRecord] = field(default_factory=list)
    error: str = ""


def process_elf_path(path_str: str) -> IndexWorkerResult:
    """Parse one ELF file off the DB path; main process applies results."""
    path = Path(path_str)
    result = IndexWorkerResult(path=path_str)

    try:
        bid = buildid.from_path(path)
    except buildid.BuildIDNotFoundError:
        result.mark_kind = "no_build_id"
        return result
    except Exception as exc:
        result.error = str(exc)
        return result

    try:
        with path.open("rb") as fh:
            elffile = ELFFile(fh)
            artifact_type_name = buildid.artifact_type(str(path), elffile)
    except Exception as exc:
        result.error = str(exc)
        return result

    try:
        st = path.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
    except OSError as exc:
        result.error = str(exc)
        return result

    result.artifact = ArtifactRecord(
        build_id=bid.value,
        artifact_type=artifact_type_name,
        file_path=str(path.resolve()),
        build_id_kind=bid.kind,
        raw_build_id=bid.raw,
        mtime_ns=mtime_ns,
    )
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    # pyelftools DWARF on large .debug files is very slow and RAM-heavy vs Go debug/dwarf.
    # Index sources from executables only; debuginfo build-id is enough for /buildid/... HTTP.
    if artifact_type_name == "executable" and (
        _DWARF_MAX_BYTES <= 0 or file_size <= _DWARF_MAX_BYTES
    ):
        result.sources = _extract_dwarf_sources(path, bid.value)
    result.indexed = True
    result.mark_kind = "elf"
    return result


def _extract_dwarf_sources(elf_path: Path, build_id_value: str) -> list[SourceRecord]:
    try:
        with elf_path.open("rb") as fh:
            elffile = ELFFile(fh)
            if not elffile.has_dwarf_info():
                return []
            dwarf = elffile.get_dwarf_info()
    except Exception:
        return []

    out: list[SourceRecord] = []
    seen: set[str] = set()
    for cu in dwarf.iter_CUs():
        try:
            top = cu.get_top_DIE()
            comp_dir = ""
            if "DW_AT_comp_dir" in top.attributes:
                comp_dir = top.attributes["DW_AT_comp_dir"].value
                if isinstance(comp_dir, bytes):
                    comp_dir = comp_dir.decode("utf-8", errors="replace")
            file_name = ""
            if "DW_AT_name" in top.attributes:
                file_name = top.attributes["DW_AT_name"].value
                if isinstance(file_name, bytes):
                    file_name = file_name.decode("utf-8", errors="replace")
            if not file_name:
                continue

            if file_name.startswith("/"):
                source_path = file_name
            elif comp_dir:
                source_path = f"{comp_dir.rstrip('/')}/{file_name}"
            else:
                continue

            if source_path in seen:
                continue
            seen.add(source_path)

            src = _find_source_on_disk(source_path, elf_path)
            if src is None:
                continue

            try:
                st = src.stat()
                mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
            except OSError:
                continue

            out.append(
                SourceRecord(
                    build_id=build_id_value,
                    source_path=source_path,
                    file_path=str(src.resolve()),
                    mtime_ns=mtime_ns,
                )
            )
        except Exception:
            continue
    return out


def _find_source_on_disk(source_path: str, elf_path: Path) -> Path | None:
    candidates = [Path(source_path)]
    if not source_path.startswith("/"):
        candidates.append(elf_path.parent / source_path)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None
