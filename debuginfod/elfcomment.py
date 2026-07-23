"""Parse ELF .comment for git commit tag (debuginfod-go/pkg/elfcomment)."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Any

from elftools.elf.elffile import ELFFile

_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHORT_COMMIT_RE = re.compile(r"^[0-9a-f]{7,39}$")
_PRODUCT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
_GIT_TAG_RE = re.compile(r"^(?:v?\d+\.\d+\.\d+(?:[-+][\w.-]+)?|release[-_][\w.-]+)$")


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


def from_path_or_empty(path: str | Path) -> str:
    """Return git commit from ELF .comment or empty string on failure."""
    try:
        return from_path(path)
    except (ValueError, OSError):
        return ""


def info_from_path(path: str | Path) -> dict[str, Any] | None:
    """Parse .comment into UI fields (debuginfod-go/pkg/elfcomment.InfoFromPath)."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    lines = _split_comment_lines(data)
    if not lines:
        return None

    info: dict[str, Any] = {"lines": lines}
    try:
        info["git_commit"] = from_bytes(data)
    except ValueError:
        pass

    for line in lines:
        lower = line.lower()
        if _is_toolchain_line(line):
            info.setdefault("toolchain", line)
            continue
        if lower.startswith("(c)"):
            info["copyright"] = line
            continue
        if _PRODUCT_VERSION_RE.fullmatch(line):
            info["product_version"] = line
            continue
        if _FULL_COMMIT_RE.fullmatch(line) or _SHORT_COMMIT_RE.fullmatch(line):
            continue
        if _prefixed_git_label(line):
            continue
        info.setdefault("labels", []).append(line)

    if info.get("labels") == []:
        info.pop("labels", None)
    return info


def _split_comment_lines(data: bytes) -> list[str]:
    raw = data.decode("utf-8", errors="replace").split("\x00")
    return [line.strip() for line in raw if line.strip()]


def _is_toolchain_line(line: str) -> bool:
    lower = line.lower()
    return any(lower.startswith(prefix) for prefix in ("gcc:", "clang:", "rustc:", "go version", "go build"))


def _is_noise_line(line: str) -> bool:
    lower = line.lower()
    if lower.startswith("(c)"):
        return True
    for noise in ("library", "quik server", "server"):
        if noise in lower and "/" not in line:
            return True
    return bool(_PRODUCT_VERSION_RE.fullmatch(line))


def _prefixed_git_label(line: str) -> bool:
    lower = line.lower()
    return any(lower.startswith(prefix) for prefix in ("tag:", "commit:", "build:", "git:"))


def _extract_tag(raw: str) -> str:
    lines = _split_comment_lines(raw.encode("utf-8", errors="replace")) if isinstance(raw, str) else []
    if not lines and isinstance(raw, str):
        lines = [ln.strip() for ln in raw.replace("\r\n", "\n").split("\n") if ln.strip()]

    for line in lines:
        lower = line.lower()
        for prefix in ("tag:", "commit:", "build:", "git:"):
            if lower.startswith(prefix):
                val = line[len(prefix) :].strip()
                if val:
                    return val

    for line in lines:
        if _is_toolchain_line(line) or _is_noise_line(line):
            continue
        if _FULL_COMMIT_RE.fullmatch(line):
            return line

    for line in lines:
        if _is_toolchain_line(line) or _is_noise_line(line):
            continue
        if _SHORT_COMMIT_RE.fullmatch(line):
            return line

    for line in lines:
        if _is_toolchain_line(line) or _is_noise_line(line):
            continue
        if _GIT_TAG_RE.fullmatch(line):
            return line

    raise ValueError("build label not found in .comment")
