from __future__ import annotations

import multiprocessing
import sqlite3
import threading
import time

import pytest

from video_labeler.domain import Event, Person, Sample
from video_labeler.storage.file_lock import FileLock, LockTimeoutError
from video_labeler.storage.sqlite_store import ConflictError, SQLiteStore


def _person(sample_id: str = "s1", person_id: str = "p1") -> Person:
    return Person(
        sample_id=sample_id,
        person_id=person_id,
        age_group="adult",
        face_familiarity="stranger",
        body_reid_familiarity="unknown",
    )


def _event(sample_id: str = "s1", event_type: str = "person_fall") -> Event:
    return Event(sample_id=sample_id, event_type=event_type, start_time_ms=100, end_time_ms=200)


def test_connection_configures_durable_pragmas(store):
    connection = store.connection()
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_transaction_rolls_back_on_error(store):
    with pytest.raises(RuntimeError):
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO datasets(dataset_id, root_path) VALUES (?, ?)", ("d1", ".")
            )
            raise RuntimeError("abort")
    assert store.connection().execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0


def test_nested_transaction_does_not_commit_outer_transaction(store):
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO datasets(dataset_id, root_path) VALUES (?, ?)", ("outer", ".")
        )
        with store.transaction() as nested:
            assert nested is connection
            nested.execute(
                "INSERT INTO datasets(dataset_id, root_path) VALUES (?, ?)", ("inner", ".")
            )
        assert connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 2
    assert store.connection().execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 2


def test_nested_transaction_rolls_back_only_inner_savepoint(store):
    with store.transaction() as connection:
        connection.execute("INSERT INTO datasets(dataset_id, root_path) VALUES ('outer', '.')")
        with pytest.raises(RuntimeError):
            with store.transaction() as nested:
                nested.execute("INSERT INTO datasets(dataset_id, root_path) VALUES ('inner', '.')")
                raise RuntimeError("abort inner")
        assert connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 1
    assert store.connection().execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 1


def test_sample_event_person_crud_round_trip(store):
    store.upsert_dataset("d1", "videos")
    sample = Sample(sample_id="s1", dataset_id="d1", relative_path="a.mp4")
    store.upsert_sample(sample)
    assert store.get_sample("s1") == sample
    assert store.list_samples(limit=10, offset=0) == [sample]
    assert store.list_samples(limit=10, offset=0, status="reviewed") == []

    assert store.replace_events("s1", [_event()], expected_revision=0) == 1
    assert store.replace_persons("s1", [_person()], expected_revision=1) == 2
    assert store.get_events("s1")[0].event_type == "person_fall"
    assert store.get_persons("s1")[0].person_id == "p1"
    assert store.sample_revision("s1") == 2


def test_upsert_sample_preserves_revision(store):
    store.upsert_dataset("d1", ".")
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d1", relative_path="a.mp4"))
    assert store.replace_persons("s1", [_person()], expected_revision=0) == 1
    updated = Sample(sample_id="s1", dataset_id="d1", relative_path="renamed.mp4", revision=0)
    store.upsert_sample(updated)
    assert store.sample_revision("s1") == 1
    assert store.get_sample("s1").relative_path == "renamed.mp4"


def test_stale_revision_does_not_overwrite_people(store):
    store.upsert_dataset("d1", ".")
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d1", relative_path="a.mp4"))
    revision = store.sample_revision("s1")
    store.replace_persons("s1", [_person()], revision)
    with pytest.raises(ConflictError):
        store.replace_persons("s1", [], revision)
    assert len(store.get_persons("s1")) == 1
    assert store.sample_revision("s1") == 1


def test_replace_requires_existing_sample(store):
    with pytest.raises(KeyError):
        store.replace_events("missing", [], expected_revision=None)


def test_foreign_key_integrity_is_exposed(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO samples(sample_id, dataset_id, relative_path, created_at, updated_at) "
                "VALUES ('s1', 'missing', 'a.mp4', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')"
            )


def _hold_lock(path: str, ready: multiprocessing.Queue, release: multiprocessing.Event) -> None:
    with FileLock(path, timeout_seconds=2):
        ready.put(True)
        release.wait(2)


def test_file_lock_exclusive_across_processes(tmp_path):
    lock_path = str(tmp_path / "dataset.lock")
    ready: multiprocessing.Queue = multiprocessing.Queue()
    release = multiprocessing.Event()
    process = multiprocessing.Process(target=_hold_lock, args=(lock_path, ready, release))
    process.start()
    assert ready.get(timeout=3) is True
    started = time.monotonic()
    with pytest.raises(LockTimeoutError):
        with FileLock(lock_path, timeout_seconds=0.15):
            pass
    assert time.monotonic() - started >= 0.1
    release.set()
    process.join(timeout=3)
    assert process.exitcode == 0


def test_file_lock_releases_after_exception(tmp_path):
    lock_path = tmp_path / "dataset.lock"
    with pytest.raises(ValueError):
        with FileLock(lock_path):
            raise ValueError("boom")
    with FileLock(lock_path, timeout_seconds=0.1):
        pass


def test_file_lock_does_not_grow_on_repeated_acquisition(tmp_path):
    lock_path = tmp_path / "dataset.lock"
    with FileLock(lock_path):
        pass
    first_size = lock_path.stat().st_size
    for _ in range(3):
        with FileLock(lock_path):
            pass
    assert lock_path.stat().st_size == first_size == 1


def test_store_initialization_uses_sibling_lock(tmp_path):
    database_path = tmp_path / "dataset.db"
    SQLiteStore(database_path)
    assert database_path.with_name("dataset.db.lock").is_file()


def test_reads_are_serialized_with_writes(store):
    store.upsert_dataset("d1", ".")
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d1", relative_path="a.mp4"))
    entered = threading.Event()
    release = threading.Event()
    read_done = threading.Event()

    def writer() -> None:
        with store.transaction() as connection:
            entered.set()
            release.wait(2)
            connection.execute("UPDATE samples SET status = 'reviewed' WHERE sample_id = 's1'")

    def reader() -> None:
        store.get_sample("s1")
        read_done.set()

    writer_thread = threading.Thread(target=writer)
    reader_thread = threading.Thread(target=reader)
    writer_thread.start()
    assert entered.wait(1)
    reader_thread.start()
    time.sleep(0.1)
    assert not read_done.is_set()
    release.set()
    writer_thread.join(timeout=2)
    reader_thread.join(timeout=2)
    assert not writer_thread.is_alive()
    assert not reader_thread.is_alive()
    assert store.get_sample("s1").status == "reviewed"
