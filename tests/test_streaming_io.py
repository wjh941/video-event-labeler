from __future__ import annotations

import json

import pytest

from video_labeler.domain import Sample
from video_labeler.quality import export_jsonl
from video_labeler.storage.csv_adapter import CancellationError, export_csv, import_csv


def test_import_reports_progress_incrementally(tmp_path, store):
    csv_path = tmp_path / "input.csv"
    csv_path.write_text(
        "sample_id,video_path,events\n"
        "s1,a.mp4,[]\n"
        "s2,b.mp4,[]\n"
        "s3,c.mp4,[]\n",
        encoding="utf-8",
    )
    progress: list[int] = []

    report = import_csv(csv_path, store, tmp_path, progress=progress.append)

    assert report.created == 3
    assert progress == [1, 2, 3]


def test_import_can_be_cancelled_between_rows(tmp_path, store):
    csv_path = tmp_path / "input.csv"
    csv_path.write_text(
        "sample_id,video_path,events\n"
        "s1,a.mp4,[]\n"
        "s2,b.mp4,[]\n"
        "s3,c.mp4,[]\n",
        encoding="utf-8",
    )
    seen: list[int] = []

    with pytest.raises(CancellationError):
        import_csv(csv_path, store, tmp_path, progress=seen.append, cancel=lambda: len(seen) >= 2)

    assert seen == [1, 2]
    assert store.get_sample("s1") is not None
    assert store.get_sample("s2") is not None
    assert store.get_sample("s3") is None


def test_export_reports_progress_without_materializing_rows(tmp_path, store):
    store.upsert_dataset("dataset", str(tmp_path))
    for sample_id in ("s1", "s2", "s3"):
        store.upsert_sample(Sample(sample_id=sample_id, dataset_id="dataset", relative_path=f"{sample_id}.mp4"))
    progress: list[int] = []

    report = export_csv(store, tmp_path / "out.csv", tmp_path, progress=progress.append)

    assert report.sample_count == 3
    assert progress == [1, 2, 3]


def test_export_can_be_cancelled(tmp_path, store):
    store.upsert_dataset("dataset", str(tmp_path))
    for sample_id in ("s1", "s2", "s3"):
        store.upsert_sample(Sample(sample_id=sample_id, dataset_id="dataset", relative_path=f"{sample_id}.mp4"))
    seen: list[int] = []

    with pytest.raises(CancellationError):
        export_csv(store, tmp_path / "out.csv", tmp_path, progress=seen.append, cancel=lambda: len(seen) >= 2)


def test_jsonl_backup_name_is_timestamped(tmp_path, store):
    store.upsert_sample(Sample(sample_id="s1", relative_path="a.mp4"))
    output = tmp_path / "dataset.jsonl"
    export_jsonl(store, output)
    report = export_jsonl(store, output)

    assert report.backup_path is not None
    assert report.backup_path.name.startswith("dataset.before_export_")
    assert report.backup_path.suffix == ".jsonl"
    assert len(report.backup_path.stem.rsplit("_", 1)[-1]) >= 16
