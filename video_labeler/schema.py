"""SQLite schema and idempotent migration runner."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

CURRENT_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _migration_v1(connection: sqlite3.Connection) -> None:
    # The statements are deliberately executed one at a time.  sqlite3.executescript
    # issues an implicit COMMIT, which would make a failed migration leave partial DDL.
    schema_script = (
        """
        CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            root_path TEXT NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) CHECK(created_at LIKE '%Z'),
            updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) CHECK(updated_at LIKE '%Z')
        );
        CREATE TABLE IF NOT EXISTS samples (
            sample_id TEXT PRIMARY KEY,
            dataset_id TEXT,
            relative_path TEXT NOT NULL,
            source_sha256 TEXT,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed','rejected')),
            schema_version INTEGER NOT NULL DEFAULT 1,
            revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL CHECK(created_at LIKE '%Z'),
            updated_at TEXT NOT NULL CHECK(updated_at LIKE '%Z'),
            FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS media_assets (
            asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id TEXT NOT NULL,
            modality TEXT NOT NULL CHECK(modality IN ('video','audio','transcript','image')),
            uri TEXT NOT NULL,
            duration_ms INTEGER,
            fps REAL,
            width INTEGER,
            height INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            source_sha256 TEXT,
            probe_status TEXT NOT NULL DEFAULT 'unknown',
            FOREIGN KEY(sample_id) REFERENCES samples(sample_id) ON DELETE CASCADE,
            UNIQUE(sample_id, modality, uri)
        );
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            sample_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            start_time_ms INTEGER,
            end_time_ms INTEGER,
            source TEXT NOT NULL CHECK(source IN ('human','model','imported')),
            confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','accepted','rejected')),
            annotator TEXT,
            revision INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(sample_id) REFERENCES samples(sample_id) ON DELETE CASCADE,
            CHECK(start_time_ms IS NULL OR start_time_ms >= 0),
            CHECK(end_time_ms IS NULL OR end_time_ms >= 0),
            CHECK(start_time_ms IS NULL OR end_time_ms IS NULL OR end_time_ms >= start_time_ms),
            CHECK(review_status = 'draft' OR (start_time_ms IS NOT NULL AND end_time_ms IS NOT NULL))
        );
        CREATE TABLE IF NOT EXISTS persons (
            person_record_id TEXT PRIMARY KEY,
            sample_id TEXT NOT NULL,
            person_id TEXT NOT NULL,
            track_id TEXT,
            age_group TEXT NOT NULL CHECK(age_group IN ('child','adult','elderly','unknown')),
            face_familiarity TEXT NOT NULL CHECK(face_familiarity IN ('familiar','stranger','unknown','not_visible')),
            body_reid_familiarity TEXT NOT NULL CHECK(body_reid_familiarity IN ('familiar','stranger','unknown','not_visible')),
            source TEXT NOT NULL CHECK(source IN ('human','model','imported')),
            confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            review_status TEXT NOT NULL DEFAULT 'draft' CHECK(review_status IN ('draft','accepted','rejected')),
            annotator TEXT,
            revision INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(sample_id) REFERENCES samples(sample_id) ON DELETE CASCADE,
            UNIQUE(sample_id, person_id)
        );
        CREATE TABLE IF NOT EXISTS evidence (
            evidence_id TEXT PRIMARY KEY,
            sample_id TEXT NOT NULL,
            modality TEXT NOT NULL CHECK(modality IN ('video','audio','transcript','image')),
            start_time_ms INTEGER,
            end_time_ms INTEGER,
            uri TEXT,
            text TEXT,
            source TEXT NOT NULL CHECK(source IN ('human','model','imported')),
            confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            FOREIGN KEY(sample_id) REFERENCES samples(sample_id) ON DELETE CASCADE,
            CHECK(start_time_ms IS NULL OR start_time_ms >= 0),
            CHECK(end_time_ms IS NULL OR end_time_ms >= 0),
            CHECK(start_time_ms IS NULL OR end_time_ms IS NULL OR end_time_ms >= start_time_ms)
        );
        CREATE TABLE IF NOT EXISTS model_predictions (
            prediction_id TEXT PRIMARY KEY,
            sample_id TEXT NOT NULL,
            task TEXT NOT NULL,
            label_json TEXT NOT NULL,
            model_name TEXT NOT NULL,
            model_version TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            created_at TEXT NOT NULL CHECK(created_at LIKE '%Z'),
            FOREIGN KEY(sample_id) REFERENCES samples(sample_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS annotation_revisions (
            revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            actor TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            before_json TEXT,
            after_json TEXT,
            app_version TEXT,
            created_at TEXT NOT NULL CHECK(created_at LIKE '%Z'),
            FOREIGN KEY(sample_id) REFERENCES samples(sample_id) ON DELETE CASCADE,
            UNIQUE(sample_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_samples_status ON samples(status);
        CREATE INDEX IF NOT EXISTS idx_media_assets_sample_id ON media_assets(sample_id);
        CREATE INDEX IF NOT EXISTS idx_media_assets_modality_uri ON media_assets(modality, uri);
        CREATE INDEX IF NOT EXISTS idx_events_sample_id ON events(sample_id);
        CREATE INDEX IF NOT EXISTS idx_events_status ON events(review_status);
        CREATE INDEX IF NOT EXISTS idx_persons_sample_id ON persons(sample_id);
        CREATE INDEX IF NOT EXISTS idx_persons_status ON persons(review_status);
        CREATE INDEX IF NOT EXISTS idx_evidence_sample_id ON evidence(sample_id);
        CREATE INDEX IF NOT EXISTS idx_predictions_sample_id ON model_predictions(sample_id);
        CREATE INDEX IF NOT EXISTS idx_revisions_sample_id ON annotation_revisions(sample_id);
        """
    )
    # sqlite3.complete_statement understands quoted semicolons and trigger bodies,
    # so future migrations can safely add those without changing this runner.
    statement_buffer = ""
    for character in schema_script:
        statement_buffer += character
        if character == ";" and sqlite3.complete_statement(statement_buffer):
            connection.execute(statement_buffer.strip())
            statement_buffer = ""
    if statement_buffer.strip():
        connection.execute(statement_buffer.strip())


def _upgrade_schema_migrations_table(connection: sqlite3.Connection) -> None:
    """Rebuild a pre-v1 marker table so its timestamp policy is enforced."""
    definition = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not definition:
        return
    sql = definition[0].upper()
    if "CHECK(APPLIED_AT LIKE '%Z')" in sql and "DEFAULT (STRFTIME" in sql:
        return
    connection.execute("ALTER TABLE schema_migrations RENAME TO schema_migrations_legacy")
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) CHECK(applied_at LIKE '%Z'))"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) SELECT version, CASE WHEN applied_at LIKE '%Z' THEN applied_at ELSE applied_at || 'Z' END FROM schema_migrations_legacy"
    )
    connection.execute("DROP TABLE schema_migrations_legacy")


def migrate_schema(connection: sqlite3.Connection, target_version: int = CURRENT_SCHEMA_VERSION) -> None:
    if target_version < 0 or target_version > CURRENT_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version: {target_version}")
    connection.execute("PRAGMA foreign_keys = ON")
    started_transaction = not connection.in_transaction
    try:
        if started_transaction:
            connection.execute("BEGIN")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) CHECK(applied_at LIKE '%Z'))")
        _upgrade_schema_migrations_table(connection)
        current = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
        if current > target_version:
            raise ValueError(f"database schema {current} is newer than target {target_version}")
        if current < 1 <= target_version:
            _migration_v1(connection)
            connection.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)", (1, _utc_now()))
        elif current >= 1 and target_version >= 1:
            # Re-run IF NOT EXISTS declarations to repair deleted tables/indexes even
            # when the migration marker is already present.
            _migration_v1(connection)
        if started_transaction:
            connection.commit()
    except Exception:
        if started_transaction:
            connection.rollback()
        raise


def initialize_schema(connection: sqlite3.Connection) -> None:
    migrate_schema(connection, CURRENT_SCHEMA_VERSION)
