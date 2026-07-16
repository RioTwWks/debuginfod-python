"""Benchmark runner: compare debuginfod-go vs debuginfod-python."""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LatencyStats:
    mean_ms: float
    min_ms: float
    max_ms: float

    def to_dict(self) -> dict[str, float]:
        return {"mean": self.mean_ms, "min": self.min_ms, "max": self.max_ms}


@dataclass
class BinaryBenchmark:
    label: str
    file: str
    build_id: str
    file_size_bytes: int
    go_latency_ms: LatencyStats | None = None
    py_latency_ms: LatencyStats | None = None
    go_error: str = ""
    py_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "label": self.label,
            "file": self.file,
            "build_id": self.build_id,
            "file_size_bytes": self.file_size_bytes,
        }
        if self.go_latency_ms is not None:
            payload["go_latency_ms"] = self.go_latency_ms.to_dict()
        if self.py_latency_ms is not None:
            payload["py_latency_ms"] = self.py_latency_ms.to_dict()
        if self.go_error:
            payload["go_error"] = self.go_error
        if self.py_error:
            payload["py_error"] = self.py_error
        return payload


@dataclass
class BenchmarkReport:
    go_url: str
    py_url: str
    testdata: str
    runs: int
    binaries: list[BinaryBenchmark] = field(default_factory=list)
    python_storage_stats: dict[str, Any] | None = None
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        go_means = [
            b.go_latency_ms.mean_ms for b in self.binaries if b.go_latency_ms is not None
        ]
        py_means = [
            b.py_latency_ms.mean_ms for b in self.binaries if b.py_latency_ms is not None
        ]
        file_sizes = [b.file_size_bytes for b in self.binaries]
        py_stats = self.python_storage_stats or {}

        summary: dict[str, Any] = {
            "binary_count": len(self.binaries),
            "go_mean_latency_ms": statistics.mean(go_means) if go_means else None,
            "py_mean_latency_ms": statistics.mean(py_means) if py_means else None,
            "go_disk_bytes": sum(file_sizes),
            "py_original_bytes": py_stats.get("total_original_bytes", sum(file_sizes)),
            "py_stored_bytes": py_stats.get("total_stored_bytes", 0),
            "py_bytes_saved": py_stats.get("bytes_saved", 0),
            "py_compression_ratio": py_stats.get("compression_ratio", 1.0),
        }
        if summary["go_mean_latency_ms"] and summary["py_mean_latency_ms"]:
            summary["latency_ratio_py_vs_go"] = (
                summary["py_mean_latency_ms"] / summary["go_mean_latency_ms"]
            )
        if summary["go_disk_bytes"] and summary["py_stored_bytes"]:
            summary["storage_ratio_py_vs_go"] = (
                summary["py_stored_bytes"] / summary["go_disk_bytes"]
            )

        return {
            "finished_at": self.finished_at.replace(microsecond=0).isoformat(),
            "go_url": self.go_url,
            "py_url": self.py_url,
            "testdata": self.testdata,
            "runs": self.runs,
            "summary": summary,
            "binaries": [b.to_dict() for b in self.binaries],
            "python_storage_stats": self.python_storage_stats,
        }


def discover_binaries(testdata: Path, pattern: str = "demo_v*") -> list[Path]:
    """Find ELF binaries for benchmark in testdata directory."""
    if not testdata.is_dir():
        return []
    return sorted(testdata.glob(pattern))


def extract_build_id(path: Path) -> str:
    """Extract GNU/Go build-id from ELF (pyelftools, без зависимости от readelf)."""
    from debuginfod.buildid import BuildIDNotFoundError, from_path

    try:
        return from_path(path).value
    except BuildIDNotFoundError as exc:
        raise RuntimeError(
            f"no build-id in {path}; пересоберите с GNU build-id, например: "
            f"gcc -g -Wl,--build-id=sha1 -o {path.name} source.c "
            f"(или запустите scripts/generate_test_artifacts.py заново)"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"cannot parse ELF {path}: {exc}") from exc


def resolve_build_id(
    path: Path,
    client: httpx.Client | None = None,
    server_urls: list[str] | None = None,
) -> str:
    """Extract build-id locally or lookup via debuginfod /metadata?key=file."""
    try:
        return extract_build_id(path)
    except RuntimeError:
        if client is None or not server_urls:
            raise

    abs_path = str(path.resolve())
    for base in server_urls:
        try:
            resp = client.get(
                f"{base.rstrip('/')}/metadata",
                params={"key": "file", "value": abs_path},
            )
            if resp.status_code != 200:
                continue
            results = resp.json().get("results") or []
            if results:
                build_id = results[0].get("buildid") or results[0].get("build_id")
                if build_id:
                    logger.info("Resolved build-id for %s via %s metadata", path.name, base)
                    return str(build_id)
        except Exception as exc:
            logger.debug("metadata lookup failed for %s on %s: %s", abs_path, base, exc)

    raise RuntimeError(
        f"no build-id in {path} and not found via debuginfod metadata; "
        "пересоберите бинарники (scripts/generate_test_artifacts.py) "
        "и убедитесь, что оба сервера проиндексировали testdata"
    )


def _fetch_latency_ms(
    client: httpx.Client,
    base_url: str,
    build_id: str,
    runs: int,
) -> LatencyStats:
    endpoint = f"{base_url.rstrip('/')}/buildid/{build_id}/executable"
    samples: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        resp = client.get(endpoint)
        resp.raise_for_status()
        _ = resp.content
        samples.append((time.perf_counter() - started) * 1000.0)
    return LatencyStats(
        mean_ms=statistics.mean(samples),
        min_ms=min(samples),
        max_ms=max(samples),
    )


def run_benchmark(
    go_url: str,
    py_url: str,
    testdata: Path,
    runs: int = 3,
    pattern: str = "demo_v*",
    timeout_sec: float = 120.0,
) -> BenchmarkReport:
    """Run full comparison benchmark and return structured report."""
    binaries = discover_binaries(testdata, pattern)
    if not binaries:
        raise FileNotFoundError(f"no binaries matching {pattern!r} in {testdata}")

    report = BenchmarkReport(
        go_url=go_url,
        py_url=py_url,
        testdata=str(testdata),
        runs=runs,
    )

    with httpx.Client(timeout=timeout_sec) as client:
        try:
            py_stats_resp = client.get(f"{py_url.rstrip('/')}/stats")
            if py_stats_resp.status_code == 200:
                report.python_storage_stats = py_stats_resp.json()
        except Exception as exc:
            logger.warning("Failed to fetch Python /stats: %s", exc)

        for path in binaries:
            build_id = resolve_build_id(path, client, [py_url, go_url])
            entry = BinaryBenchmark(
                label=path.name,
                file=str(path.resolve()),
                build_id=build_id,
                file_size_bytes=path.stat().st_size,
            )
            try:
                entry.go_latency_ms = _fetch_latency_ms(client, go_url, build_id, runs)
            except Exception as exc:
                entry.go_error = str(exc)
            try:
                entry.py_latency_ms = _fetch_latency_ms(client, py_url, build_id, runs)
            except Exception as exc:
                entry.py_error = str(exc)
            report.binaries.append(entry)

    return report
