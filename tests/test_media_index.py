from video_labeler.media_index import index_media
from video_labeler.cli import main
import json


def test_index_media_discovers_hashes_probes_and_is_idempotent(tmp_path, store):
    media = tmp_path / "nested" / "clip.mp4"
    media.parent.mkdir()
    media.write_bytes(b"clip")
    report = index_media(tmp_path, store)
    assert (report.scanned, report.indexed, report.skipped, report.errors) == (1, 1, 0, [])
    samples = store.list_samples(10, 0)
    assert len(samples) == 1
    assert samples[0].relative_path == "nested/clip.mp4"
    assets = store.get_media_assets(samples[0].sample_id)
    assert len(assets) == 1
    assert assets[0].modality == "video"
    assert assets[0].source_sha256

    second = index_media(tmp_path, store)
    assert (second.scanned, second.indexed, second.skipped, second.errors) == (1, 0, 1, [])


def test_index_media_ignores_escaping_symlink(tmp_path, store):
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"outside")
    link = tmp_path / "escape.mp4"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return
    report = index_media(tmp_path, store)
    assert report.scanned == 0
    assert store.list_samples(10, 0) == []


def test_cli_index_media_reports_scan(tmp_path, capsys):
    (tmp_path / "clip.mp4").write_bytes(b"clip")
    database = tmp_path / "dataset.db"

    assert main(["index-media", "--db", str(database), "--video-root", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["scanned"] == 1
    assert payload["indexed"] == 1
