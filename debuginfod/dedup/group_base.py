"""Find an existing dedup base for singleton groups (debuginfod-go parity)."""

from __future__ import annotations

from debuginfod.db import Database, DedupFileRecord
from debuginfod.dedup.project_group import normalize_dedup_group_project


class DedupNotFoundError(LookupError):
    """No suitable base file exists for the target."""


def find_group_base(db: Database, target: DedupFileRecord) -> DedupFileRecord:
    """Return a done base file with the same stem and normalized project."""
    norm = normalize_dedup_group_project(target.project_name)
    for base in db.list_dedup_bases_by_stem(target.file_stem, 64):
        if base.id == target.id:
            continue
        if normalize_dedup_group_project(base.project_name) == norm:
            return base
    raise DedupNotFoundError(f"no base for stem={target.file_stem!r} project={target.project_name!r}")
