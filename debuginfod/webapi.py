"""debuginfod-compatible HTTP API."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.requests import Request as StarletteRequest

from debuginfod import buildid
from debuginfod.benchmark_store import BenchmarkStore
from debuginfod.db import Database
from debuginfod.elfsection import extract_first
from debuginfod.metrics import MetricsCollector
from debuginfod.scan_runner import ScanRunner

logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^[A-Za-z0-9_.]+$")


class DedupRestorer(Protocol):
    def restore_to_cache(self, cache_dir: str | Path, file_path: str) -> str: ...


def _validate_source_path(path: str) -> None:
    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail="source path must be absolute")
    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="invalid source path")


def _validate_section_name(name: str) -> None:
    if not _SECTION_RE.match(name):
        raise HTTPException(status_code=400, detail="invalid section name")


def _resolve_file_path(
    cache_dir: Path,
    file_path: str,
    restorer: DedupRestorer | None,
) -> Path:
    if not file_path:
        raise HTTPException(status_code=404, detail="not found")
    if restorer is not None:
        try:
            resolved = restorer.restore_to_cache(cache_dir, file_path)
            return Path(resolved)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        except Exception as exc:
            logger.exception("Restore failed for %s", file_path)
            raise HTTPException(status_code=500, detail="restore failed") from exc
    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return path


def create_app(
    db: Database,
    scan_runner: ScanRunner | None,
    cache_dir: Path,
    dedup_restorer: DedupRestorer | None = None,
    scan_enabled: bool = True,
    dedup_enabled: bool = False,
    metadata_maxtime_sec: float = 5.0,
    metadata_page_size: int = 100,
    admin_key: str = "",
    ui_enabled: bool = True,
    metrics: MetricsCollector | None = None,
    benchmark_store: BenchmarkStore | None = None,
    benchmark_go_url: str = "http://localhost:8002",
    benchmark_py_url: str = "http://localhost:8003",
    benchmark_testdata: Path | None = None,
    benchmark_go_admin_key: str = "",
    benchmark_py_admin_key: str = "",
    scan_paths: list[Path] | None = None,
) -> FastAPI:
    app = FastAPI(title="debuginfod-python", version="0.2.0")
    collector = metrics or MetricsCollector()
    cache_root = cache_dir.resolve()

    @app.middleware("http")
    async def record_http_requests(request: StarletteRequest, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        collector.record_http()
        return response

    def _file_response(path: Path) -> FileResponse:
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=path.name,
        )

    @app.get("/healthz")
    async def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/readyz")
    async def readyz() -> PlainTextResponse:
        if scan_runner is not None and not scan_runner.ready and not db.is_ready():
            raise HTTPException(status_code=503, detail="not ready")
        return PlainTextResponse("ok")

    @app.get("/stats")
    async def stats() -> JSONResponse:
        """Storage statistics for comparison with debuginfod-go."""
        return JSONResponse(db.get_stats())

    @app.post("/admin/rescan")
    async def admin_rescan(request: Request) -> JSONResponse:
        if admin_key:
            token = request.headers.get("X-Admin-Token") or request.query_params.get("key", "")
            if token != admin_key:
                raise HTTPException(status_code=401, detail="unauthorized")
        if scan_runner is None:
            raise HTTPException(status_code=503, detail="scan disabled")
        result = scan_runner.run_once()
        return JSONResponse(
            {
                "status": "ok",
                "files_indexed": result.files_indexed,
                "files_skipped": result.files_skipped,
                "errors": result.errors,
            }
        )

    @app.post("/admin/dedup-backfill")
    async def admin_dedup_backfill(
        request: Request,
        project: str = Query(""),
        dry_run: bool = Query(False),
    ) -> JSONResponse:
        if admin_key:
            token = request.headers.get("X-Admin-Token") or request.query_params.get("key", "")
            if token != admin_key:
                raise HTTPException(status_code=401, detail="unauthorized")
        if dedup_restorer is None or not hasattr(dedup_restorer, "run_backfill"):
            raise HTTPException(status_code=503, detail="dedup disabled")
        result = dedup_restorer.run_backfill(project=project, dry_run=dry_run)  # type: ignore[attr-defined]
        return JSONResponse(
            {
                "status": "ok",
                "files_registered": result.files_registered,
                "files_compressed": result.files_compressed,
                "errors": result.errors,
                "bytes_before": result.bytes_before,
                "bytes_after": result.bytes_after,
                "dry_run": result.dry_run,
            }
        )

    @app.get("/metadata")
    async def metadata(
        key: str = Query(...),
        value: str = Query(...),
        offset: int = Query(0, ge=0),
        limit: int = Query(0, ge=0),
    ) -> JSONResponse:
        started = time.monotonic()
        page_size = limit or metadata_page_size
        results, complete, next_offset = db.search_metadata(key, value, offset, page_size)
        if time.monotonic() - started > metadata_maxtime_sec:
            raise HTTPException(status_code=504, detail="metadata query timeout")

        payload: dict[str, Any] = {
            "results": [
                {
                    "buildid": r.buildid,
                    "type": r.type,
                    "file": r.file,
                    **({"archive": r.archive} if r.archive else {}),
                    **({"buildid_kind": r.buildid_kind} if r.buildid_kind else {}),
                    **({"raw_buildid": r.raw_buildid} if r.raw_buildid else {}),
                }
                for r in results
            ],
            "complete": complete,
        }
        if not complete:
            payload["next_offset"] = next_offset
        return JSONResponse(payload)

    @app.get("/buildid/{build_id}/{kind}")
    async def buildid_kind_only(build_id: str, kind: str) -> Response:
        if kind in ("debuginfo", "executable"):
            return await _serve_artifact(build_id, kind)
        raise HTTPException(status_code=404, detail="not found")

    @app.get("/buildid/{build_id}/source/{source_path:path}")
    async def buildid_source(build_id: str, source_path: str) -> Response:
        full_path = "/" + source_path
        _validate_source_path(full_path)
        bid = buildid.normalize(build_id)

        record = db.get_source(bid, full_path)
        if record is None:
            record = db.get_source_by_suffix(full_path)
        if record is None:
            raise HTTPException(status_code=404, detail="not found")

        file_path = record.file_path or record.archive_path
        if not file_path:
            raise HTTPException(status_code=404, detail="not found")
        return _file_response(_resolve_file_path(cache_root, file_path, dedup_restorer))

    @app.get("/buildid/{build_id}/section/{section_name}")
    async def buildid_section(build_id: str, section_name: str) -> Response:
        _validate_section_name(section_name)
        bid = buildid.normalize(build_id)

        paths: list[Path] = []
        for artifact_type in ("debuginfo", "executable"):
            artifact = db.get_artifact(bid, artifact_type)
            if artifact is not None and artifact.file_path:
                try:
                    paths.append(_resolve_file_path(cache_root, artifact.file_path, dedup_restorer))
                except HTTPException:
                    logger.debug("Failed to load %s for section", artifact_type, exc_info=True)

        section_data = extract_first([p.read_bytes() for p in paths], section_name)
        if section_data is None:
            raise HTTPException(status_code=404, detail="not found")

        return StreamingResponse(
            iter([section_data]),
            media_type="application/octet-stream",
            headers={"Content-Length": str(len(section_data))},
        )

    async def _serve_artifact(build_id: str, artifact_type: str) -> Response:
        bid = buildid.normalize(build_id)
        artifact = db.get_artifact(bid, artifact_type)
        if artifact is None:
            raise HTTPException(status_code=404, detail="not found")
        if not artifact.file_path:
            raise HTTPException(status_code=404, detail="not found")
        return _file_response(_resolve_file_path(cache_root, artifact.file_path, dedup_restorer))

    if ui_enabled:
        from debuginfod.webui import register_webui

        register_webui(
            app,
            db=db,
            metrics=collector,
            cache_dir=cache_root,
            scan_runner=scan_runner,
            scan_enabled=scan_enabled,
            dedup_enabled=dedup_enabled,
            benchmark_store=benchmark_store,
            benchmark_go_url=benchmark_go_url,
            benchmark_py_url=benchmark_py_url,
            benchmark_testdata=benchmark_testdata,
            benchmark_go_admin_key=benchmark_go_admin_key,
            benchmark_py_admin_key=benchmark_py_admin_key,
            scan_paths=scan_paths,
        )

    return app
