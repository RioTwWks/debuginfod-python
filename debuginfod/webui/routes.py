"""Web UI routes and API handlers."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from debuginfod.benchmark import discover_binaries, resolve_testdata_path, run_benchmark
from debuginfod.benchmark_store import BenchmarkStore
from debuginfod.db import Database, MetadataResult
from debuginfod.webui.search import (
    artifact_detail_for_ui,
    enrich_flat_results,
    metadata_to_ui_row,
    search_buildid_grouped,
    search_name_for_ui,
    search_path_for_ui,
)
from debuginfod.metrics import MetricsCollector

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
STATS_CACHE_TTL_SEC = 15.0


class BenchmarkRunRequest(BaseModel):
    go_url: str = "http://localhost:8002"
    py_url: str = "http://localhost:8003"
    testdata: str = "testdata/versions"
    runs: int = Field(default=3, ge=1, le=20)
    pattern: str = "demo_v*"
    rescan: bool = True
    go_admin_key: str = ""
    py_admin_key: str = ""


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
    cache_dir: Path,
    scan_runner: object | None = None,
    scan_enabled: bool = True,
    dedup_enabled: bool = False,
    benchmark_store: BenchmarkStore | None = None,
    benchmark_go_url: str = "http://localhost:8002",
    benchmark_py_url: str = "http://localhost:8003",
    benchmark_testdata: Path | None = None,
    benchmark_go_admin_key: str = "",
    benchmark_py_admin_key: str = "",
    scan_paths: list[Path] | None = None,
) -> None:
    """Mount /ui dashboard routes on the FastAPI app."""
    router = APIRouter()
    bench_store = benchmark_store or BenchmarkStore()
    default_testdata = benchmark_testdata or Path("testdata/versions")
    stats_cache: dict[str, Any] = {"payload": None, "expires_at": 0.0}

    def _cached_stats_payload() -> dict[str, Any]:
        now = time.monotonic()
        cached = stats_cache.get("payload")
        if cached is not None and now < float(stats_cache.get("expires_at", 0.0)):
            return cached

        counts = db.count_stats()
        storage = db.get_stats()
        scan = metrics.last_scan()
        dedup_totals = db.dedup_storage_totals() if dedup_enabled else {}

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
            "cache_bytes": _dir_size(cache_dir),
            "index_bytes_on_disk": storage.get("bytes_on_disk", 0),
            "scan_enabled": scan_enabled,
            "dedup_enabled": dedup_enabled,
            "dedup_bytes_saved": int(dedup_totals.get("bytes_saved", 0)),
            "dedup_saved_percent": float(dedup_totals.get("saved_percent", 0.0)),
        }
        if scan.finished_at is not None:
            payload["last_scan_finished_at"] = scan.finished_at.replace(microsecond=0).isoformat()
        stats_cache["payload"] = payload
        stats_cache["expires_at"] = now + STATS_CACHE_TTL_SEC
        return payload

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
            "go_admin_key_configured": bool(benchmark_go_admin_key),
            "py_admin_key_configured": bool(benchmark_py_admin_key),
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
        try:
            testdata = resolve_testdata_path(body.testdata, scan_paths)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            go_key = body.go_admin_key or benchmark_go_admin_key
            py_key = body.py_admin_key or benchmark_py_admin_key
            report = await asyncio.to_thread(
                run_benchmark,
                body.go_url,
                body.py_url,
                testdata,
                body.runs,
                body.pattern,
                120.0,
                body.rescan,
                go_key,
                py_key,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Benchmark run failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        payload = report.to_dict()
        bench_store.save(payload)
        return {"report": payload}

    @router.get("/ui/api/projects", include_in_schema=False)
    async def ui_projects() -> dict[str, Any]:
        return {"projects": db.list_projects()}

    @router.get("/ui/api/projects/{project_name}/batches", include_in_schema=False)
    async def ui_project_batches(project_name: str) -> dict[str, Any]:
        return {
            "project": project_name,
            "batches": db.list_batches(project_name),
        }

    @router.get("/ui/api/stats", include_in_schema=False)
    async def ui_stats() -> dict[str, Any]:
        return await asyncio.to_thread(_cached_stats_payload)

    @router.get("/ui/api/artifact/{build_id}", include_in_schema=False)
    async def ui_artifact_detail(build_id: str) -> dict[str, Any]:
        roots = scan_paths or []
        detail = await asyncio.to_thread(artifact_detail_for_ui, db, build_id, roots)
        if detail is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return detail

    @router.get("/ui/api/scans", include_in_schema=False)
    async def ui_scans(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        index_scans = [
            {
                "id": r.id,
                "finished_at": r.finished_at,
                "duration_ms": r.duration_ms,
                "indexed": r.indexed,
                "skipped": r.skipped,
                "errors": r.errors,
                "artifacts_total": r.artifacts_total,
                "scanned_files": r.scanned_files,
                "bytes_on_disk": r.bytes_on_disk,
            }
            for r in db.list_scan_runs(limit)
        ]
        return {
            "index_summary": db.index_summary(),
            "index_scans": index_scans,
            "dedup_runs": db.list_dedup_runs(limit) if dedup_enabled else [],
            "dedup_totals": db.dedup_storage_totals() if dedup_enabled else {},
            "dedup_by_project": db.dedup_totals_by_project() if dedup_enabled else [],
            "dedup_enabled": dedup_enabled,
        }

    @router.post("/ui/api/rescan", include_in_schema=False)
    async def ui_rescan() -> dict[str, Any]:
        if scan_runner is None or not scan_enabled:
            raise HTTPException(status_code=503, detail="scan disabled")
        if getattr(scan_runner, "scanning", False):
            return {"status": "already_running"}
        stats = await asyncio.to_thread(scan_runner.run_once)
        return {
            "status": "ok",
            "indexed": stats.files_indexed,
            "skipped": stats.files_skipped,
            "errors": stats.errors,
            "cancelled": stats.cancelled,
        }

    @router.get("/ui/api/search", include_in_schema=False)
    async def ui_search(
        key: str = Query("buildid"),
        q: str = Query(""),
        value: str = Query(""),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        mode = key.lower().strip() or "buildid"
        roots = scan_paths or []

        if mode == "buildid":
            grouped = await asyncio.to_thread(
                search_buildid_grouped,
                db,
                q,
                limit,
                roots,
            )
            return {
                "key": mode,
                "query": q,
                "grouped": grouped,
                "count": len(grouped),
                "complete": True,
            }

        if mode == "path":
            search_value = (value or q).strip()
            results, complete, next_offset = await asyncio.to_thread(
                search_path_for_ui,
                db,
                roots,
                search_value,
                offset,
                limit,
            )
            payload: dict[str, Any] = {
                "key": mode,
                "value": search_value,
                "results": results,
                "count": len(results),
                "complete": complete,
            }
            if not complete:
                payload["next_offset"] = next_offset
            return payload

        if mode == "name":
            search_value = (value or q).strip()
            if not search_value:
                raise HTTPException(status_code=400, detail="value required for name search")
            results, complete, next_offset = await asyncio.to_thread(
                search_name_for_ui,
                db,
                roots,
                search_value,
                offset,
                limit,
            )
            payload = {
                "key": mode,
                "value": search_value,
                "results": results,
                "count": len(results),
                "complete": complete,
            }
            if not complete:
                payload["next_offset"] = next_offset
            return payload

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
            enriched = await asyncio.to_thread(
                enrich_flat_results,
                db,
                [
                    metadata_to_ui_row(record)
                    for record in results
                ],
                roots,
            )
            payload = {
                "key": mode,
                "value": search_value,
                "results": enriched,
                "count": len(enriched),
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
