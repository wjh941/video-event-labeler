"""Database backup and integrity helpers."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .schema import CURRENT_SCHEMA_VERSION
from .storage.sqlite_store import SQLiteStore


@dataclass(frozen=True, slots=True)
class DatabaseCheck:
    ok: bool
    integrity_check: str
    schema_version: int


def backup_database(store: SQLiteStore, output: Path) -> Path:
    """Create an atomic SQLite backup at *output*."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent))
    os.close(fd)
    try:
        destination = sqlite3.connect(temporary)
        try:
            with store._lock:
                store.connection().backup(destination)
            destination.commit()
        finally:
            destination.close()
        os.replace(temporary, output)
        return output
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_database(store: SQLiteStore) -> DatabaseCheck:
    """Run SQLite integrity check and report the applied schema version."""
    connection = store.connection()
    result_cursor = connection.execute("PRAGMA integrity_check")
    result = result_cursor.fetchone() if hasattr(result_cursor, "fetchone") else next(iter(result_cursor), None)
    integrity = str(result[0]) if result else "unknown"
    # Read schema metadata from the store's real connection so callers can
    # wrap the integrity check independently (for diagnostics/tests).
    row = store._connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    schema_version = int(row[0]) if row else 0
    return DatabaseCheck(integrity == "ok" and schema_version == CURRENT_SCHEMA_VERSION, integrity, schema_version)


__all__ = ["DatabaseCheck", "backup_database", "check_database"]
