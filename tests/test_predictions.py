import json

import pytest

from video_labeler.domain import Prediction, Sample
from video_labeler.services import AnnotationService


def _prediction(sample_id="s1", prediction_id="pred-1", task="event", label=None):
    if label is None:
        label = {"event_type": "person_fall", "start_time_ms": 100, "end_time_ms": 200}
    return Prediction(prediction_id, sample_id, task, json.dumps(label), "model", "v1", 0.8)


def _service(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="clip.mp4"))
    return AnnotationService(store, tmp_path)


def test_accept_event_prediction_creates_accepted_event_and_audit(tmp_path, store):
    service = _service(tmp_path, store)
    store.upsert_prediction(_prediction())

    result = service.accept_prediction("pred-1", actor="reviewer")

    event = store.get_events("s1")[0]
    assert event.event_type == "person_fall"
    assert event.review_status == "accepted"
    assert event.annotator == "reviewer"
    assert result.revision == 1
    assert store.prediction_record("pred-1")[1] == "accepted"
    assert store.get_revisions("s1")[-1]["actor"] == "reviewer"


def test_accept_person_prediction_preserves_empty_person_collection_contract(tmp_path, store):
    service = _service(tmp_path, store)
    store.upsert_prediction(_prediction(task="person", label={"person_id": "p1", "age_group": "adult"}))

    result = service.accept_prediction("pred-1", actor="reviewer")

    person = store.get_persons("s1")[0]
    assert person.person_id == "p1"
    assert person.review_status == "accepted"
    assert person.annotator == "reviewer"
    assert result.revision == 1


def test_reject_prediction_records_decision_without_mutating_annotations(tmp_path, store):
    service = _service(tmp_path, store)
    store.upsert_prediction(_prediction())

    service.reject_prediction("pred-1", actor="reviewer")

    assert store.get_events("s1") == []
    assert store.prediction_record("pred-1")[1] == "rejected"
    assert store.get_revisions("s1")[-1]["summary"] == "reject prediction pred-1"


def test_repeat_prediction_decision_fails_without_new_revision(tmp_path, store):
    service = _service(tmp_path, store)
    store.upsert_prediction(_prediction())
    service.reject_prediction("pred-1", actor="reviewer")

    with pytest.raises(KeyError):
        service.reject_prediction("pred-1", actor="reviewer")
    assert store.sample_revision("s1") == 1


def test_unsupported_prediction_label_fails_without_mutation(tmp_path, store):
    service = _service(tmp_path, store)
    store.upsert_prediction(_prediction(task="audio", label={"value": "unknown"}))

    with pytest.raises(ValueError):
        service.accept_prediction("pred-1", actor="reviewer")
    assert store.prediction_record("pred-1")[1] == "draft"
    assert store.sample_revision("s1") == 0


def test_list_predictions_delegates_to_store(tmp_path, store):
    service = _service(tmp_path, store)
    store.upsert_prediction(_prediction(prediction_id="pred-b"))
    store.upsert_prediction(_prediction(prediction_id="pred-a"))
    assert [item.prediction_id for item in service.list_predictions("s1")] == ["pred-a", "pred-b"]
