from __future__ import annotations

import json
from pathlib import Path

from video_labeler.domain import MediaAsset, Sample
from video_labeler.media import (
    is_safe_media_path,
    iter_video_files,
    probe_media,
    sha256_file,
)


def test_scan_is_deterministic_and_ignores_unknown_extensions(tmp_path):
    (tmp_path / "b.mp4").touch()
    (tmp_path / "a.mkv").touch()
    (tmp_path / "ignore.txt").touch()
    assert [p.name for p in iter_video_files(tmp_path)] == ["a.mkv", "b.mp4"]


def test_path_traversal_is_rejected(tmp_path):
    assert not is_safe_media_path(tmp_path, tmp_path / ".." / "outside.mp4")


def test_sha256_hashes_file(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"media")
    assert sha256_file(path) == "721c9525ade2ea8903d343ef25cf68b9bf4ab0aad56bb7b01fbe48d09bc7fcf4"


def test_probe_without_ffprobe_is_unavailable(tmp_path):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"not a real video")
    metadata = probe_media(path, ffprobe_path=tmp_path / "missing-ffprobe")
    assert metadata.probe_status == "unavailable"
    assert metadata.duration_ms is None
    assert metadata.audio_present is None


def test_probe_parses_ffprobe_json(tmp_path):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"x")
    ffprobe = tmp_path / "ffprobe.py"
    ffprobe.write_text(
        "@echo {\"format\": {\"duration\": \"2.5\"}, \"streams\": "
        "[{\"codec_type\":\"video\",\"width\":640,\"height\":360,\"r_frame_rate\":\"30/1\"}, "
        "{\"codec_type\":\"audio\"}]}\n",
        encoding="utf-8",
    )
    metadata = probe_media(media, ffprobe_path=ffprobe)
    assert metadata.probe_status == "ok"
    assert metadata.duration_ms == 2500
    assert metadata.fps == 30.0
    assert metadata.width == 640
    assert metadata.height == 360
    assert metadata.audio_present is True


def test_media_asset_crud(store):
    store.upsert_dataset("d1", ".")
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d1", relative_path="sample.mp4"))
    asset = MediaAsset(
        sample_id="s1",
        modality="video",
        uri="sample.mp4",
        duration_ms=1000,
        fps=25.0,
        width=640,
        height=360,
        metadata_json=json.dumps({"codec": "h264"}),
        source_sha256="abc",
        probe_status="ok",
    )
    store.upsert_media_asset(asset)
    assert store.get_media_assets("s1") == [asset]
    updated = MediaAsset(sample_id="s1", modality="video", uri="sample.mp4", probe_status="unavailable")
    store.upsert_media_asset(updated)
    assert store.get_media_assets("s1") == [updated]
