import time

from video_labeler.domain import Sample


def test_paginated_sample_query_is_bounded(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    for i in range(5000):
        store.upsert_sample(Sample(sample_id=f"s{i:05d}", dataset_id="d", relative_path=f"{i}.mp4"))
    started = time.perf_counter()
    rows = store.list_samples(limit=100, offset=2500)
    assert len(rows) == 100
    assert time.perf_counter() - started < 2.0
