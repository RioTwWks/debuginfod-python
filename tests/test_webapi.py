"""HTTP API integration tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from debuginfod.db import Database
from debuginfod.indexer import Indexer
from debuginfod.scan_runner import ScanRunner
from debuginfod.webapi import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = Database(tmp_path / "api.sqlite")
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

    indexer = Indexer(db=db, scan_paths=[scan_dir])
    runner = ScanRunner(indexer=indexer, interval_sec=3600)
    runner.run_once()

    app = create_app(db=db, scan_runner=runner, cache_dir=cache_dir)
    return TestClient(app)


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").text == "ok"


def test_stats(client: TestClient) -> None:
    resp = client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "artifact_count" in data
    assert data["artifact_count"] >= 1


def test_executable_download(client: TestClient) -> None:
    meta = client.get("/metadata", params={"key": "glob", "value": "*hello*"})
    assert meta.status_code == 200
    results = meta.json()["results"]
    assert results
    build_id = results[0]["buildid"]
    resp = client.get(f"/buildid/{build_id}/executable")
    assert resp.status_code == 200
    assert resp.content[:4] == b"\x7fELF"
