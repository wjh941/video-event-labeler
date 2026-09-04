"""Application services shared by the event and person annotation adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .domain import Event, Person, Prediction, utc_now
from .providers import AnnotationProvider
from .serialization import snapshot_payload
from .storage.csv_adapter import export_csv
from .storage.sqlite_store import SQLiteStore


@dataclass(frozen=True)
class SaveResult:
    sample_id: str
    revision: int
    review_status: str = "draft"
    behavior_class: str = ""


@dataclass(frozen=True)
class RowPayload:
    sample_id: str
    video_url: str
    behaviors: tuple[dict[str, Any], ...]
    person_identity_attributes: tuple[dict[str, Any], ...]
    person_count: int
    csv_revision: str
    revision: int
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "video_url": self.video_url,
            "behaviors": list(self.behaviors),
            "events": list(self.behaviors),
            "person_identity_attributes": list(self.person_identity_attributes),
            "person_count": self.person_count,
            "csv_revision": self.csv_revision,
            "revision": self.revision,
            "status": self.status,
        }


class AnnotationService:
    MAX_PAGE_SIZE = 500

    def __init__(self, store: SQLiteStore, video_root: Path, provider: AnnotationProvider | None = None) -> None:
        self.store = store
        self.video_root = Path(video_root).resolve()
        self.provider = provider

    @staticmethod
    def _event(value: Event | Mapping[str, Any], sample_id: str) -> Event:
        if isinstance(value, Event):
            if value.sample_id != sample_id:
                raise ValueError("event belongs to another sample")
            return value
        return Event(sample_id=sample_id, event_type=str(value.get("event_type", "")).strip(),
                     start_time_ms=value.get("start_time_ms", value.get("start_time")),
                     end_time_ms=value.get("end_time_ms", value.get("end_time")),
                     source=str(value.get("source") or "human"), confidence=value.get("confidence"),
                     review_status=str(value.get("review_status") or "draft"),
                     annotator=value.get("annotator"), revision=int(value.get("revision") or 0),
                     event_id=str(value.get("event_id") or ""))

    @staticmethod
    def _person(value: Person | Mapping[str, Any], sample_id: str) -> Person:
        if isinstance(value, Person):
            if value.sample_id != sample_id:
                raise ValueError("person belongs to another sample")
            return value
        return Person(sample_id=sample_id, person_id=str(value.get("person_id") or "").strip(),
                      age_group=str(value.get("age_group") or "unknown"),
                      face_familiarity=str(value.get("face_familiarity") or "unknown"),
                      body_reid_familiarity=str(value.get("body_reid_familiarity") or value.get("body_familiarity") or "unknown"),
                      track_id=value.get("track_id"), source=str(value.get("source") or "human"),
                      confidence=value.get("confidence"), review_status=str(value.get("review_status") or "draft"),
                      annotator=value.get("annotator"), revision=int(value.get("revision") or 0),
                      person_record_id=str(value.get("person_record_id") or ""))

    def _row(self, sample) -> RowPayload:
        events = self.store.get_events(sample.sample_id)
        people = self.store.get_persons(sample.sample_id)
        event_payload = tuple({"event_type": e.event_type, "start_time_ms": e.start_time_ms,
                               "end_time_ms": e.end_time_ms, "source": e.source,
                               "confidence": e.confidence, "review_status": e.review_status} for e in events)
        person_payload = tuple({"person_id": p.person_id, "age_group": p.age_group,
                                "face_familiarity": p.face_familiarity,
                                "body_reid_familiarity": p.body_reid_familiarity,
                                **({"track_id": p.track_id} if p.track_id else {})} for p in people)
        return RowPayload(sample.sample_id, f"/video/{sample.sample_id}", event_payload,
                          person_payload, len(people), str(sample.revision), sample.revision, sample.status)

    def list_rows(self, offset: int = 0, limit: int = 100, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        self._validate_page(offset, limit)
        status = (filters or {}).get("status") if filters else None
        return [self._row(s).as_dict() for s in self.store.list_samples(limit, offset, status=status)]

    @classmethod
    def _validate_page(cls, offset: int, limit: int) -> None:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= cls.MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {cls.MAX_PAGE_SIZE}")

    def count_rows(self, status: str | None = None) -> int:
        if status == "pending":
            status = "draft"
        if status not in (None, "draft", "reviewed", "rejected"):
            raise ValueError("status must be draft, reviewed, rejected, or omitted")
        return self.store.count_samples(status)

    def list_prediction_records(
        self,
        status: str | None = None,
        task: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._validate_page(offset, limit)
        if status not in (None, "draft", "accepted", "rejected"):
            raise ValueError("status must be draft, accepted, rejected, or omitted")
        if task not in (None, "event", "person"):
            raise ValueError("task must be event, person, or omitted")
        records = []
        for row in self.store.list_prediction_records(limit, offset, status, task):
            try:
                label: Any = json.loads(row["label_json"])
            except json.JSONDecodeError:
                label = None
            records.append({
                "prediction_id": row["prediction_id"],
                "sample_id": row["sample_id"],
                "task": row["task"],
                "label_json": row["label_json"],
                "label": label,
                "model_name": row["model_name"],
                "model_version": row["model_version"],
                "confidence": row["confidence"],
                "created_at": row["created_at"],
                "review_status": row["review_status"],
                "annotator": row["annotator"],
                "decided_at": row["decided_at"],
            })
        return records

    def count_predictions(self, status: str | None = None, task: str | None = None) -> int:
        if status not in (None, "draft", "accepted", "rejected"):
            raise ValueError("status must be draft, accepted, rejected, or omitted")
        if task not in (None, "event", "person"):
            raise ValueError("task must be event, person, or omitted")
        return self.store.count_predictions(status, task)

    def quality_snapshot(self, mode: str = "draft") -> dict[str, Any]:
        from .quality import dataset_stats, validate_dataset

        return {
            "stats": dataset_stats(self.store),
            "quality": validate_dataset(self.store, mode=mode).to_dict(),
            "generated_at": utc_now(),
        }

    def get_row(self, sample_id: str) -> RowPayload:
        sample = self.store.get_sample(sample_id)
        if sample is None:
            raise KeyError(sample_id)
        return self._row(sample)

    def save_events(self, sample_id: str, events: Iterable[Event | Mapping[str, Any]], expected_revision: int | None = None, actor: str = "human") -> SaveResult:
        converted = [self._event(e, sample_id) for e in events]
        revision = self.store.replace_annotations(sample_id, events=converted, expected_revision=expected_revision, actor=actor, summary="save events")
        return SaveResult(sample_id, revision, "reviewed" if any(e.review_status == "accepted" for e in converted) else "draft")

    def save_people(self, sample_id: str, people: Iterable[Person | Mapping[str, Any]], expected_revision: int | None = None, actor: str = "human") -> SaveResult:
        converted = [self._person(p, sample_id) for p in people]
        revision = self.store.replace_annotations(sample_id, people=converted, expected_revision=expected_revision, actor=actor, summary="save people")
        return SaveResult(sample_id, revision)

    def restore_revision(self, sample_id: str, revision: int, actor: str, expected_revision: int | None = None) -> SaveResult:
        # Keep the historical snapshot and the untouched collection from one
        # consistent read while replace_annotations runs in a nested savepoint.
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM annotation_revisions WHERE sample_id = ? AND revision = ?",
                (sample_id, revision),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown revision {sample_id}:{revision}")
            events_json, people_json = snapshot_payload(row["after_json"] or "")
            events = [self._event(value, sample_id) for value in events_json]
            people = [self._person(value, sample_id) for value in people_json]
            # Single-collection saves preserve the untouched side by design.
            summary = str(row["summary"] or "")
            if summary == "save events":
                people = self.store._persons_from_connection(connection, sample_id)
            elif summary == "save people":
                events = self.store._events_from_connection(connection, sample_id)
            new_revision = self.store.replace_annotations(sample_id, events=events, people=people,
                                                           expected_revision=expected_revision, actor=actor,
                                                           summary=f"restore revision {revision}")
            return SaveResult(sample_id, new_revision)

    def export_csv(self, path: Path):
        return export_csv(self.store, Path(path), self.video_root)

    def predict(self, sample_id: str) -> list[Prediction]:
        if self.provider is None:
            raise RuntimeError("no annotation provider configured")
        sample = self.store.get_sample(sample_id)
        if sample is None:
            raise KeyError(sample_id)
        predictions = list(self.provider.predict(sample))
        for prediction in predictions:
            self.store.upsert_prediction(prediction)
        return predictions

    def get_prediction(self, prediction_id: str) -> Prediction | None:
        return self.store.get_prediction(prediction_id)

    def list_predictions(self, sample_id: str) -> list[Prediction]:
        if self.store.get_sample(sample_id) is None:
            raise KeyError(sample_id)
        return self.store.list_predictions(sample_id)

    def accept_prediction(self, prediction_id: str, actor: str, expected_revision: int | None = None) -> SaveResult:
        with self.store.transaction():
            record = self.store.prediction_record(prediction_id)
            if record is None:
                raise KeyError(prediction_id)
            prediction, status, _, _ = record
            if status != "draft":
                raise KeyError(f"unknown or already decided prediction: {prediction_id}")
            try:
                label = json.loads(prediction.label_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("prediction label_json must be valid JSON") from exc
            if not isinstance(label, dict):
                raise ValueError("prediction label must be an object")
            common = {"source": "model", "confidence": prediction.confidence, "review_status": "accepted", "annotator": actor}
            if prediction.task == "event":
                event = self._event({**label, **common}, prediction.sample_id)
                result_revision = self.store.replace_annotations(prediction.sample_id, events=[event], expected_revision=expected_revision, actor=actor, summary=f"accept prediction {prediction_id}")
                result = SaveResult(prediction.sample_id, result_revision, "reviewed")
            elif prediction.task == "person":
                person = self._person({**label, **common}, prediction.sample_id)
                result_revision = self.store.replace_annotations(prediction.sample_id, people=[person], expected_revision=expected_revision, actor=actor, summary=f"accept prediction {prediction_id}")
                result = SaveResult(prediction.sample_id, result_revision, "draft")
            else:
                raise ValueError(f"unsupported prediction task: {prediction.task}")
            self.store.decide_prediction(prediction_id, "accepted", actor, utc_now())
            return result

    def reject_prediction(self, prediction_id: str, actor: str) -> None:
        self.store.decide_prediction(prediction_id, "rejected", actor, utc_now(), record_revision=True)


__all__ = ["AnnotationService", "RowPayload", "SaveResult"]
