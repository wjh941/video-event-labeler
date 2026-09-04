"""CSV compatibility adapter for the SQLite annotation store.

CSV is deliberately kept at the boundary of the application.  The SQLite
repository remains the source of truth and this module owns legacy migration,
normalisation, and deterministic child identifiers.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ..domain import Event, Person, Sample, utc_now
from ..schema import CURRENT_SCHEMA_VERSION
from .file_lock import FileLock
from .sqlite_store import SQLiteStore, StorageError

KNOWN_COLUMNS = (
    "sample_id", "video_path", "lighting", "lighting_evidence", "behavior_class",
    "behavior_id", "security_zone_points", "person_count",
    "person_identity_attributes", "events",
)
_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ImportError:
    row_number: int
    message: str
    sample_id: str | None = None


class CancellationError(RuntimeError):
    """Raised when a streaming import or export is cancelled by the caller."""


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    stale: int = 0
    errors: list[ImportError] = field(default_factory=list)


@dataclass(frozen=True)
class ExportReport:
    path: Path
    sample_count: int
    backup_path: Path | None
    meta_path: Path
    manifest_path: Path | None = None


def _normalise_relative_path(value: str, video_root: Path) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    candidate = Path(raw)
    root = video_root.resolve()
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(root)
        except ValueError:
            raise ValueError("video_path must be inside video_root")
    else:
        try:
            candidate = (root / candidate).resolve().relative_to(root)
        except ValueError:
            raise ValueError("video_path must be inside video_root")
    return candidate.as_posix().lstrip("./")


def sample_id_for_path(relative_path: str, source_sha256: str) -> str:
    """Return a stable identifier for one path/content pair."""
    normalised = str(relative_path or "").replace("\\", "/").strip().lstrip("./")
    digest = hashlib.sha256(f"{normalised}\0{source_sha256 or ''}".encode("utf-8")).hexdigest()
    return f"sample-{digest}"


def child_id(sample_id: str, kind: str, ordinal: int) -> str:
    if ordinal < 0:
        raise ValueError("ordinal must be non-negative")
    digest = hashlib.sha256(f"{sample_id}\0{kind}\0{ordinal}".encode("utf-8")).hexdigest()
    return f"{kind}-{digest}"


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> tuple[list[str], Iterator[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(sample.splitlines(), dialect=dialect)
        fields = [field.strip() for field in (reader.fieldnames or []) if field and field.strip()]
    if len(fields) != len(set(fields)):
        raise ValueError("CSV contains duplicate column names")

    def rows() -> Iterator[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, dialect=dialect):
                yield {str(k).strip(): (v or "") for k, v in row.items() if k is not None and str(k).strip()}

    return fields, rows()


def _json_value(raw: str, default: Any, field_name: str) -> Any:
    if raw is None or not str(raw).strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} contains malformed JSON: {exc.msg}") from exc


def _as_int(value: Any) -> int | None:
    if value is None or value == "" or str(value).lower() == "null":
        return None
    return int(value)


def _as_float(value: Any) -> float | None:
    if value is None or value == "" or str(value).lower() == "null":
        return None
    return float(value)


def _events(raw: str, sample_id: str) -> list[Event]:
    values = _json_value(raw, [], "events")
    if not isinstance(values, list):
        raise ValueError("events must be a JSON array")
    result: list[Event] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict) or not str(item.get("event_type", "")).strip():
            raise ValueError(f"events[{index}] must contain event_type")
        result.append(Event(
            sample_id=sample_id,
            event_type=str(item["event_type"]).strip(),
            start_time_ms=_as_int(item.get("start_time_ms", item.get("start_time"))),
            end_time_ms=_as_int(item.get("end_time_ms", item.get("end_time"))),
            source=str(item.get("source") or "imported"),
            confidence=_as_float(item.get("confidence")),
            review_status=str(item.get("review_status") or "draft"),
            annotator=item.get("annotator"),
            revision=int(item.get("revision") or 0),
            event_id=child_id(sample_id, "event", index),
        ))
    return result


def _people(raw: str, sample_id: str) -> list[Person]:
    values = _json_value(raw, [], "person_identity_attributes")
    if not isinstance(values, list):
        raise ValueError("person_identity_attributes must be a JSON array")
    result: list[Person] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise ValueError(f"person_identity_attributes[{index}] must be an object")
        result.append(Person(
            sample_id=sample_id,
            person_id=str(item.get("person_id") or f"p{index + 1}"),
            age_group=str(item.get("age_group") or "unknown"),
            face_familiarity=str(item.get("face_familiarity") or "unknown"),
            body_reid_familiarity=str(item.get("body_reid_familiarity") or item.get("body_familiarity") or "unknown"),
            track_id=item.get("track_id"),
            source=str(item.get("source") or "imported"),
            confidence=_as_float(item.get("confidence")),
            review_status=str(item.get("review_status") or "draft"),
            annotator=item.get("annotator"),
            revision=int(item.get("revision") or 0),
            person_record_id=child_id(sample_id, "person", index),
        ))
    return result


def _dataset_id(video_root: Path) -> str:
    return "dataset-" + hashlib.sha256(str(video_root.resolve()).encode("utf-8")).hexdigest()[:24]


def _extra_fields(fields: Iterable[str], row: dict[str, str]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in fields if field not in KNOWN_COLUMNS and field != "person_tag_list"}


def _existing_by_path(store: SQLiteStore, relative_path: str) -> Sample | None:
    row = store.connection().execute("SELECT * FROM samples WHERE relative_path = ?", (relative_path,)).fetchone()
    return store._sample_from_row(row) if row else None


def import_csv(path: Path, store: SQLiteStore, video_root: Path,
               progress: Callable[[int], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> ImportReport:
    path, video_root = Path(path), Path(video_root)
    fields, rows = _read_csv(path)
    report = ImportReport()
    dataset_id = _dataset_id(video_root)
    store.upsert_dataset(dataset_id, str(video_root.resolve()))
    with FileLock(path.with_name(path.name + ".lock")):
        processed = 0
        for row_number, row in enumerate(rows, start=2):
            if cancel is not None and cancel():
                raise CancellationError("CSV import cancelled")
            supplied_id = row.get("sample_id", "").strip() or None
            try:
                relative_path = _normalise_relative_path(row.get("video_path", ""), video_root)
                source_path = video_root / relative_path
                source_hash = _sha256_file(source_path)
                sample_id = supplied_id or sample_id_for_path(relative_path, source_hash or "missing")
                current = store.get_sample(sample_id) or _existing_by_path(store, relative_path)
                if current and current.source_sha256 != source_hash and (current.source_sha256 or source_hash):
                    report.stale += 1
                    processed += 1
                    if progress is not None:
                        progress(processed)
                    continue
                canonical_id = current.sample_id if current else sample_id
                try:
                    events = _events(row.get("events", ""), canonical_id)
                    people = _people(row.get("person_identity_attributes", ""), canonical_id)
                except (ValueError, TypeError, OverflowError) as exc:
                    report.errors.append(ImportError(row_number, str(exc), supplied_id))
                    if current is None and row.get("status", "draft").strip() != "reviewed":
                        sample = Sample(sample_id=canonical_id, dataset_id=dataset_id, relative_path=relative_path, source_sha256=source_hash)
                        store.replace_sample_bundle(sample, "{}", [], [])
                        report.created += 1
                    processed += 1
                    if progress is not None:
                        progress(processed)
                    continue
                extra = _extra_fields(fields, row)
                status = row.get("status", "draft").strip() or "draft"
                if status not in ("draft", "reviewed", "rejected"):
                    status = "draft"
                if current:
                    sample_id = canonical_id
                    unchanged = current.relative_path == relative_path and current.source_sha256 == source_hash and current.status == status
                    existing_extra = store.connection().execute("SELECT extra_json FROM samples WHERE sample_id = ?", (sample_id,)).fetchone()
                    if unchanged and (not extra or json.loads(existing_extra[0] or "{}") == extra) and store.get_events(sample_id) == events and store.get_persons(sample_id) == people:
                        report.skipped += 1
                        continue
                    sample = Sample(sample_id=sample_id, dataset_id=dataset_id, relative_path=relative_path, source_sha256=source_hash, status=status, revision=current.revision)
                    store.replace_sample_bundle(sample, json.dumps(extra, ensure_ascii=False, sort_keys=True), events, people)
                    report.updated += 1
                else:
                    sample = Sample(sample_id=sample_id, dataset_id=dataset_id, relative_path=relative_path, source_sha256=source_hash, status=status)
                    store.replace_sample_bundle(sample, json.dumps(extra, ensure_ascii=False, sort_keys=True), events, people)
                    report.created += 1
            except (ValueError, TypeError, OverflowError, KeyError, StorageError) as exc:
                report.errors.append(ImportError(row_number, str(exc), supplied_id))
            processed += 1
            if progress is not None:
                progress(processed)
    return report


def _atomic_write(path: Path, writer, *, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig" if bom else "utf-8", newline="") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            os.fsync(dir_fd)
            os.close(dir_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.stem}.before_export_{timestamp}{path.suffix}")
    shutil.copy2(path, target)
    return target


def export_csv(store: SQLiteStore, path: Path, video_root: Path,
               progress: Callable[[int], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> ExportReport:
    path, video_root = Path(path), Path(video_root)
    samples = store.list_samples(limit=10**9, offset=0)
    unknown: set[str] = set()
    for sample in samples:
        row_db = store.connection().execute("SELECT extra_json FROM samples WHERE sample_id = ?", (sample.sample_id,)).fetchone()
        try:
            extra = json.loads(row_db[0] or "{}") if row_db else {}
        except json.JSONDecodeError:
            extra = {}
        if isinstance(extra, dict):
            unknown.update(str(key) for key in extra if key not in KNOWN_COLUMNS and key != "person_tag_list")
    columns = list(KNOWN_COLUMNS) + sorted(unknown)

    def rows() -> Iterator[dict[str, str]]:
        for processed, sample in enumerate(samples, start=1):
            if cancel is not None and cancel():
                raise CancellationError("CSV export cancelled")
            row_db = store.connection().execute("SELECT extra_json FROM samples WHERE sample_id = ?", (sample.sample_id,)).fetchone()
            try:
                extra = json.loads(row_db[0] or "{}") if row_db else {}
            except json.JSONDecodeError:
                extra = {}
            if not isinstance(extra, dict):
                extra = {}
            people = store.get_persons(sample.sample_id)
            events = store.get_events(sample.sample_id)
            person_json = [{"person_id": p.person_id, "age_group": p.age_group, "face_familiarity": p.face_familiarity,
                            "body_reid_familiarity": p.body_reid_familiarity, **({"track_id": p.track_id} if p.track_id else {})}
                           for p in people]
            event_json = [{"event_type": e.event_type, "start_time_ms": e.start_time_ms, "end_time_ms": e.end_time_ms,
                           "source": e.source, "confidence": e.confidence, "review_status": e.review_status}
                          for e in events]
            row = {field: str(extra.get(field, "")) for field in KNOWN_COLUMNS if field not in ("sample_id", "video_path", "person_count", "person_identity_attributes", "events")}
            row.update({"sample_id": sample.sample_id, "video_path": sample.relative_path, "person_count": str(len(people)),
                        "person_identity_attributes": json.dumps(person_json, ensure_ascii=False, separators=(",", ":")),
                        "events": json.dumps(event_json, ensure_ascii=False, separators=(",", ":"))})
            row.update({key: str(extra.get(key, "")) for key in unknown})
            if progress is not None:
                progress(processed)
            yield row

    backup_path = None
    with FileLock(path.with_name(path.name + ".lock")):
        backup_path = _backup(path)
        _atomic_write(path, lambda handle: _write_rows(handle, columns, rows()), bom=True)
        meta_path = path.with_name(path.name + ".meta.json")
        max_revision = max((sample.revision for sample in samples), default=0)
        metadata = {"schema_version": CURRENT_SCHEMA_VERSION, "exported_at": utc_now(), "database_revision": max_revision, "sample_count": len(samples)}
        _atomic_write(meta_path, lambda handle: json.dump(metadata, handle, ensure_ascii=False, indent=2))
    return ExportReport(path=path, sample_count=len(samples), backup_path=backup_path, meta_path=meta_path)


def _write_rows(handle, columns: list[str], rows: Iterable[dict[str, str]]) -> None:
    writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


__all__ = ["CancellationError", "ExportReport", "ImportError", "ImportReport", "KNOWN_COLUMNS", "child_id", "export_csv", "import_csv", "sample_id_for_path"]
