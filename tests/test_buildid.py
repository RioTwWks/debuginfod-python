"""Tests for build-id parsing."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest

from debuginfod import buildid


def _make_elf_with_gnu_note(build_id_bytes: bytes) -> bytes:
    """Minimal ELF64 header + one SHT_NOTE with GNU build-id."""
    # Very minimal: we use pyelftools in integration; here test parse_notes directly.
    namesz = 4
    descsz = len(build_id_bytes)
    name = b"GNU\x00"
    desc = build_id_bytes
    note = struct.pack("<III", namesz, descsz, buildid.NT_GNU_BUILD_ID)
    note += name + b"\x00" * (4 - len(name) % 4 if len(name) % 4 else 0)
    pad = (4 - descsz % 4) % 4
    note += desc + b"\x00" * pad
    return note


def test_parse_gnu_build_id() -> None:
    bid = bytes.fromhex("deadbeef" * 5)
    result = buildid.parse_notes(_make_elf_with_gnu_note(bid))
    assert result.kind == "gnu"
    assert result.value == bid.hex()


def test_go_canonical_id() -> None:
    raw = "test/build-id"
    expected = hashlib.sha256(raw.encode()).hexdigest()
    assert buildid.go_canonical_id(raw) == expected


def test_normalize() -> None:
    assert buildid.normalize("0xABCD-EF") == "abcdef"
    assert buildid.family_key("executable", "/usr/lib/.build-id/ab/cdef1234") == (
        "executable|/usr/lib/.build-id/*/*"
    )
    assert buildid.family_key("executable", "/tmp/demo_v3") == "executable|/tmp/demo"
