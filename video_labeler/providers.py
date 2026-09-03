"""Model-provider contracts and a deterministic provider for demos/tests."""
from __future__ import annotations

import hashlib
import json
from typing import Protocol, Sequence

from .domain import Prediction, Sample


class AnnotationProvider(Protocol):
    def predict(self, sample: Sample) -> Sequence[Prediction]: ...


class MockAnnotationProvider:
    """Return reproducible, provenance-rich event predictions without a model dependency."""

    model_name = "mock"
    model_version = "1.0"

    def predict(self, sample: Sample) -> list[Prediction]:
        digest = hashlib.sha256(sample.sample_id.encode("utf-8")).hexdigest()[:12]
        evidence_id = f"evidence-{sample.sample_id}-mock"
        prediction_id = f"prediction-{sample.sample_id}-{digest}"
        label = {"event_type": "person_fall", "start_time_ms": 0, "end_time_ms": 1000}
        return [Prediction(prediction_id=prediction_id, sample_id=sample.sample_id, task="event",
                           label_json=json.dumps(label, separators=(",", ":")), model_name=self.model_name,
                           model_version=self.model_version, confidence=0.5, evidence_ids=(evidence_id,))]


__all__ = ["AnnotationProvider", "MockAnnotationProvider"]
