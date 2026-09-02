import csv
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import person_identity_labeler as annotator


def test_person_defaults_and_round_trip():
    people = annotator.parse_person_attributes('[{"person_id":"p1","age_group":"adult"}]')
    assert people == [{
        "person_id": "p1",
        "age_group": "adult",
        "face_familiarity": "unknown",
        "body_reid_familiarity": "unknown",
    }]
    assert json.loads(annotator.format_person_attributes(people)) == people


def test_zero_people_is_valid():
    assert annotator.parse_person_attributes("") == []
    assert annotator.format_person_attributes([]) == "[]"


def test_html_has_person_and_segment_controls():
    html = annotator.HTML_PAGE
    assert "person_identity_attributes" in html
    assert "person_count" in html
    assert "playEventSegment" in html
    assert "person_tag_list" not in html


def test_app_state_adds_columns_and_saves_zero_people(tmp_path: Path):
    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text(
        "sample_id,video_path,behavior_id,events,person_tag_list\n"
        "clip-1,video.mp4,person_fall,[]\n",
        encoding="utf-8",
    )
    (tmp_path / "video.mp4").touch()
    state = annotator.AppState(csv_path, tmp_path)
    assert "person_count" in state.fieldnames
    assert "person_identity_attributes" in state.fieldnames
    assert "person_tag_list" not in state.fieldnames

    row = state.save_row({
        "row_index": 0,
        "sample_id": "clip-1",
        "person_count": 0,
        "people": [],
    })
    assert row["person_count"] == 0
    assert row["person_identity_attributes"] == []
    assert (tmp_path / "manifest.bak").is_file()

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert "person_tag_list" not in (reader.fieldnames or [])
        saved = next(reader)
    assert saved["person_count"] == "0"
    assert json.loads(saved["person_identity_attributes"]) == []


def test_save_rejects_invalid_person_enum(tmp_path: Path):
    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text("sample_id,events\nclip-1,[]\n", encoding="utf-8")
    state = annotator.AppState(csv_path, tmp_path)
    with pytest.raises(ValueError, match="年龄段无效"):
        state.save_row({
            "row_index": 0,
            "sample_id": "clip-1",
            "people": [{
                "person_id": "p1",
                "age_group": "teen",
                "face_familiarity": "unknown",
                "body_reid_familiarity": "unknown",
            }],
        })


def test_save_rejects_duplicate_person_ids(tmp_path: Path):
    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text("sample_id,events\nclip-1,[]\n", encoding="utf-8")
    state = annotator.AppState(csv_path, tmp_path)
    person = {
        "person_id": "p1",
        "age_group": "adult",
        "face_familiarity": "unknown",
        "body_reid_familiarity": "unknown",
    }
    with pytest.raises(ValueError, match="人员编号重复"):
        state.save_row({
            "row_index": 0,
            "sample_id": "clip-1",
            "people": [person, dict(person)],
        })


def test_person_save_preserves_behavior_events_and_uses_row_video(tmp_path: Path):
    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text(
        "sample_id,video_path,behavior_id,events,person_count,person_identity_attributes\n"
        "clip-1,nested/video.mp4,person_fall,\"[{\\\"event_type\\\":\\\"person_fall\\\",\\\"start_time_ms\\\":100,\\\"end_time_ms\\\":200}]\",0,[]\n",
        encoding="utf-8",
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "video.mp4").touch()
    state = annotator.AppState(csv_path, tmp_path)
    assert state.video_path_for_row(0) == (nested / "video.mp4").resolve()
    row = state.save_row({
        "row_index": 0,
        "sample_id": "clip-1",
        "people": [{
            "person_id": "p1",
            "age_group": "adult",
            "face_familiarity": "stranger",
            "body_reid_familiarity": "unknown",
        }],
    })
    assert row["behaviors"][0]["event_type"] == "person_fall"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        saved_row = next(csv.DictReader(handle))
    assert saved_row["behavior_id"] == "person_fall"


def test_person_save_rejects_stale_csv_revision(tmp_path: Path):
    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text("sample_id,events\nclip-1,[]\n", encoding="utf-8")
    state = annotator.AppState(csv_path, tmp_path)
    with pytest.raises(annotator.CsvConflictError):
        state.save_row({
            "row_index": 0,
            "sample_id": "clip-1",
            "people": [],
            "csv_revision": "0" * 64,
        })


def test_video_endpoint_returns_404_for_missing_row_video(tmp_path: Path):
    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text(
        "sample_id,video_path,events\nclip-1,missing.mp4,[]\n",
        encoding="utf-8",
    )
    state = annotator.AppState(csv_path, tmp_path)
    server, port = annotator.choose_server("127.0.0.1", 0)
    annotator.VideoCsvHandler.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/video?row=0")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
