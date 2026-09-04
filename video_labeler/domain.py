"""Validated, immutable domain records shared by all annotation adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

AGE_GROUPS = ("child", "adult", "elderly", "unknown")
FACE_FAMILIARITY_VALUES = ("familiar", "stranger", "unknown", "not_visible")
BODY_FAMILIARITY_VALUES = FACE_FAMILIARITY_VALUES
SAMPLE_STATUSES = ("draft", "reviewed", "rejected")
MEDIA_MODALITIES = ("video", "audio", "transcript", "image")
ANNOTATION_SOURCES = ("human", "model", "imported")
REVIEW_STATUSES = ("draft", "accepted", "rejected")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _time_range(start: int | None, end: int | None, review_status: str) -> None:
    if start is not None and (not isinstance(start, int) or start < 0):
        raise ValueError("start_time_ms must be a non-negative integer or null")
    if end is not None and (not isinstance(end, int) or end < 0):
        raise ValueError("end_time_ms must be a non-negative integer or null")
    if (start is None or end is None) and review_status != "draft":
        raise ValueError("non-draft records require both event times")
    if start is not None and end is not None and end < start:
        raise ValueError("end_time_ms must be greater than or equal to start_time_ms")


@dataclass(frozen=True, slots=True)
class Sample:
    sample_id: str
    dataset_id: str | None = None
    relative_path: str = ""
    source_sha256: str | None = None
    status: str = "draft"
    schema_version: int = 1
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    revision: int = 0

    def __post_init__(self) -> None:
        _required(self.sample_id, "sample_id")
        if self.dataset_id is not None:
            _required(self.dataset_id, "dataset_id")
        if self.status not in SAMPLE_STATUSES:
            raise ValueError(f"status must be one of {SAMPLE_STATUSES}")
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")


@dataclass(frozen=True, slots=True)
class MediaAsset:
    sample_id: str
    modality: str
    uri: str
    duration_ms: int | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    metadata_json: str = "{}"
    source_sha256: str | None = None
    probe_status: str = "unknown"

    def __post_init__(self) -> None:
        _required(self.sample_id, "sample_id")
        _required(self.uri, "uri")
        if self.modality not in MEDIA_MODALITIES:
            raise ValueError(f"modality must be one of {MEDIA_MODALITIES}")
        for value, name in ((self.duration_ms, "duration_ms"), (self.width, "width"), (self.height, "height")):
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or null")
        if self.fps is not None and (not isinstance(self.fps, (int, float)) or self.fps < 0):
            raise ValueError("fps must be non-negative or null")
        try:
            parsed: Any = json.loads(self.metadata_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("metadata_json must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("metadata_json must contain a JSON object")


@dataclass(frozen=True, slots=True)
class Event:
    sample_id: str
    event_type: str
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    source: str = "human"
    confidence: float | None = None
    review_status: str = "draft"
    annotator: str | None = None
    revision: int = 0
    event_id: str = ""
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.sample_id, "sample_id")
        _required(self.event_type, "event_type")
        if self.source not in ANNOTATION_SOURCES:
            raise ValueError(f"source must be one of {ANNOTATION_SOURCES}")
        if self.review_status not in REVIEW_STATUSES:
            raise ValueError(f"review_status must be one of {REVIEW_STATUSES}")
        _time_range(self.start_time_ms, self.end_time_ms, self.review_status)
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")


@dataclass(frozen=True, slots=True)
class Person:
    sample_id: str
    person_id: str
    age_group: str
    face_familiarity: str = "unknown"
    body_reid_familiarity: str = "unknown"
    track_id: str | None = None
    source: str = "human"
    confidence: float | None = None
    review_status: str = "draft"
    annotator: str | None = None
    revision: int = 0
    person_record_id: str = ""

    def __post_init__(self) -> None:
        for value, name in ((self.sample_id, "sample_id"), (self.person_id, "person_id")):
            _required(value, name)
        if self.age_group not in AGE_GROUPS:
            raise ValueError(f"age_group must be one of {AGE_GROUPS}")
        if self.face_familiarity not in FACE_FAMILIARITY_VALUES:
            raise ValueError(f"face_familiarity must be one of {FACE_FAMILIARITY_VALUES}")
        if self.body_reid_familiarity not in BODY_FAMILIARITY_VALUES:
            raise ValueError(f"body_reid_familiarity must be one of {BODY_FAMILIARITY_VALUES}")
        if self.source not in ANNOTATION_SOURCES or self.review_status not in REVIEW_STATUSES:
            raise ValueError("invalid annotation source or review status")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")


@dataclass(frozen=True, slots=True)
class Evidence:
    sample_id: str
    modality: str
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    uri: str | None = None
    text: str | None = None
    source: str = "human"
    confidence: float | None = None
    evidence_id: str = ""

    def __post_init__(self) -> None:
        _required(self.sample_id, "sample_id")
        if self.modality not in MEDIA_MODALITIES:
            raise ValueError(f"modality must be one of {MEDIA_MODALITIES}")
        _time_range(self.start_time_ms, self.end_time_ms, "draft")
        if self.source not in ANNOTATION_SOURCES:
            raise ValueError(f"source must be one of {ANNOTATION_SOURCES}")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Prediction:
    prediction_id: str
    sample_id: str
    task: str
    label_json: str
    model_name: str
    model_version: str
    confidence: float
    created_at: str = field(default_factory=utc_now)
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in ((self.prediction_id, "prediction_id"), (self.sample_id, "sample_id"), (self.task, "task"), (self.model_name, "model_name"), (self.model_version, "model_version")):
            _required(value, name)
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        try:
            json.loads(self.label_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("label_json must be valid JSON") from exc
