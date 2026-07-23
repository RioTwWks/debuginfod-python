"""Web UI browse tree tests (Go parity)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from debuginfod.db import Database
from debuginfod.dedup.discover import discover
from debuginfod.indexer import Indexer
from debuginfod.metrics import MetricsCollector
from debuginfod.scan_runner import ScanRunner
from debuginfod.webapi import create_app


@pytest.fixture
def browse_client(tmp_path: Path) -> TestClient:
    db = Database(tmp_path / "browse.sqlite")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    scan_root = tmp_path / "scan"
    project_dir = scan_root / "Released" / "ProjA" / "build_1_2026-01-01"
    project_dir.mkdir(parents=True)

    debug_path = project_dir / "libfoo.so.debug"
    debug_path.write_bytes(b"fake-debug")

    discover(db, [scan_root], None)

    src = tmp_path / "hello.c"
    src.write_text("int main(void) { return 0; }\n")
    binary = project_dir / "hello"
    subprocess.run(["gcc", "-g", "-O0", "-o", str(binary), str(src)], check=True)

    metrics = MetricsCollector()
    indexer = Indexer(db=db, scan_paths=[scan_root])
    runner = ScanRunner(indexer=indexer, interval_sec=3600, metrics=metrics)
    runner.run_once()

    app = create_app(
        db=db,
        scan_runner=runner,
        cache_dir=cache_dir,
        metrics=metrics,
        ui_enabled=True,
        scan_paths=[scan_root],
    )
    return TestClient(app)


def test_ui_index_browse_section(browse_client: TestClient) -> None:
    resp = browse_client.get("/ui/")
    assert resp.status_code == 200
    assert "Файлы .debug" in resp.text
    assert 'id="browse-tree"' in resp.text
    assert "/zabbix" in resp.text


def test_ui_api_browse(browse_client: TestClient) -> None:
    resp = browse_client.get("/ui/api/browse")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert data["projects"]
    assert data["projects"][0]["name"] == "Released/ProjA"


def test_ui_api_rescan_accepted(browse_client: TestClient) -> None:
    resp = browse_client.post("/ui/api/rescan")
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"


def test_ui_dedup_download(tmp_path: Path) -> None:
    root = tmp_path / "store"
    build_dir = root / "Released" / "Qt_Library" / "qt" / "build_1_2026-01-01"
    build_dir.mkdir(parents=True)
    debug_path = build_dir / "libQt5Core.so.5.15.2.100.debug"
    content = b"fake-debug-content"
    debug_path.write_bytes(content)

    db = Database(tmp_path / "dedup-browse.sqlite")
    discover(db, [root], None)
    record = db.get_dedup_file_by_path(str(debug_path.resolve()))
    assert record is not None

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    app = create_app(
        db=db,
        scan_runner=None,
        cache_dir=cache_dir,
        ui_enabled=True,
        scan_paths=[root],
    )
    client = TestClient(app)

    browse = client.get("/ui/api/browse")
    assert browse.status_code == 200
    assert browse.json()["count"] == 1

    download = client.get(f"/ui/api/download/dedup/{record.id}")
    assert download.status_code == 200
    assert download.content == content
