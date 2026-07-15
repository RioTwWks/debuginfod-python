"""ELF section extraction for /buildid/.../section/ API."""

from __future__ import annotations

from io import BytesIO

from elftools.elf.elffile import ELFFile


def extract_section(data: bytes, section_name: str) -> bytes | None:
    """Extract raw section bytes by name from ELF data."""
    elffile = ELFFile(BytesIO(data))
    section = elffile.get_section_by_name(section_name)
    if section is None:
        return None
    if section["sh_type"] == "SHT_NOBITS":
        return None
    return section.data()


def extract_first(data_list: list[bytes], section_name: str) -> bytes | None:
    """Try each ELF blob until section is found."""
    for data in data_list:
        result = extract_section(data, section_name)
        if result is not None:
            return result
    return None
