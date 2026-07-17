"""xdelta3-based content-addressed blob storage with delta compression."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
from pathlib import Path

from debuginfod.db import BlobRecord, Database, StorageKind

logger = logging.getLogger(__name__)


class XDeltaNotFoundError(RuntimeError):
    """xdelta3 binary is missing or failed."""


class DeltaStore:
    """Store ELF payloads as full blobs or xdelta3 patches."""

    def __init__(
        self,
        db: Database,
        blob_dir: Path,
        reconstruct_cache_dir: Path,
        xdelta3_path: str = "xdelta3",
        delta_min_ratio: float = 0.85,
    ) -> None:
        self.db = db
        self.blob_dir = blob_dir
        self.reconstruct_cache_dir = reconstruct_cache_dir
        self.xdelta3_path = xdelta3_path
        self.delta_min_ratio = delta_min_ratio

        self.full_dir = blob_dir / "full"
        self.delta_dir = blob_dir / "delta"
        self.full_dir.mkdir(parents=True, exist_ok=True)
        self.delta_dir.mkdir(parents=True, exist_ok=True)
        self.reconstruct_cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def content_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _blob_path(self, kind: StorageKind, content_hash: str) -> Path:
        subdir = self.full_dir if kind == "full" else self.delta_dir
        return subdir / content_hash[:2] / content_hash[2:]

    def _run_xdelta(self, args: list[str]) -> None:
        cmd = [self.xdelta3_path, *args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise XDeltaNotFoundError(
                f"xdelta3 not found at {self.xdelta3_path!r}; install xdelta3 package"
            ) from exc
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise XDeltaNotFoundError(f"xdelta3 failed ({cmd}): {stderr}")

    def store_full(self, data: bytes) -> BlobRecord:
        """Store complete file content."""
        digest = self.content_hash(data)
        existing = self.db.get_blob(digest)
        if existing is not None:
            return existing

        path = self._blob_path("full", digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        record = BlobRecord(
            content_hash=digest,
            storage_kind="full",
            stored_path=str(path),
            original_size=len(data),
            stored_size=len(data),
        )
        self.db.upsert_blob(record)
        self.db.increment_stat("blobs_full")
        self.db.increment_stat("bytes_original", len(data))
        self.db.increment_stat("bytes_stored", len(data))
        return record

    def try_store_delta(
        self,
        data: bytes,
        base_hash: str,
        base_data: bytes,
    ) -> BlobRecord | None:
        """Try to store as xdelta3 patch against base content."""
        digest = self.content_hash(data)
        existing = self.db.get_blob(digest)
        if existing is not None:
            return existing

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_file = tmp_path / "base"
            new_file = tmp_path / "new"
            patch_file = tmp_path / "patch"
            base_file.write_bytes(base_data)
            new_file.write_bytes(data)

            self._run_xdelta(
                ["-e", "-s", str(base_file), str(new_file), str(patch_file)]
            )
            patch_data = patch_file.read_bytes()

        if len(patch_data) >= len(data) * self.delta_min_ratio:
            logger.debug(
                "Delta not beneficial for %s: patch=%d original=%d",
                digest[:12],
                len(patch_data),
                len(data),
            )
            return None

        path = self._blob_path("delta", digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(patch_data)

        record = BlobRecord(
            content_hash=digest,
            storage_kind="delta",
            stored_path=str(path),
            original_size=len(data),
            stored_size=len(patch_data),
            base_hash=base_hash,
        )
        self.db.upsert_blob(record)
        self.db.increment_stat("blobs_delta")
        self.db.increment_stat("bytes_original", len(data))
        self.db.increment_stat("bytes_stored", len(patch_data))
        self.db.increment_stat("bytes_saved", len(data) - len(patch_data))
        return record

    def store_delta_patch(
        self,
        content_hash: str,
        patch_data: bytes,
        original_size: int,
        base_hash: str,
    ) -> BlobRecord:
        """Store a pre-verified xdelta3 patch keyed by original content hash."""
        existing = self.db.get_blob(content_hash)
        if existing is not None:
            return existing

        path = self._blob_path("delta", content_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(patch_data)

        record = BlobRecord(
            content_hash=content_hash,
            storage_kind="delta",
            stored_path=str(path),
            original_size=original_size,
            stored_size=len(patch_data),
            base_hash=base_hash,
        )
        self.db.upsert_blob(record)
        self.db.increment_stat("blobs_delta")
        self.db.increment_stat("bytes_original", original_size)
        self.db.increment_stat("bytes_stored", len(patch_data))
        self.db.increment_stat("bytes_saved", original_size - len(patch_data))
        return record

    def store_content(
        self,
        data: bytes,
        family_key: str,
        build_id: str,
    ) -> tuple[BlobRecord, str]:
        """
        Store content using delta when a family predecessor exists.

        Returns (blob_record, base_build_id).
        """
        digest = self.content_hash(data)
        existing = self.db.get_blob(digest)
        if existing is not None:
            family = self.db.get_family_latest(family_key)
            base_build_id = family[1] if family and family[0] != digest else ""
            return existing, base_build_id

        base_build_id = ""
        family = self.db.get_family_latest(family_key)
        record: BlobRecord | None = None

        if family is not None:
            base_hash, base_build_id_prev = family
            if base_hash != digest:
                base_blob = self.db.get_blob(base_hash)
                if base_blob is not None:
                    base_data = self.reconstruct(base_hash)
                    record = self.try_store_delta(data, base_hash, base_data)
                    if record is not None:
                        base_build_id = base_build_id_prev

        if record is None:
            record = self.store_full(data)
            base_build_id = ""

        self.db.set_family_latest(family_key, digest, build_id)
        return record, base_build_id

    def reconstruct(self, content_hash: str) -> bytes:
        """Reconstruct original bytes from full blob or delta chain."""
        cache_path = self.reconstruct_cache_dir / content_hash[:2] / content_hash[2:]
        if cache_path.is_file():
            return cache_path.read_bytes()

        blob = self.db.get_blob(content_hash)
        if blob is None:
            raise FileNotFoundError(f"blob not found: {content_hash}")

        if blob.storage_kind == "full":
            data = Path(blob.stored_path).read_bytes()
        else:
            if not blob.base_hash:
                raise ValueError(f"delta blob {content_hash} missing base_hash")
            base_data = self.reconstruct(blob.base_hash)
            patch_path = Path(blob.stored_path)

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                base_file = tmp_path / "base"
                out_file = tmp_path / "out"
                base_file.write_bytes(base_data)
                self._run_xdelta(
                    ["-d", "-s", str(base_file), str(patch_path), str(out_file)]
                )
                data = out_file.read_bytes()

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        return data

    def reconstruct_to_path(self, content_hash: str, dest: Path) -> Path:
        """Reconstruct content and write to dest path."""
        data = self.reconstruct(content_hash)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def verify_xdelta3(self) -> None:
        """Ensure xdelta3 is available at startup."""
        self._run_xdelta(["-V"])
