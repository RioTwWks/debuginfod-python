"""Filesystem scanner that ingests ELF files into delta storage."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from elftools.elf.elffile import ELFFile

from debuginfod import buildid
from debuginfod.db import ArtifactRecord, Database, SourceRecord
from debuginfod.delta_store import DeltaStore

logger = logging.getLogger(__name__)


@dataclass
class ScanStats:
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    errors: int = 0
    artifacts_added: int = 0
    deltas_stored: int = 0
    full_stored: int = 0


class Indexer:
    """Walk scan paths and index ELF artifacts with xdelta3 storage."""

    def __init__(self, db: Database, store: DeltaStore, scan_paths: list[Path]) -> None:
        self.db = db
        self.store = store
        self.scan_paths = [p.resolve() for p in scan_paths]

    def scan(self) -> ScanStats:
        stats = ScanStats()
        elf_files: list[Path] = []
        source_files: list[Path] = []

        for root in self.scan_paths:
            if not root.exists():
                logger.warning("Scan path does not exist: %s", root)
                continue
            if root.is_file():
                if buildid.is_elf(root):
                    elf_files.append(root.resolve())
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    path = Path(dirpath) / name
                    stats.files_seen += 1
                    if buildid.is_elf(path):
                        elf_files.append(path.resolve())
                    elif name.endswith((".c", ".h", ".cpp", ".cc", ".hpp", ".s", ".S")):
                        source_files.append(path.resolve())

        # Индексируем ELF в порядке mtime — дельты строятся от более ранних версий.
        elf_files.sort(key=lambda p: p.stat().st_mtime_ns if hasattr(p.stat(), "st_mtime_ns") else int(p.stat().st_mtime * 1e9))

        for path in elf_files:
            try:
                self._index_elf_file(path, stats)
            except Exception:
                stats.errors += 1
                logger.exception("Failed to index %s", path)

        for path in source_files:
            try:
                self._index_source_file(path, stats)
            except Exception:
                stats.errors += 1
                logger.exception("Failed to index %s", path)

        return stats

    def _should_scan(self, path: Path) -> bool:
        try:
            st = path.stat()
        except OSError:
            return False
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        return self.db.needs_scan(str(path.resolve()), mtime_ns, st.st_size)

    def _mark_scanned(self, path: Path, kind: str) -> None:
        st = path.stat()
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        self.db.mark_scanned(str(path.resolve()), mtime_ns, st.st_size, kind)

    def _index_elf_file(self, path: Path, stats: ScanStats) -> None:
        if not self._should_scan(path):
            stats.files_skipped += 1
            return

        data = path.read_bytes()
        try:
            bid = buildid.from_bytes(data)
        except buildid.BuildIDNotFoundError:
            stats.files_skipped += 1
            self._mark_scanned(path, "elf")
            return

        from io import BytesIO

        elffile = ELFFile(BytesIO(data))
        artifact_type_name = buildid.artifact_type(str(path), elffile)
        fam = buildid.family_key(artifact_type_name, str(path.resolve()))

        blob, base_build_id = self.store.store_content(
            data,
            family_key=fam,
            build_id=bid.value,
        )

        record = ArtifactRecord(
            build_id=bid.value,
            artifact_type=artifact_type_name,
            file_path=str(path.resolve()),
            content_hash=blob.content_hash,
            storage_kind=blob.storage_kind,
            build_id_kind=bid.kind,
            raw_build_id=bid.raw,
            family_key=fam,
            base_build_id=base_build_id,
            mtime_ns=getattr(path.stat(), "st_mtime_ns", int(path.stat().st_mtime * 1_000_000_000)),
            original_size=blob.original_size,
            stored_size=blob.stored_size,
        )
        with self.db.transaction():
            self.db.upsert_artifact(record)
            self._mark_scanned(path, "elf")
            self._index_dwarf_sources(path, data, bid.value, stats)

        stats.files_indexed += 1
        stats.artifacts_added += 1
        if blob.storage_kind == "delta":
            stats.deltas_stored += 1
        else:
            stats.full_stored += 1

        logger.info(
            "Indexed %s build_id=%s type=%s storage=%s ratio=%.2f",
            path,
            bid.value[:12],
            artifact_type_name,
            blob.storage_kind,
            blob.stored_size / max(blob.original_size, 1),
        )

    def _index_dwarf_sources(
        self,
        elf_path: Path,
        data: bytes,
        build_id_value: str,
        stats: ScanStats,
    ) -> None:
        from io import BytesIO

        try:
            elffile = ELFFile(BytesIO(data))
            if not elffile.has_dwarf_info():
                return
            dwarf = elffile.get_dwarf_info()
        except Exception:
            return

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

                src = Path(source_path)
                if not src.is_file():
                    continue
                if not self._should_scan(src):
                    continue

                src_data = src.read_bytes()
                blob = self.store.store_full(src_data)
                fam = buildid.family_key("source", source_path)
                self.db.set_family_latest(fam, blob.content_hash, build_id_value)

                self.db.upsert_source(
                    SourceRecord(
                        build_id=build_id_value,
                        source_path=source_path,
                        file_path=str(src.resolve()),
                        content_hash=blob.content_hash,
                        storage_kind=blob.storage_kind,
                        mtime_ns=getattr(
                            src.stat(),
                            "st_mtime_ns",
                            int(src.stat().st_mtime * 1_000_000_000),
                        ),
                    )
                )
                self._mark_scanned(src, "source")
            except Exception:
                logger.debug("DWARF CU source extraction failed for %s", elf_path, exc_info=True)

    def _index_source_file(self, path: Path, stats: ScanStats) -> None:
        if not self._should_scan(path):
            stats.files_skipped += 1
            return
        # Standalone sources without build-id binding are indexed by path suffix only.
        data = path.read_bytes()
        blob = self.store.store_full(data)
        source_path = str(path.resolve())
        with self.db.transaction():
            self.db.upsert_source(
                SourceRecord(
                    build_id="",
                    source_path=source_path,
                    file_path=source_path,
                    content_hash=blob.content_hash,
                    storage_kind=blob.storage_kind,
                    mtime_ns=getattr(path.stat(), "st_mtime_ns", int(path.stat().st_mtime * 1_000_000_000)),
                )
            )
            self._mark_scanned(path, "source")
        stats.files_indexed += 1
