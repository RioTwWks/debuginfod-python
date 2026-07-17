"""xdelta3 deduplication with mandatory round-trip verification."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DedupResult:
    """Result of delta encode + verify."""

    original_size: int
    patch_size: int
    patch_data: bytes
    verified: bool
    content_hash: str


class QuikDeduper:
    """Create and verify xdelta3 patches against a master file."""

    def __init__(self, xdelta3_path: str = "xdelta3", lzma_enabled: bool = False) -> None:
        self.xdelta3_path = xdelta3_path
        self.lzma_enabled = lzma_enabled

    @staticmethod
    def content_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _run_xdelta(self, args: list[str]) -> None:
        cmd = [self.xdelta3_path, *args]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"xdelta3 failed ({cmd}): {stderr}")

    def _maybe_lzma_compress(self, data: bytes) -> bytes:
        if not self.lzma_enabled:
            return data
        import lzma

        return lzma.compress(data)

    def _maybe_lzma_decompress(self, data: bytes) -> bytes:
        if not self.lzma_enabled:
            return data
        import lzma

        return lzma.decompress(data)

    def create_verified_delta(self, master: bytes, candidate: bytes) -> DedupResult:
        """
        Encode candidate as xdelta3 patch from master and verify round-trip.

        Raises RuntimeError if verification fails.
        """
        digest = self.content_hash(candidate)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            master_file = tmp_path / "master"
            candidate_file = tmp_path / "candidate"
            patch_file = tmp_path / "patch"
            restored_file = tmp_path / "restored"

            master_file.write_bytes(master)
            candidate_file.write_bytes(candidate)

            self._run_xdelta(
                ["-e", "-s", str(master_file), str(candidate_file), str(patch_file)]
            )
            patch_data = self._maybe_lzma_compress(patch_file.read_bytes())

            # Decode for verification (write patch without lzma for xdelta3)
            verify_patch = tmp_path / "verify.patch"
            verify_patch.write_bytes(
                patch_file.read_bytes() if self.lzma_enabled else patch_data
            )
            self._run_xdelta(
                ["-d", "-s", str(master_file), str(verify_patch), str(restored_file)]
            )
            restored = restored_file.read_bytes()

        verified = restored == candidate
        if not verified:
            raise RuntimeError(
                f"round-trip verify failed for hash {digest[:16]}… "
                f"(restored {len(restored)} bytes, expected {len(candidate)})"
            )

        logger.debug(
            "Verified delta %s: %d -> %d bytes (%.1f%%)",
            digest[:12],
            len(candidate),
            len(patch_data),
            100.0 * len(patch_data) / max(len(candidate), 1),
        )
        return DedupResult(
            original_size=len(candidate),
            patch_size=len(patch_data),
            patch_data=patch_data,
            verified=True,
            content_hash=digest,
        )
