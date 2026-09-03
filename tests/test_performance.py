import time

from video_labeler.domain import Sample


def test_paginated_sample_query_is_bounded(tmp_path, store):
    store.upsert_dataset("d", str(tmp_path))
    with store.transaction() as connection:
        connection.executemany(
            """INSERT INTO samples(sample_id, dataset_id, relative_path, created_at, updated_at)
            VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
            [(f"s{i:05d}", "d", f"{i}.mp4") for i in range(5000)],
        )
    started = time.perf_counter()
    rows = store.list_samples(limit=100, offset=2500)
    assert len(rows) == 100
    assert time.perf_counter() - started < 2.0
