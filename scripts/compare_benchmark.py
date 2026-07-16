#!/usr/bin/env python3
"""CLI wrapper around debuginfod.benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from debuginfod.benchmark import run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark debuginfod-go vs debuginfod-python")
    parser.add_argument("--go-url", default="http://localhost:8002")
    parser.add_argument("--py-url", default="http://localhost:8003")
    parser.add_argument("--testdata", type=Path, default=Path("testdata/versions"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--no-rescan", action="store_true", help="Skip POST /admin/rescan before benchmark")
    args = parser.parse_args()

    if not args.testdata.is_dir():
        print(f"testdata not found: {args.testdata}", file=sys.stderr)
        print("Run: python scripts/generate_test_artifacts.py", file=sys.stderr)
        sys.exit(1)

    try:
        report = run_benchmark(
            go_url=args.go_url,
            py_url=args.py_url,
            testdata=args.testdata,
            runs=args.runs,
            rescan=not args.no_rescan,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
