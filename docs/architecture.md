# Architecture

The application is a local, SQLite-first annotation tool. CSV is a compatibility boundary only.

```text
video files -> CSV import -> SQLiteStore -> AnnotationService -> browser adapters
                                      -> quality/stats -> CSV or JSONL export
```

`SQLiteStore` owns transactions and optimistic sample revisions. `AnnotationService` projects rows for both annotators and delegates event/person saves. Media paths are resolved beneath the configured video root; traversal and arbitrary file serving are rejected. CSV export writes a sibling temporary file, fsyncs it, then atomically replaces the destination and keeps a timestamped backup.

The CLI is implemented with standard-library `argparse`:

```text
python -m video_labeler import-csv --csv MANIFEST --video-root VIDEOS --db dataset.db
python -m video_labeler export-csv --csv MANIFEST --video-root VIDEOS --db dataset.db
python -m video_labeler validate --db dataset.db
python -m video_labeler stats --db dataset.db
python -m video_labeler export --db dataset.db --format jsonl --output train.jsonl
```

Stale revision writes fail with a conflict (HTTP 409 in adapters); callers must reload before retrying.
