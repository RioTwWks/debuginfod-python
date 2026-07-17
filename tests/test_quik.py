"""Tests for Quik deduplication pipeline."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from debuginfod.quik.dedup import QuikDeduper
from debuginfod.quik.elf_comment import (
    parse_build_number_from_dir,
    parse_comment_text,
    file_mask_from_name,
)
from debuginfod.quik.grouping import BuildBatch, discover_build_batches, group_batches
from debuginfod.quik.master import select_master_batch


def test_parse_comment_text() -> None:
    raw = "GCC 11.2\nCompany\nDesc\n1.2.3.4\nabc123def4567890\nextra"
    info = parse_comment_text(raw)
    assert info is not None
    assert info.version == "1.2.3.4"
    assert info.commit_tag_id == "abc123def4567890"


def test_file_mask_from_name() -> None:
    mask, build = file_mask_from_name("libfoo.1.2.3.45.7zip.debug")
    assert mask == "libfoo"
    assert build == 45


def test_parse_build_number_from_dir() -> None:
    assert parse_build_number_from_dir("build_65_2025-03_03_19_32_08") == 65
    assert parse_build_number_from_dir("other") is None


def test_group_batches_and_master(tmp_path: Path) -> None:
    batches = [
        BuildBatch("QuikServer", tmp_path / "b1", 10, "tag1", "maskA", "", []),
        BuildBatch("QuikServer", tmp_path / "b2", 5, "tag1", "maskA", "", []),
        BuildBatch("QuikServer", tmp_path / "b3", 20, "tag1", "maskA", "", []),
    ]
    groups = group_batches(batches)
    assert len(groups) == 1
    master = select_master_batch(groups[0])
    assert master.build_number == 5


def test_quik_deduper_round_trip() -> None:
    try:
        subprocess.run(["xdelta3", "-V"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("xdelta3 not installed")

    master = b"M" * 4096 + b"master-content-here"
    candidate = master[:4000] + b"delta-bytes-changed" + master[4019:]
    deduper = QuikDeduper()
    result = deduper.create_verified_delta(master, candidate)
    assert result.verified is True
    assert result.patch_size < result.original_size
    assert result.content_hash == hashlib.sha256(candidate).hexdigest()


def test_discover_build_batches(tmp_path: Path) -> None:
    project = tmp_path / "QuikServer"
    batch = project / "build_1_2025-01_01"
    batch.mkdir(parents=True)
    (batch / "libx.1.0.0.1.debug").write_bytes(b"\x7fELF" + b"\x00" * 100)
    found = discover_build_batches("QuikServer", project)
    assert len(found) == 1
    assert found[0].build_number == 1
