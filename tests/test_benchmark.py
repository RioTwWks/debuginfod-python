"""Unit tests for benchmark module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from debuginfod.benchmark import (
    BenchmarkReport,
    BinaryBenchmark,
    LatencyStats,
    discover_binaries,
    extract_build_id,
    resolve_build_id,
    run_benchmark,
)


def test_discover_binaries(tmp_path: Path) -> None:
    (tmp_path / "demo_v1").write_bytes(b"x")
    (tmp_path / "demo_v2").write_bytes(b"y")
    (tmp_path / "other").write_bytes(b"z")
    found = discover_binaries(tmp_path)
    assert [p.name for p in found] == ["demo_v1", "demo_v2"]


def test_benchmark_report_summary() -> None:
    from debuginfod.benchmark import BinaryBenchmark

    report = BenchmarkReport(
        go_url="http://go",
        py_url="http://py",
        testdata="/tmp",
        runs=3,
        python_storage_stats={
            "total_original_bytes": 1000,
            "total_stored_bytes": 200,
            "bytes_saved": 800,
            "compression_ratio": 0.2,
        },
    )
    report.binaries = [
        BinaryBenchmark(
            label="demo_v1",
            file="/tmp/demo_v1",
            build_id="abc",
            file_size_bytes=500,
            go_latency_ms=LatencyStats(10, 8, 12),
            py_latency_ms=LatencyStats(30, 25, 35),
        )
    ]
    summary = report.to_dict()["summary"]
    assert summary["go_mean_latency_ms"] == 10
    assert summary["py_mean_latency_ms"] == 30
    assert summary["latency_ratio_py_vs_go"] == 3.0
    assert summary["py_compression_ratio"] == 0.2


def test_extract_build_id_from_gcc_binary(tmp_path: Path) -> None:
    if not shutil_which("gcc"):
        pytest.skip("gcc not available")

    src = tmp_path / "t.c"
    src.write_text("int main(void) { return 0; }\n")
    binary = tmp_path / "demo_v1"
    subprocess.run(
        ["gcc", "-g", "-Wl,--build-id=sha1", "-o", str(binary), str(src)],
        check=True,
    )
    build_id = extract_build_id(binary)
    assert len(build_id) >= 8
    assert all(c in "0123456789abcdef" for c in build_id)


def shutil_which(cmd: str) -> str | None:
    import shutil

    return shutil.which(cmd)


def test_resolve_build_id_metadata_fallback(tmp_path: Path) -> None:
    binary = tmp_path / "demo_v1"
    binary.write_bytes(b"not elf")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metadata":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"buildid": "deadbeef" * 5, "type": "executable", "file": str(binary.resolve())}
                    ],
                    "complete": True,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://py")
    build_id = resolve_build_id(binary, client, ["http://py"])
    assert build_id == "deadbeef" * 5


@patch("debuginfod.benchmark.resolve_build_id", return_value="deadbeef")
@patch("debuginfod.benchmark._fetch_latency_ms")
def test_run_benchmark(
    mock_latency: MagicMock,
    _mock_build_id: MagicMock,
    tmp_path: Path,
) -> None:
    binary = tmp_path / "demo_v1"
    binary.write_bytes(b"\x7fELF")

    mock_latency.side_effect = [
        LatencyStats(5, 4, 6),
        LatencyStats(15, 12, 18),
    ]

    mock_client = MagicMock()
    mock_stats = MagicMock()
    mock_stats.status_code = 200
    mock_stats.json.return_value = {"total_stored_bytes": 100}
    mock_client.get.return_value = mock_stats

    with patch("debuginfod.benchmark.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value = mock_client
        report = run_benchmark("http://go", "http://py", tmp_path, runs=1)

    assert len(report.binaries) == 1
    assert report.binaries[0].go_latency_ms is not None
    assert report.binaries[0].py_latency_ms is not None
    payload = report.to_dict()
    assert payload["summary"]["binary_count"] == 1
