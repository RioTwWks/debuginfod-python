#!/usr/bin/env python3
"""CI smoke test: file-based index over generated demo ELF binaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from debuginfod.db import Database
from debuginfod.indexer import Indexer


def main() -> int:
    scan_root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/versions")
    db_path = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/ci-debuginfod.sqlite")

    db = Database(db_path)
    stats = Indexer(db=db, scan_paths=[scan_root]).scan()
    report = db.get_stats()

    if stats.files_indexed != 5:
        raise SystemExit(f"expected 5 indexed files, got {stats}")
    if report["artifact_count"] != 5:
        raise SystemExit(f"expected 5 artifacts, got {report}")
    if report["bytes_on_disk"] <= 0:
        raise SystemExit(f"expected bytes_on_disk > 0, got {report}")

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
