#!/usr/bin/env python3
"""CLI wrapper around debuginfod.benchmark."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from debuginfod.benchmark import run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark debuginfod-go vs debuginfod-python")
    parser.add_argument("--go-url", default="http://localhost:8002")
    parser.add_argument("--py-url", default="http://localhost:8003")
    parser.add_argument("--testdata", type=Path, default=Path("testdata/versions"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--go-admin-key",
        default=os.getenv("DEBUGINFOD_BENCHMARK_GO_ADMIN_KEY", ""),
        help="X-Admin-Token for debuginfod-go rescan (or DEBUGINFOD_BENCHMARK_GO_ADMIN_KEY)",
    )
    parser.add_argument(
        "--py-admin-key",
        default=os.getenv(
            "DEBUGINFOD_BENCHMARK_PY_ADMIN_KEY",
            os.getenv("DEBUGINFOD_ADMIN_KEY", ""),
        ),
        help="X-Admin-Token for Python rescan",
    )
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
            go_admin_key=args.go_admin_key,
            py_admin_key=args.py_admin_key,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    payload = report.to_dict()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if payload.get("warnings"):
        print("\nWarnings:", file=sys.stderr)
        for warning in payload["warnings"]:
            print(f"  - {warning}", file=sys.stderr)


if __name__ == "__main__":
    main()
