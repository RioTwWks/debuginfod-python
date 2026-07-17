"""Parse ELF .comment section for Quik build metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from elftools.elf.elffile import ELFFile

_COMMENT_SECTION = ".comment"


@dataclass(frozen=True)
class CommentInfo:
    """Parsed Quik .comment metadata."""

    version: str
    commit_tag_id: str
    raw: str
    source: str  # "comment" | "filename"


@dataclass(frozen=True)
class DebugFileInfo:
    """One unpacked .debug ELF file."""

    path: Path
    file_mask: str
    version: str
    commit_tag_id: str
    comment: CommentInfo | None
    build_number: int | None


def _read_comment_section(data: bytes) -> str:
    elffile = ELFFile(BytesIO(data))
    section = elffile.get_section_by_name(_COMMENT_SECTION)
    if section is None:
        return ""
    raw = section.data()
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def parse_comment_text(raw: str) -> CommentInfo | None:
    """
  Parse .comment body into version and commit tag id.

  Expected order (per filediffs spec): version line, then commit tag id line,
  after optional compiler/company/description headers.
  """
    if not raw.strip():
        return None

    lines = [ln.strip() for ln in raw.replace("\r\n", "\n").split("\n") if ln.strip()]
    if len(lines) < 2:
        return None

    # Heuristic: last two meaningful tokens are version + commit tag when tagged.
    version = ""
    commit_tag = ""
    for idx, line in enumerate(lines):
        if re.fullmatch(r"[0-9a-fA-F]{8,40}", line):
            if idx > 0:
                version = lines[idx - 1]
                commit_tag = line
                break

    if not version or not commit_tag:
        # Fallback: last two lines
        version = lines[-2]
        commit_tag = lines[-1]

    if not version or not commit_tag:
        return None

    return CommentInfo(
        version=version,
        commit_tag_id=commit_tag,
        raw=raw,
        source="comment",
    )


_FILENAME_RE = re.compile(
    r"^(?P<mask>.+)\.(?P<major>\d+)\.(?P<minor>\d+)\.(?P<extra>\d+)\.(?P<build>\d+)\.7zip\.debug$",
    re.IGNORECASE,
)

_DEBUG_RE = re.compile(
    r"^(?P<mask>.+)\.(?P<major>\d+)\.(?P<minor>\d+)\.(?P<extra>\d+)\.(?P<build>\d+)\.debug$",
    re.IGNORECASE,
)

_BUILD_DIR_RE = re.compile(r"^build_(?P<num>\d+)_", re.IGNORECASE)


def parse_build_number_from_dir(dir_name: str) -> int | None:
    match = _BUILD_DIR_RE.match(dir_name)
    if match is None:
        return None
    return int(match.group("num"))


def file_mask_from_name(name: str) -> tuple[str, int | None]:
    """Extract logical file mask and optional build number from Quik debug filename."""
    for pattern in (_FILENAME_RE, _DEBUG_RE):
        match = pattern.match(name)
        if match:
            return match.group("mask"), int(match.group("build"))
    if name.endswith(".debug"):
        base = name[: -len(".debug")]
        parts = base.rsplit(".", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0], int(parts[1])
        return base, None
    return name, None


def parse_debug_file(path: Path, batch_build_number: int | None = None) -> DebugFileInfo:
    """Parse one .debug ELF for grouping metadata."""
    file_mask, build_from_name = file_mask_from_name(path.name)
    data = path.read_bytes()
    comment_raw = _read_comment_section(data)
    comment = parse_comment_text(comment_raw)

    if comment:
        version = comment.version
        commit_tag = comment.commit_tag_id
    else:
        # External file: version from filename, ignore build number in grouping key.
        match = _DEBUG_RE.match(path.name) or _FILENAME_RE.match(path.name)
        if match:
            version = f"{match.group('major')}.{match.group('minor')}.{match.group('extra')}"
        commit_tag = ""

    build_number = build_from_name if build_from_name is not None else batch_build_number

    return DebugFileInfo(
        path=path,
        file_mask=file_mask,
        version=version,
        commit_tag_id=commit_tag,
        comment=comment,
        build_number=build_number,
    )


def enumerate_debug_files(batch_dir: Path) -> list[DebugFileInfo]:
    """List all .debug files under a build batch directory."""
    batch_build = parse_build_number_from_dir(batch_dir.name)
    files: list[DebugFileInfo] = []
    for path in sorted(batch_dir.rglob("*.debug")):
        if path.name.endswith(".7zip.debug"):
            continue
        if path.name.endswith(".debug.delta"):
            continue
        if path.name.endswith(".debug.tagid"):
            continue
        try:
            files.append(parse_debug_file(path, batch_build))
        except Exception:
            continue
    return files


def batch_commit_tag(files: list[DebugFileInfo]) -> str:
    """Derive batch-level commit tag from tagged files."""
    tags = {f.commit_tag_id for f in files if f.commit_tag_id}
    if len(tags) == 1:
        return tags.pop()
    if tags:
        return sorted(tags)[0]
    return ""


def batch_tag_signature(files: list[DebugFileInfo]) -> str:
    """Signature for grouping batches (tagged file masks)."""
    tagged = sorted({f.file_mask for f in files if f.commit_tag_id})
    return ",".join(tagged)


def batch_external_signature(files: list[DebugFileInfo]) -> str:
    """Signature for untagged external files in batch."""
    external = sorted({f.file_mask for f in files if not f.commit_tag_id})
    return ",".join(external)
