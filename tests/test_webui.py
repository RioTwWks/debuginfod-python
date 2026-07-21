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
    indexer = Indexer(db=db, scan_paths=[scan_dir])
    runner = ScanRunner(indexer=indexer, interval_sec=3600, metrics=metrics)
    runner.run_once()

    app = create_app(
        db=db,
        scan_runner=runner,
        cache_dir=cache_dir,
        metrics=metrics,
        ui_enabled=True,
    )
    return TestClient(app)


def test_ui_index(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui/")
    assert resp.status_code == 200
    assert "debuginfod-python" in resp.text


def test_ui_redirect(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/ui/"


def test_ui_static_assets(ui_client: TestClient) -> None:
    css = ui_client.get("/ui/static/app.css")
    js = ui_client.get("/ui/static/app.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "stat-card" in css.text


def test_ui_api_stats(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["artifacts_total"] >= 1
    assert "uptime_seconds" in data
    assert "cache_bytes" in data


def test_ui_api_search_buildid(ui_client: TestClient) -> None:
    stats = ui_client.get("/ui/api/stats").json()
    assert stats["artifacts_total"] >= 1

    all_results = ui_client.get("/ui/api/search")
    assert all_results.status_code == 200
    assert all_results.json()["count"] >= 1

    build_id = all_results.json()["results"][0]["buildid"]
    prefix = build_id[:4]
    filtered = ui_client.get("/ui/api/search", params={"q": prefix})
    assert filtered.status_code == 200
    assert filtered.json()["count"] >= 1


def test_ui_api_search_glob(ui_client: TestClient) -> None:
    resp = ui_client.get("/ui/api/search", params={"key": "glob", "value": "*hello*"})
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


def test_ui_api_search_file(ui_client: TestClient) -> None:
    meta = ui_client.get("/ui/api/search").json()
    file_path = meta["results"][0]["file"]
    resp = ui_client.get("/ui/api/search", params={"key": "file", "value": file_path})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_ui_api_search_errors(ui_client: TestClient) -> None:
    missing = ui_client.get("/ui/api/search", params={"key": "glob"})
    assert missing.status_code == 400

    unknown = ui_client.get("/ui/api/search", params={"key": "unknown"})
    assert unknown.status_code == 400
