import os

import pytest

from video_labeler.domain import Sample
from video_labeler.storage.csv_adapter import export_csv


def test_interrupted_export_keeps_original(tmp_path, store, monkeypatch):
    store.upsert_dataset("d", str(tmp_path))
    store.upsert_sample(Sample(sample_id="s1", dataset_id="d", relative_path="clip.mp4"))
    path = tmp_path / "manifest.csv"
    path.write_text("original\n", encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("fail")))
    with pytest.raises(OSError):
        export_csv(store, path, tmp_path)
    assert path.read_bytes() == original

