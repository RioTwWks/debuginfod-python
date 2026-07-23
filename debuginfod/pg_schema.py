"""PostgreSQL DDL for debuginfod metadata."""

DEDUP_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS dedup_projects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS dedup_build_dirs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES dedup_projects(id),
    dir_path TEXT NOT NULL UNIQUE,
    dir_build_num INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error_msg TEXT NOT NULL DEFAULT '',
    processed_at BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_dedup_build_dirs_status ON dedup_build_dirs(status);

CREATE TABLE IF NOT EXISTS dedup_files (
    id SERIAL PRIMARY KEY,
    build_dir_id INTEGER NOT NULL REFERENCES dedup_build_dirs(id),
    file_path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    file_stem TEXT NOT NULL,
    version TEXT NOT NULL,
    file_build_num INTEGER NOT NULL,
    commit_tag TEXT NOT NULL DEFAULT '',
    storage_kind TEXT NOT NULL DEFAULT 'full',
    base_file_id INTEGER REFERENCES dedup_files(id),
    delta_path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    original_size BIGINT NOT NULL DEFAULT 0,
    compressed_size BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    error_msg TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dedup_files_status ON dedup_files(status);
CREATE INDEX IF NOT EXISTS idx_dedup_files_group ON dedup_files(file_stem, version, commit_tag);

CREATE TABLE IF NOT EXISTS dedup_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dedup_runs (
    id SERIAL PRIMARY KEY,
    finished_at TEXT NOT NULL,
    duration_ms BIGINT NOT NULL DEFAULT 0,
    project TEXT NOT NULL DEFAULT '',
    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
    build_dirs_processed INTEGER NOT NULL DEFAULT 0,
    files_registered INTEGER NOT NULL DEFAULT 0,
    files_compressed INTEGER NOT NULL DEFAULT 0,
    files_dedup_ref INTEGER NOT NULL DEFAULT 0,
    files_skipped INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    bytes_before BIGINT NOT NULL DEFAULT 0,
    bytes_after BIGINT NOT NULL DEFAULT 0
);
"""

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
    file_path TEXT NOT NULL DEFAULT '',
    archive_path TEXT NOT NULL DEFAULT '',
    member_path TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    storage_kind TEXT NOT NULL DEFAULT '',
    build_id_kind TEXT NOT NULL DEFAULT 'gnu',
    raw_build_id TEXT NOT NULL DEFAULT '',
    family_key TEXT NOT NULL DEFAULT '',
    base_build_id TEXT NOT NULL DEFAULT '',
    git_commit TEXT NOT NULL DEFAULT '',
    mtime_ns BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (build_id, type)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_build_id ON artifacts(build_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_family ON artifacts(family_key);
CREATE INDEX IF NOT EXISTS idx_artifacts_git_commit ON artifacts(git_commit);
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_name);

CREATE TABLE IF NOT EXISTS sources (
    build_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    file_path TEXT NOT NULL DEFAULT '',
    archive_path TEXT NOT NULL DEFAULT '',
    member_path TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
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
