"""Transactional SQLite repository for validated annotation records."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from ..domain import Event, Evidence, MediaAsset, Person, Prediction, Sample
from ..schema import migrate_schema
from .file_lock import FileLock


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
        self._savepoint_counter = 0
        self._configure_connection()
        with FileLock(self.path.with_name(self.path.name + ".lock")):
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
            savepoint = None
            if outer:
                self._connection.execute("BEGIN IMMEDIATE")
            else:
                self._savepoint_counter += 1
                savepoint = f"sp_{self._savepoint_counter}"
                self._connection.execute(f"SAVEPOINT {savepoint}")
            try:
                yield self._connection
            except Exception:
                if outer and self._connection.in_transaction:
                    self._connection.rollback()
                elif savepoint is not None:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            else:
                if outer and self._connection.in_transaction:
                    self._connection.commit()
                elif savepoint is not None:
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")

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
        with self._lock:
            row = self._connection.execute("SELECT * FROM samples WHERE sample_id = ?", (sample_id,)).fetchone()
            return self._sample_from_row(row) if row else None

    def upsert_media_asset(self, asset: MediaAsset) -> None:
        """Insert or replace one asset without changing the sample revision.

        Asset identity is the stable ``(sample_id, modality, uri)`` tuple.  Keeping
        media metadata separate from annotation revisions lets a re-index refresh
        file hashes/probe results without marking human labels as edited.
        """
        try:
            with self.transaction() as connection:
                connection.execute(
                    """INSERT INTO media_assets(
                        sample_id, modality, uri, duration_ms, fps, width, height,
                        metadata_json, source_sha256, probe_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sample_id, modality, uri) DO UPDATE SET
                        duration_ms=excluded.duration_ms,
                        fps=excluded.fps,
                        width=excluded.width,
                        height=excluded.height,
                        metadata_json=excluded.metadata_json,
                        source_sha256=excluded.source_sha256,
                        probe_status=excluded.probe_status""",
                    (
                        asset.sample_id,
                        asset.modality,
                        asset.uri,
                        asset.duration_ms,
                        asset.fps,
                        asset.width,
                        asset.height,
                        asset.metadata_json,
                        asset.source_sha256,
                        asset.probe_status,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"media asset upsert failed: {exc}") from exc

    def get_media_assets(self, sample_id: str) -> list[MediaAsset]:
        """Return all assets for a sample in deterministic modality/URI order."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT sample_id, modality, uri, duration_ms, fps, width, height,
                    metadata_json, source_sha256, probe_status
                    FROM media_assets
                    WHERE sample_id = ?
                    ORDER BY modality, uri, asset_id""",
                (sample_id,),
            ).fetchall()
            return [
                MediaAsset(
                    sample_id=row["sample_id"],
                    modality=row["modality"],
                    uri=row["uri"],
                    duration_ms=row["duration_ms"],
                    fps=row["fps"],
                    width=row["width"],
                    height=row["height"],
                    metadata_json=row["metadata_json"],
                    source_sha256=row["source_sha256"],
                    probe_status=row["probe_status"],
                )
                for row in rows
            ]

    def list_samples(self, limit: int, offset: int, status: str | None = None) -> list[Sample]:
        if limit < 0 or offset < 0:
            raise ValueError("limit and offset must be non-negative")
        with self._lock:
            if status is None:
                rows = self._connection.execute("SELECT * FROM samples ORDER BY sample_id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            else:
                rows = self._connection.execute("SELECT * FROM samples WHERE status = ? ORDER BY sample_id LIMIT ? OFFSET ?", (status, limit, offset)).fetchall()
            return [self._sample_from_row(row) for row in rows]

    def sample_revision(self, sample_id: str) -> int:
        with self._lock:
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

    def replace_sample_bundle(self, sample: Sample, extra_json: str, events: Sequence[Event], people: Sequence[Person]) -> None:
        """Atomically upsert sample metadata and replace its child annotations."""
        if any(event.sample_id != sample.sample_id for event in events):
            raise ValueError("all events must belong to sample_id")
        if any(person.sample_id != sample.sample_id for person in people):
            raise ValueError("all persons must belong to sample_id")
        try:
            with self.transaction() as connection:
                connection.execute(
                    """INSERT INTO samples(sample_id, dataset_id, relative_path, source_sha256, status,
                    schema_version, revision, created_at, updated_at, extra_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sample_id) DO UPDATE SET dataset_id=excluded.dataset_id,
                    relative_path=excluded.relative_path, source_sha256=excluded.source_sha256,
                    status=excluded.status, schema_version=excluded.schema_version,
                    updated_at=excluded.updated_at, extra_json=excluded.extra_json""",
                    (sample.sample_id, sample.dataset_id, sample.relative_path, sample.source_sha256,
                     sample.status, sample.schema_version, sample.revision, sample.created_at,
                     sample.updated_at, extra_json),
                )
                connection.execute("DELETE FROM events WHERE sample_id = ?", (sample.sample_id,))
                connection.executemany("""INSERT INTO events(event_id, sample_id, event_type, start_time_ms, end_time_ms,
                    source, confidence, review_status, annotator, revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(_event_id(event, index), sample.sample_id, event.event_type, event.start_time_ms,
                      event.end_time_ms, event.source, event.confidence, event.review_status,
                      event.annotator, event.revision) for index, event in enumerate(events)])
                connection.execute("DELETE FROM persons WHERE sample_id = ?", (sample.sample_id,))
                connection.executemany("""INSERT INTO persons(person_record_id, sample_id, person_id, track_id, age_group,
                    face_familiarity, body_reid_familiarity, source, confidence, review_status, annotator, revision)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(_person_record_id(person, index), sample.sample_id, person.person_id, person.track_id,
                      person.age_group, person.face_familiarity, person.body_reid_familiarity, person.source,
                      person.confidence, person.review_status, person.annotator, person.revision)
                     for index, person in enumerate(people)])
        except sqlite3.IntegrityError as exc:
            raise StorageError(f"sample bundle replacement failed: {exc}") from exc

    def get_events(self, sample_id: str) -> list[Event]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM events WHERE sample_id = ? ORDER BY event_id", (sample_id,)).fetchall()
            return [Event(sample_id=row["sample_id"], event_type=row["event_type"], start_time_ms=row["start_time_ms"], end_time_ms=row["end_time_ms"], source=row["source"], confidence=row["confidence"], review_status=row["review_status"], annotator=row["annotator"], revision=row["revision"], event_id=row["event_id"]) for row in rows]

    def get_persons(self, sample_id: str) -> list[Person]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM persons WHERE sample_id = ? ORDER BY person_id", (sample_id,)).fetchall()
            return [Person(sample_id=row["sample_id"], person_id=row["person_id"], age_group=row["age_group"], face_familiarity=row["face_familiarity"], body_reid_familiarity=row["body_reid_familiarity"], track_id=row["track_id"], source=row["source"], confidence=row["confidence"], review_status=row["review_status"], annotator=row["annotator"], revision=row["revision"], person_record_id=row["person_record_id"]) for row in rows]

    def upsert_evidence(self, evidence: Evidence) -> None:
        with self.transaction() as connection:
            connection.execute("""INSERT INTO evidence(evidence_id, sample_id, modality, start_time_ms, end_time_ms, uri, text, source, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(evidence_id) DO UPDATE SET modality=excluded.modality,
                start_time_ms=excluded.start_time_ms, end_time_ms=excluded.end_time_ms, uri=excluded.uri,
                text=excluded.text, source=excluded.source, confidence=excluded.confidence""",
                (evidence.evidence_id, evidence.sample_id, evidence.modality, evidence.start_time_ms,
                 evidence.end_time_ms, evidence.uri, evidence.text, evidence.source, evidence.confidence))

    def get_evidence(self, sample_id: str) -> list[Evidence]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM evidence WHERE sample_id = ? ORDER BY evidence_id", (sample_id,)).fetchall()
            return [Evidence(sample_id=r["sample_id"], modality=r["modality"], start_time_ms=r["start_time_ms"], end_time_ms=r["end_time_ms"], uri=r["uri"], text=r["text"], source=r["source"], confidence=r["confidence"], evidence_id=r["evidence_id"]) for r in rows]

    def get_evidence_by_id(self, evidence_id: str) -> Evidence | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
            if row is None:
                return None
            return Evidence(sample_id=row["sample_id"], modality=row["modality"], start_time_ms=row["start_time_ms"], end_time_ms=row["end_time_ms"], uri=row["uri"], text=row["text"], source=row["source"], confidence=row["confidence"], evidence_id=row["evidence_id"])

    def upsert_prediction(self, prediction: Prediction) -> None:
        import json
        self.validate_evidence_references(prediction.sample_id, prediction.evidence_ids)
        with self.transaction() as connection:
            connection.execute("""INSERT INTO model_predictions(prediction_id, sample_id, task, label_json, model_name,
                model_version, confidence, created_at, evidence_ids_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prediction_id) DO UPDATE SET label_json=excluded.label_json, confidence=excluded.confidence""",
                (prediction.prediction_id, prediction.sample_id, prediction.task, prediction.label_json,
                 prediction.model_name, prediction.model_version, prediction.confidence, prediction.created_at,
                 json.dumps(list(prediction.evidence_ids), separators=(",", ":"))))

    def validate_evidence_references(self, sample_id: str, evidence_ids: Sequence[str]) -> None:
        if not evidence_ids:
            return
        with self._lock:
            rows = self._connection.execute(
                "SELECT evidence_id, sample_id FROM evidence WHERE evidence_id IN (%s)"
                % ",".join("?" for _ in evidence_ids),
                tuple(evidence_ids),
            ).fetchall()
        found = {row["evidence_id"]: row["sample_id"] for row in rows}
        for evidence_id in evidence_ids:
            if evidence_id not in found:
                raise ValueError(f"unknown evidence reference: {evidence_id}")
            if found[evidence_id] != sample_id:
                raise ValueError(f"evidence reference belongs to another sample: {evidence_id}")

    def get_prediction(self, prediction_id: str) -> Prediction | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM model_predictions WHERE prediction_id = ?", (prediction_id,)).fetchone()
            if row is None:
                return None
            import json
            return Prediction(prediction_id=row["prediction_id"], sample_id=row["sample_id"], task=row["task"], label_json=row["label_json"], model_name=row["model_name"], model_version=row["model_version"], confidence=row["confidence"], created_at=row["created_at"], evidence_ids=tuple(json.loads(row["evidence_ids_json"] or "[]")))

    def prediction_record(self, prediction_id: str):
        """Return prediction plus its human decision metadata."""
        with self._lock:
            row = self._connection.execute("SELECT review_status, annotator, decided_at FROM model_predictions WHERE prediction_id = ?", (prediction_id,)).fetchone()
            if row is None:
                return None
            prediction = self.get_prediction(prediction_id)
            return prediction, row["review_status"], row["annotator"], row["decided_at"]

    def list_predictions(self, sample_id: str) -> list[Prediction]:
        with self._lock:
            rows = self._connection.execute("SELECT prediction_id FROM model_predictions WHERE sample_id = ? ORDER BY prediction_id", (sample_id,)).fetchall()
        return [prediction for row in rows if (prediction := self.get_prediction(row["prediction_id"])) is not None]

    def decide_prediction(self, prediction_id: str, status: str, annotator: str, decided_at: str) -> None:
        if status not in ("accepted", "rejected"):
            raise ValueError("status must be accepted or rejected")
        with self.transaction() as connection:
            changed = connection.execute("UPDATE model_predictions SET review_status=?, annotator=?, decided_at=? WHERE prediction_id=? AND review_status='draft'", (status, annotator, decided_at, prediction_id)).rowcount
            if not changed:
                raise KeyError(f"unknown or already decided prediction: {prediction_id}")

    def add_revision(self, sample_id: str, revision: int, actor: str, summary: str, before_json: str | None, after_json: str | None, app_version: str = "video-labeler/1") -> None:
        with self.transaction() as connection:
            connection.execute("INSERT INTO annotation_revisions(sample_id, revision, actor, summary, before_json, after_json, app_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))", (sample_id, revision, actor, summary, before_json, after_json, app_version))

    def get_revisions(self, sample_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute("SELECT * FROM annotation_revisions WHERE sample_id = ? ORDER BY revision", (sample_id,)).fetchall()
