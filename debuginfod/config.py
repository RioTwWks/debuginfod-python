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
    cache_dir: Path
    rescan_interval_sec: int
    scan_workers: int
    xdelta3_path: str
    dwz_path: str
    objcopy_path: str
    log_level: str
    log_dir: str = ""
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
    dedup_projects: tuple[str, ...] = ()
    dedup_enabled: bool = False
    dedup_workers: int = 4
    dedup_strategy: str = "xdelta-decompress-dwz"
    dedup_compress_base: bool = True
    database_url: str = ""
    memory_max_ram_mb: int = 0
    memory_max_swap_mb: int = 0
    memory_min_available_mb: int = 512
    memory_dedup_peak_factor: float = 3.0
    memory_dedup_peak_factor_decompress: float = 10.0
    memory_dedup_serial_above_mb: int = 64
    memory_dedup_max_file_mb: int = 256
    memory_max_system_ram_pct: int = 65
    zabbix_key: str = ""
    scan_dwarf_max_mb: int = 32
    # legacy blob settings (kept for compatibility, unused in Go-parity mode)
    blob_dir: Path = Path(".debuginfod-blobs")
    reconstruct_cache_dir: Path = Path(".debuginfod-reconstruct-cache")
    delta_min_ratio: float = 0.85


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

    cache_dir = Path(
        os.getenv(
            "DEBUGINFOD_CACHE_DIR",
            os.getenv("DEBUGINFOD_RECONSTRUCT_CACHE_DIR", ".debuginfod-cache"),
        )
    )

    return Settings(
        db_path=Path(os.getenv("DEBUGINFOD_DB_PATH", "debuginfod.sqlite")),
        scan_paths=scan_paths,
        port=_env_int("DEBUGINFOD_PORT", 8003),
        cache_dir=cache_dir,
        rescan_interval_sec=_env_int("DEBUGINFOD_RESCAN_INTERVAL", 3600),
        scan_workers=_env_int("DEBUGINFOD_SCAN_WORKERS", 4),
        xdelta3_path=os.getenv("DEBUGINFOD_XDELTA_PATH", os.getenv("DEBUGINFOD_XDELTA3_PATH", "xdelta3")),
        dwz_path=os.getenv("DEBUGINFOD_DWZ_PATH", "dwz"),
        objcopy_path=os.getenv("DEBUGINFOD_OBJCOPY_PATH", "objcopy"),
        log_level=os.getenv("DEBUGINFOD_LOG_LEVEL", "info").lower(),
        log_dir=os.getenv("DEBUGINFOD_LOG_DIR", "").strip(),
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
        dedup_projects=dedup_projects,
        dedup_enabled=_env_bool("DEBUGINFOD_DEDUP_ENABLED", bool(dedup_projects)),
        dedup_workers=_env_int("DEBUGINFOD_DEDUP_WORKERS", 4),
        dedup_strategy=os.getenv("DEBUGINFOD_DEDUP_STRATEGY", "xdelta-decompress-dwz"),
        dedup_compress_base=_env_bool("DEBUGINFOD_DEDUP_COMPRESS_BASE", True),
        database_url=os.getenv("DEBUGINFOD_DATABASE_URL", ""),
        memory_max_ram_mb=_env_int("DEBUGINFOD_MEMORY_MAX_RAM_MB", 0),
        memory_max_swap_mb=_env_int("DEBUGINFOD_MEMORY_MAX_SWAP_MB", 0),
        memory_min_available_mb=_env_int("DEBUGINFOD_MEMORY_MIN_AVAILABLE_MB", 512),
        memory_dedup_peak_factor=_env_float("DEBUGINFOD_MEMORY_DEDUP_PEAK_FACTOR", 3.0),
        memory_dedup_peak_factor_decompress=_env_float(
            "DEBUGINFOD_MEMORY_DEDUP_PEAK_FACTOR_DECOMPRESS", 20.0
        ),
        memory_dedup_serial_above_mb=_env_int("DEBUGINFOD_MEMORY_DEDUP_SERIAL_ABOVE_MB", 64),
        memory_dedup_max_file_mb=_env_int("DEBUGINFOD_DEDUP_MAX_FILE_MB", 256),
        memory_max_system_ram_pct=_env_int("DEBUGINFOD_MEMORY_MAX_SYSTEM_RAM_PCT", 65),
        zabbix_key=os.getenv("DEBUGINFOD_ZABBIX_KEY", ""),
        scan_dwarf_max_mb=_env_int("DEBUGINFOD_SCAN_DWARF_MAX_MB", 32),
        blob_dir=Path(os.getenv("DEBUGINFOD_BLOB_DIR", ".debuginfod-blobs")),
        reconstruct_cache_dir=cache_dir,
        delta_min_ratio=_env_float("DEBUGINFOD_DELTA_MIN_RATIO", 0.85),
    )


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, Settings]:
    """Parse CLI arguments and merge with environment settings."""
    parser = argparse.ArgumentParser(description="debuginfod server (Python, Go-parity)")
    parser.add_argument("-d", "--db", dest="db_path", help="SQLite database path")
    parser.add_argument("-s", "--scan-path", dest="scan_path", help="Comma-separated scan roots")
    parser.add_argument("-p", "--port", type=int, help="HTTP port")
    parser.add_argument("--cache-dir", help="Cache directory for dedup restore")
    parser.add_argument("--xdelta3-path", help="Path to xdelta3 binary")
    parser.add_argument("--env-file", default=None, help="Alternate .env file")
    parser.add_argument("--no-scan", action="store_true", help="Disable background rescan")
    parser.add_argument("--no-ui", action="store_true", help="Disable Web UI dashboard")
    args = parser.parse_args(argv)

    settings = load_settings(args.env_file)

    overrides: dict[str, object] = {}
    if args.db_path:
        overrides["db_path"] = Path(args.db_path)
    if args.scan_path:
        overrides["scan_paths"] = [Path(p.strip()) for p in args.scan_path.split(",") if p.strip()]
    if args.port is not None:
        overrides["port"] = args.port
    if args.cache_dir:
        overrides["cache_dir"] = Path(args.cache_dir)
    if args.xdelta3_path:
        overrides["xdelta3_path"] = args.xdelta3_path
    if args.no_scan:
        overrides["scan_enabled"] = False
    if args.no_ui:
        overrides["ui_enabled"] = False

    if overrides:
        settings = Settings(**{**settings.__dict__, **overrides})

    return args, settings
