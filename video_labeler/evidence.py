"""Persistence service for multimodal evidence attached to samples."""
from __future__ import annotations

from .domain import Evidence
from .storage.sqlite_store import SQLiteStore


class EvidenceService:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def attach(self, evidence: Evidence) -> None:
        self.store.upsert_evidence(evidence)

    def list_for_sample(self, sample_id: str) -> list[Evidence]:
        return self.store.get_evidence(sample_id)


__all__ = ["EvidenceService"]
