"""Core domain and persistence primitives for the video annotation platform."""

from .domain import Event, Evidence, MediaAsset, Person, Prediction, Sample

__all__ = ["Event", "Evidence", "MediaAsset", "Person", "Prediction", "Sample"]
