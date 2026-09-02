import pytest

from video_labeler.domain import (
    AGE_GROUPS,
    BODY_FAMILIARITY_VALUES,
    FACE_FAMILIARITY_VALUES,
    SAMPLE_STATUSES,
    Event,
    Evidence,
    MediaAsset,
    Person,
    Prediction,
    Sample,
)


def test_person_requires_declared_enums():
    with pytest.raises(ValueError):
        Person(sample_id="s1", person_id="p1", age_group="teen")


def test_person_accepts_zero_count_semantics_without_count_field():
    person = Person(sample_id="s1", person_id="p1", age_group="adult", face_familiarity="stranger", body_reid_familiarity="unknown")
    assert person.person_id == "p1"
    assert not hasattr(person, "person_count")


def test_event_end_must_be_after_start():
    with pytest.raises(ValueError):
        Event(sample_id="s1", event_type="fall", start_time_ms=20, end_time_ms=10)


def test_event_allows_draft_without_times_but_rejects_negative_times():
    assert Event(sample_id="s1", event_type="fall").start_time_ms is None
    with pytest.raises(ValueError):
        Event(sample_id="s1", event_type="fall", start_time_ms=-1, end_time_ms=10)


def test_domain_models_validate_and_are_frozen():
    sample = Sample(sample_id="s1", dataset_id="d1", relative_path="a.mp4")
    asset = MediaAsset(sample_id="s1", modality="video", uri="a.mp4")
    evidence = Evidence(sample_id="s1", modality="video", start_time_ms=0, end_time_ms=10)
    prediction = Prediction(prediction_id="pred-1", sample_id="s1", task="event", label_json='{"event_type":"fall"}', model_name="mock", model_version="1", confidence=0.5)
    assert sample.schema_version >= 1
    assert asset.modality == "video"
    assert evidence.end_time_ms == 10
    assert prediction.confidence == 0.5
    with pytest.raises((AttributeError, TypeError)):
        sample.sample_id = "s2"


def test_enum_constants_are_exact():
    assert AGE_GROUPS == ("child", "adult", "elderly", "unknown")
    assert FACE_FAMILIARITY_VALUES == ("familiar", "stranger", "unknown", "not_visible")
    assert BODY_FAMILIARITY_VALUES == FACE_FAMILIARITY_VALUES
    assert SAMPLE_STATUSES == ("draft", "reviewed", "rejected")
