# Architecture

The application is a local, SQLite-first annotation tool. CSV is a compatibility boundary only.

The adapters expose optional pagination parameters without changing their legacy
no-query response shapes. `GET /api/predictions` reads model candidates with
review metadata, while `GET /api/quality?mode=draft|strict` combines
`dataset_stats()` and `validate_dataset()` into the dashboard payload. Browser
panels use the same revision-aware accept/reject and save routes as the existing
annotation workflow.

```text
video files -> CSV import -> SQLiteStore -> AnnotationService -> browser adapters
                                      -> quality/stats -> CSV or JSONL export
```

`SQLiteStore` owns transactions and optimistic sample revisions. `AnnotationService` projects rows for both annotators and delegates event/person saves. Media paths are resolved beneath the configured video root; traversal and arbitrary file serving are rejected. CSV export writes a sibling temporary file, fsyncs it, then atomically replaces the destination and keeps a timestamped backup.

The CLI is implemented with standard-library `argparse`:

```text
python -m video_labeler import-csv --csv MANIFEST --video-root VIDEOS --db dataset.db
python -m video_labeler export-csv --csv MANIFEST --video-root VIDEOS --db dataset.db
python -m video_labeler index-media --db dataset.db --video-root VIDEOS
python -m video_labeler validate --db dataset.db --mode strict
python -m video_labeler stats --db dataset.db
python -m video_labeler export --db dataset.db --format jsonl --output train.jsonl
python -m video_labeler backup-db --db dataset.db --output dataset.backup.db
python -m video_labeler check-db --db dataset.db
```

The recommended launcher is `python run_video_annotation.py --video-root VIDEOS`; it defaults the database to `VIDEOS/dataset.db`, imports a compatible CSV when present, and indexes supported videos with safe paths and optional ffprobe metadata.

The event browser exposes `/api/status`, `/api/videos`, `/api/update`, and
`/video/<relative-path>`. The person browser exposes `/api/state`, `/api/save`,
and `/video?row=<index>`. Both adapters expose prediction reads and accept or
reject actions under `/api/predictions/<prediction-id>`; stale revisions return
HTTP 409 and missing samples or media return HTTP 404.

Stale revision writes fail with a conflict (HTTP 409 in adapters); callers must reload before retrying.
