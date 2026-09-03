from __future__ import annotations

import json

from video_labeler.domain import Event, Evidence, Person, Prediction, Sample
from video_labeler.quality import export_jsonl


def test_jsonl_export_contains_modalities_and_provenance(store, tmp_path):
    store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4"))
    store.replace_events("s1", [Event(sample_id="s1", event_type="fall", start_time_ms=0, end_time_ms=100)])
    store.replace_persons("s1", [Person(sample_id="s1", person_id="p1", age_group="adult")])
    store.upsert_evidence(Evidence(sample_id="s1", modality="video", uri="a.mp4", evidence_id="ev1"))
    store.upsert_prediction(Prediction(prediction_id="pred1", sample_id="s1", task="event", label_json='{"x":1}', model_name="demo", model_version="1", confidence=.9))
    output = tmp_path / "train.jsonl"
    report = export_jsonl(store, output)
    record = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert {"sample", "media", "events", "persons", "evidence", "provenance"} <= record.keys()
    assert record["events"][0]["event_type"] == "fall"
    assert record["provenance"]["predictions"][0]["label"] == {"x": 1}
    assert report.sample_count == 1


def test_jsonl_export_is_deterministic_and_atomic(store, tmp_path):
    store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4"))
    output = tmp_path / "train.jsonl"
    export_jsonl(store, output)
    first = output.read_bytes()
    export_jsonl(store, output)
    assert output.read_bytes() == first
    assert any(path.name.startswith("train.before_export") for path in tmp_path.iterdir())
