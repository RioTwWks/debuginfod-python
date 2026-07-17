"""Quik mass-build indexing: unzip, group, master+delta dedup, debuginfod index."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from debuginfod import buildid
from debuginfod.db import ArtifactRecord, Database
from debuginfod.delta_store import DeltaStore
from debuginfod.quik.archive import unzip_batch_directory
from debuginfod.quik.dedup import QuikDeduper
from debuginfod.quik.elf_comment import (
    batch_commit_tag,
    enumerate_debug_files,
)
from debuginfod.quik.grouping import BatchGroup, BuildBatch, discover_build_batches, group_batches
from debuginfod.quik.master import select_master_batch

logger = logging.getLogger(__name__)


@dataclass
class QuikScanStats:
    projects_seen: int = 0
    batches_seen: int = 0
    archives_unzipped: int = 0
    files_indexed: int = 0
    deltas_stored: int = 0
    full_stored: int = 0
    verify_passed: int = 0
    verify_failed: int = 0
    errors: int = 0
    bytes_saved: int = 0


@dataclass
class QuikIndexer:
    """Index QuikServer-style input trees with DEVOPS-110 deduplication."""

    db: Database
    store: DeltaStore
    input_path: Path
    work_path: Path
    dedup_projects: list[str]
    seven_zip_path: str = ""
    xdelta3_path: str = "xdelta3"
    lzma_enabled: bool = False
    remove_original_after_dedup: bool = True
    move_to_work: bool = True

    def scan(self) -> QuikScanStats:
        stats = QuikScanStats()
        if not self.dedup_projects:
            return stats

        self.input_path.mkdir(parents=True, exist_ok=True)
        self.work_path.mkdir(parents=True, exist_ok=True)
        deduper = QuikDeduper(self.xdelta3_path, self.lzma_enabled)

        all_batches: list[BuildBatch] = []
        for project in self.dedup_projects:
            project_dir = self.input_path / project
            if not project_dir.is_dir():
                logger.warning("Quik project dir missing: %s", project_dir)
                continue
            stats.projects_seen += 1
            self.db.upsert_project(project, dedup_enabled=True)

            for batch in discover_build_batches(project, project_dir):
                try:
                    stats.archives_unzipped += unzip_batch_directory(
                        batch.directory, self.seven_zip_path
                    )
                    batch.files = enumerate_debug_files(batch.directory)
                    batch.commit_tag_id = batch_commit_tag(batch.files)
                    all_batches.append(batch)
                    stats.batches_seen += 1
                except Exception:
                    stats.errors += 1
                    logger.exception("Failed to prepare batch %s", batch.directory)

        for group in group_batches(all_batches):
            try:
                self._process_group(group, deduper, stats)
            except Exception:
                stats.errors += 1
                logger.exception("Failed Quik group project=%s tag=%s", group.project, group.commit_tag_id)

        return stats

    def _process_group(
        self,
        group: BatchGroup,
        deduper: QuikDeduper,
        stats: QuikScanStats,
    ) -> None:
        master = select_master_batch(group)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

        for batch in group.batches:
            is_master = batch.build_number == master.build_number
            self.db.upsert_build_batch(
                project_name=batch.project,
                batch_name=batch.name,
                directory=str(batch.directory.resolve()),
                build_number=batch.build_number,
                commit_tag_id=batch.commit_tag_id or group.commit_tag_id,
                is_master=is_master,
                indexed_at=now,
            )

        masks = {f.file_mask for f in master.files}
        for file_mask in sorted(masks):
            master_file = self._find_file(master, file_mask)
            if master_file is None:
                continue
            try:
                master_data = master_file.path.read_bytes()
                master_bid = buildid.from_bytes(master_data)
            except Exception:
                stats.errors += 1
                logger.exception("Cannot read master %s", master_file.path)
                continue

            fam = self._family_key(group.project, group.commit_tag_id, file_mask)
            master_blob, _ = self.store.store_content(
                master_data,
                family_key=fam,
                build_id=master_bid.value,
            )
            self._index_artifact(
                master_file,
                master_data,
                master_bid,
                master_blob.content_hash,
                master_blob.storage_kind,
                group.project,
                master.name,
                is_master=True,
                base_build_id="",
                family_key=fam,
            )
            stats.files_indexed += 1
            stats.full_stored += 1
            self.db.upsert_dedup_manifest(
                project_name=group.project,
                batch_name=master.name,
                file_mask=file_mask,
                master_build_number=master.build_number,
                content_hash=master_blob.content_hash,
                master_hash=master_blob.content_hash,
                verify_ok=True,
            )

            for batch in group.batches:
                if batch.build_number == master.build_number:
                    continue
                candidate = self._find_file(batch, file_mask)
                if candidate is None:
                    continue
                try:
                    candidate_data = candidate.path.read_bytes()
                    candidate_bid = buildid.from_bytes(candidate_data)
                    delta_result = deduper.create_verified_delta(master_data, candidate_data)
                    stats.verify_passed += 1

                    delta_blob = self.store.store_delta_patch(
                        content_hash=delta_result.content_hash,
                        patch_data=delta_result.patch_data,
                        original_size=delta_result.original_size,
                        base_hash=master_blob.content_hash,
                    )
                    self._index_artifact(
                        candidate,
                        candidate_data,
                        candidate_bid,
                        delta_blob.content_hash,
                        "delta",
                        group.project,
                        batch.name,
                        is_master=False,
                        base_build_id=master_bid.value,
                        family_key=fam,
                    )
                    stats.files_indexed += 1
                    stats.deltas_stored += 1
                    stats.bytes_saved += delta_result.original_size - delta_result.patch_size
                    self.db.upsert_dedup_manifest(
                        project_name=group.project,
                        batch_name=batch.name,
                        file_mask=file_mask,
                        master_build_number=master.build_number,
                        content_hash=delta_blob.content_hash,
                        master_hash=master_blob.content_hash,
                        verify_ok=True,
                    )
                    self.db.increment_stat("dedup_verify_passed")

                    if self.remove_original_after_dedup:
                        candidate.path.unlink(missing_ok=True)

                    if self.move_to_work:
                        self._relocate_to_work(group.project, batch.name, candidate, candidate_data)

                except Exception:
                    stats.verify_failed += 1
                    stats.errors += 1
                    self.db.increment_stat("dedup_verify_failed")
                    logger.exception(
                        "Delta failed for %s/%s mask=%s",
                        group.project,
                        batch.name,
                        file_mask,
                    )

    def _find_file(self, batch: BuildBatch, file_mask: str) -> DebugFileInfo | None:
        for info in batch.files:
            if info.file_mask == file_mask:
                return info
        return None

    @staticmethod
    def _family_key(project: str, commit_tag: str, file_mask: str) -> str:
        return f"quik|{project}|{commit_tag or 'untagged'}|{file_mask}"

    def _index_artifact(
        self,
        file_info: DebugFileInfo,
        data: bytes,
        bid: buildid.BuildIDResult,
        content_hash: str,
        storage_kind: str,
        project: str,
        batch_name: str,
        is_master: bool,
        base_build_id: str,
        family_key: str,
    ) -> None:
        from io import BytesIO

        from elftools.elf.elffile import ELFFile

        elffile = ELFFile(BytesIO(data))
        artifact_type = buildid.artifact_type(str(file_info.path), elffile)
        mtime_ns = getattr(
            file_info.path.stat(),
            "st_mtime_ns",
            int(file_info.path.stat().st_mtime * 1_000_000_000),
        )
        blob = self.db.get_blob(content_hash)
        record = ArtifactRecord(
            build_id=bid.value,
            artifact_type=artifact_type,
            file_path=str(file_info.path.resolve()),
            content_hash=content_hash,
            storage_kind=storage_kind,  # type: ignore[arg-type]
            build_id_kind=bid.kind,
            raw_build_id=bid.raw,
            family_key=family_key,
            base_build_id=base_build_id,
            mtime_ns=mtime_ns,
            original_size=blob.original_size if blob else len(data),
            stored_size=blob.stored_size if blob else len(data),
            project_name=project,
            batch_name=batch_name,
            is_master=is_master,
            file_mask=file_info.file_mask,
        )
        with self.db.transaction():
            self.db.upsert_artifact(record)
            self.db.mark_scanned(str(file_info.path.resolve()), mtime_ns, len(data), "quik_debug")

    def _relocate_to_work(
        self,
        project: str,
        batch_name: str,
        file_info: DebugFileInfo,
        data: bytes,
    ) -> None:
        rel = Path(project) / batch_name / file_info.path.name
        dest = self.work_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(data)
        if file_info.path.exists() and file_info.path.resolve() != dest.resolve():
            try:
                shutil.move(str(file_info.path), str(dest))
            except OSError:
                logger.debug("Could not move %s to work path", file_info.path)
