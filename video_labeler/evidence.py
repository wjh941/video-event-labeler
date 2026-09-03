"""Persistence service for multimodal evidence attached to samples."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from urllib.parse import urlparse
import uuid

from .domain import Evidence
from .storage.sqlite_store import SQLiteStore


class EvidenceService:
    def __init__(self, store: SQLiteStore, media_root: Path | None = None) -> None:
        self.store = store
        self.media_root = Path(media_root).resolve() if media_root is not None else None

    def attach(self, evidence: Evidence) -> Evidence:
        if not evidence.evidence_id:
            evidence = replace(evidence, evidence_id=f"evidence-{uuid.uuid4().hex}")
        existing = self.store.get_evidence_by_id(evidence.evidence_id)
        if existing is not None and existing.sample_id != evidence.sample_id:
            raise ValueError("evidence ID already belongs to another sample")
        if self.media_root is not None and evidence.uri:
            parsed = urlparse(evidence.uri)
            is_drive_path = re.match(r"^[A-Za-z]:[\\/]", evidence.uri) is not None
            if parsed.scheme in ("", "file") or is_drive_path:
                raw_path = Path(parsed.path if parsed.scheme == "file" else evidence.uri)
                candidate = (raw_path if raw_path.is_absolute() else self.media_root / raw_path).resolve()
                try:
                    candidate.relative_to(self.media_root)
                except ValueError as exc:
                    raise ValueError("local evidence uri must stay within media_root") from exc
                evidence = replace(evidence, uri=str(candidate))
        self.store.upsert_evidence(evidence)
        return self.store.get_evidence_by_id(evidence.evidence_id) or evidence

    def list_for_sample(self, sample_id: str) -> list[Evidence]:
        return self.store.get_evidence(sample_id)


__all__ = ["EvidenceService"]
