"""PostgreSQL DDL for debuginfod metadata."""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs (
    content_hash TEXT PRIMARY KEY,
    storage_kind TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    original_size BIGINT NOT NULL,
    stored_size BIGINT NOT NULL,
    base_hash TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS artifacts (
    build_id TEXT NOT NULL,
    type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    storage_kind TEXT NOT NULL,
    build_id_kind TEXT NOT NULL DEFAULT 'gnu',
    raw_build_id TEXT NOT NULL DEFAULT '',
    family_key TEXT NOT NULL DEFAULT '',
    base_build_id TEXT NOT NULL DEFAULT '',
    mtime_ns BIGINT NOT NULL DEFAULT 0,
    project_name TEXT NOT NULL DEFAULT '',
    batch_name TEXT NOT NULL DEFAULT '',
    is_master BOOLEAN NOT NULL DEFAULT FALSE,
    file_mask TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (build_id, type)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_build_id ON artifacts(build_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_family ON artifacts(family_key);
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_name);

CREATE TABLE IF NOT EXISTS sources (
    build_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    storage_kind TEXT NOT NULL DEFAULT 'full',
    mtime_ns BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (build_id, source_path)
);
CREATE INDEX IF NOT EXISTS idx_sources_build_id ON sources(build_id);

CREATE TABLE IF NOT EXISTS families (
    family_key TEXT PRIMARY KEY,
    latest_content_hash TEXT NOT NULL,
    latest_build_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scanned_files (
    path TEXT PRIMARY KEY,
    mtime_ns BIGINT NOT NULL,
    size BIGINT NOT NULL,
    kind TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_stats (
    key TEXT PRIMARY KEY,
    value BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    name TEXT PRIMARY KEY,
    dedup_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    input_subpath TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS build_batches (
    id SERIAL PRIMARY KEY,
    project_name TEXT NOT NULL,
    batch_name TEXT NOT NULL,
    directory TEXT NOT NULL,
    build_number INTEGER NOT NULL,
    commit_tag_id TEXT NOT NULL DEFAULT '',
    is_master BOOLEAN NOT NULL DEFAULT FALSE,
    indexed_at TEXT NOT NULL DEFAULT '',
    UNIQUE(project_name, batch_name)
);
CREATE INDEX IF NOT EXISTS idx_build_batches_project ON build_batches(project_name);

CREATE TABLE IF NOT EXISTS dedup_manifest (
    id SERIAL PRIMARY KEY,
    project_name TEXT NOT NULL,
    batch_name TEXT NOT NULL,
    file_mask TEXT NOT NULL,
    master_build_number INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    master_hash TEXT NOT NULL,
    verify_ok BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(project_name, batch_name, file_mask)
);
"""
