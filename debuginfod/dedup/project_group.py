"""Dedup project path normalization."""

from __future__ import annotations

import re

_VERSION_SEGMENT = re.compile(r"^\d+(\.\d+)*$")


def normalize_dedup_group_project(project: str) -> str:
    """Collapse trailing version path segments for grouping key."""
    project = project.strip().replace("\\", "/")
    if not project or project == ".":
        return project
    parts = project.split("/")
    while len(parts) > 1 and _VERSION_SEGMENT.match(parts[-1]):
        parts = parts[:-1]
    return "/".join(parts)
