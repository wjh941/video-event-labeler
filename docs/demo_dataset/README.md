# Demo Dataset

Create a private synthetic dataset without committing media:

```powershell
mkdir demo\videos
Set-Content demo\videos\clip.mp4 "placeholder"
python -m video_labeler import-csv --csv demo\manifest.csv --video-root demo\videos --db demo\dataset.db
python -m video_labeler stats --db demo\dataset.db
python -m video_labeler validate --db demo\dataset.db
python -m video_labeler export --db demo\dataset.db --format jsonl --output demo\train.jsonl
```

Replace the placeholder with local media when exercising browser playback. The demo intentionally contains no personal data or proprietary models.
