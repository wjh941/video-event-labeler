import sqlite3

import pytest

from video_labeler.schema import CURRENT_SCHEMA_VERSION, initialize_schema, migrate_schema


EXPECTED_TABLES = {"datasets", "samples", "media_assets", "events", "persons", "evidence", "model_predictions", "annotation_revisions", "schema_migrations"}


def test_initialize_schema_is_idempotent_and_enables_foreign_keys():
    connection = sqlite3.connect(":memory:")
    initialize_schema(connection)
    first = connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    initialize_schema(connection)
    second = connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert first == second
    assert {name for name, _ in first} >= EXPECTED_TABLES
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [(CURRENT_SCHEMA_VERSION,)]


def test_schema_has_required_indexes_and_foreign_keys():
    connection = sqlite3.connect(":memory:")
    initialize_schema(connection)
    indexes = {row[1] for row in connection.execute("PRAGMA index_list('media_assets')").fetchall()}
    assert any("sample" in index for index in indexes)
    assert any("modality" in index for index in indexes)
    assert any("sample" in row[1] for row in connection.execute("PRAGMA index_list('events')"))
    assert any("sample" in row[1] for row in connection.execute("PRAGMA index_list('persons')"))
    for table in EXPECTED_TABLES - {"schema_migrations"}:
        foreign_keys = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        if table != "datasets":
            assert foreign_keys, table


def test_migrate_schema_rejects_future_version():
    connection = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        migrate_schema(connection, CURRENT_SCHEMA_VERSION + 1)


def test_dataset_minimal_insert_uses_utc_timestamp_defaults():
    connection = sqlite3.connect(":memory:")
    initialize_schema(connection)
    connection.execute("INSERT INTO datasets(dataset_id, root_path) VALUES (?, ?)", ("d1", "."))
    created, updated = connection.execute("SELECT created_at, updated_at FROM datasets").fetchone()
    assert created.endswith("Z")
    assert updated.endswith("Z")
