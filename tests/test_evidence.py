from pathlib import Path

import pytest

from video_labeler.domain import Evidence, Prediction, Sample
from video_labeler.evidence import EvidenceService


def test_attach_generates_distinct_ids_for_empty_ids(store):
    store.upsert_sample(Sample(sample_id="s1"))
    service = EvidenceService(store)

    first = service.attach(Evidence(sample_id="s1", modality="video", uri="a.mp4"))
    second = service.attach(Evidence(sample_id="s1", modality="video", uri="b.mp4"))

    assert first.evidence_id.startswith("evidence-")
    assert second.evidence_id.startswith("evidence-")
    assert first.evidence_id != second.evidence_id


def test_attach_rejects_local_uri_outside_media_root(store, tmp_path: Path):
    store.upsert_sample(Sample(sample_id="s1"))
    service = EvidenceService(store, media_root=tmp_path / "media")

    with pytest.raises(ValueError, match="media_root"):
        service.attach(Evidence(sample_id="s1", modality="video", uri="../outside.mp4"))


def test_attach_rejects_windows_drive_path_outside_media_root(store, tmp_path: Path):
    store.upsert_sample(Sample(sample_id="s1"))
    service = EvidenceService(store, media_root=tmp_path / "media")

    with pytest.raises(ValueError, match="media_root"):
        service.attach(Evidence(sample_id="s1", modality="video", uri="C:\\outside.mp4"))


def test_attach_rejects_existing_id_for_another_sample(store):
    store.upsert_sample(Sample(sample_id="s1"))
    store.upsert_sample(Sample(sample_id="s2"))
    service = EvidenceService(store)
    service.attach(Evidence(sample_id="s1", modality="video", uri="a.mp4", evidence_id="ev1"))

    with pytest.raises(ValueError, match="sample"):
        service.attach(Evidence(sample_id="s2", modality="video", uri="b.mp4", evidence_id="ev1"))


def test_prediction_rejects_unknown_evidence_reference(store):
    store.upsert_sample(Sample(sample_id="s1"))
    prediction = Prediction(
        prediction_id="pred-1",
        sample_id="s1",
        task="event",
        label_json='{"event_type":"fall"}',
        model_name="model",
        model_version="1",
        confidence=0.5,
        evidence_ids=("missing",),
    )

    with pytest.raises(ValueError, match="evidence"):
        store.upsert_prediction(prediction)
