#!/usr/bin/env python3
"""
Compare debuginfod-go vs debuginfod-python (xdelta3) for storage and latency.

Usage:
  python scripts/compare_benchmark.py \
    --go-url http://localhost:8002 \
    --py-url http://localhost:8003 \
    --testdata testdata/versions
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx


def _extract_build_id(path: Path) -> str:
    result = subprocess.run(
        ["readelf", "-n", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if "Build ID:" in line:
            return line.split(":", 1)[1].strip().lower()
    raise RuntimeError(f"no build-id in {path}")


def _fetch_latency(client: httpx.Client, url: str, build_id: str, runs: int) -> list[float]:
    latencies: list[float] = []
    endpoint = f"{url.rstrip('/')}/buildid/{build_id}/executable"
    for _ in range(runs):
        start = time.perf_counter()
        resp = client.get(endpoint)
        resp.raise_for_status()
        _ = resp.content
        latencies.append(time.perf_counter() - start)
    return latencies


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark debuginfod-go vs debuginfod-python")
    parser.add_argument("--go-url", default="http://localhost:8002")
    parser.add_argument("--py-url", default="http://localhost:8003")
    parser.add_argument("--testdata", type=Path, default=Path("testdata/versions"))
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()

    if not args.testdata.is_dir():
        print(f"testdata not found: {args.testdata}", file=sys.stderr)
        print("Run: python scripts/generate_test_artifacts.py", file=sys.stderr)
        sys.exit(1)

    binaries = sorted(args.testdata.glob("demo_v*"))
    if not binaries:
        print("No demo_v* binaries in testdata", file=sys.stderr)
        sys.exit(1)

    report: dict[str, object] = {
        "go_url": args.go_url,
        "py_url": args.py_url,
        "binaries": [],
    }

    with httpx.Client(timeout=120.0) as client:
        py_stats = client.get(f"{args.py_url.rstrip('/')}/stats")
        if py_stats.status_code == 200:
            report["python_storage_stats"] = py_stats.json()

        for binary in binaries:
            build_id = _extract_build_id(binary)
            entry: dict[str, object] = {
                "file": str(binary),
                "build_id": build_id,
            }
            try:
                go_lat = _fetch_latency(client, args.go_url, build_id, args.runs)
                entry["go_latency_sec"] = {
                    "mean": statistics.mean(go_lat),
                    "min": min(go_lat),
                    "max": max(go_lat),
                }
            except Exception as exc:
                entry["go_error"] = str(exc)

            try:
                py_lat = _fetch_latency(client, args.py_url, build_id, args.runs)
                entry["py_latency_sec"] = {
                    "mean": statistics.mean(py_lat),
                    "min": min(py_lat),
                    "max": max(py_lat),
                }
            except Exception as exc:
                entry["py_error"] = str(exc)

            report["binaries"].append(entry)

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
