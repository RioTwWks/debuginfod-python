"""Filesystem scanner — metadata index only (debuginfod-go parity)."""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from elftools.elf.elffile import ELFFile

from debuginfod import buildid
from debuginfod.db import ArtifactRecord, Database, SourceRecord

logger = logging.getLogger(__name__)


@dataclass
class ScanStats:
    files_seen: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    errors: int = 0
    artifacts_added: int = 0
    dedup_files_registered: int = 0
    dedup_files_compressed: int = 0
    dedup_errors: int = 0
    cancelled: bool = False


class Indexer:
    """Walk scan paths and index ELF artifacts by file path (no blob storage)."""

    def __init__(
        self,
        db: Database,
        scan_paths: list[Path],
        workers: int = 4,
        dedup_hook: object | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.db = db
        self.scan_paths = [p.resolve() for p in scan_paths]
        self.workers = max(1, workers)
        self.dedup_hook = dedup_hook
        self._stop = stop_event or threading.Event()

    def bind_stop_event(self, stop_event: threading.Event) -> None:
        self._stop = stop_event

    def request_stop(self) -> None:
        self._stop.set()

    def scan(self) -> ScanStats:
        stats = ScanStats()
        if self._stop.is_set():
            stats.cancelled = True
            return stats

        jobs: list[Path] = []
        for root in self.scan_paths:
            if self._stop.is_set():
                stats.cancelled = True
                return stats
            if not root.exists():
                logger.warning("Scan path does not exist: %s", root)
                continue
            if root.is_file():
                if buildid.is_elf(root):
                    stats.files_seen += 1
                    if self._should_scan(root):
                        jobs.append(root.resolve())
                    else:
                        stats.files_skipped += 1
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                if self._stop.is_set():
                    stats.cancelled = True
                    break
                for name in filenames:
                    path = Path(dirpath) / name
                    stats.files_seen += 1
                    if not buildid.is_elf(path):
                        continue
                    if not self._should_scan(path):
                        stats.files_skipped += 1
                        continue
                    jobs.append(path.resolve())
            if stats.cancelled:
                break

        if jobs and not self._stop.is_set():
            pool = ThreadPoolExecutor(max_workers=self.workers)
            futures = {pool.submit(self._index_elf_file, path): path for path in jobs}
            try:
                for future in as_completed(futures):
                    if self._stop.is_set():
                        stats.cancelled = True
                        for pending in futures:
                            pending.cancel()
                        break
                    path = futures[future]
                    try:
                        indexed = future.result()
                        if indexed:
                            stats.files_indexed += 1
                            stats.artifacts_added += 1
                        else:
                            stats.files_skipped += 1
                    except Exception:
                        stats.errors += 1
                        logger.exception("Failed to index %s", path)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        if self._stop.is_set():
            stats.cancelled = True
            return stats

        if self.dedup_hook is not None:
            try:
                self.dedup_hook.run_ingest_after_scan()
                dedup = self.db.dedup_stats()
                stats.dedup_files_registered = int(dedup.get("total_files", 0))
                stats.dedup_files_compressed = int(dedup.get("delta_files", 0))
            except Exception:
                stats.dedup_errors += 1
                logger.exception("Dedup ingest after scan failed")

        return stats

    def _should_scan(self, path: Path) -> bool:
        if self._stop.is_set():
            return False
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

    def _index_elf_file(self, path: Path) -> bool:
        if self._stop.is_set():
            return False

        try:
            bid = buildid.from_path(path)
        except buildid.BuildIDNotFoundError:
            self._mark_scanned(path, "no_build_id")
            logger.debug("skip elf without build-id: %s", path)
            return False

        with path.open("rb") as fh:
            elffile = ELFFile(fh)
            artifact_type_name = buildid.artifact_type(str(path), elffile)

        mtime_ns = getattr(path.stat(), "st_mtime_ns", int(path.stat().st_mtime * 1_000_000_000))
        record = ArtifactRecord(
            build_id=bid.value,
            artifact_type=artifact_type_name,
            file_path=str(path.resolve()),
            build_id_kind=bid.kind,
            raw_build_id=bid.raw,
            mtime_ns=mtime_ns,
        )
        with self.db.transaction():
            self.db.upsert_artifact(record)
            self._mark_scanned(path, "elf")
            if not self._stop.is_set():
                self._index_dwarf_sources(path, bid.value)

        logger.debug(
            "Indexed %s build_id=%s type=%s",
            path,
            bid.value[:12],
            artifact_type_name,
        )
        return True

    def _index_dwarf_sources(self, elf_path: Path, build_id_value: str) -> None:
        try:
            with elf_path.open("rb") as fh:
                elffile = ELFFile(fh)
                if not elffile.has_dwarf_info():
                    return
                dwarf = elffile.get_dwarf_info()
        except Exception:
            return

        seen: set[str] = set()
        for cu in dwarf.iter_CUs():
            if self._stop.is_set():
                return
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

                src = self._find_source_on_disk(source_path, elf_path)
                if src is None:
                    continue
                if not self._should_scan(src):
                    continue

                self.db.upsert_source(
                    SourceRecord(
                        build_id=build_id_value,
                        source_path=source_path,
                        file_path=str(src.resolve()),
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

    @staticmethod
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
