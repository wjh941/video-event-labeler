"""Pieces shared by the two root-level single-file web labelers."""

from __future__ import annotations


class CsvConflictError(Exception):
    """Raised when another annotation process changed the CSV first."""
