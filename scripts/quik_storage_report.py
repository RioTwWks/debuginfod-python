#!/usr/bin/env python3
"""Compare storage: Go (full files) vs Quik filediffs-style vs debuginfod-python."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def dir_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def count_debug_files(path: Path) -> int:
    return sum(1 for _ in path.rglob("*.debug") if _.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description="Quik storage comparison report")
    parser.add_argument("--testdata", type=Path, required=True, help="Root with build_* dirs")
    parser.add_argument("--python-blob-dir", type=Path, default=Path(".debuginfod-blobs"))
    parser.add_argument("--go-scan-path", type=Path, help="Go server scan path for comparison")
    args = parser.parse_args()

    if not args.testdata.is_dir():
        print(f"testdata not found: {args.testdata}", file=sys.stderr)
        sys.exit(1)

    original_bytes = dir_size(args.testdata)
    debug_count = count_debug_files(args.testdata)
    py_blob_bytes = dir_size(args.python_blob_dir)
    go_bytes = dir_size(args.go_scan_path) if args.go_scan_path else original_bytes

    report = {
        "testdata": str(args.testdata.resolve()),
        "debug_files": debug_count,
        "original_bytes": original_bytes,
        "go_equivalent_bytes": go_bytes,
        "python_blob_bytes": py_blob_bytes,
        "savings_vs_go": max(0, go_bytes - py_blob_bytes),
        "compression_ratio_vs_go": (py_blob_bytes / go_bytes) if go_bytes else 1.0,
        "notes": [
            "Go: stores each .debug file in full (no xdelta dedup).",
            "Python: master + verified xdelta3 deltas (DEVOPS-110).",
            "PowerShell filediffs: offline batch only, no debuginfod API.",
        ],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
