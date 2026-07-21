"""Web UI benchmark page tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from debuginfod.benchmark import BenchmarkReport, BinaryBenchmark, LatencyStats
from debuginfod.benchmark_store import BenchmarkStore
from debuginfod.db import Database
from debuginfod.indexer import Indexer
from debuginfod.metrics import MetricsCollector
from debuginfod.scan_runner import ScanRunner
from debuginfod.webapi import create_app


@pytest.fixture
def bench_client(tmp_path: Path) -> TestClient:
    db = Database(tmp_path / "bench.sqlite")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    (scan_dir / "demo_v1").write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 60)

    metrics = MetricsCollector()
    indexer = Indexer(db=db, scan_paths=[scan_dir])
    runner = ScanRunner(indexer=indexer, interval_sec=3600, metrics=metrics)
    runner.run_once()

    app = create_app(
        db=db,
        scan_runner=runner,
        cache_dir=cache_dir,
        metrics=metrics,
        ui_enabled=True,
        benchmark_store=BenchmarkStore(),
        benchmark_testdata=scan_dir,
    )
    return TestClient(app)


def test_benchmark_page(bench_client: TestClient) -> None:
    resp = bench_client.get("/ui/benchmark/")
    assert resp.status_code == 200
    assert "Benchmark" in resp.text
    assert "benchmark.js" in resp.text


def test_benchmark_config(bench_client: TestClient) -> None:
    resp = bench_client.get("/ui/api/benchmark/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["binary_count"] >= 1
    assert "demo_v1" in data["binaries"]


def test_benchmark_run_mocked(bench_client: TestClient, tmp_path: Path) -> None:
    testdata = tmp_path / "versions"
    testdata.mkdir()
    (testdata / "demo_v1").write_bytes(b"\x7fELF")

    fake_report = BenchmarkReport(
        go_url="http://go",
        py_url="http://py",
        testdata=str(testdata),
        runs=1,
    )
    fake_report.binaries.append(
        BinaryBenchmark(
            label="demo_v1",
            file=str(testdata / "demo_v1"),
            build_id="abc123",
            file_size_bytes=100,
            go_latency_ms=LatencyStats(5, 4, 6),
            py_latency_ms=LatencyStats(20, 18, 22),
        )
    )

    with patch("debuginfod.webui.routes.run_benchmark", return_value=fake_report):
        resp = bench_client.post(
            "/ui/api/benchmark/run",
            json={
                "go_url": "http://go",
                "py_url": "http://py",
                "testdata": str(testdata),
                "runs": 1,
            },
        )
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert report["summary"]["binary_count"] == 1

    last = bench_client.get("/ui/api/benchmark/last")
    assert last.json()["report"]["summary"]["binary_count"] == 1

    history = bench_client.get("/ui/api/benchmark/history")
    assert len(history.json()["history"]) == 1


def test_benchmark_run_missing_testdata(bench_client: TestClient) -> None:
    resp = bench_client.post(
        "/ui/api/benchmark/run",
        json={
            "go_url": "http://go",
            "py_url": "http://py",
            "testdata": "/no/such/path",
            "runs": 1,
        },
    )
    assert resp.status_code == 400
