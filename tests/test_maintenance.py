from __future__ import annotations

import json
import sqlite3

from video_labeler.cli import main
from video_labeler.domain import Sample
from video_labeler.maintenance import backup_database, check_database
from video_labeler.schema import CURRENT_SCHEMA_VERSION
from video_labeler.storage.sqlite_store import SQLiteStore


def test_backup_database_can_restore_records(tmp_path, store):
    store.upsert_dataset("d1", ".")
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d1", relative_path="clip.mp4"))
    output = tmp_path / "backup.db"

    assert backup_database(store, output) == output

    restored = SQLiteStore(tmp_path / "restored.db")
    source = sqlite3.connect(output)
    try:
        source.backup(restored.connection())
        restored.connection().commit()
        assert restored.get_sample("s1").relative_path == "clip.mp4"
    finally:
        source.close()
        restored.close()


def test_check_database_reports_integrity_and_schema_version(store):
    report = check_database(store)

    assert report.ok is True
    assert report.integrity_check == "ok"
    assert report.schema_version == CURRENT_SCHEMA_VERSION


def test_check_database_reports_failed_integrity(monkeypatch, store):
    class FakeConnection:
        def execute(self, sql, *args):
            if sql == "PRAGMA integrity_check":
                return [("corrupt page",)]
            return store.connection().execute(sql, *args)

    monkeypatch.setattr(store, "connection", lambda: FakeConnection())
    report = check_database(store)

    assert report.ok is False
    assert report.integrity_check == "corrupt page"


def test_cli_backup_and_check_db(tmp_path, capsys):
    database = tmp_path / "dataset.db"
    store = SQLiteStore(database)
    store.close()

    output = tmp_path / "copy.db"
    assert main(["backup-db", "--db", str(database), "--output", str(output)]) == 0
    assert output.is_file()
    assert main(["check-db", "--db", str(database)]) == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["schema_version"] == CURRENT_SCHEMA_VERSION


def test_existing_database_gets_pre_migration_backup(tmp_path):
    database = tmp_path / "dataset.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE legacy(value TEXT)")
    connection.execute("INSERT INTO legacy(value) VALUES ('kept')")
    connection.commit()
    connection.close()

    store = SQLiteStore(database)
    store.close()

    backups = list(tmp_path.glob("dataset.db.pre-migration-*.db"))
    assert backups
    check = sqlite3.connect(backups[0])
    try:
        assert check.execute("SELECT value FROM legacy").fetchone()[0] == "kept"
    finally:
        check.close()


def test_pre_migration_backup_includes_wal_committed_data(tmp_path):
    database = tmp_path / "dataset.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE legacy(value TEXT)")
    connection.execute("INSERT INTO legacy(value) VALUES ('wal-kept')")
    connection.commit()

    store = SQLiteStore(database)
    store.close()
    connection.close()

    backups = list(tmp_path.glob("dataset.db.pre-migration-*.db"))
    assert backups
    check = sqlite3.connect(backups[0])
    try:
        assert check.execute("SELECT value FROM legacy").fetchone()[0] == "wal-kept"
    finally:
        check.close()
