import csv
import json
from pathlib import Path

import video_event_labeler as event_labeler
import person_identity_labeler as person_labeler


def test_generated_manifest_can_be_annotated_without_changing_events(tmp_path: Path):
    video = tmp_path / "跌倒" / "pos" / "fall-pos.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")

    manifest, added = event_labeler.import_video_directory(tmp_path)
    assert added == 1
    rows, fields = event_labeler.read_csv_rows(manifest, "utf-8-sig")
    assert fields == event_labeler.MANIFEST_FIELDS
    original_events = rows[0]["events"]

    state = person_labeler.AppState(manifest, tmp_path)
    state.save_row({
        "row_index": 0,
        "sample_id": rows[0]["sample_id"],
        "people": [{
            "person_id": "p1",
            "age_group": "adult",
            "face_familiarity": "stranger",
            "body_reid_familiarity": "unknown",
        }],
    })

    with manifest.open(newline="", encoding="utf-8-sig") as handle:
        saved = next(csv.DictReader(handle))
    assert saved["events"] == original_events
    assert saved["person_count"] == "1"
    assert json.loads(saved["person_identity_attributes"])[0]["person_id"] == "p1"
