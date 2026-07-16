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
    go_build_id: str = ""
    py_build_id: str = ""
    py_stored_bytes: int = 0
    storage_kind: str = ""
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
        if self.go_build_id and self.go_build_id != self.build_id:
            payload["go_build_id"] = self.go_build_id
        if self.py_build_id and self.py_build_id != self.build_id:
            payload["py_build_id"] = self.py_build_id
        if self.py_stored_bytes:
            payload["py_stored_bytes"] = self.py_stored_bytes
        if self.storage_kind:
            payload["storage_kind"] = self.storage_kind
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
    warnings: list[str] = field(default_factory=list)
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        go_means = [
            b.go_latency_ms.mean_ms for b in self.binaries if b.go_latency_ms is not None
        ]
        py_means = [
            b.py_latency_ms.mean_ms for b in self.binaries if b.py_latency_ms is not None
        ]
        file_sizes = [b.file_size_bytes for b in self.binaries]
        py_stored_testdata = sum(b.py_stored_bytes for b in self.binaries)
        py_original_testdata = sum(file_sizes)

        summary: dict[str, Any] = {
            "binary_count": len(self.binaries),
            "go_mean_latency_ms": statistics.mean(go_means) if go_means else None,
            "py_mean_latency_ms": statistics.mean(py_means) if py_means else None,
            "go_disk_bytes": py_original_testdata,
            "py_original_bytes": py_original_testdata,
            "py_stored_bytes": py_stored_testdata,
            "py_bytes_saved": max(0, py_original_testdata - py_stored_testdata),
            "py_compression_ratio": (
                py_stored_testdata / py_original_testdata if py_original_testdata else 1.0
            ),
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
            "warnings": self.warnings,
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


def lookup_artifact_metadata(
    client: httpx.Client,
    base_url: str,
    path: Path,
) -> dict[str, Any] | None:
    """Lookup indexed artifact row from debuginfod /metadata."""
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
                    return row
        except Exception as exc:
            logger.debug("metadata %s %s on %s failed: %s", key, value, base_url, exc)
    return None


def lookup_build_id_metadata(
    client: httpx.Client,
    base_url: str,
    path: Path,
) -> str | None:
    """Lookup indexed build-id from debuginfod /metadata."""
    row = lookup_artifact_metadata(client, base_url, path)
    if row is None:
        return None
    build_id = row.get("buildid") or row.get("build_id")
    return str(build_id) if build_id else None


def resolve_build_id_for_server(
    client: httpx.Client,
    base_url: str,
    path: Path,
) -> str:
    """Resolve build-id for a specific debuginfod server."""
    build_id = lookup_build_id_metadata(client, base_url, path)
    if build_id:
        logger.info("Resolved build-id for %s via %s", path.name, base_url)
        return build_id
    return extract_build_id(path)


def resolve_build_id(
    path: Path,
    client: httpx.Client | None = None,
    server_urls: list[str] | None = None,
) -> str:
    """Resolve build-id (legacy): first server hit, else local ELF."""
    if client is not None and server_urls:
        for base in server_urls:
            build_id = lookup_build_id_metadata(client, base, path)
            if build_id:
                return build_id
    return extract_build_id(path)


def _estimate_py_stored_bytes(row: dict[str, Any] | None, file_size: int) -> int:
    if row is None:
        return file_size
    ratio = row.get("compression_ratio")
    if isinstance(ratio, (int, float)) and ratio > 0:
        return int(file_size * float(ratio))
    return file_size


def trigger_rescan(
    client: httpx.Client,
    base_url: str,
    admin_key: str = "",
) -> dict[str, Any]:
    """POST /admin/rescan on debuginfod server."""
    headers: dict[str, str] = {}
    if admin_key:
        headers["X-Admin-Token"] = admin_key
    try:
        resp = client.post(
            f"{base_url.rstrip('/')}/admin/rescan",
            headers=headers,
            timeout=300.0,
        )
        if resp.status_code == 200:
            try:
                return {"status": "ok", **resp.json()}
            except Exception:
                return {"status": "ok", "body": resp.text[:200]}
        return {"status": "error", "code": resp.status_code, "body": resp.text[:300]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _collect_warnings(report: BenchmarkReport) -> list[str]:
    warnings: list[str] = []
    go_rescan = report.rescan_results.get("go", {})
    if go_rescan.get("status") == "error":
        if go_rescan.get("code") == 401:
            warnings.append(
                "Go rescan: 401 unauthorized — задайте DEBUGINFOD_BENCHMARK_GO_ADMIN_KEY "
                "или тот же ключ, что у debuginfod-go (DEBUGINFOD_ADMIN_KEY)"
            )
        else:
            warnings.append(f"Go rescan не удался: {go_rescan.get('body') or go_rescan.get('message')}")

    py_stats = report.python_storage_stats or {}
    artifact_count = py_stats.get("artifact_count", 0)
    if artifact_count > len(report.binaries) * 3:
        warnings.append(
            f"Python-сервер проиндексировал {artifact_count} артефактов "
            f"(не только testdata). Для честного сравнения запустите с "
            "DEBUGINFOD_SCAN_PATH=testdata/versions"
        )

    mismatched = [
        b.label
        for b in report.binaries
        if b.go_build_id and b.py_build_id and b.go_build_id != b.py_build_id
    ]
    if mismatched:
        warnings.append(
            "Разные build-id на Go и Python для: "
            + ", ".join(mismatched[:5])
            + ("…" if len(mismatched) > 5 else "")
            + ". Выполните rescan на обоих серверах."
        )

    go_errors = sum(1 for b in report.binaries if b.go_error)
    if go_errors:
        warnings.append(f"Go latency: ошибки у {go_errors} из {len(report.binaries)} бинарников")

    return warnings


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
    go_admin_key: str = "",
    py_admin_key: str = "",
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
                "go": trigger_rescan(client, go_url, go_admin_key),
                "python": trigger_rescan(client, py_url, py_admin_key),
            }

        try:
            py_stats_resp = client.get(f"{py_url.rstrip('/')}/stats")
            if py_stats_resp.status_code == 200:
                report.python_storage_stats = py_stats_resp.json()
        except Exception as exc:
            logger.warning("Failed to fetch Python /stats: %s", exc)

        for path in binaries:
            file_size = path.stat().st_size
            go_build_id = resolve_build_id_for_server(client, go_url, path)
            py_build_id = resolve_build_id_for_server(client, py_url, path)
            py_meta = lookup_artifact_metadata(client, py_url, path)

            entry = BinaryBenchmark(
                label=path.name,
                file=str(path.resolve()),
                build_id=py_build_id,
                file_size_bytes=file_size,
                go_build_id=go_build_id,
                py_build_id=py_build_id,
                py_stored_bytes=_estimate_py_stored_bytes(py_meta, file_size),
                storage_kind=str(py_meta.get("storage_kind", "")) if py_meta else "",
            )
            try:
                entry.go_latency_ms = _fetch_latency_with_fallback(
                    client, go_url, path, go_build_id, runs
                )
            except Exception as exc:
                entry.go_error = str(exc)
            try:
                entry.py_latency_ms = _fetch_latency_with_fallback(
                    client, py_url, path, py_build_id, runs
                )
            except Exception as exc:
                entry.py_error = str(exc)
            report.binaries.append(entry)

        report.warnings = _collect_warnings(report)

    return report
