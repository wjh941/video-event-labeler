"""Deterministic indexing of local media into the SQLite store."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .domain import MediaAsset, Sample, utc_now
from .media import iter_video_files, probe_media, sha256_file
from .storage.csv_adapter import _dataset_id, sample_id_for_path
from .storage.sqlite_store import SQLiteStore


@dataclass
class MediaIndexReport:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def index_media(root: Path, store: SQLiteStore, ffprobe_path: Path | None = None) -> MediaIndexReport:
    """Discover supported media beneath *root* and upsert samples/assets."""
    root = Path(root).expanduser().resolve()
    report = MediaIndexReport()
    if not root.is_dir():
        report.errors.append(f"media root is not a directory: {root}")
        return report
    dataset_id = _dataset_id(root)
    store.upsert_dataset(dataset_id, str(root))
    files = list(iter_video_files(root))
    report.scanned = len(files)
    for path in files:
        relative_path = path.relative_to(root).as_posix()
        try:
            digest = sha256_file(path, root=root)
            current = store.connection().execute(
                "SELECT sample_id FROM samples WHERE relative_path = ?", (relative_path,)
            ).fetchone()
            sample_id = str(current[0]) if current else sample_id_for_path(relative_path, digest)
            if not current:
                store.upsert_sample(Sample(sample_id=sample_id, dataset_id=dataset_id, relative_path=relative_path, source_sha256=digest))
            else:
                existing = store.get_sample(sample_id)
                if existing and (existing.source_sha256 != digest or existing.dataset_id != dataset_id):
                    store.upsert_sample(Sample(sample_id=sample_id, dataset_id=dataset_id, relative_path=relative_path, source_sha256=digest, status=existing.status, schema_version=existing.schema_version, created_at=existing.created_at, updated_at=utc_now(), revision=existing.revision))
            metadata = probe_media(path, ffprobe_path=ffprobe_path, root=root)
            asset = MediaAsset(
                sample_id=sample_id,
                modality="video",
                uri=relative_path,
                duration_ms=metadata.duration_ms,
                fps=metadata.fps,
                width=metadata.width,
                height=metadata.height,
                metadata_json=json.dumps({"audio_present": metadata.audio_present}, sort_keys=True, separators=(",", ":")),
                source_sha256=digest,
                probe_status=metadata.probe_status,
            )
            existing_asset = store.get_media_assets(sample_id)
            unchanged = any(a.modality == "video" and a.uri == relative_path and a.source_sha256 == digest and a.probe_status == asset.probe_status for a in existing_asset)
            store.upsert_media_asset(asset)
            if unchanged:
                report.skipped += 1
            else:
                report.indexed += 1
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            report.errors.append(f"{relative_path}: {exc}")
    return report

