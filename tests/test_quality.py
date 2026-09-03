from __future__ import annotations

from video_labeler.domain import Event, MediaAsset, Person, Sample
from video_labeler.quality import dataset_stats, validate_dataset


def test_quality_report_flags_event_outside_duration(store):
    store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4"))
    store.upsert_media_asset(MediaAsset(sample_id="s1", modality="video", uri="a.mp4", duration_ms=1000, probe_status="ok"))
    store.replace_events("s1", [Event(sample_id="s1", event_type="fall", start_time_ms=900, end_time_ms=1200)])
    report = validate_dataset(store)
    assert any(item.code == "event_out_of_bounds" for item in report.errors)


def test_quality_and_stats_allow_zero_people(store):
    store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4", status="reviewed"))
    report = validate_dataset(store)
    assert report.checked_samples == 1
    stats = dataset_stats(store)
    assert stats["person_count"] == 0
    assert stats["completion_rate"] == 1.0


def test_draft_missing_media_is_warning_but_strict_is_error(store):
    store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4"))

    draft = validate_dataset(store, mode="draft")
    strict = validate_dataset(store, mode="strict")

    assert any(item.code == "missing_media" for item in draft.warnings)
    assert not any(item.code == "missing_media" for item in draft.errors)
    assert any(item.code == "missing_media" for item in strict.errors)


def test_strict_flags_duration_overflow(store):
    store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4"))
    store.upsert_media_asset(MediaAsset(sample_id="s1", modality="video", uri="a.mp4", duration_ms=1000, probe_status="ok"))
    store.replace_events("s1", [Event(sample_id="s1", event_type="fall", start_time_ms=900, end_time_ms=1200)])

    report = validate_dataset(store, mode="strict")

    assert any(item.code == "event_out_of_bounds" for item in report.errors)


def test_quality_flags_duplicate_person_ids(store, monkeypatch):
    store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4"))
    duplicate = Person(sample_id="s1", person_id="p1", age_group="adult")
    monkeypatch.setattr(store, "get_persons", lambda sample_id: [duplicate, duplicate])

    report = validate_dataset(store, mode="strict")

    assert any(item.code == "duplicate_person_id" for item in report.errors)
