"""Web UI routes and API handlers."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from debuginfod.db import Database, MetadataResult
from debuginfod.metrics import MetricsCollector

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


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
) -> None:
    """Mount /ui dashboard routes on the FastAPI app."""
    router = APIRouter()

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
