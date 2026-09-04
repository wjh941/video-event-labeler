import json

import pytest

from video_labeler.domain import Event, Person, Prediction, Sample
from video_labeler.services import AnnotationService


def test_event_and_people_saves_share_revision(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="clip.mp4"))
    service = AnnotationService(store, tmp_path)
    first = service.save_events("s1", [Event(sample_id="s1", event_type="person_fall")], expected_revision=0)
    second = service.save_people("s1", [Person(sample_id="s1", person_id="p1", age_group="adult")], expected_revision=first.revision)
    assert second.revision == first.revision + 1
    assert service.get_row("s1").person_count == 1


def test_row_projection_uses_safe_video_identifier(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="nested/clip.mp4"))
    row = AnnotationService(store, tmp_path).get_row("s1")
    assert row.video_url == "/video/s1"
    assert row.as_dict()["csv_revision"] == "0"


def test_service_paginates_and_counts_rows_by_status(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    for sample_id, status in (("s1", "draft"), ("s2", "reviewed"), ("s3", "draft")):
        store.upsert_sample(Sample(sample_id=sample_id, dataset_id="d", relative_path=f"{sample_id}.mp4", status=status))

    service = AnnotationService(store, tmp_path)

    assert service.count_rows() == 3
    assert service.count_rows("draft") == 2
    assert [row["sample_id"] for row in service.list_rows(offset=1, limit=1)] == ["s2"]
    assert [row["sample_id"] for row in service.list_rows(limit=10, filters={"status": "draft"})] == ["s1", "s3"]


def test_service_lists_prediction_records_with_filters(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="s1.mp4"))
    store.upsert_sample(Sample(sample_id="s2", dataset_id="d", relative_path="s2.mp4"))
    for prediction_id, sample_id, task in (("p1", "s1", "event"), ("p2", "s1", "person"), ("p3", "s2", "event")):
        store.upsert_prediction(Prediction(prediction_id, sample_id, task, json.dumps({"value": prediction_id}), "model", "v1", 0.8))
    store.decide_prediction("p2", "accepted", "reviewer", "2026-01-01T00:00:00.000Z")

    records = AnnotationService(store, tmp_path).list_prediction_records(status="draft", task="event")

    assert [item["prediction_id"] for item in records] == ["p1", "p3"]
    assert records[0]["label"] == {"value": "p1"}
    assert records[0]["review_status"] == "draft"
    assert records[0]["sample_revision"] == 0


def test_service_quality_snapshot_has_stats_and_report(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="s1.mp4"))

    snapshot = AnnotationService(store, tmp_path).quality_snapshot()

    assert set(snapshot) == {"stats", "quality", "generated_at"}
    assert snapshot["stats"]["sample_count"] == 1
    assert snapshot["quality"]["checked_samples"] == 1


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"limit": 0}, {"limit": 501}])
def test_service_rejects_invalid_pagination(tmp_path, store, kwargs):
    service = AnnotationService(store, tmp_path)

    with pytest.raises(ValueError):
        service.list_rows(**kwargs)
