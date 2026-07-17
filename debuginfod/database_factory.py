"""Open SQLite or PostgreSQL database from settings."""

from __future__ import annotations

from debuginfod.config import Settings
from debuginfod.db import Database


def open_database(settings: Settings) -> Database:
    """Return metadata store for configured backend."""
    return Database(settings.db_path, database_url=settings.database_url)
