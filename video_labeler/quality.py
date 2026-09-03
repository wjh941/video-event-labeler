"""Dataset quality checks, statistics, and ML-friendly JSONL export."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .domain import utc_now
from .storage.csv_adapter import ExportReport
from .storage.file_lock import FileLock
from .storage.sqlite_store import SQLiteStore


@dataclass(frozen=True, slots=True)
class QualityIssue:
    sample_id: str | None
    field: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class QualityReport:
    errors: tuple[QualityIssue, ...] = ()
    warnings: tuple[QualityIssue, ...] = ()
    checked_samples: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked_samples": self.checked_samples,
            "errors": [asdict(item) for item in self.errors],
            "warnings": [asdict(item) for item in self.warnings],
        }


def _issue(sample_id: str | None, field: str, code: str, message: str) -> QualityIssue:
    return QualityIssue(sample_id=sample_id, field=field, code=code, message=message)


def validate_dataset(store: SQLiteStore) -> QualityReport:
    """Validate cross-table consistency and media/annotation completeness.

    Database constraints catch malformed enum values at write time.  This pass
    focuses on relationships and conditions that depend on multiple records.
    Missing optional media/probe metadata is reported as a warning so a CSV-only
    dataset can still be exported and reviewed.
    """
    errors: list[QualityIssue] = []
    warnings: list[QualityIssue] = []
    samples = store.list_samples(limit=10**9, offset=0)
    for sample in samples:
        sid = sample.sample_id
        if not sample.relative_path.strip():
            errors.append(_issue(sid, "relative_path", "missing_relative_path", "sample has no media path"))
        if not sample.source_sha256:
            warnings.append(_issue(sid, "source_sha256", "missing_source_hash", "source hash is not recorded"))

        assets = store.get_media_assets(sid)
        video_assets = [asset for asset in assets if asset.modality == "video"]
        if not assets:
            warnings.append(_issue(sid, "media_assets", "missing_media", "no indexed media assets"))
        for asset in assets:
            if asset.probe_status != "ok":
                warnings.append(_issue(sid, f"media.{asset.modality}", "media_probe_unavailable", f"probe status is {asset.probe_status!r}"))
            if sample.source_sha256 and asset.source_sha256 and sample.source_sha256 != asset.source_sha256:
                warnings.append(_issue(sid, "source_sha256", "stale_source_hash", "media hash differs from sample hash"))

        duration_ms = next((asset.duration_ms for asset in video_assets if asset.duration_ms is not None), None)
        for event in store.get_events(sid):
            if event.start_time_ms is None or event.end_time_ms is None:
                if event.review_status != "draft":
                    errors.append(_issue(sid, "events", "missing_event_time", f"event {event.event_id} is not draft but has no complete time range"))
                continue
            if event.end_time_ms <= event.start_time_ms:
                errors.append(_issue(sid, "events", "event_non_positive_duration", f"event {event.event_id} has non-positive duration"))
            if duration_ms is not None and event.end_time_ms > duration_ms:
                errors.append(_issue(sid, "events", "event_out_of_bounds", f"event {event.event_id} ends at {event.end_time_ms}ms, media is {duration_ms}ms"))

        people = store.get_persons(sid)
        ids = [person.person_id for person in people]
        for person_id, count in Counter(ids).items():
            if count > 1:
                errors.append(_issue(sid, "persons.person_id", "duplicate_person_id", f"person_id {person_id!r} occurs {count} times"))
        for prediction in store.list_predictions(sid):
            record = store.prediction_record(prediction.prediction_id)
            if record is not None and record[1] not in ("draft", "accepted", "rejected"):
                errors.append(_issue(sid, "model_predictions.review_status", "invalid_prediction_review_status", f"prediction {prediction.prediction_id} has invalid review status"))
            try:
                label = json.loads(prediction.label_json)
            except json.JSONDecodeError:
                errors.append(_issue(sid, "model_predictions.label_json", "invalid_prediction_json", f"prediction {prediction.prediction_id} has malformed label JSON"))
            else:
                if not isinstance(label, (dict, list)):
                    errors.append(_issue(sid, "model_predictions.label_json", "invalid_prediction_json", f"prediction {prediction.prediction_id} label must be an object or array"))
    return QualityReport(errors=tuple(errors), warnings=tuple(warnings), checked_samples=len(samples))


def dataset_stats(store: SQLiteStore) -> dict[str, int | float]:
    """Return deterministic aggregate counts suitable for dashboards/monitoring."""
    samples = store.list_samples(limit=10**9, offset=0)
    stats: dict[str, int | float] = {
        "sample_count": len(samples),
        "reviewed_samples": sum(sample.status == "reviewed" for sample in samples),
        "draft_samples": sum(sample.status == "draft" for sample in samples),
        "rejected_samples": sum(sample.status == "rejected" for sample in samples),
        "event_count": 0,
        "person_count": 0,
        "evidence_count": 0,
        "prediction_count": 0,
        "total_duration_ms": 0,
        "video_duration_ms": 0,
    }
    event_types: Counter[str] = Counter()
    age_groups: Counter[str] = Counter()
    face_values: Counter[str] = Counter()
    body_values: Counter[str] = Counter()
    modalities: Counter[str] = Counter()
    for sample in samples:
        events = store.get_events(sample.sample_id)
        people = store.get_persons(sample.sample_id)
        evidence = store.get_evidence(sample.sample_id)
        predictions = store.list_predictions(sample.sample_id)
        stats["event_count"] += len(events)
        stats["person_count"] += len(people)
        stats["evidence_count"] += len(evidence)
        stats["prediction_count"] += len(predictions)
        for event in events:
            event_types[event.event_type] += 1
        for person in people:
            age_groups[person.age_group] += 1
            face_values[person.face_familiarity] += 1
            body_values[person.body_reid_familiarity] += 1
        for asset in store.get_media_assets(sample.sample_id):
            modalities[asset.modality] += 1
            if asset.modality == "video" and asset.duration_ms is not None:
                stats["video_duration_ms"] += asset.duration_ms
                stats["total_duration_ms"] += asset.duration_ms
    if samples:
        stats["completion_rate"] = stats["reviewed_samples"] / len(samples)
        stats["error_rate"] = stats["rejected_samples"] / len(samples)
    else:
        stats["completion_rate"] = 0.0
        stats["error_rate"] = 0.0
    for key, value in modalities.items():
        stats[f"modality_{key}"] = value
    for prefix, values in (("event_type", event_types), ("age_group", age_groups), ("face_familiarity", face_values), ("body_reid_familiarity", body_values)):
        for key, value in values.items():
            safe_key = "".join(ch if ch.isalnum() else "_" for ch in key).strip("_") or "unknown"
            stats[f"{prefix}_{safe_key}"] = value
    return stats


def _json_record(store: SQLiteStore, sample: Any) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    row = store.connection().execute("SELECT extra_json FROM samples WHERE sample_id = ?", (sample.sample_id,)).fetchone()
    if row:
        try:
            parsed = json.loads(row[0] or "{}")
            if isinstance(parsed, dict):
                extra = parsed
        except json.JSONDecodeError:
            extra = {}
    media = [asdict(asset) for asset in store.get_media_assets(sample.sample_id)]
    events = [asdict(event) for event in store.get_events(sample.sample_id)]
    persons = [asdict(person) for person in store.get_persons(sample.sample_id)]
    evidence = [asdict(item) for item in store.get_evidence(sample.sample_id)]
    predictions = []
    for prediction in store.list_predictions(sample.sample_id):
        item = asdict(prediction)
        item["label"] = json.loads(item.pop("label_json"))
        item["evidence_ids"] = list(item.get("evidence_ids", ()))
        decision = store.prediction_record(prediction.prediction_id)
        if decision:
            _, status, annotator, decided_at = decision
            item["review_status"] = status
            item["annotator"] = annotator
            item["decided_at"] = decided_at
        predictions.append(item)
    revisions = [dict(revision) for revision in store.get_revisions(sample.sample_id)]
    return {
        "sample": {"sample_id": sample.sample_id, "dataset_id": sample.dataset_id, "relative_path": sample.relative_path,
                    "source_sha256": sample.source_sha256, "status": sample.status, "schema_version": sample.schema_version,
                    "revision": sample.revision, **extra},
        "media": media,
        "events": events,
        "persons": persons,
        "evidence": evidence,
        "provenance": {"predictions": predictions, "revisions": revisions},
    }


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_jsonl(store: SQLiteStore, path: Path) -> ExportReport:
    """Export one deterministic multimodal/provenance record per sample."""
    path = Path(path)
    records = [_json_record(store, sample) for sample in store.list_samples(limit=10**9, offset=0)]
    backup_path: Path | None = None
    meta_path = path.with_name(path.name + ".meta.json")
    with FileLock(path.with_name(path.name + ".lock")):
        if path.exists():
            backup_path = path.with_name(f"{path.stem}.before_export{path.suffix}")
            backup_path.write_bytes(path.read_bytes())
        _atomic_jsonl(path, records)
        metadata = {"schema_version": 3, "exported_at": utc_now(), "sample_count": len(records), "format": "jsonl"}
        fd, temporary = tempfile.mkstemp(prefix=f".{meta_path.name}.", suffix=".tmp", dir=str(meta_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, ensure_ascii=False, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, meta_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return ExportReport(path=path, sample_count=len(records), backup_path=backup_path, meta_path=meta_path)


__all__ = ["QualityIssue", "QualityReport", "dataset_stats", "export_jsonl", "validate_dataset"]
