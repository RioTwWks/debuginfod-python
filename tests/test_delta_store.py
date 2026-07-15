"""Tests for xdelta3 delta storage."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from debuginfod.db import Database
from debuginfod.delta_store import DeltaStore, XDeltaNotFoundError


@pytest.fixture
def xdelta_available() -> None:
    try:
        subprocess.run(["xdelta3", "-V"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("xdelta3 not installed")


@pytest.fixture
def store(tmp_path: Path, xdelta_available: None) -> DeltaStore:
    db = Database(tmp_path / "test.sqlite")
    return DeltaStore(
        db=db,
        blob_dir=tmp_path / "blobs",
        reconstruct_cache_dir=tmp_path / "cache",
        delta_min_ratio=0.95,
    )


def test_store_full_and_reconstruct(store: DeltaStore) -> None:
    data = b"full blob content " * 100
    record = store.store_full(data)
    assert record.storage_kind == "full"
    assert store.reconstruct(record.content_hash) == data


def test_delta_roundtrip(store: DeltaStore) -> None:
    base = b"version 1: " + b"x" * 4096
    new = b"version 2: " + b"x" * 4096 + b" small patch"

    base_record = store.store_full(base)
    delta = store.try_store_delta(new, base_record.content_hash, base)
    assert delta is not None
    assert delta.storage_kind == "delta"
    assert store.reconstruct(delta.content_hash) == new


def test_family_chain(store: DeltaStore) -> None:
    v1 = b"binary v1 " * 512
    v2 = b"binary v2 " * 512 + b"delta"
    v3 = b"binary v3 " * 512 + b"delta more"

    r1, _ = store.store_content(v1, "exec|/bin/demo", "build1")
    r2, base2 = store.store_content(v2, "exec|/bin/demo", "build2")
    r3, base3 = store.store_content(v3, "exec|/bin/demo", "build3")

    assert r1.storage_kind == "full"
    assert store.reconstruct(r2.content_hash) == v2
    assert store.reconstruct(r3.content_hash) == v3
    assert base2 == "build1"
    assert base3 == "build2"
