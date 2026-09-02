"""Transactional SQLite repository for validated annotation records."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from ..domain import Event, Person, Sample
from ..schema import migrate_schema


class StorageError(RuntimeError):
    """Storage operation failed while preserving the original cause."""


class ConflictError(StorageError):
    """The caller supplied a stale sample revision."""


def _event_id(event: Event, index: int) -> str:
    return event.event_id or f"event-{event.sample_id}-{index}-{uuid.uuid4().hex}"


def _person_record_id(person: Person, index: int) -> str:
    return person.person_record_id or f"person-{person.sample_id}-{index}-{uuid.uuid4().hex}"


class SQLiteStore:
    """One-connection repository with serialized writes and optimistic revisions."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._configure_connection()
        migrate_schema(self._connection)

    def _configure_connection(self) -> None:
        connection = self._connection
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA journal_mode = WAL")

    def connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        with self._lock:
            if self._connection.in_transaction:
                self._connection.rollback()
            self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            outer = not self._connection.in_transaction
            if outer:
                self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except Exception:
                if outer and self._connection.in_transaction:
                    self._connection.rollback()
                raise
            else:
                if outer and self._connection.in_transaction:
                    self._connection.commit()

    def upsert_dataset(self, dataset_id: str, root_path: str) -> None:
        try:
            with self.transaction() as connection:
                connection.execute(
                    """INSERT INTO datasets(dataset_id, root_path) VALUES (?, ?)
                    ON CONFLICT(dataset_id) DO UPDATE SET root_path=excluded.root_path,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
                    (dataset_id, root_path),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"dataset upsert failed: {exc}") from exc

    def upsert_sample(self, sample: Sample) -> None:
        try:
            with self.transaction() as connection:
                connection.execute(
                    """INSERT INTO samples(sample_id, dataset_id, relative_path, source_sha256,
                    status, schema_version, revision, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sample_id) DO UPDATE SET dataset_id=excluded.dataset_id,
                    relative_path=excluded.relative_path, source_sha256=excluded.source_sha256,
                    status=excluded.status, schema_version=excluded.schema_version,
                    updated_at=excluded.updated_at""",
                    (sample.sample_id, sample.dataset_id, sample.relative_path, sample.source_sha256,
                     sample.status, sample.schema_version, sample.revision, sample.created_at, sample.updated_at),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"sample upsert failed: {exc}") from exc

    @staticmethod
    def _sample_from_row(row: sqlite3.Row) -> Sample:
        return Sample(sample_id=row["sample_id"], dataset_id=row["dataset_id"], relative_path=row["relative_path"],
                      source_sha256=row["source_sha256"], status=row["status"], schema_version=row["schema_version"],
                      created_at=row["created_at"], updated_at=row["updated_at"], revision=row["revision"])

    def get_sample(self, sample_id: str) -> Sample | None:
        row = self._connection.execute("SELECT * FROM samples WHERE sample_id = ?", (sample_id,)).fetchone()
        return self._sample_from_row(row) if row else None

    def list_samples(self, limit: int, offset: int, status: str | None = None) -> list[Sample]:
        if limit < 0 or offset < 0:
            raise ValueError("limit and offset must be non-negative")
        if status is None:
            rows = self._connection.execute("SELECT * FROM samples ORDER BY sample_id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        else:
            rows = self._connection.execute("SELECT * FROM samples WHERE status = ? ORDER BY sample_id LIMIT ? OFFSET ?", (status, limit, offset)).fetchall()
        return [self._sample_from_row(row) for row in rows]

    def sample_revision(self, sample_id: str) -> int:
        row = self._connection.execute("SELECT revision FROM samples WHERE sample_id = ?", (sample_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown sample: {sample_id}")
        return int(row[0])

    def _check_revision(self, connection: sqlite3.Connection, sample_id: str, expected_revision: int | None) -> int:
        row = connection.execute("SELECT revision FROM samples WHERE sample_id = ?", (sample_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown sample: {sample_id}")
        current = int(row[0])
        if expected_revision is not None and expected_revision != current:
            raise ConflictError(f"sample {sample_id} revision is {current}, expected {expected_revision}")
        return current

    def replace_events(self, sample_id: str, events: Sequence[Event], expected_revision: int | None = None) -> int:
        if any(event.sample_id != sample_id for event in events):
            raise ValueError("all events must belong to sample_id")
        try:
            with self.transaction() as connection:
                current = self._check_revision(connection, sample_id, expected_revision)
                connection.execute("DELETE FROM events WHERE sample_id = ?", (sample_id,))
                connection.executemany("""INSERT INTO events(event_id, sample_id, event_type, start_time_ms, end_time_ms,
                    source, confidence, review_status, annotator, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(_event_id(event, index), sample_id, event.event_type, event.start_time_ms, event.end_time_ms,
                      event.source, event.confidence, event.review_status, event.annotator, event.revision)
                     for index, event in enumerate(events)])
                new_revision = current + 1
                connection.execute("UPDATE samples SET revision = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE sample_id = ?", (new_revision, sample_id))
                return new_revision
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"event replacement failed: {exc}") from exc

    def replace_persons(self, sample_id: str, people: Sequence[Person], expected_revision: int | None = None) -> int:
        if any(person.sample_id != sample_id for person in people):
            raise ValueError("all persons must belong to sample_id")
        try:
            with self.transaction() as connection:
                current = self._check_revision(connection, sample_id, expected_revision)
                connection.execute("DELETE FROM persons WHERE sample_id = ?", (sample_id,))
                connection.executemany("""INSERT INTO persons(person_record_id, sample_id, person_id, track_id, age_group,
                    face_familiarity, body_reid_familiarity, source, confidence, review_status, annotator, revision)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(_person_record_id(person, index), sample_id, person.person_id, person.track_id, person.age_group,
                      person.face_familiarity, person.body_reid_familiarity, person.source, person.confidence,
                      person.review_status, person.annotator, person.revision) for index, person in enumerate(people)])
                new_revision = current + 1
                connection.execute("UPDATE samples SET revision = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE sample_id = ?", (new_revision, sample_id))
                return new_revision
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"person replacement failed: {exc}") from exc

    def get_events(self, sample_id: str) -> list[Event]:
        rows = self._connection.execute("SELECT * FROM events WHERE sample_id = ? ORDER BY event_id", (sample_id,)).fetchall()
        return [Event(sample_id=row["sample_id"], event_type=row["event_type"], start_time_ms=row["start_time_ms"], end_time_ms=row["end_time_ms"], source=row["source"], confidence=row["confidence"], review_status=row["review_status"], annotator=row["annotator"], revision=row["revision"], event_id=row["event_id"]) for row in rows]

    def get_persons(self, sample_id: str) -> list[Person]:
        rows = self._connection.execute("SELECT * FROM persons WHERE sample_id = ? ORDER BY person_id", (sample_id,)).fetchall()
        return [Person(sample_id=row["sample_id"], person_id=row["person_id"], age_group=row["age_group"], face_familiarity=row["face_familiarity"], body_reid_familiarity=row["body_reid_familiarity"], track_id=row["track_id"], source=row["source"], confidence=row["confidence"], review_status=row["review_status"], annotator=row["annotator"], revision=row["revision"], person_record_id=row["person_record_id"]) for row in rows]
