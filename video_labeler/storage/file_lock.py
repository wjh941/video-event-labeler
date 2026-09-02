"""Small cross-platform process lock used for migration/export critical sections."""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockTimeoutError(TimeoutError):
    """Raised when a lock cannot be acquired before its deadline."""


class FileLock:
    def __init__(self, path: Path, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be non-negative")
        self.path = Path(path)
        self.timeout_seconds = float(timeout_seconds)
        self._handle = None
        self._locked = False

    def __enter__(self) -> "FileLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a+b")
            self._handle.seek(0, 2)
            if self._handle.tell() == 0:
                self._handle.write(b"0")
                self._handle.flush()
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                try:
                    self._acquire_once()
                    self._locked = True
                    return self
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise LockTimeoutError(f"timed out acquiring lock: {self.path}")
                    time.sleep(0.05)
        except Exception:
            self._close()
            raise

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self._locked:
                self._release_once()
        finally:
            self._close()
        return False

    def _acquire_once(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release_once(self) -> None:
        assert self._handle is not None
        self._handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)

    def _close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        self._locked = False
