"""Web UI route tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from debuginfod.db import Database
from debuginfod.indexer import Indexer
from debuginfod.metrics import MetricsCollector
from debuginfod.scan_runner import ScanRunner
from debuginfod.webapi import create_app


@pytest.fixture
def ui_client(tmp_path: Path) -> TestClient:
    db = Database(tmp_path / "ui.sqlite")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()

    src = tmp_path / "hello.c"
    src.write_text(
        '#include <stdio.h>\n'
        "int main(void) { printf(\"hello\\n\"); return 0; }\n"
    )
    binary = scan_dir / "hello"
    subprocess.run(["gcc", "-g", "-O0", "-o", str(binary), str(src)], check=True)

    metrics = MetricsCollector()
    indexer = Indexer(db=db, scan_paths=[scan_dir], metrics=metrics)
    runner = ScanRunner(indexer=indexer, interval_sec=3600, metrics=metrics)
    runner.run_once()

    app = create_app(
        db=db,
        scan_runner=runner,
        cache_dir=cache_dir,
        metrics=metrics,
        ui_enabled=True,
    )
    client = TestClient(app)
    client._metrics = metrics  # type: ignore[attr-defined]
    return client


def test_ui_index(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui/")
    assert resp.status_code == 200
    assert "debuginfod-python" in resp.text
    assert 'class="console"' in resp.text
    assert 'class="brand-mast"' in resp.text
    assert "Файлы .debug" in resp.text
    assert 'id="browse-tree"' in resp.text
    assert "debuginfo_2x.png" in resp.text


def test_ui_redirect(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/ui/"


def test_ui_static_assets(ui_client: TestClient) -> None:
    css = ui_client.get("/ui/static/app.css")
    js = ui_client.get("/ui/static/app.js")
    theme = ui_client.get("/ui/static/theme.js")
    logo = ui_client.get("/ui/static/debuginfo_2x.png")
    assert css.status_code == 200
    assert js.status_code == 200
    assert theme.status_code == 200
    assert logo.status_code == 200
    assert "brand-mast" in css.text
    assert "renderMastBadges" in js.text


def test_ui_api_stats(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["artifacts_total"] >= 1
    assert "uptime_seconds" in data
    assert "cache_bytes" in data
    assert data["scan_running"] is False


def test_ui_api_stats_scan_progress(ui_client: TestClient) -> None:
    metrics = ui_client._metrics  # type: ignore[attr-defined]
    metrics.begin_scan("indexing")
    metrics.update_indexing_progress(42, 100, 2)
    try:
        data = ui_client.get("/ui/api/stats").json()
        assert data["scan_running"] is True
        assert data["scan_phase"] == "indexing"
        assert data["scan_indexed"] == 42
        assert data["scan_skipped"] == 100
        assert data["scan_errors"] == 2
    finally:
        metrics.end_scan()


def test_ui_api_search_buildid(ui_client: TestClient) -> None:
    stats = ui_client.get("/ui/api/stats").json()
    assert stats["artifacts_total"] >= 1

    all_results = ui_client.get("/ui/api/search", params={"key": "buildid"})
    assert all_results.status_code == 200
    payload = all_results.json()
    assert payload["count"] >= 1
    assert payload["grouped"]
    assert payload["grouped"][0]["buildid"]

    build_id = payload["grouped"][0]["buildid"]
    prefix = build_id[:4]
    filtered = ui_client.get("/ui/api/search", params={"key": "buildid", "q": prefix})
    assert filtered.status_code == 200
    assert filtered.json()["count"] >= 1


def test_ui_api_search_path(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui/api/search", params={"key": "path", "value": "*hello*"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert data["results"][0]["relative_path"]

    browse = ui_client.get("/ui/api/search", params={"key": "path"})
    assert browse.status_code == 200
    assert browse.json()["count"] >= 1


def test_ui_api_artifact_detail(ui_client: TestClient) -> None:
    meta = ui_client.get("/ui/api/search", params={"key": "buildid"}).json()
    build_id = meta["grouped"][0]["buildid"]
    resp = ui_client.get(f"/ui/api/artifact/{build_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["buildid"] == build_id
    assert detail["entries"]


def test_ui_api_search_name(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui/api/search", params={"key": "name", "value": "hello"})
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


def test_ui_api_search_glob(ui_client: TestClient) -> None:
    meta = ui_client.get("/ui/api/search", params={"key": "buildid"}).json()
    file_path = meta["grouped"][0]["entries"][0]["file"]
    resp = ui_client.get("/ui/api/search", params={"key": "glob", "value": f"*{Path(file_path).name}"})
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


def test_ui_api_search_file(ui_client: TestClient) -> None:
    meta = ui_client.get("/ui/api/search", params={"key": "buildid"}).json()
    file_path = meta["grouped"][0]["entries"][0]["file"]
    resp = ui_client.get("/ui/api/search", params={"key": "file", "value": file_path})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_ui_api_search_errors(ui_client: TestClient) -> None:
    missing = ui_client.get("/ui/api/search", params={"key": "glob"})
    assert missing.status_code == 400

    missing_name = ui_client.get("/ui/api/search", params={"key": "name"})
    assert missing_name.status_code == 400

    unknown = ui_client.get("/ui/api/search", params={"key": "unknown"})
    assert unknown.status_code == 400
