"""Benchmark runner: compare debuginfod-go vs debuginfod-python."""

from __future__ import annotations

import logging
import re
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
    rescan_results: dict[str, Any] = field(default_factory=dict)
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
            "rescan_results": self.rescan_results,
        }


def _natural_sort_key(path: Path) -> tuple[str, int, str]:
    name = path.name
    match = re.search(r"(\d+)$", name)
    if match:
        return (name[: match.start()], int(match.group(1)), name)
    return (name, 0, name)


def resolve_testdata_path(path: str | Path, scan_paths: list[Path] | None = None) -> Path:
    """Resolve testdata directory (relative paths → cwd or scan_paths)."""
    candidate = Path(path)
    if candidate.is_dir():
        return candidate.resolve()

    cwd_candidate = Path.cwd() / candidate
    if cwd_candidate.is_dir():
        return cwd_candidate.resolve()

    if scan_paths:
        for root in scan_paths:
            if root.is_dir() and root.resolve() == cwd_candidate:
                return root.resolve()
            nested = root / candidate
            if nested.is_dir():
                return nested.resolve()
            if root.name == candidate.name and root.is_dir():
                return root.resolve()

    raise FileNotFoundError(
        f"testdata not found: {path} (cwd={Path.cwd()}); "
        "укажите абсолютный путь или запустите сервер из корня проекта"
    )


def discover_binaries(testdata: Path, pattern: str = "demo_v*") -> list[Path]:
    """Find ELF binaries for benchmark in testdata directory."""
    if not testdata.is_dir():
        return []
    return sorted(testdata.glob(pattern), key=_natural_sort_key)


def extract_build_id(path: Path) -> str:
    """Extract GNU/Go build-id from ELF (pyelftools)."""
    from debuginfod.buildid import BuildIDNotFoundError, from_path

    try:
        return from_path(path).value
    except BuildIDNotFoundError as exc:
        raise RuntimeError(
            f"no build-id in {path}; пересоберите: "
            f"python scripts/generate_test_artifacts.py -o {path.parent}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"cannot read {path}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"cannot parse ELF {path}: {exc}") from exc


def lookup_build_id_metadata(
    client: httpx.Client,
    base_url: str,
    path: Path,
) -> str | None:
    """Lookup indexed build-id from debuginfod /metadata."""
    abs_path = str(path.resolve())
    queries = [
        ("file", abs_path),
        ("file", str(path)),
        ("glob", f"*{path.name}"),
        ("glob", f"*/{path.name}"),
    ]
    for key, value in queries:
        try:
            resp = client.get(
                f"{base_url.rstrip('/')}/metadata",
                params={"key": key, "value": value},
            )
            if resp.status_code != 200:
                continue
            results = resp.json().get("results") or []
            for row in results:
                if row.get("type") != "executable":
                    continue
                file_path = row.get("file") or ""
                if key == "file" or file_path.endswith(path.name) or path.name in file_path:
                    build_id = row.get("buildid") or row.get("build_id")
                    if build_id:
                        return str(build_id)
        except Exception as exc:
            logger.debug("metadata %s %s on %s failed: %s", key, value, base_url, exc)
    return None


def resolve_build_id(
    path: Path,
    client: httpx.Client | None = None,
    server_urls: list[str] | None = None,
) -> str:
    """Resolve build-id: prefer server index, then local ELF."""
    if client is not None and server_urls:
        for base in server_urls:
            build_id = lookup_build_id_metadata(client, base, path)
            if build_id:
                logger.info("Resolved build-id for %s via %s metadata", path.name, base)
                return build_id

    try:
        return extract_build_id(path)
    except RuntimeError:
        pass

    if client is not None and server_urls:
        raise RuntimeError(
            f"build-id для {path.name} не найден ни в ELF, ни в индексе серверов; "
            "пересоберите testdata (scripts/generate_test_artifacts.py) "
            "и выполните rescan на обоих debuginfod"
        )

    raise RuntimeError(f"no build-id in {path}")


def trigger_rescan(client: httpx.Client, base_url: str) -> dict[str, Any]:
    """POST /admin/rescan on debuginfod server."""
    try:
        resp = client.post(f"{base_url.rstrip('/')}/admin/rescan", timeout=300.0)
        if resp.status_code == 200:
            try:
                return {"status": "ok", **resp.json()}
            except Exception:
                return {"status": "ok", "body": resp.text[:200]}
        return {"status": "error", "code": resp.status_code, "body": resp.text[:300]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


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


def _fetch_latency_with_fallback(
    client: httpx.Client,
    base_url: str,
    path: Path,
    build_id: str,
    runs: int,
) -> LatencyStats:
    try:
        return _fetch_latency_ms(client, base_url, build_id, runs)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        alt_id = lookup_build_id_metadata(client, base_url, path)
        if alt_id and alt_id != build_id:
            return _fetch_latency_ms(client, base_url, alt_id, runs)
        raise RuntimeError(
            f"404: сервер {base_url} не знает build-id {build_id[:16]}… для {path.name}; "
            "проверьте DEBUGINFOD_SCAN_PATH и выполните rescan"
        ) from exc


def run_benchmark(
    go_url: str,
    py_url: str,
    testdata: Path,
    runs: int = 3,
    pattern: str = "demo_v*",
    timeout_sec: float = 120.0,
    rescan: bool = True,
) -> BenchmarkReport:
    """Run full comparison benchmark and return structured report."""
    binaries = discover_binaries(testdata, pattern)
    if not binaries:
        raise FileNotFoundError(f"no binaries matching {pattern!r} in {testdata}")

    report = BenchmarkReport(
        go_url=go_url,
        py_url=py_url,
        testdata=str(testdata.resolve()),
        runs=runs,
    )

    with httpx.Client(timeout=timeout_sec) as client:
        if rescan:
            report.rescan_results = {
                "go": trigger_rescan(client, go_url),
                "python": trigger_rescan(client, py_url),
            }

        try:
            py_stats_resp = client.get(f"{py_url.rstrip('/')}/stats")
            if py_stats_resp.status_code == 200:
                report.python_storage_stats = py_stats_resp.json()
        except Exception as exc:
            logger.warning("Failed to fetch Python /stats: %s", exc)

        server_urls = [py_url, go_url]
        for path in binaries:
            build_id = resolve_build_id(path, client, server_urls)
            entry = BinaryBenchmark(
                label=path.name,
                file=str(path.resolve()),
                build_id=build_id,
                file_size_bytes=path.stat().st_size,
            )
            try:
                entry.go_latency_ms = _fetch_latency_with_fallback(
                    client, go_url, path, build_id, runs
                )
            except Exception as exc:
                entry.go_error = str(exc)
            try:
                entry.py_latency_ms = _fetch_latency_with_fallback(
                    client, py_url, path, build_id, runs
                )
            except Exception as exc:
                entry.py_error = str(exc)
            report.binaries.append(entry)

    return report
