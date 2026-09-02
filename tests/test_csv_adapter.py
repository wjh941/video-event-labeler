from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from video_labeler.domain import Event, Person, Sample
from video_labeler.storage.csv_adapter import (
    export_csv,
    import_csv,
    sample_id_for_path,
)


def test_legacy_person_tag_is_removed_and_people_are_empty(tmp_path, store):
    csv_path = tmp_path / "legacy.csv"
    csv_path.write_text(
        "sample_id,video_path,person_tag_list,events\n"
        "s1,a.mp4,stranger,[]\n",
        encoding="utf-8-sig",
    )
    report = import_csv(csv_path, store, tmp_path)
    assert report.created == 1
    assert store.get_persons("s1") == []
    columns = {row[1] for row in store.connection().execute("PRAGMA table_info(samples)")}
    assert "person_tag_list" not in columns


def test_export_preserves_event_and_person_semantics(tmp_path, store):
    store.upsert_dataset("dataset", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="dataset", relative_path="a.mp4"))
    store.replace_events("s1", [Event(sample_id="s1", event_type="person_fall", start_time_ms=100, end_time_ms=200)])
    store.replace_persons("s1", [Person(sample_id="s1", person_id="p1", age_group="adult", face_familiarity="stranger")], expected_revision=1)
    report = export_csv(store, tmp_path / "out.csv", tmp_path)
    with (tmp_path / "out.csv").open(newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    assert json.loads(row["person_identity_attributes"])[0]["person_id"] == "p1"
    assert json.loads(row["events"])[0]["event_type"] == "person_fall"
    assert report.meta_path.name == "out.csv.meta.json"
    assert report.backup_path is None


def test_import_is_idempotent_and_derives_ids(tmp_path, store):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    path = tmp_path / "manifest.csv"
    path.write_text("video_path,events\nclip.mp4,[]\n", encoding="utf-8")
    first = import_csv(path, store, tmp_path)
    second = import_csv(path, store, tmp_path)
    assert first.created == 1
    assert second.skipped == 1
    assert len(store.list_samples(10, 0)) == 1
    expected = sample_id_for_path("clip.mp4", hashlib.sha256(b"video").hexdigest())
    assert store.list_samples(10, 0)[0].sample_id == expected


def test_replaced_source_is_stale_and_does_not_overwrite_annotations(tmp_path, store):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"one")
    path = tmp_path / "manifest.csv"
    path.write_text("sample_id,video_path,events\ns1,clip.mp4,[]\n", encoding="utf-8")
    import_csv(path, store, tmp_path)
    store.replace_persons("s1", [Person(sample_id="s1", person_id="p1", age_group="adult")])
    video.write_bytes(b"two")
    report = import_csv(path, store, tmp_path)
    assert report.stale == 1
    assert [person.person_id for person in store.get_persons("s1")] == ["p1"]


def test_malformed_json_is_reported_without_aborting_other_rows(tmp_path, store):
    path = tmp_path / "manifest.csv"
    path.write_text("sample_id,video_path,events\nbad,a.mp4,{bad}\ngood,b.mp4,[]\n", encoding="utf-8")
    report = import_csv(path, store, tmp_path)
    assert report.created == 2
    assert report.errors and report.errors[0].row_number == 2
    assert store.get_sample("bad") is not None


def test_source_deletion_is_reported_stale(tmp_path, store):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"one")
    path = tmp_path / "manifest.csv"
    path.write_text("sample_id,video_path,events\ns1,clip.mp4,[]\n", encoding="utf-8")
    import_csv(path, store, tmp_path)
    video.unlink()
    report = import_csv(path, store, tmp_path)
    assert report.stale == 1


def test_export_writes_backup_on_second_export_and_metadata(tmp_path, store):
    store.upsert_dataset("dataset", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="dataset", relative_path="a.mp4"))
    path = tmp_path / "out.csv"
    first = export_csv(store, path, tmp_path)
    second = export_csv(store, path, tmp_path)
    assert first.backup_path is None
    assert second.backup_path is not None and second.backup_path.exists()
    metadata = json.loads(second.meta_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] >= 2
    assert metadata["sample_count"] == 1
    assert metadata["database_revision"] >= 0
