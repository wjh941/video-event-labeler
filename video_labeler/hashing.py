"""Streaming hashing helpers shared by the web apps and storage adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path

HASH_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of *path*, read in fixed-size chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
