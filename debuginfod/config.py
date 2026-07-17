"""Configuration from environment variables and CLI."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    db_path: Path
    scan_paths: list[Path]
    port: int
    blob_dir: Path
    reconstruct_cache_dir: Path
    rescan_interval_sec: int
    delta_min_ratio: float
    xdelta3_path: str
    log_level: str
    host: str = "0.0.0.0"
    metadata_maxtime_sec: float = 5.0
    metadata_page_size: int = 100
    admin_key: str = ""
    scan_enabled: bool = True
    ui_enabled: bool = True
    benchmark_go_url: str = "http://localhost:8002"
    benchmark_testdata: Path = Path("testdata/versions")
    benchmark_go_admin_key: str = ""
    benchmark_py_admin_key: str = ""
    input_path: Path = Path("incoming")
    work_path: Path = Path("store")
    dedup_projects: tuple[str, ...] = ()
    dedup_enabled: bool = False
    seven_zip_path: str = ""
    delta_lzma: bool = False
    database_url: str = ""
    remove_original_after_dedup: bool = True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def load_settings(env_file: str | None = None) -> Settings:
    """Load settings from .env and environment."""
    if env_file:
        load_dotenv(env_file)
    else:
        load_dotenv()

    scan_raw = os.getenv("DEBUGINFOD_SCAN_PATH", ".")
    scan_paths = [Path(p.strip()) for p in scan_raw.split(",") if p.strip()]

    dedup_raw = os.getenv("DEBUGINFOD_DEDUP_PROJECTS", "")
    dedup_projects = tuple(p.strip() for p in dedup_raw.split(",") if p.strip())

    return Settings(
        db_path=Path(os.getenv("DEBUGINFOD_DB_PATH", "debuginfod.sqlite")),
        scan_paths=scan_paths,
        port=_env_int("DEBUGINFOD_PORT", 8003),
        blob_dir=Path(os.getenv("DEBUGINFOD_BLOB_DIR", ".debuginfod-blobs")),
        reconstruct_cache_dir=Path(
            os.getenv("DEBUGINFOD_RECONSTRUCT_CACHE_DIR", ".debuginfod-reconstruct-cache")
        ),
        rescan_interval_sec=_env_int("DEBUGINFOD_RESCAN_INTERVAL", 3600),
        delta_min_ratio=_env_float("DEBUGINFOD_DELTA_MIN_RATIO", 0.85),
        xdelta3_path=os.getenv("DEBUGINFOD_XDELTA3_PATH", "xdelta3"),
        log_level=os.getenv("DEBUGINFOD_LOG_LEVEL", "info").lower(),
        host=os.getenv("DEBUGINFOD_HOST", "0.0.0.0"),
        metadata_maxtime_sec=_env_float("DEBUGINFOD_METADATA_MAXTIME", 5.0),
        metadata_page_size=_env_int("DEBUGINFOD_METADATA_PAGE_SIZE", 100),
        admin_key=os.getenv("DEBUGINFOD_ADMIN_KEY", ""),
        scan_enabled=_env_bool("DEBUGINFOD_SCAN_ENABLED", True),
        ui_enabled=_env_bool("DEBUGINFOD_UI_ENABLED", True),
        benchmark_go_url=os.getenv("DEBUGINFOD_BENCHMARK_GO_URL", "http://localhost:8002"),
        benchmark_testdata=Path(os.getenv("DEBUGINFOD_BENCHMARK_TESTDATA", "testdata/versions")),
        benchmark_go_admin_key=os.getenv("DEBUGINFOD_BENCHMARK_GO_ADMIN_KEY", ""),
        benchmark_py_admin_key=os.getenv(
            "DEBUGINFOD_BENCHMARK_PY_ADMIN_KEY",
            os.getenv("DEBUGINFOD_ADMIN_KEY", ""),
        ),
        input_path=Path(os.getenv("DEBUGINFOD_INPUT_PATH", "incoming")),
        work_path=Path(os.getenv("DEBUGINFOD_WORK_PATH", "store")),
        dedup_projects=dedup_projects,
        dedup_enabled=_env_bool("DEBUGINFOD_DEDUP_ENABLED", bool(dedup_projects)),
        seven_zip_path=os.getenv("DEBUGINFOD_SEVEN_ZIP_PATH", ""),
        delta_lzma=_env_bool("DEBUGINFOD_DELTA_LZMA", False),
        database_url=os.getenv("DEBUGINFOD_DATABASE_URL", ""),
        remove_original_after_dedup=_env_bool("DEBUGINFOD_REMOVE_ORIGINAL_AFTER_DEDUP", True),
    )


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, Settings]:
    """Parse CLI arguments and merge with environment settings."""
    parser = argparse.ArgumentParser(description="debuginfod server (Python + xdelta3)")
    parser.add_argument("-d", "--db", dest="db_path", help="SQLite database path")
    parser.add_argument("-s", "--scan-path", dest="scan_path", help="Comma-separated scan roots")
    parser.add_argument("-p", "--port", type=int, help="HTTP port")
    parser.add_argument("--blob-dir", help="Directory for full blobs and deltas")
    parser.add_argument("--delta-min-ratio", type=float, help="Store delta if size < ratio * original")
    parser.add_argument("--xdelta3-path", help="Path to xdelta3 binary")
    parser.add_argument("--env-file", default=None, help="Alternate .env file")
    parser.add_argument("--no-scan", action="store_true", help="Disable background rescan")
    parser.add_argument("--no-ui", action="store_true", help="Disable Web UI dashboard")
    args = parser.parse_args(argv)

    settings = load_settings(args.env_file)

    # CLI overrides
    overrides: dict[str, object] = {}
    if args.db_path:
        overrides["db_path"] = Path(args.db_path)
    if args.scan_path:
        overrides["scan_paths"] = [Path(p.strip()) for p in args.scan_path.split(",") if p.strip()]
    if args.port is not None:
        overrides["port"] = args.port
    if args.blob_dir:
        overrides["blob_dir"] = Path(args.blob_dir)
    if args.delta_min_ratio is not None:
        overrides["delta_min_ratio"] = args.delta_min_ratio
    if args.xdelta3_path:
        overrides["xdelta3_path"] = args.xdelta3_path
    if args.no_scan:
        overrides["scan_enabled"] = False
    if args.no_ui:
        overrides["ui_enabled"] = False

    if overrides:
        settings = Settings(**{**settings.__dict__, **overrides})

    return args, settings
