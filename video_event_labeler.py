#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local video event labeling helpers and web application."""

from __future__ import annotations

import argparse
import ctypes
import csv
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


BEHAVIOR_LABELS = (
    "person_fall",
    "climb_fence",
    "peep_car_window",
    "pickup_package",
    "linger_wander",
    "stay_long",
    "cat_enter_frame",
    "dog_enter_frame",
    "car_enter_frame",
    "stranger_enter_frame",
    "approach_risk_zone",
    "normal_scene",
)

LABEL_NEEDLES = {
    "person_fall": ("跌倒", "fall"),
    "climb_fence": ("翻墙", "climb", "fence"),
    "peep_car_window": ("窥视", "peep_car"),
    "pickup_package": ("拾取包裹", "pick_up_package", "pick_up_packages"),
    "linger_wander": ("徘徊", "linger"),
    "stay_long": ("逗留", "stay"),
    "cat_enter_frame": ("cat_come", "cat_in"),
    "dog_enter_frame": ("dog_come", "dog_in", "dog_out"),
    "stranger_enter_frame": ("strange_car_invasion", "stranger_in"),
    "approach_risk_zone": ("靠近", "approach_risk_zone"),
}

BEHAVIOR_CLASSES = {
    "person_fall": "人员跌倒",
    "climb_fence": "翻越围栏",
    "peep_car_window": "窥视车窗",
    "pickup_package": "拾取包裹",
    "linger_wander": "徘徊",
    "stay_long": "长时间逗留",
    "cat_enter_frame": "猫进入画面",
    "dog_enter_frame": "狗进入画面",
    "car_enter_frame": "车辆进入画面",
    "stranger_enter_frame": "入侵",
    "approach_risk_zone": "靠近风险区域",
    "normal_scene": "正常场景",
}

TIME_PATTERN = re.compile(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)")
NEGATIVE_MARKER = re.compile(r"(?:^|[/\\\-_+\s.])neg(?=$|[/\\\-_+\s.])")
EVENT_PATTERN = re.compile(
    r'"event_type"\s*:\s*"([^"]+)"\s*,\s*'
    r'"start_time_ms"\s*:\s*(null|-?\d+\s*ms|-?\d+)\s*,\s*'
    r'"end_time_ms"\s*:\s*(null|-?\d+\s*ms|-?\d+)',
    re.DOTALL,
)
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
VIDEO_READ_CHUNK_SIZE = 1024 * 1024
PICKER_STARTUP_TIMEOUT_SECONDS = 3.0
PICKER_SELECTION_TIMEOUT_SECONDS = 300.0
PICKER_POLL_INTERVAL_SECONDS = 0.1
MANIFEST_FIELDS = [
    "sample_id",
    "video_path",
    "lighting",
    "lighting_evidence",
    "behavior_class",
    "behavior_id",
    "security_zone_points",
    "person_tag_list",
    "events",
]


def detect_manifest_mode(fieldnames: list[str]) -> str:
    """Return the compatible editor mode for CSV field names."""
    names = set(fieldnames)
    if "events" in names:
        return "events"
    if {"start_time", "end_time"}.issubset(names):
        return "simple"
    raise ValueError("CSV must contain events or both start_time and end_time")


def is_reference_manifest(fieldnames: list[str]) -> bool:
    """Return whether a manifest has the exact approved event schema."""
    return fieldnames == MANIFEST_FIELDS


def infer_prelabels(relative_path: Path) -> tuple[str, list[str]]:
    """Infer draft labels from a dataset path without treating them as reviewed."""
    parts = tuple(part.casefold() for part in relative_path.parts)
    text = "/".join(parts)
    if NEGATIVE_MARKER.search(text):
        return "neg", ["normal_scene"]
    if "pos" in parts:
        stratum = "pos"
    else:
        stratum = ""

    matched: list[tuple[int, int, str]] = []
    for order, label in enumerate(BEHAVIOR_LABELS):
        needles = (label, *LABEL_NEEDLES.get(label, ()))
        positions = [text.find(needle.casefold()) for needle in needles]
        positions = [position for position in positions if position >= 0]
        if positions:
            matched.append((min(positions), order, label))
    matched.sort()
    return stratum, [label for _, _, label in matched]


def behavior_class_value(labels: list[str]) -> str:
    """Return the reference CSV behavior classes in event order."""
    return ",".join(BEHAVIOR_CLASSES[label] for label in labels)


def infer_lighting(relative_path: Path) -> str:
    """Infer the reference lighting value from a dataset path."""
    text = "/".join(part.casefold() for part in relative_path.parts)
    if "daytime" in text:
        return "白天"
    if "night_black_white" in text:
        return "红外"
    return "黑夜" if "night" in text else ""


def parse_time_text(value: str) -> int | None:
    """Convert H:MM:SS[.sss] text to milliseconds."""
    text = (value or "").strip().lower()
    if text in ("", "null"):
        return None
    match = TIME_PATTERN.fullmatch(text)
    if not match:
        raise ValueError("time must use H:MM:SS or H:MM:SS.sss")
    hours, minutes, seconds = match.groups()
    if int(minutes) >= 60 or float(seconds) >= 60:
        raise ValueError("time has an invalid minute or second value")
    return int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000))


def format_time_text(value: int | None) -> str:
    """Format milliseconds as fixed-width H:MM:SS.mmm."""
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("milliseconds must be a non-negative integer or null")
    total_seconds, milliseconds = divmod(value, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def is_valid_custom_label(label: str) -> bool:
    """Return whether a manually entered event label is safe to store in CSV."""
    return 1 <= len(label) <= 64 and not any(char in label for char in ",\r\n")


def validate_events(
    events: list[dict[str, object]], permitted_labels: set[str], review: bool
) -> list[dict[str, object]]:
    """Validate and normalize browser event payloads before a CSV write."""
    if not isinstance(events, list):
        raise ValueError("events must be a list")

    cleaned: list[dict[str, object]] = []
    event_types: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("each event must be an object")
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type is required")
        event_type = event_type.strip()
        if event_type not in permitted_labels and not is_valid_custom_label(event_type):
            raise ValueError(f"unsupported behavior label: {event_type}")
        start = event.get("start_time_ms")
        end = event.get("end_time_ms")
        for name, value in (("start_time_ms", start), ("end_time_ms", end)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or null")
        if start is not None and end is not None and end <= start:
            raise ValueError("event end time must be later than its start time")

        event_types.add(event_type)
        cleaned.append(
            {
                "event_type": event_type,
                "start_time_ms": start,
                "end_time_ms": end,
            }
        )

    if "normal_scene" in event_types and len(event_types) > 1:
        raise ValueError("normal_scene cannot be combined with positive labels")
    if review:
        if not cleaned:
            raise ValueError("review requires at least one behavior label")
        if event_types != {"normal_scene"}:
            for event in cleaned:
                if event["start_time_ms"] is None or event["end_time_ms"] is None:
                    raise ValueError("reviewed positive events need start and end times")
    return cleaned


def detect_encoding(path: Path) -> str:
    """Return the supported CSV encoding without silently corrupting it."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for encoding in ("utf-8", "gb18030"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"unsupported CSV encoding: {path}")


def _validate_fieldnames(fieldnames: list[str]) -> None:
    if not fieldnames:
        raise ValueError("CSV must contain a header row")
    if any(not isinstance(name, str) or not name.strip() for name in fieldnames):
        raise ValueError("CSV header contains an empty field name")
    if len(set(fieldnames)) != len(fieldnames):
        raise ValueError("CSV header contains duplicate fields")


def _validate_row_identity(rows: list[dict[str, str]], root: Path) -> None:
    sample_ids: set[str] = set()
    video_paths: set[str] = set()
    for row in rows:
        if None in row:
            raise ValueError("CSV row has more fields than its header")
        sample_id = (row.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required")
        if sample_id:
            if sample_id in sample_ids:
                raise ValueError(f"duplicate sample_id: {sample_id}")
            sample_ids.add(sample_id)
        raw_video_path = (row.get("video_path") or "").strip()
        if not raw_video_path:
            raise ValueError("video_path is required")
        video_path = _normalized_video_path(root, raw_video_path)
        if video_path in video_paths:
            raise ValueError(f"duplicate video_path: {row.get('video_path', '')}")
        video_paths.add(video_path)
        if "events" in row:
            parse_events(row.get("events", ""), _split_behavior_ids(row.get("behavior_id", "")))


def read_csv_rows(path: Path, encoding: str) -> tuple[list[dict[str, str]], list[str]]:
    """Read a manifest while preserving its declared field order."""
    with path.open("r", encoding=encoding, newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = list(reader.fieldnames or [])
        _validate_fieldnames(fieldnames)
        return list(reader), fieldnames


def _event_time_from_csv(value: str) -> int | None:
    value = value.strip().lower()
    if value == "null":
        return None
    if value.endswith("ms"):
        value = value[:-2].strip()
    return int(value)


def _normalize_event_time(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer or null")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str):
        try:
            normalized = _event_time_from_csv(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be a non-negative integer or null") from error
    else:
        raise ValueError(f"{name} must be a non-negative integer or null")
    if normalized is None:
        return None
    if normalized < 0:
        raise ValueError(f"{name} must be a non-negative integer or null")
    return normalized


def events_to_csv_value(events: list[dict[str, object]]) -> str:
    """Write the established ms-suffixed events representation."""
    lines = []
    for event in events:
        start = "null" if event["start_time_ms"] is None else f'{event["start_time_ms"]}ms'
        end = "null" if event["end_time_ms"] is None else f'{event["end_time_ms"]}ms'
        event_type = json.dumps(event["event_type"], ensure_ascii=False)
        lines.append(
            "{"
            f'"event_type":{event_type},\n'
            f'"start_time_ms":{start},\n'
            f'"end_time_ms":{end}'
            "}"
        )
    return "[\n" + ",\n".join(lines) + "\n]"


def parse_events(value: str, behavior_ids: list[str]) -> list[dict[str, object]]:
    """Read both standard JSON events and the legacy ms-suffixed format."""
    parsed: list[dict[str, object]] = []
    value = (value or "").strip()
    if value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = None
            matches = EVENT_PATTERN.findall(value)
            if not matches:
                raise ValueError("invalid events")
            for event_type, start, end in matches:
                parsed.append(
                    {
                        "event_type": event_type,
                        "start_time_ms": _normalize_event_time(start, "start_time_ms"),
                        "end_time_ms": _normalize_event_time(end, "end_time_ms"),
                    }
                )
        else:
            if not isinstance(decoded, list):
                raise ValueError("invalid events")
            for event in decoded:
                if not isinstance(event, dict) or not isinstance(event.get("event_type"), str):
                    raise ValueError("invalid events")
                parsed.append(
                    {
                        "event_type": event["event_type"],
                        "start_time_ms": _normalize_event_time(
                            event.get("start_time_ms"), "start_time_ms"
                        ),
                        "end_time_ms": _normalize_event_time(
                            event.get("end_time_ms"), "end_time_ms"
                        ),
                    }
                )

    if parsed:
        return parsed
    return [
        {"event_type": behavior_id, "start_time_ms": None, "end_time_ms": None}
        for behavior_id in behavior_ids
        if behavior_id
    ]


def _normalized_video_path(root: Path, value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    source = Path(value)
    if not source.is_absolute():
        source = root / source
    return str(source.resolve()).casefold()


def folder_behavior_class(root: Path, source: Path) -> str | None:
    """Return the first directory below root for a video path."""
    root = root.resolve()
    source = source if source.is_absolute() else root / source
    try:
        relative = source.resolve().relative_to(root)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) > 1 else root.name


def make_import_row(root: Path, source: Path) -> dict[str, str]:
    """Build a reference-schema row for a video inside the imported root."""
    relative = source.relative_to(root)
    _, labels = infer_prelabels(relative)
    events = [
        {"event_type": label, "start_time_ms": None, "end_time_ms": None}
        for label in labels
    ]
    return {
        "sample_id": source.name,
        "video_path": str(source.resolve()),
        "lighting": infer_lighting(relative),
        "lighting_evidence": "人工确认",
        "behavior_class": folder_behavior_class(root, source) or root.name,
        "behavior_id": ",".join(labels),
        "security_zone_points": "null",
        "person_tag_list": "",
        "events": events_to_csv_value(events),
    }


def _backup_path(path: Path) -> Path:
    backup_dir = path.parent / "event_labeler_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = backup_dir / f"{path.stem}.before_event_labeling_{timestamp}{path.suffix}"
    suffix = 2
    while candidate.exists():
        candidate = backup_dir / f"{path.stem}.before_event_labeling_{timestamp}_{suffix}{path.suffix}"
        suffix += 1
    return candidate


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(directory_fd)
        except OSError:
            return
    finally:
        os.close(directory_fd)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(VIDEO_READ_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv_atomic(
    path: Path,
    rows: list[dict[str, str]],
    fieldnames: list[str],
    encoding: str,
    backups: dict[Path, Path],
) -> Path | None:
    """Back up an existing CSV once, then replace it atomically."""
    backup = backups.get(path)
    if backup is None and path.exists():
        backup = _backup_path(path)
        shutil.copy2(path, backup)
        backups[path] = backup

    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding=encoding, newline="", dir=path.parent, delete=False
        ) as file:
            temp_name = file.name
            writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
        _fsync_directory(path.parent)
    except Exception:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return backup


def import_video_directory(
    root: Path,
    manifest_name: str = "video_labeler_manifest.csv",
    backups: dict[Path, Path] | None = None,
) -> tuple[Path, int]:
    """Create or incrementally extend an event-mode manifest for a video directory."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"video directory does not exist: {root}")
    manifest = root / manifest_name
    existing = manifest.exists()
    if existing:
        encoding = detect_encoding(manifest)
        rows, fieldnames = read_csv_rows(manifest, encoding)
        if detect_manifest_mode(fieldnames) != "events":
            raise ValueError("the import manifest must use the events schema")
    else:
        encoding = "utf-8-sig"
        rows, fieldnames = [], list(MANIFEST_FIELDS)

    sources = sorted(
        (
            source
            for source in root.rglob("*")
            if source.is_file() and source.suffix.casefold() in VIDEO_EXTENSIONS
        ),
        key=lambda source: source.relative_to(root).as_posix().casefold(),
    )
    sources_by_path = {
        _normalized_video_path(root, str(source)): source
        for source in sources
    }
    known_videos = {
        _normalized_video_path(root, row.get("video_path", ""))
        for row in rows
        if row.get("video_path")
    }
    classes_changed = False
    if "behavior_class" in fieldnames:
        for row in rows:
            source = sources_by_path.get(_normalized_video_path(root, row.get("video_path", "")))
            behavior_class = folder_behavior_class(root, source) if source else None
            if behavior_class and row.get("behavior_class") != behavior_class:
                row["behavior_class"] = behavior_class
                classes_changed = True
    added_rows = []
    for source in sources:
        if _normalized_video_path(root, str(source)) not in known_videos:
            added_rows.append(make_import_row(root, source))

    _validate_row_identity(rows + added_rows, root)
    if added_rows or classes_changed or not existing:
        rows.extend(added_rows)
        write_csv_atomic(
            manifest,
            rows,
            fieldnames,
            encoding,
            backups if backups is not None else {},
        )
    return manifest, len(added_rows)


PERSON_TAG_VALUES = {"stranger", "acquaintance", "null"}


@dataclass
class AppState:
    csv_path: Path | None = None
    video_root: Path | None = None
    csv_encoding: str = "utf-8-sig"
    backups: dict[Path, Path] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    _rows_cache: list[dict[str, str]] | None = field(default=None, init=False, repr=False)
    _fieldnames_cache: list[str] | None = field(default=None, init=False, repr=False)
    _file_signature: tuple[int, int] | None = field(default=None, init=False, repr=False)
    _revision_cache: str | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_paths(cls, csv_path: Path, video_root: Path) -> "AppState":
        csv_path = csv_path.resolve()
        video_root = video_root.resolve()
        if not csv_path.is_file():
            raise ValueError(f"CSV does not exist: {csv_path}")
        if not video_root.is_dir():
            raise ValueError(f"video directory does not exist: {video_root}")
        encoding = detect_encoding(csv_path)
        rows, fieldnames = read_csv_rows(csv_path, encoding)
        detect_manifest_mode(fieldnames)
        _validate_row_identity(rows, video_root)
        state = cls(csv_path=csv_path, video_root=video_root, csv_encoding=encoding)
        state._set_snapshot_unlocked(rows, fieldnames)
        return state

    @property
    def ready(self) -> bool:
        return self.csv_path is not None and self.video_root is not None

    def _set_snapshot_unlocked(
        self, rows: list[dict[str, str]], fieldnames: list[str]
    ) -> None:
        self._rows_cache = [dict(row) for row in rows]
        self._fieldnames_cache = list(fieldnames)
        signature = self.csv_path.stat()
        self._file_signature = (signature.st_mtime_ns, signature.st_size)
        self._revision_cache = _sha256_file(self.csv_path)

    def _snapshot_unlocked(self) -> tuple[list[dict[str, str]], list[str]]:
        if not self.ready:
            return [], []
        signature = self.csv_path.stat()
        current_signature = (signature.st_mtime_ns, signature.st_size)
        if self._rows_cache is None or self._file_signature != current_signature:
            rows, fieldnames = read_csv_rows(self.csv_path, self.csv_encoding)
            detect_manifest_mode(fieldnames)
            _validate_row_identity(rows, self.video_root)
            self._set_snapshot_unlocked(rows, fieldnames)
        return [dict(row) for row in self._rows_cache], list(self._fieldnames_cache or [])

    def snapshot(self) -> tuple[list[dict[str, str]], list[str]]:
        with self.lock:
            return self._snapshot_unlocked()

    def _revision_unlocked(self) -> str:
        if not self.ready:
            return ""
        revision = _sha256_file(self.csv_path)
        if revision != self._revision_cache:
            rows, fieldnames = read_csv_rows(self.csv_path, self.csv_encoding)
            detect_manifest_mode(fieldnames)
            _validate_row_identity(rows, self.video_root)
            self._set_snapshot_unlocked(rows, fieldnames)
        return self._revision_cache or revision

    def csv_revision(self) -> str:
        with self.lock:
            return self._revision_unlocked()

    def mode(self) -> str | None:
        if not self.ready:
            return None
        _, fieldnames = self.snapshot()
        return detect_manifest_mode(fieldnames)

    def status(self) -> dict[str, object]:
        revision = self.csv_revision() if self.ready else ""
        return {
            "ready": self.ready,
            "mode": self.mode(),
            "csv_name": self.csv_path.name if self.csv_path else "",
            "video_root_name": self.video_root.name if self.video_root else "",
            "csv_revision": revision,
        }


def apply_imported_root(state: AppState, root: Path) -> tuple[Path, int]:
    """Import a directory and publish its manifest only after it is valid."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"video directory does not exist: {root}")
    manifest, added = import_video_directory(root, backups=state.backups)
    encoding = detect_encoding(manifest)
    rows, fieldnames = read_csv_rows(manifest, encoding)
    with state.lock:
        state.csv_path = manifest.resolve()
        state.video_root = root
        state.csv_encoding = encoding
        state._set_snapshot_unlocked(rows, fieldnames)
    return manifest, added


def safe_video_path(root: Path, relative: str) -> Path | None:
    """Resolve a browser video path only when it remains under the configured root."""
    if not relative:
        return None
    candidate = Path(relative)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _split_behavior_ids(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,，]", value or "") if part.strip()]


def _validate_person_tag(value: object) -> str:
    if value not in PERSON_TAG_VALUES:
        raise ValueError("person_tag_list must be stranger, acquaintance, or null")
    return str(value)


class CsvConflictError(Exception):
    """Raised when a client tries to save against an older CSV revision."""


def _update_row(state: AppState, payload: dict[str, object]) -> dict[str, object]:
    if not state.ready:
        raise ValueError("choose a video folder first")
    sample_id = payload.get("sample_id")
    if not isinstance(sample_id, str) or not sample_id:
        raise ValueError("sample_id is required")
    person_tag = _validate_person_tag(payload.get("person_tag_list"))
    review = payload.get("review", False)
    if not isinstance(review, bool):
        raise ValueError("review must be true or false")
    expected_revision = payload.get("csv_revision")
    if expected_revision is not None and (
        not isinstance(expected_revision, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_revision)
    ):
        raise ValueError("csv_revision must be a SHA-256 hex digest")

    with state.lock:
        if expected_revision is not None and state._revision_unlocked() != expected_revision:
            raise CsvConflictError("CSV was modified externally; reload before saving")
        rows, fieldnames = state._snapshot_unlocked()
        mode = detect_manifest_mode(fieldnames)
        reference_manifest = is_reference_manifest(fieldnames)
        video_path = payload.get("video_path")
        if video_path is not None and not isinstance(video_path, str):
            raise ValueError("video_path must be a string")
        row = None
        if video_path:
            row = next((item for item in rows if item.get("video_path") == video_path), None)
        if row is None:
            row = next((item for item in rows if item.get("sample_id") == sample_id), None)
        if row is None:
            raise LookupError("sample_id not found")

        if mode == "events":
            provided_events = payload.get("events")
            existing_types = _split_behavior_ids(row.get("behavior_id", ""))
            if not existing_types:
                existing_types = [event["event_type"] for event in parse_events(row.get("events", ""), [])]
            permitted = set(BEHAVIOR_LABELS) | set(existing_types)
            events = validate_events(provided_events, permitted, review)
            row["events"] = events_to_csv_value(events)
            labels = [str(event["event_type"]) for event in events]
            row["behavior_id"] = ",".join(labels)
            if reference_manifest:
                behavior_class = folder_behavior_class(state.video_root, Path(row.get("video_path", "")))
                if behavior_class:
                    row["behavior_class"] = behavior_class
        else:
            start = payload.get("start_time")
            end = payload.get("end_time")
            if not isinstance(start, str) or not isinstance(end, str):
                raise ValueError("start_time and end_time are required")
            start_ms, end_ms = parse_time_text(start), parse_time_text(end)
            if start_ms is not None and end_ms is not None and end_ms <= start_ms:
                raise ValueError("event end time must be later than its start time")
            if review and (start_ms is None or end_ms is None):
                raise ValueError("reviewed simple rows need start and end times")
            row["start_time"] = start.strip() or "null"
            row["end_time"] = end.strip() or "null"

        if "person_tag_list" in fieldnames:
            row["person_tag_list"] = person_tag
        if "review_status" in fieldnames:
            row["review_status"] = "reviewed" if review else "pending"
        backup = write_csv_atomic(state.csv_path, rows, fieldnames, state.csv_encoding, state.backups)
        state._set_snapshot_unlocked(rows, fieldnames)
    return {
        "ok": True,
        "review_status": row.get("review_status", "ready" if review else "pending"),
        "behavior_class": row.get("behavior_class", ""),
        "backup_name": backup.name if backup else "",
        "csv_revision": state._revision_cache or "",
    }


def _choose_video_root_tk() -> Path | None:
    """Use Tk as a fallback on platforms without the Windows dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="选择视频文件夹")
        window.destroy()
        return Path(selected) if selected else None
    except Exception as error:  # pragma: no cover - depends on desktop availability
        raise ValueError(f"folder picker is unavailable: {error}") from error


def _process_has_visible_window(process_id: int) -> bool:
    """Return whether a Windows process owns a visible top-level window."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        found = False
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def inspect_window(window: int, _: int) -> bool:
            nonlocal found
            owner = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
            if owner.value == process_id and user32.IsWindowVisible(window):
                alpha = ctypes.c_ubyte(255)
                flags = ctypes.c_ulong()
                is_layered = user32.GetLayeredWindowAttributes(
                    window, None, ctypes.byref(alpha), ctypes.byref(flags)
                )
                if is_layered and flags.value & 0x2 and alpha.value == 0:
                    return True
                found = True
                return False
            return True

        user32.EnumWindows(callback_type(inspect_window), 0)
        return found
    except (AttributeError, OSError):
        return False


def _terminate_picker_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Terminate and reap a picker process without leaving it orphaned."""
    try:
        process.terminate()
    except OSError:
        pass
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=2)


def choose_video_root() -> Path | None:
    """Open a desktop folder picker without requiring Tk in the HTTP process."""
    if os.name != "nt":
        return _choose_video_root_tk()

    dialog_script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.ShowInTaskbar=$false;"
        "$owner.FormBorderStyle=[System.Windows.Forms.FormBorderStyle]::FixedToolWindow;"
        "$owner.StartPosition=[System.Windows.Forms.FormStartPosition]::CenterScreen;"
        "$owner.Size=New-Object System.Drawing.Size(1,1);"
        "$owner.Opacity=0;"
        "$owner.TopMost=$true;"
        "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$dialog.Description='选择视频文件夹';"
        "$dialog.ShowNewFolderButton=$false;"
        "try{$owner.Show();$owner.Activate();"
        "$result=$dialog.ShowDialog($owner);"
        "if($result -eq [System.Windows.Forms.DialogResult]::OK) "
        "{[Console]::WriteLine($dialog.SelectedPath)}}"
        "finally{$dialog.Dispose();$owner.Close();$owner.Dispose()}"
    )
    command = ["powershell.exe", "-NoProfile", "-STA", "-Command", dialog_script]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        raise ValueError(f"folder picker is unavailable: {error}") from error

    deadline = time.monotonic() + PICKER_STARTUP_TIMEOUT_SECONDS
    visible = False
    while process.poll() is None:
        if _process_has_visible_window(process.pid):
            visible = True
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(PICKER_POLL_INTERVAL_SECONDS)

    if process.poll() is None and not visible:
        _terminate_picker_process(process)
        raise ValueError(
            "系统文件夹选择器不可用，请在路径框中输入视频目录并点击“按路径导入”"
        )

    try:
        stdout, stderr = process.communicate(
            timeout=PICKER_SELECTION_TIMEOUT_SECONDS if visible else 2
        )
    except subprocess.TimeoutExpired as error:
        _terminate_picker_process(process)
        raise ValueError("文件夹选择超时，请重试或使用“按路径导入”") from error

    if process.returncode != 0:
        detail = stderr.strip() or f"exit code {process.returncode}"
        raise ValueError(f"folder picker is unavailable: {detail}")
    selected = stdout.strip()
    return Path(selected) if selected else None


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>视频事件标注</title>
<style>
:root{color-scheme:dark;--bg:#15181c;--panel:#20262d;--panel-2:#292f37;--line:#404955;--text:#edf2f7;--muted:#aab5c2;--blue:#3b98d1;--blue-strong:#176d9f;--green:#2b9b69;--amber:#c68c2f;--red:#c55555;--focus:#75c8ff}*{box-sizing:border-box}html,body{height:100%;margin:0}body{background:var(--bg);color:var(--text);font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif;overflow:hidden;font-size:14px}button,input,select{font:inherit}button{border:1px solid var(--line);border-radius:5px;background:#303842;color:var(--text);cursor:pointer;min-height:32px;padding:5px 9px;transition:background .16s ease,border-color .16s ease}button:hover:not(:disabled){background:#3b4652}button:active:not(:disabled){background:#27313a}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--focus);outline-offset:2px}button:disabled{cursor:not-allowed;opacity:.48}.app{display:grid;grid-template-rows:auto minmax(0,1fr);height:100%}.topbar{align-items:center;background:#1b2026;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:12px;min-height:48px;padding:6px 14px}.topbar h1{font-size:15px;font-weight:650;margin:0}.dataset{color:var(--muted);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.spacer{flex:1}.speed{align-items:center;display:flex;gap:4px}.speed span{color:var(--muted);font-size:12px;margin-right:2px}.speed button{min-height:27px;padding:2px 8px}.speed button.active{background:var(--blue-strong);border-color:#64b9ed}.import{background:var(--blue-strong);border-color:#58b2e8;font-weight:600}.root-import{align-items:center;display:flex;gap:6px;min-width:0}.root-import input{width:220px}.root-import button{white-space:nowrap}.main{display:grid;grid-template-columns:minmax(0,1fr) 390px;min-height:0}.viewer{background:#080a0d;display:flex;flex-direction:column;min-width:0}.video-wrap{align-items:center;display:flex;flex:1;justify-content:center;min-height:0;padding:12px}video{background:#000;display:block;max-height:100%;max-width:100%;object-fit:contain}.meta{background:#1b2026;border-top:1px solid var(--line);color:var(--muted);font-size:12px;min-height:35px;overflow:hidden;padding:9px 12px;text-overflow:ellipsis;white-space:nowrap}.meta strong{color:#8dd1f7}.side{background:var(--panel);border-left:1px solid var(--line);display:flex;flex-direction:column;min-height:0}.scroll{overflow:auto;padding:12px}.section{border-bottom:1px solid var(--line);padding:11px 12px}.section-title{color:var(--muted);font-size:12px;font-weight:600;margin-bottom:8px}.tag-group{display:grid;gap:6px;grid-template-columns:repeat(3,1fr)}.tag.selected{background:#2d779e;border-color:#75c8ff}.tag[data-tag="null"].selected{background:#4a525b;border-color:#aab5c2}.behavior-add{display:grid;gap:6px;grid-template-columns:minmax(0,1fr) auto}select,input{background:#151a20;border:1px solid var(--line);border-radius:5px;color:var(--text);min-height:32px;padding:5px 8px}select{width:100%}.event-list{display:grid;gap:8px;margin-top:8px}.event{background:var(--panel-2);border:1px solid var(--line);border-radius:6px;padding:9px}.event-head{align-items:center;display:flex;gap:8px}.event-name{color:#8dd1f7;flex:1;font-size:13px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.icon{min-width:28px;padding:2px 5px}.time-row{align-items:center;display:grid;gap:6px;grid-template-columns:35px minmax(0,1fr) auto auto;margin-top:7px}.time-row label{color:var(--muted);font-size:12px}.time-row input{font-family:Consolas,"Cascadia Mono",monospace;text-align:center}.capture{font-size:12px;padding:3px 7px}.loop.active{background:#207a55;border-color:#5cc89a}.actions{display:grid;gap:6px;grid-template-columns:32px 32px minmax(0,1fr) minmax(0,1fr);margin-top:10px}.primary{background:var(--blue-strong);border-color:#58b2e8;font-weight:600}.review{background:#207a55;border-color:#5cc89a;font-weight:600}.review:hover:not(:disabled){background:var(--green)}.status{border-top:1px solid var(--line);color:#98d9b6;font-size:12px;min-height:34px;padding:9px 12px}.status.error{background:#342226;color:#ffacac}.filters{align-items:center;border-top:1px solid var(--line);display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px}.progress{color:var(--muted);flex:1;font-size:11px;font-variant-numeric:tabular-nums;min-width:0}.filters select{flex:0 0 116px}.list{border-top:1px solid var(--line);flex:1;overflow:auto;padding:5px}.item{align-items:center;border:1px solid transparent;border-radius:5px;cursor:pointer;display:flex;gap:7px;min-height:37px;padding:6px}.item:hover{background:#2a313a}.item.active{background:#203d4d;border-color:#346d87}.number{color:#7f8d9b;font-variant-numeric:tabular-nums;text-align:right;width:28px}.sample{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.badge{border:1px solid #59636e;border-radius:3px;color:var(--muted);font-size:10px;padding:2px 4px;white-space:nowrap}.badge.reviewed{border-color:#43986f;color:#92ddae}.badge.needs-time{border-color:#b77e2a;color:#f2bd69}.empty{color:var(--muted);font-size:13px;line-height:1.5;padding:18px 10px;text-align:center}@media(max-width:820px){body{overflow:auto}.app{height:auto;min-height:100%}.main{grid-template-columns:1fr;grid-template-rows:minmax(300px,55vh) auto}.side{border-left:0;border-top:1px solid var(--line);min-height:550px}.scroll{max-height:520px}.topbar{gap:8px}.dataset{display:none}.speed span{display:none}.root-import{flex:1 1 100%;order:5}.root-import input{flex:1;width:auto}}@media(max-width:470px){.topbar{padding:6px 8px}.topbar h1{font-size:14px}.speed button{padding:2px 5px}.import{font-size:12px;padding:4px 7px}.root-import button{font-size:12px;padding:4px 7px}.main{grid-template-rows:minmax(260px,48vh) auto}.side{min-height:520px}.actions{grid-template-columns:32px 32px 1fr}.review{grid-column:1/-1}}
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <h1>视频事件标注</h1><span id="dataset" class="dataset">未选择视频目录</span><span class="spacer"></span>
    <div class="speed"><span>倍速</span><button data-speed="0.5">0.5x</button><button class="active" data-speed="1">1x</button><button data-speed="2">2x</button></div>
    <div class="root-import"><input id="video-root-path" type="text" placeholder="D:\\videos" aria-label="视频目录路径"><button id="import-path" class="import">按路径导入</button></div>
    <button id="import-folder" class="import">导入视频文件夹</button>
  </header>
  <main class="main">
    <section class="viewer"><div class="video-wrap"><video id="video" controls preload="metadata"></video></div><div id="meta" class="meta">导入视频文件夹后开始审核</div></section>
    <aside class="side">
      <div class="scroll">
        <section class="section"><div class="section-title">人员标签</div><div id="person-tags" class="tag-group"><button class="tag" data-tag="stranger">陌生人</button><button class="tag" data-tag="acquaintance">熟人</button><button class="tag" data-tag="null">未判断</button></div></section>
        <section id="events-section" class="section"><div class="section-title">行为与时间段</div><div class="behavior-add"><select id="behavior-picker"><option value="person_fall">person_fall</option><option value="climb_fence">climb_fence</option><option value="peep_car_window">peep_car_window</option><option value="pickup_package">pickup_package</option><option value="linger_wander">linger_wander</option><option value="stay_long">stay_long</option><option value="cat_enter_frame">cat_enter_frame</option><option value="dog_enter_frame">dog_enter_frame</option><option value="car_enter_frame">car_enter_frame</option><option value="stranger_enter_frame">stranger_enter_frame</option><option value="approach_risk_zone">approach_risk_zone</option><option value="normal_scene">normal_scene</option></select><button id="add-event-segment">新建事件片段</button></div><div class="behavior-add"><input id="custom-behavior" maxlength="64" placeholder="自定义行为标签" aria-label="自定义行为标签"><button id="add-custom-event">添加自定义片段</button></div><div id="event-list" class="event-list"></div></section>
        <section class="section"><div class="actions"><button id="previous-row" class="icon" title="上一条" aria-label="上一条">&larr;</button><button id="next-row" class="icon" title="下一条" aria-label="下一条">&rarr;</button><button id="save-draft" class="primary">保存草稿</button><button id="review-next" class="review">审核并下一条</button></div></section>
      </div>
      <div id="status" class="status">就绪</div><div class="filters"><span id="progress" class="progress" aria-live="polite"></span><select id="filter"><option value="all">全部视频</option><option value="pending">待审核</option><option value="reviewed">已审核</option><option value="needs-time">需补时间</option></select></div><div id="list" class="list"></div>
    </aside>
  </main>
</div>
<script>
const $=id=>document.getElementById(id),video=$("video"),eventList=$("event-list");
let rows=[],current=-1,mode=null,speed=1,dirty=false,selectedTag="null",csvRevision="";
const CSV_CONFLICT_MESSAGE="CSV 已被外部修改，当前保存未覆盖；请刷新页面后重新确认";
function setStatus(text,error=false){const box=$("status");box.textContent=text;box.className=error?"status error":"status"}
function timeText(ms){if(ms===null||ms===undefined)return "";const total=Math.trunc(ms),hours=Math.floor(total/3600000),minutes=Math.floor(total%3600000/60000),seconds=Math.floor(total%60000/1000),milliseconds=total%1000;return `${hours}:${String(minutes).padStart(2,"0")}:${String(seconds).padStart(2,"0")}.${String(milliseconds).padStart(3,"0")}`}
function parseTime(value){const text=value.trim().toLowerCase();if(!text||text==="null")return null;const match=text.match(/^(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$/);if(!match)throw new Error("时间格式应为 0:00:07");const[,h,m,s]=match;if(Number(m)>=60||Number(s)>=60)throw new Error("时间格式不合法");return Math.round((Number(h)*3600+Number(m)*60+Number(s))*1000)}
async function request(url,options){const response=await fetch(url,options);let body={};try{body=await response.json()}catch{throw new Error("服务器返回了无效响应")}if(!response.ok){const error=new Error(body.error||"操作失败");error.status=response.status;throw error}return body}
function eventState(row){if(mode==="simple"){return row.start_time&&row.start_time!=="null"&&row.end_time&&row.end_time!=="null"?"ready":"needs-time"}const events=row.events||[];if(events.length===0)return "needs-time";if(events.length===1&&events[0].event_type==="normal_scene")return "ready";return events.every(item=>item.start_time_ms!==null&&item.end_time_ms!==null)?"ready":"needs-time"}
function isVisible(row){const filter=$("filter").value;if(filter==="all")return true;if(filter==="needs-time")return eventState(row)==="needs-time";return (row.review_status||"pending")===filter}
function renderList(){const list=$("list");list.replaceChildren();const visible=rows.map((row,index)=>({row,index})).filter(item=>isVisible(item.row));if(!visible.length){const empty=document.createElement("div");empty.className="empty";empty.textContent=rows.length?"当前筛选没有匹配的视频":"尚未导入视频";list.append(empty);return}for(const{row,index}of visible){const item=document.createElement("button");item.className="item"+(index===current?" active":"");item.type="button";const number=document.createElement("span");number.className="number";number.textContent=index+1;const name=document.createElement("span");name.className="sample";name.textContent=row.sample_id;name.title=row.sample_id;const badge=document.createElement("span");badge.className="badge "+(row.review_status==="reviewed"?"reviewed":eventState(row)==="needs-time"?"needs-time":"");badge.textContent=row.review_status==="reviewed"?"已审核":eventState(row)==="needs-time"?"需补时间":"待审核";item.append(number,name,badge);item.onclick=()=>openRow(index);list.append(item)}}
function setTag(value){selectedTag=["stranger","acquaintance","null"].includes(value)?value:"null";document.querySelectorAll(".tag").forEach(button=>button.classList.toggle("selected",button.dataset.tag===selectedTag))}
function changeDirty(){dirty=true}
function makeButton(text,className=""){const button=document.createElement("button");button.type="button";button.textContent=text;if(className)button.className=className;return button}
function makeTimeRow(label,value,capture){const row=document.createElement("div");row.className="time-row";const labelEl=document.createElement("label");labelEl.textContent=label;const input=document.createElement("input");input.value=timeText(value);input.placeholder="0:00:00.000";input.inputMode="decimal";input.addEventListener("input",changeDirty);const captureButton=makeButton("截取","capture");captureButton.onclick=()=>{input.value=timeText(Math.floor(video.currentTime*1000));changeDirty()};const clear=makeButton("清空","capture");clear.onclick=()=>{input.value="";changeDirty()};row.append(labelEl,input,captureButton,clear);return{row,input}}
function renderEventCard(event){const card=document.createElement("article");card.className="event";card.dataset.eventType=event.event_type;const head=document.createElement("div");head.className="event-head";const name=document.createElement("span");name.className="event-name";name.textContent=event.event_type;const remove=makeButton("删除","icon");remove.title="删除该行为";remove.onclick=()=>{card.remove();changeDirty()};head.append(name,remove);const start=makeTimeRow("开始",event.start_time_ms);const end=makeTimeRow("结束",event.end_time_ms);card.append(head,start.row,end.row);eventList.append(card)}
function renderSimple(row){eventList.replaceChildren();const card=document.createElement("article");card.className="event";card.dataset.eventType="__simple__";const title=document.createElement("div");title.className="event-name";title.textContent="行为时间段";const start=makeTimeRow("开始",parseTimeSafe(row.start_time));const end=makeTimeRow("结束",parseTimeSafe(row.end_time));card.append(title,start.row,end.row);eventList.append(card)}
function parseTimeSafe(text){try{return parseTime(text||"")}catch{return null}}
function renderEvents(row){eventList.replaceChildren();if(mode==="simple"){renderSimple(row);return}for(const event of row.events||[])renderEventCard(event)}
function currentEvents(){return[...eventList.querySelectorAll(".event")].map(card=>({event_type:card.dataset.eventType,start_time_ms:parseTime(card.querySelectorAll("input")[0].value),end_time_ms:parseTime(card.querySelectorAll("input")[1].value)}))}
function buildPayload(review){if(current<0)throw new Error("没有可保存的视频");const row=rows[current];if(mode==="simple"){const inputs=eventList.querySelectorAll("input");return{sample_id:row.sample_id,person_tag_list:selectedTag,start_time:inputs[0].value.trim()||"null",end_time:inputs[1].value.trim()||"null",review}}return{sample_id:row.sample_id,person_tag_list:selectedTag,events:currentEvents(),review}}
async function save(review=false){if(current<0)return false;let payload;try{payload=buildPayload(review)}catch(error){setStatus(error.message,true);return false}setStatus(review?"正在审核...":"正在保存...");try{const result=await request("/api/update",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});rows[current].person_tag_list=selectedTag;rows[current].review_status=result.review_status;if(mode==="simple"){rows[current].start_time=payload.start_time;rows[current].end_time=payload.end_time}else{rows[current].events=payload.events;rows[current].behavior_id=payload.events.map(event=>event.event_type).join(",")}dirty=false;renderList();setStatus(result.review_status==="reviewed"?"已审核":"草稿已保存");return true}catch(error){setStatus(error.message||"保存失败",true);return false}}
async function openRow(index){if(index<0||index>=rows.length)return;if(current!==index&&dirty&&!(await save(false)))return;current=index;const row=rows[index];video.src=row.video_url;video.playbackRate=speed;$("meta").replaceChildren();const strong=document.createElement("strong");strong.textContent=row.sample_id;$("meta").append(strong,document.createTextNode(`  |  ${row.behavior_id||"未选择行为"}  |  ${row.data_stratum||""}`));setTag(row.person_tag_list);renderEvents(row);dirty=false;renderList();setStatus(row.review_status==="reviewed"?"该视频已审核":"待审核")}
function addBehavior(){if(mode!=="events"||current<0)return;const value=$("behavior-picker").value;const existing=[...eventList.querySelectorAll(".event")].map(card=>card.dataset.eventType);if(existing.includes(value)){setStatus("该行为已存在",true);return}if(value==="normal_scene"&&existing.length&&!confirm("添加 normal_scene 会清除其他未保存行为，是否继续？"))return;if(value!=="normal_scene"&&existing.includes("normal_scene")&&!confirm("添加正例会清除 normal_scene，是否继续？"))return;if(value==="normal_scene")eventList.replaceChildren();if(value!=="normal_scene"&&existing.includes("normal_scene"))eventList.replaceChildren();renderEventCard({event_type:value,start_time_ms:null,end_time_ms:null});changeDirty()}
async function load(){try{const status=await request("/api/status");mode=status.mode;$("dataset").textContent=status.ready?`${status.video_root_name} / ${status.csv_name}`:"未选择视频目录";$("events-section").style.display=status.ready?"block":"none";rows=await request("/api/videos");renderList();if(rows.length)await openRow(0);else setStatus(status.ready?"没有发现可播放视频":"请选择一个视频文件夹")}catch(error){setStatus(error.message,true)}}
async function importVideoRoot(){
  const input=$("video-root-path"),path=input.value.trim();
  if(!path){setStatus("请输入视频目录",true);input.focus();return}
  setStatus("正在导入视频目录...");
  try{
    await request("/api/import-folder",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({video_root:path})});
    current=-1;dirty=false;await load();setStatus("视频目录已导入");
  }catch(error){setStatus(error.message,true)}
}
$("import-path").onclick=importVideoRoot;
$("video-root-path").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();importVideoRoot()}});
async function importWithFolderPicker(){
  const button=$("import-folder");
  button.disabled=true;
  setStatus("正在打开文件夹选择器...");
  try{
    await request("/api/import-folder",{method:"POST"});
    current=-1;dirty=false;await load();setStatus("视频目录已导入");
  }catch(error){setStatus(error.message,true)}
  finally{button.disabled=false}
}
$("import-folder").onclick=importWithFolderPicker;
$("add-event-segment").onclick=()=>addBehavior();$("save-draft").onclick=()=>save(false);$("review-next").onclick=async()=>{if(await save(true)){const next=rows.findIndex((row,index)=>index>current&&isVisible(row));if(next>=0)await openRow(next)}};$("filter").onchange=renderList;document.querySelectorAll(".tag").forEach(button=>button.onclick=()=>{setTag(button.dataset.tag);changeDirty()});document.querySelectorAll(".speed button").forEach(button=>button.onclick=()=>{document.querySelectorAll(".speed button").forEach(item=>item.classList.remove("active"));button.classList.add("active");speed=Number(button.dataset.speed);video.playbackRate=speed});document.addEventListener("keydown",event=>{if(event.target.tagName==="INPUT"||event.target.tagName==="SELECT")return;if(event.key==="ArrowLeft")moveVisibleRow(-1);if(event.key==="ArrowRight")moveVisibleRow(1);if(event.key===" "){event.preventDefault();video.paused?video.play():video.pause()}});load();
</script>
<script>
function eventState(row){if(mode==="simple"){const start=parseTimeSafe(row.start_time),end=parseTimeSafe(row.end_time);return start!==null&&end!==null&&end>start?"ready":"needs-time"}const events=row.events||[];if(events.length===0)return "needs-time";if(events.length===1&&events[0].event_type==="normal_scene")return "ready";return events.every(item=>item.start_time_ms!==null&&item.end_time_ms!==null&&item.end_time_ms>item.start_time_ms)?"ready":"needs-time"}
function isVisible(row){const filter=$("filter").value;return filter==="all"||eventState(row)===filter}
function renderList(){const list=$("list");list.replaceChildren();const visible=rows.map((row,index)=>({row,index})).filter(item=>isVisible(item.row));if(!visible.length){const empty=document.createElement("div");empty.className="empty";empty.textContent=rows.length?"当前筛选没有匹配的视频":"尚未导入视频";list.append(empty);return}for(const{row,index}of visible){const item=document.createElement("button");item.className="item"+(index===current?" active":"");item.type="button";const number=document.createElement("span");number.className="number";number.textContent=index+1;const name=document.createElement("span");name.className="sample";name.textContent=row.sample_id;name.title=row.sample_id;const state=eventState(row);const badge=document.createElement("span");badge.className="badge "+(state==="needs-time"?"needs-time":"reviewed");badge.textContent=state==="needs-time"?"需补时间":"可审核";item.append(number,name,badge);item.onclick=()=>openRow(index);list.append(item)}}
function buildPayload(review){if(current<0)throw new Error("没有可保存的视频");const row=rows[current];if(mode==="simple"){const inputs=eventList.querySelectorAll("input");return{sample_id:row.sample_id,video_path:row.video_path,person_tag_list:selectedTag,start_time:inputs[0].value.trim()||"null",end_time:inputs[1].value.trim()||"null",review}}return{sample_id:row.sample_id,video_path:row.video_path,person_tag_list:selectedTag,events:currentEvents(),review}}
async function save(review=false){if(current<0)return false;let payload;try{payload=buildPayload(review)}catch(error){setStatus(error.message,true);return false}setStatus(review?"正在审核...":"正在保存...");try{const result=await request("/api/update",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});rows[current].person_tag_list=selectedTag;if(mode==="simple"){rows[current].start_time=payload.start_time;rows[current].end_time=payload.end_time}else{rows[current].events=payload.events;rows[current].behavior_id=payload.events.map(event=>event.event_type).join(",");rows[current].behavior_class=result.behavior_class||rows[current].behavior_class}dirty=false;renderList();setStatus(review?"已审核":"草稿已保存");return true}catch(error){setStatus(error.message||"保存失败",true);return false}}
async function openRow(index){if(index<0||index>=rows.length)return;if(current!==index&&dirty&&!(await save(false)))return;current=index;const row=rows[index];video.src=row.video_url;video.playbackRate=speed;$("meta").replaceChildren();const strong=document.createElement("strong");strong.textContent=row.sample_id;const details=[row.behavior_class||row.behavior_id||"未选择行为",row.lighting||""].filter(Boolean);$("meta").append(strong,document.createTextNode(`  |  ${details.join("  |  ")}`));setTag(row.person_tag_list);renderEvents(row);dirty=false;renderList();setStatus(eventState(row)==="needs-time"?"需补时间":"可审核")}
$("filter").innerHTML='<option value="all">全部视频</option><option value="needs-time">需补时间</option><option value="ready">可审核</option>';$("filter").onchange=renderList;
</script>
<script>
let loopRange=null;
function cardRange(card){const inputs=card.querySelectorAll("input");if(inputs.length<2)return null;try{const start=parseTime(inputs[0].value),end=parseTime(inputs[1].value);return start!==null&&end!==null&&end>start?{start:start/1000,end:end/1000}:null}catch{return null}}
function stopLoop(){loopRange=null;eventList.querySelectorAll(".loop.active").forEach(button=>button.classList.remove("active"))}
function updateLoopButton(card){const button=card.querySelector(".loop");if(button)button.disabled=!cardRange(card)}
function addLoopControl(card,head){const button=makeButton("循环片段","capture loop");button.title="重复播放该行为的标注时间段";button.onclick=()=>{const range=cardRange(card);if(!range){setStatus("请先填写有效的开始和结束时间",true);return}if(loopRange&&loopRange.card===card){stopLoop();return}stopLoop();loopRange={card,start:range.start,end:range.end};button.classList.add("active");video.currentTime=range.start;video.play().catch(()=>setStatus("浏览器阻止了自动播放，请点击视频后重试",true))};head.append(button);card.querySelectorAll(".time-row input,.time-row .capture").forEach(control=>control.addEventListener(control.tagName==="INPUT"?"input":"click",()=>{stopLoop();updateLoopButton(card)}));updateLoopButton(card)}
function eventTypeOptions(events){const labels=[...$("behavior-picker").options].map(option=>option.value);for(const event of events)if(!labels.includes(event.event_type))labels.push(event.event_type);return labels}
function changeEventType(card,value){let events;try{events=currentEvents()}catch(error){setStatus(error.message,true);return}const index=[...eventList.querySelectorAll(".event")].indexOf(card);if(index<0||events[index].event_type===value)return;if(value==="normal_scene"){if(events.length>1&&!confirm("选择 normal_scene 会清除其他未保存行为，是否继续？")){card.querySelector(".event-type").value=events[index].event_type;return}events=[{event_type:"normal_scene",start_time_ms:null,end_time_ms:null}]}else{if(events[index].event_type==="normal_scene"){events[index].start_time_ms=null;events[index].end_time_ms=null}events[index].event_type=value;events=events.filter(event=>event.event_type!=="normal_scene")}stopLoop();renderEvents({events});changeDirty()}
function renderEventCard(event,labels){const card=document.createElement("article");card.className="event";card.dataset.eventType=event.event_type;const head=document.createElement("div");head.className="event-head";const select=document.createElement("select");select.className="event-type";select.style.flex="1";select.style.minWidth="0";select.setAttribute("aria-label","行为标签");for(const eventType of labels){const option=document.createElement("option");option.value=eventType;option.textContent=eventType;select.append(option)}select.value=event.event_type;select.addEventListener("change",()=>changeEventType(card,select.value));const remove=makeButton("删除","icon");remove.title="删除该行为";remove.onclick=()=>{if(loopRange&&loopRange.card===card)stopLoop();card.remove();changeDirty()};head.append(select,remove);card.append(head);if(event.event_type!=="normal_scene"){const start=makeTimeRow("开始",event.start_time_ms);const end=makeTimeRow("结束",event.end_time_ms);card.append(start.row,end.row);addLoopControl(card,head)}eventList.append(card)}
function renderSimple(row){eventList.replaceChildren();const card=document.createElement("article");card.className="event";card.dataset.eventType="__simple__";const head=document.createElement("div");head.className="event-head";const title=document.createElement("span");title.className="event-name";title.textContent="行为时间段";head.append(title);const start=makeTimeRow("开始",parseTimeSafe(row.start_time));const end=makeTimeRow("结束",parseTimeSafe(row.end_time));card.append(head,start.row,end.row);addLoopControl(card,head);eventList.append(card)}
function renderEvents(row){eventList.replaceChildren();if(mode==="simple"){renderSimple(row);return}const events=row.events||[],labels=eventTypeOptions(events);for(const event of events)renderEventCard(event,labels)}
function currentEvents(){return[...eventList.querySelectorAll(".event")].map(card=>{const inputs=card.querySelectorAll("input");return{event_type:card.dataset.eventType,start_time_ms:inputs.length?parseTime(inputs[0].value):null,end_time_ms:inputs.length?parseTime(inputs[1].value):null}})}
function addBehavior(){if(mode!=="events"||current<0)return;const value=$("behavior-picker").value;const existing=[...eventList.querySelectorAll(".event")].map(card=>card.dataset.eventType);if(value==="normal_scene"&&existing.length&&!confirm("添加 normal_scene 会清除其他未保存行为，是否继续？"))return;if(value!=="normal_scene"&&existing.includes("normal_scene")&&!confirm("添加正例会清除 normal_scene，是否继续？"))return;if(value==="normal_scene")eventList.replaceChildren();if(value!=="normal_scene"&&existing.includes("normal_scene"))eventList.replaceChildren();renderEventCard({event_type:value,start_time_ms:null,end_time_ms:null});changeDirty()}
function addEventSegment(value=$("behavior-picker").value){if(mode!=="events"||current<0)return;let events;try{events=currentEvents()}catch(error){setStatus(error.message,true);return}const hasNormal=events.some(event=>event.event_type==="normal_scene");if(value==="normal_scene"&&events.length&&!confirm("添加 normal_scene 会清除其他未保存行为，是否继续？"))return;if(value!=="normal_scene"&&hasNormal&&!confirm("添加正例会清除 normal_scene，是否继续？"))return;if(value==="normal_scene")events=[];if(value!=="normal_scene")events=events.filter(event=>event.event_type!=="normal_scene");events.push({event_type:value,start_time_ms:null,end_time_ms:null});stopLoop();renderEvents({events});changeDirty()}
function addCustomEventSegment(){const input=$("custom-behavior"),value=input.value.trim();if(!value||value.length>64||/[,\r\n]/.test(value)){setStatus("自定义标签需为 1-64 个字符，且不能包含逗号或换行",true);return}addEventSegment(value);input.value=""}
function visibleRows(){return rows.map((row,index)=>({row,index})).filter(item=>isVisible(item.row))}
function renderProgress(visible){const position=visible.findIndex(item=>item.index===current)+1;const ready=visible.filter(item=>eventState(item.row)==="ready").length;const needsTime=visible.length-ready;$("progress").textContent=`第 ${Math.max(position,0)} / ${visible.length} 条 | 可审核 ${ready} | 需补时间 ${needsTime}`;$("previous-row").disabled=position<=1;$("next-row").disabled=position<1||position>=visible.length}
function renderList(){const list=$("list");list.replaceChildren();const visible=visibleRows();renderProgress(visible);if(!visible.length){const empty=document.createElement("div");empty.className="empty";empty.textContent=rows.length?"当前筛选没有匹配的视频":"尚未导入视频";list.append(empty);return}for(const{row,index}of visible){const item=document.createElement("button");item.className="item"+(index===current?" active":"");item.type="button";const number=document.createElement("span");number.className="number";number.textContent=index+1;const name=document.createElement("span");name.className="sample";name.textContent=row.sample_id;name.title=row.sample_id;const state=eventState(row);const badge=document.createElement("span");badge.className="badge "+(state==="needs-time"?"needs-time":"reviewed");badge.textContent=state==="needs-time"?"需补时间":"可审核";item.append(number,name,badge);item.onclick=()=>openRow(index);list.append(item)}}
async function moveVisibleRow(delta){const visible=visibleRows();const position=visible.findIndex(item=>item.index===current);const target=visible[position+delta];if(target)await openRow(target.index)}
async function openRow(index){if(index<0||index>=rows.length)return;if(current!==index&&dirty&&!(await save(false)))return;stopLoop();current=index;const row=rows[index];video.src=row.video_url;video.playbackRate=speed;$("meta").replaceChildren();const strong=document.createElement("strong");strong.textContent=row.sample_id;const details=[row.behavior_class||row.behavior_id||"未选择行为",row.lighting||""].filter(Boolean);$("meta").append(strong,document.createTextNode(`  |  ${details.join("  |  ")}`));setTag(row.person_tag_list);renderEvents(row);dirty=false;renderList();setStatus(eventState(row)==="needs-time"?"需补时间":"可审核")}
video.addEventListener("timeupdate",()=>{if(loopRange&&video.currentTime>=loopRange.end){video.currentTime=loopRange.start;video.play().catch(()=>{})}});
video.addEventListener("ended",()=>{if(loopRange){video.currentTime=loopRange.start;video.play().catch(()=>{})}});
$("add-event-segment").onclick=addEventSegment;$("add-custom-event").onclick=addCustomEventSegment;$("custom-behavior").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();addCustomEventSegment()}});$("previous-row").onclick=()=>moveVisibleRow(-1);$("next-row").onclick=()=>moveVisibleRow(1);$("review-next").onclick=async()=>{if(await save(true))await moveVisibleRow(1)};
</script>
<script>
let focusedEventCard=null;
function captureShortcutTime(boundary){
  if(current<0){setStatus("请先选择视频",true);return}
  let card=focusedEventCard&&focusedEventCard.isConnected?focusedEventCard:null;
  if(!card)card=[...eventList.querySelectorAll(".event")].find(item=>item.dataset.eventType!=="normal_scene");
  if(!card){setStatus("当前没有可计时事件",true);return}
  const inputs=card.querySelectorAll("input");
  if(inputs.length<2){setStatus("当前事件不支持时间捕获",true);return}
  const input=inputs[boundary==="end"?1:0];
  input.value=timeText(Math.floor(video.currentTime*1000));
  input.dispatchEvent(new Event("input",{bubbles:true}));
  input.focus();
  setStatus(boundary==="end"?"已捕获结束时间":"已捕获开始时间");
}
async function moveNeedsTime(delta){
  const pending=rows.map((row,index)=>({row,index})).filter(item=>eventState(item.row)==="needs-time");
  const position=pending.findIndex(item=>item.index===current);
  const target=pending[position+delta]||(
    position<0?pending[delta>0?0:pending.length-1]:null
  );
  if(target)await openRow(target.index);else setStatus("没有更多需要补时间的视频");
}
eventList.addEventListener("focusin",event=>{
  const card=event.target.closest(".event");
  if(card)focusedEventCard=card;
});
document.addEventListener("keydown",async event=>{
  if(event.ctrlKey||event.metaKey||event.altKey)return;
  if(event.target.matches("input,select,textarea")||event.target.isContentEditable)return;
  const key=event.key.toLowerCase();
  if(["s","r","n","p","i","o","1","2","3"].includes(key))event.preventDefault();
  if(key==="s"){await save(false);return}
  if(key==="r"){if(await save(true))await moveVisibleRow(1);return}
  if(key==="n"){await moveNeedsTime(1);return}
  if(key==="p"){await moveNeedsTime(-1);return}
  if(key==="i"){captureShortcutTime("start");return}
  if(key==="o"){captureShortcutTime("end");return}
  if(["1","2","3"].includes(key)){
    const tags={"1":"stranger","2":"acquaintance","3":"null"};
    setTag(tags[key]);changeDirty();setStatus("人员标签已更新");
  }
});
</script>
<script>
const RESUME_STORAGE_KEY="video-labeler:resume:v1";
let resume=readResumeState(),resumeDatasetApplied="",resumeDatasetConsumed="";
function readResumeState(){
  try{
    const value=JSON.parse(localStorage.getItem(RESUME_STORAGE_KEY)||"null");
    if(!value||typeof value!=="object"||typeof value.dataset!=="string"||!value.dataset||typeof value.video_path!=="string"||typeof value.sample_id!=="string"||!value.video_path||!value.sample_id)return null;
    const filterOptions=["all","needs-time","ready"];
    const speedValue=Number(value.speed);
    return{
      dataset:value.dataset,
      video_path:value.video_path,
      sample_id:value.sample_id,
      filter:filterOptions.includes(value.filter)?value.filter:"all",
      speed:[0.5,1,2].includes(speedValue)?speedValue:1,
    };
  }catch{return null}
}
function writeResumeState(){
  if(current<0||!rows[current])return;
  const dataset=$("dataset").textContent.trim();
  if(!dataset)return;
  const row=rows[current];
  try{
    localStorage.setItem(RESUME_STORAGE_KEY,JSON.stringify({
      dataset,
      video_path:row.video_path,
      sample_id:row.sample_id,
      filter:$("filter").value,
      speed,
    }));
  }catch{}
}
const originalRenderList=renderList;
function resumeRenderList(){
  const dataset=$("dataset").textContent.trim();
  if(resumeDatasetApplied!==dataset){
    resumeDatasetApplied=dataset;
    if(resume&&resume.dataset===dataset){
      const filter=$("filter");
      if([...filter.options].some(option=>option.value===resume.filter))filter.value=resume.filter;
      speed=resume.speed;
      document.querySelectorAll(".speed button").forEach(button=>button.classList.toggle("active",Number(button.dataset.speed)===speed));
    }
  }
  return originalRenderList.apply(this,arguments);
}
renderList=resumeRenderList;
const originalOpenRow=openRow;
async function resumeOpenRow(index){
  const dataset=$("dataset").textContent.trim();
  let target=index;
  if(resumeDatasetConsumed!==dataset){
    resumeDatasetConsumed=dataset;
    if(resume&&resume.dataset===dataset){
      let match=-1;
      if(resume.video_path&&resume.video_path.length)match=rows.findIndex(row=>row.video_path===resume.video_path);
      if(match<0&&resume.sample_id)match=rows.findIndex(row=>row.sample_id===resume.sample_id);
      if(match>=0)target=match;
    }
  }
  const result=await originalOpenRow.call(this,target);
  writeResumeState();
  return result;
}
openRow=resumeOpenRow;
$("filter").onchange=resumeRenderList;
$("filter").addEventListener("change",writeResumeState);
document.querySelectorAll(".speed button").forEach(button=>button.addEventListener("click",writeResumeState));
window.addEventListener("beforeunload",writeResumeState);
</script>
<script>
const originalRequest=request;
request=async function(url,options){
  try{
    const result=await originalRequest(url,options);
    if(url==="/api/status")csvRevision=result.csv_revision||"";
    if(url==="/api/update"&&result.csv_revision)csvRevision=result.csv_revision;
    return result;
  }catch(error){
    if(error.message==="CSV was modified externally; reload before saving")error.status=409;
    if(error.status===409)error.message=CSV_CONFLICT_MESSAGE;
    throw error;
  }
};
const originalBuildPayload=buildPayload;
function buildPayloadWithRevision(review){const payload=originalBuildPayload(review);if(csvRevision)payload.csv_revision=csvRevision;return payload}
buildPayload=buildPayloadWithRevision;
</script>
</body>
</html>"""


def _normalize_html(html: str) -> str:
    """Remove legacy duplicate browser functions before serving the page."""
    lines = html.splitlines()
    scripts = [index for index, line in enumerate(lines) if line == "<script>"]
    if len(scripts) < 3:
        return html
    first_start, second_start, third_start = scripts[:3]
    first_duplicates = (
        "function eventState(",
        "function isVisible(",
        "function renderList(",
        "function renderEventCard(",
        "function renderSimple(",
        "function renderEvents(",
        "function currentEvents(",
        "function buildPayload(",
        "async function save(",
        "async function openRow(",
        "function addBehavior(",
    )
    normalized: list[str] = []
    for index, line in enumerate(lines):
        if line.endswith("load();"):
            line = line[: -len("load();")]
            if not line.strip():
                continue
        if first_start < index < second_start:
            if line.startswith(first_duplicates):
                continue
        if second_start < index < third_start and (
            line.startswith("function renderList(")
            or line.startswith("async function openRow(")
        ):
            continue
        normalized.append(line)
    load_insert_at = next(
        (index for index, line in enumerate(normalized) if line == "</body>"),
        len(normalized),
    )
    normalized.insert(load_insert_at, "load();")
    return "\n".join(normalized)


HTML = _normalize_html(HTML)


class LabelerHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], state: AppState):
        super().__init__(address, Handler)
        self.state = state


class Handler(BaseHTTPRequestHandler):
    server: LabelerHTTPServer

    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, body: dict[str, object] | list[object]) -> None:
        self.send_bytes(status, "application/json; charset=utf-8", json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _rows_for_client(self) -> list[dict[str, object]]:
        state = self.server.state
        if not state.ready:
            return []
        state.csv_revision()
        rows, fieldnames = state.snapshot()
        mode = detect_manifest_mode(fieldnames)
        output: list[dict[str, object]] = []
        for row in rows:
            item: dict[str, object] = dict(row)
            if mode == "events":
                behavior_ids = _split_behavior_ids(row.get("behavior_id", ""))
                item["events"] = parse_events(row.get("events", ""), behavior_ids)
            item["person_tag_list"] = row.get("person_tag_list") or "null"
            if not is_reference_manifest(fieldnames):
                item["review_status"] = row.get("review_status") or "pending"
            item["video_url"] = "/video/" + row.get("video_path", "").replace("\\", "/")
            output.append(item)
        return output

    def do_GET(self) -> None:
        request_path = unquote(urlparse(self.path).path)
        if request_path in ("/", "/index.html"):
            self.send_bytes(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
            return
        if request_path == "/api/status":
            try:
                self.send_json(200, self.server.state.status())
            except (ValueError, OSError) as error:
                self.send_json(400, {"ok": False, "error": str(error)})
            return
        if request_path == "/api/videos":
            try:
                self.send_json(200, self._rows_for_client())
            except (ValueError, OSError) as error:
                self.send_json(400, {"ok": False, "error": str(error)})
            return
        if request_path.startswith("/video/"):
            state = self.server.state
            source = safe_video_path(state.video_root, request_path[len("/video/"):]) if state.video_root else None
            if source is None or not source.is_file():
                self.send_error(404)
                return
            self.send_video(source)
            return
        self.send_error(404)

    def send_video(self, source: Path) -> None:
        size = source.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
            if not match:
                self.send_range_error(size)
                return
            first, last = match.groups()
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:
                    length = int(last)
                    if length <= 0:
                        raise ValueError
                    start = max(size - length, 0)
                else:
                    raise ValueError
            except ValueError:
                self.send_range_error(size)
                return
            if size == 0 or start >= size or end < start:
                self.send_range_error(size)
                return
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(source))[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with source.open("rb") as file:
            file.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = file.read(min(VIDEO_READ_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def send_range_error(self, size: int) -> None:
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        request_path = unquote(urlparse(self.path).path)
        if request_path == "/api/update":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be an object")
                self.send_json(200, _update_row(self.server.state, payload))
            except CsvConflictError as error:
                self.send_json(409, {"ok": False, "error": str(error)})
            except LookupError as error:
                self.send_json(404, {"ok": False, "error": str(error)})
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json(400, {"ok": False, "error": str(error)})
            except OSError as error:
                self.send_json(500, {"ok": False, "error": f"CSV write failed: {error}"})
            return
        if request_path == "/api/import-folder":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length)
                payload: dict[str, object] = {}
                if raw_body.strip():
                    decoded = json.loads(raw_body.decode("utf-8"))
                    if not isinstance(decoded, dict):
                        raise ValueError("request body must be an object")
                    payload = decoded
                if "video_root" in payload:
                    raw_root = payload["video_root"]
                    if not isinstance(raw_root, str) or not raw_root.strip():
                        raise ValueError("video_root is required")
                    root = Path(raw_root.strip())
                else:
                    root = choose_video_root()
                if root is None:
                    raise ValueError("no video folder was selected")
                manifest, added = apply_imported_root(self.server.state, root)
                self.send_json(200, {"ok": True, "added": added, **self.server.state.status()})
            except (ValueError, OSError, json.JSONDecodeError) as error:
                self.send_json(400, {"ok": False, "error": str(error)})
            return
        self.send_error(404)

    def log_message(self, *_: object) -> None:
        return


def create_server(state: AppState, port: int = 0) -> LabelerHTTPServer:
    return LabelerHTTPServer(("127.0.0.1", port), state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地视频事件标注工具")
    parser.add_argument("--video-root", type=Path, help="要导入或读取的视频根目录")
    parser.add_argument("--csv", type=Path, help="已有或要生成的 manifest CSV")
    parser.add_argument("--port", type=int, default=8765, help="本地网页端口，默认 8765")
    return parser


def build_state_from_args(args: argparse.Namespace) -> AppState:
    if args.csv and not args.video_root:
        csv_path = args.csv.resolve()
        if not csv_path.is_file():
            raise ValueError("--csv must exist when --video-root is omitted")
        return AppState.from_paths(csv_path, csv_path.parent)
    if not args.video_root:
        return AppState()

    root = args.video_root.resolve()
    if not root.is_dir():
        raise ValueError(f"video directory does not exist: {root}")
    csv_path = args.csv.resolve() if args.csv else root / "video_labeler_manifest.csv"
    if csv_path.exists():
        return AppState.from_paths(csv_path, root)
    if csv_path.parent != root:
        raise ValueError("a new --csv must be placed directly in --video-root")
    manifest, _ = import_video_directory(root, csv_path.name)
    return AppState.from_paths(manifest, root)


def main() -> None:
    args = build_parser().parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    try:
        state = build_state_from_args(args)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    server = create_server(state, args.port)
    print(f"打开浏览器: http://127.0.0.1:{server.server_port}")
    if state.ready:
        print(f"已加载: {state.csv_path.name}")
    else:
        print("请在网页中点击“导入视频文件夹”开始。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
