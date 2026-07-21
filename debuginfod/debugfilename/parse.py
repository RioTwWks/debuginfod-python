"""Parse Quik .debug filenames and build_* directory names."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class InvalidFormatError(ValueError):
    """Filename does not match expected Quik .debug pattern."""


@dataclass(frozen=True)
class DebugFileInfo:
    filename: str
    stem: str
    version: str
    build_num: int


_HYPHEN_VERSION_RE = re.compile(r"^(.+)-(\d+)\.(\d+)\.(\d+)\.(\d+)\.debug$", re.IGNORECASE)


def metadata_from_name(name: str) -> DebugFileInfo:
    """Metadata for any *.debug (Quik templates or generic)."""
    base = Path(name).name
    if not base.lower().endswith(".debug"):
        raise InvalidFormatError("missing .debug suffix")

    try:
        return _parse_dot_stem(base)
    except InvalidFormatError:
        pass
    try:
        return _parse_hyphen_stem(base)
    except InvalidFormatError:
        pass

    stem = base[: -len(".debug")]
    return DebugFileInfo(filename=base, stem=stem, version="", build_num=0)


def _parse_dot_stem(base: str) -> DebugFileInfo:
    without = base[: -len(".debug")]
    parts = without.split(".")
    if len(parts) < 5:
        raise InvalidFormatError("need stem.M.m.p.BUILD")

    build_str = parts[-1]
    if not build_str.isdigit():
        raise InvalidFormatError("invalid build number")
    build_num = int(build_str)

    for segment in parts[-4:-1]:
        if not segment.isdigit():
            raise InvalidFormatError("invalid version segment")

    version = ".".join(parts[-4:-1])
    stem = ".".join(parts[:-4])
    if not stem or not version:
        raise InvalidFormatError("empty stem or version")

    return DebugFileInfo(
        filename=base,
        stem=stem,
        version=version,
        build_num=build_num,
    )


def _parse_hyphen_stem(base: str) -> DebugFileInfo:
    match = _HYPHEN_VERSION_RE.match(base)
    if match is None:
        raise InvalidFormatError("unsupported name pattern")
    build_num = int(match.group(5))
    stem = match.group(1)
    if not stem:
        raise InvalidFormatError("empty stem")
    version = f"{match.group(2)}.{match.group(3)}.{match.group(4)}"
    return DebugFileInfo(filename=base, stem=stem, version=version, build_num=build_num)


def parse_build_dir(dir_name: str) -> int:
    """Extract build number from build_482_2025-03-26_… directory name."""
    name = Path(dir_name).name
    if not name.startswith("build_"):
        raise InvalidFormatError("not a build_* directory")
    rest = name[6:]
    idx = rest.find("_")
    if idx <= 0:
        raise InvalidFormatError("missing build number")
    num_str = rest[:idx]
    if not num_str.isdigit():
        raise InvalidFormatError("invalid build directory number")
    return int(num_str)
