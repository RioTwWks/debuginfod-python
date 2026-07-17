"""Group Quik build batches for deduplication."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from debuginfod.quik.elf_comment import (
    DebugFileInfo,
    batch_commit_tag,
    batch_external_signature,
    batch_tag_signature,
    enumerate_debug_files,
    parse_build_number_from_dir,
)


@dataclass
class BuildBatch:
    """One build_* directory with unpacked debug files."""

    project: str
    directory: Path
    build_number: int
    commit_tag_id: str
    tag_signature: str
    external_signature: str
    files: list[DebugFileInfo] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.directory.name


@dataclass(frozen=True)
class BatchGroup:
    """Batches sharing the same commit tag + file set."""

    project: str
    commit_tag_id: str
    tag_signature: str
    external_signature: str
    batches: tuple[BuildBatch, ...]


def discover_build_batches(project: str, project_dir: Path) -> list[BuildBatch]:
    """Find build_* directories under a project input folder."""
    batches: list[BuildBatch] = []
    if not project_dir.is_dir():
        return batches

    for entry in sorted(project_dir.iterdir()):
        if not entry.is_dir():
            continue
        build_number = parse_build_number_from_dir(entry.name)
        if build_number is None:
            continue
        files = enumerate_debug_files(entry)
        batches.append(
            BuildBatch(
                project=project,
                directory=entry,
                build_number=build_number,
                commit_tag_id=batch_commit_tag(files),
                tag_signature=batch_tag_signature(files),
                external_signature=batch_external_signature(files),
                files=files,
            )
        )
    return batches


def group_batches(batches: list[BuildBatch]) -> list[BatchGroup]:
    """Group batches by project + tag signature + external signature."""
    buckets: dict[tuple[str, str, str, str], list[BuildBatch]] = {}
    for batch in batches:
        key = (
            batch.project,
            batch.commit_tag_id,
            batch.tag_signature,
            batch.external_signature,
        )
        buckets.setdefault(key, []).append(batch)

    groups: list[BatchGroup] = []
    for (project, commit_tag, tag_sig, ext_sig), items in buckets.items():
        if not items:
            continue
        groups.append(
            BatchGroup(
                project=project,
                commit_tag_id=commit_tag,
                tag_signature=tag_sig,
                external_signature=ext_sig,
                batches=tuple(sorted(items, key=lambda b: b.build_number)),
            )
        )
    return groups
