import json

import pytest

from video_labeler.domain import Event, Person, Sample
from video_labeler.services import AnnotationService
from video_labeler.storage.sqlite_store import ConflictError


def _setup(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="clip.mp4"))
    return AnnotationService(store, tmp_path)


def test_save_records_canonical_before_and_after_json(tmp_path, store):
    service = _setup(tmp_path, store)
    result = service.save_events(
        "s1",
        [
            Event(sample_id="s1", event_type="z_event"),
            Event(sample_id="s1", event_type="a_event"),
        ],
        actor="reviewer",
        expected_revision=0,
    )

    row = store.get_revision("s1", result.revision)
    assert row is not None
    assert row["actor"] == "reviewer"
    assert json.loads(row["before_json"]) == {"events": [], "persons": []}
    after = json.loads(row["after_json"])
    assert [event["event_type"] for event in after["events"]] == ["a_event", "z_event"]
    assert after["persons"] == []


def test_restore_replaces_both_collections_and_creates_revision(tmp_path, store):
    service = _setup(tmp_path, store)
    first = service.save_events("s1", [Event(sample_id="s1", event_type="first")], actor="alice")
    service.save_people("s1", [Person(sample_id="s1", person_id="p1", age_group="adult")], actor="alice", expected_revision=first.revision)
    target = service.save_events("s1", [Event(sample_id="s1", event_type="second")], actor="alice", expected_revision=2)

    restored = service.restore_revision("s1", first.revision, actor="bob", expected_revision=target.revision)

    assert restored.revision == target.revision + 1
    assert [event.event_type for event in store.get_events("s1")] == ["first"]
    assert [person.person_id for person in store.get_persons("s1")] == ["p1"]
    audit = store.get_revision("s1", restored.revision)
    assert audit["actor"] == "bob"


def test_restore_rejects_stale_revision_without_mutation(tmp_path, store):
    service = _setup(tmp_path, store)
    first = service.save_events("s1", [Event(sample_id="s1", event_type="first")], actor="alice")
    latest = service.save_people("s1", [], actor="alice", expected_revision=first.revision)

    with pytest.raises(ConflictError):
        service.restore_revision("s1", first.revision, actor="bob", expected_revision=first.revision)

    assert store.sample_revision("s1") == latest.revision
    assert store.get_persons("s1") == []


def test_restore_people_revision_restores_explicit_empty_collection(tmp_path, store):
    service = _setup(tmp_path, store)
    empty = service.save_people("s1", [], actor="alice")
    populated = service.save_people(
        "s1", [Person(sample_id="s1", person_id="p1", age_group="adult")],
        actor="alice", expected_revision=empty.revision,
    )

    restored = service.restore_revision("s1", empty.revision, actor="bob", expected_revision=populated.revision)

    assert restored.revision == populated.revision + 1
    assert store.get_persons("s1") == []
