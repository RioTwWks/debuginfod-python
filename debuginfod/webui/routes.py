"""Web UI routes and API handlers."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from debuginfod.benchmark import discover_binaries, run_benchmark
from debuginfod.benchmark_store import BenchmarkStore
from debuginfod.db import Database, MetadataResult
from debuginfod.metrics import MetricsCollector

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class BenchmarkRunRequest(BaseModel):
    go_url: str = "http://localhost:8002"
    py_url: str = "http://localhost:8003"
    testdata: str = "testdata/versions"
    runs: int = Field(default=3, ge=1, le=20)
    pattern: str = "demo_v*"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _artifact_to_dict(record: MetadataResult) -> dict[str, str]:
    payload: dict[str, str] = {
        "buildid": record.buildid,
        "type": record.type,
        "file": record.file,
    }
    if record.archive:
        payload["archive"] = record.archive
    if record.buildid_kind:
        payload["buildid_kind"] = record.buildid_kind
    if record.raw_buildid:
        payload["raw_buildid"] = record.raw_buildid
    return payload


def register_webui(
    app: FastAPI,
    db: Database,
    metrics: MetricsCollector,
    blob_dir: Path,
    reconstruct_cache_dir: Path,
    benchmark_store: BenchmarkStore | None = None,
    benchmark_go_url: str = "http://localhost:8002",
    benchmark_py_url: str = "http://localhost:8003",
    benchmark_testdata: Path | None = None,
    scan_paths: list[Path] | None = None,
) -> None:
    """Mount /ui dashboard routes on the FastAPI app."""
    router = APIRouter()
    bench_store = benchmark_store or BenchmarkStore()
    default_testdata = benchmark_testdata or Path("testdata/versions")

    @router.get("/ui", include_in_schema=False)
    async def redirect_ui() -> RedirectResponse:
        return RedirectResponse(url="/ui/", status_code=301)

    @router.get("/ui/", include_in_schema=False)
    @router.get("/ui/index.html", include_in_schema=False)
    async def serve_index() -> HTMLResponse:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=500, detail="index not found")
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @router.get("/ui/benchmark/", include_in_schema=False)
    @router.get("/ui/benchmark/index.html", include_in_schema=False)
    async def serve_benchmark() -> HTMLResponse:
        page = STATIC_DIR / "benchmark.html"
        if not page.is_file():
            raise HTTPException(status_code=500, detail="benchmark page not found")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @router.get("/ui/api/benchmark/config", include_in_schema=False)
    async def benchmark_config() -> dict[str, Any]:
        testdata = default_testdata
        discovered = discover_binaries(testdata)
        return {
            "go_url": benchmark_go_url,
            "py_url": benchmark_py_url,
            "testdata": str(testdata),
            "scan_paths": [str(p) for p in (scan_paths or [])],
            "binary_count": len(discovered),
            "binaries": [p.name for p in discovered],
        }

    @router.get("/ui/api/benchmark/last", include_in_schema=False)
    async def benchmark_last() -> dict[str, Any]:
        last = bench_store.last()
        if last is None:
            return {"report": None}
        return {"report": last}

    @router.get("/ui/api/benchmark/history", include_in_schema=False)
    async def benchmark_history() -> dict[str, Any]:
        return {"history": bench_store.history()}

    @router.post("/ui/api/benchmark/run", include_in_schema=False)
    async def benchmark_run(body: BenchmarkRunRequest) -> dict[str, Any]:
        testdata = Path(body.testdata)
        if not testdata.is_dir():
            raise HTTPException(status_code=400, detail=f"testdata not found: {testdata}")

        try:
            report = await asyncio.to_thread(
                run_benchmark,
                body.go_url,
                body.py_url,
                testdata,
                body.runs,
                body.pattern,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Benchmark run failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        payload = report.to_dict()
        bench_store.save(payload)
        return {"report": payload}

    @router.get("/ui/api/stats", include_in_schema=False)
    async def ui_stats() -> dict[str, Any]:
        counts = db.count_stats()
        storage = db.get_stats()
        scan = metrics.last_scan()

        payload: dict[str, Any] = {
            "uptime_seconds": metrics.uptime_seconds(),
            "artifacts_total": counts.artifacts_total,
            "artifacts_executable": counts.artifacts_executable,
            "artifacts_debuginfo": counts.artifacts_debuginfo,
            "sources_total": counts.sources_total,
            "scanned_files_total": counts.scanned_files_total,
            "last_scan_duration_ms": scan.duration_ms,
            "last_scan_indexed": scan.indexed,
            "last_scan_skipped": scan.skipped,
            "last_scan_errors": scan.errors,
            "http_requests_total": metrics.http_requests(),
            "cache_bytes": _dir_size(blob_dir) + _dir_size(reconstruct_cache_dir),
            "blobs_total": storage.get("artifact_count", 0),
            "bytes_saved": storage.get("bytes_saved", 0),
            "compression_ratio": storage.get("compression_ratio", 1.0),
        }
        if scan.finished_at is not None:
            payload["last_scan_finished_at"] = scan.finished_at.replace(microsecond=0).isoformat()
        return payload

    @router.get("/ui/api/search", include_in_schema=False)
    async def ui_search(
        key: str = Query("buildid"),
        q: str = Query(""),
        value: str = Query(""),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        mode = key.lower().strip() or "buildid"

        if mode == "buildid":
            results = db.search_buildid_for_ui(q, limit)
            return {
                "key": mode,
                "query": q,
                "results": [_artifact_to_dict(r) for r in results],
                "count": len(results),
                "complete": True,
            }

        if mode in {"glob", "file"}:
            search_value = (value or q).strip()
            if not search_value:
                raise HTTPException(status_code=400, detail=f"value required for {mode} search")
            results, complete, next_offset = db.search_metadata_ui(
                mode,
                search_value,
                offset=offset,
                limit=limit,
            )
            payload: dict[str, Any] = {
                "key": mode,
                "value": search_value,
                "results": [_artifact_to_dict(r) for r in results],
                "count": len(results),
                "complete": complete,
            }
            if not complete:
                payload["next_offset"] = next_offset
            return payload

        raise HTTPException(status_code=400, detail=f"unsupported search key: {mode}")

    app.include_router(router)
    app.mount(
        "/ui/static",
        StaticFiles(directory=STATIC_DIR),
        name="ui-static",
    )
