"""Core domain and persistence primitives for the video annotation platform."""

from .domain import Event, Evidence, MediaAsset, Person, Prediction, Sample
from .quality import QualityIssue, QualityReport, dataset_stats, export_jsonl, validate_dataset
from .schema import CURRENT_SCHEMA_VERSION

__all__ = [
    "Event", "Evidence", "MediaAsset", "Person", "Prediction", "Sample",
    "QualityIssue", "QualityReport", "dataset_stats", "export_jsonl", "validate_dataset", "CURRENT_SCHEMA_VERSION",
]
