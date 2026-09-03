"""Canonical JSON snapshots for annotation audit history."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .domain import Event, Person


def _event_payload(event: Event | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(event, Event):
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "start_time_ms": event.start_time_ms,
            "end_time_ms": event.end_time_ms,
            "source": event.source,
            "confidence": event.confidence,
            "review_status": event.review_status,
            "annotator": event.annotator,
            "revision": event.revision,
        }
    return dict(event)


def _person_payload(person: Person | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(person, Person):
        return {
            "person_record_id": person.person_record_id,
            "person_id": person.person_id,
            "track_id": person.track_id,
            "age_group": person.age_group,
            "face_familiarity": person.face_familiarity,
            "body_reid_familiarity": person.body_reid_familiarity,
            "source": person.source,
            "confidence": person.confidence,
            "review_status": person.review_status,
            "annotator": person.annotator,
            "revision": person.revision,
        }
    return dict(person)


def canonical_snapshot(events: Sequence[Event], people: Sequence[Person]) -> str:
    """Return stable JSON with sorted child records and keys."""
    event_payload = [_event_payload(event) for event in events]
    person_payload = [_person_payload(person) for person in people]
    event_payload.sort(key=lambda item: (
        item.get("event_type", ""),
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    ))
    person_payload.sort(key=lambda item: (
        item.get("person_id", ""),
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    ))
    return json.dumps({"events": event_payload, "persons": person_payload}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def snapshot_payload(value: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("events"), list) or not isinstance(parsed.get("persons"), list):
        raise ValueError("annotation snapshot must contain events and persons arrays")
    return parsed["events"], parsed["persons"]


__all__ = ["canonical_snapshot", "snapshot_payload"]
