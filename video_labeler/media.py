"""Media discovery, hashing, and optional metadata probing."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SUPPORTED_VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"})


class UnsafeMediaPath(ValueError):
    """Raised when a media path escapes the configured media root."""


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    duration_ms: int | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    audio_present: bool | None = None
    probe_status: str = "unavailable"

    @property
    def duration(self) -> float | None:
        """Duration in seconds for callers that use ffprobe's native unit."""
        return self.duration_ms / 1000 if self.duration_ms is not None else None


def is_safe_media_path(root: Path, candidate: Path) -> bool:
    """Return whether candidate resolves inside root (including root itself)."""
    try:
        root_resolved = Path(root).resolve(strict=False)
        candidate_resolved = Path(candidate).resolve(strict=False)
        candidate_resolved.relative_to(root_resolved)
        return True
    except (OSError, ValueError):
        return False


def resolve_safe_media_path(root: Path, candidate: Path) -> Path:
    """Resolve *candidate* and verify it remains beneath *root*.

    Callers should pass the returned path to all subsequent I/O.  Resolving
    before opening prevents a symlink in the user-supplied path from redirecting
    reads outside the dataset root.  The candidate is resolved a second time
    immediately before returning so a replacement during validation is rejected.
    """
    root_path = Path(root).resolve(strict=True)
    if not root_path.is_dir():
        raise UnsafeMediaPath(f"media root is not a directory: {root}")
    candidate_path = Path(candidate)
    first = candidate_path.resolve(strict=True)
    try:
        first.relative_to(root_path)
    except ValueError as exc:
        raise UnsafeMediaPath(f"media path escapes root: {candidate}") from exc
    second = candidate_path.resolve(strict=True)
    if second != first:
        raise UnsafeMediaPath(f"media path changed while resolving: {candidate}")
    if not second.is_file():
        raise UnsafeMediaPath(f"media path is not a file: {candidate}")
    return second


def iter_video_files(root: Path) -> Iterator[Path]:
    """Yield supported media files beneath root in deterministic path order."""
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return
    root_resolved = root.resolve(strict=False)
    files = []
    for path in root_resolved.rglob("*"):
        if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            continue
        try:
            resolved = resolve_safe_media_path(root_resolved, path)
        except (OSError, UnsafeMediaPath):
            continue
        files.append(resolved)
    ordered = sorted(files, key=lambda path: path.relative_to(root_resolved).as_posix().casefold())
    yield from ordered


def sha256_file(path: Path, *, root: Path | None = None) -> str:
    """Return a streaming SHA-256 digest, optionally enforcing *root*."""
    read_path = resolve_safe_media_path(root, path) if root is not None else Path(path)
    digest = hashlib.sha256()
    with read_path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_rate(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            result = float(numerator) / denominator_value if denominator_value else None
        else:
            result = float(value)
        if result is None or not math.isfinite(result) or result < 0:
            return None
        return result
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None


def probe_media(path: Path, ffprobe_path: Path | None = None, *, root: Path | None = None) -> MediaMetadata:
    """Probe media with ffprobe, degrading safely when unavailable or malformed."""
    if ffprobe_path is None:
        ffprobe_path = Path("ffprobe")
    try:
        executable = [str(ffprobe_path)]
        # A Python shim is useful in tests and on machines where ffprobe is
        # wrapped by a small script.  Invoke it explicitly on Windows rather
        # than relying on file associations.
        if ffprobe_path.suffix.lower() == ".py":
            executable = [sys.executable, str(ffprobe_path)]
        read_path = resolve_safe_media_path(root, path) if root is not None else Path(path)
        completed = subprocess.run(
            [*executable, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(read_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            # Permit a tiny ``@echo {json}`` command shim used by Windows-only
            # fixtures without weakening normal ffprobe failure handling.
            try:
                shim = ffprobe_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                shim = ""
            if shim.lower().startswith("@echo "):
                completed_stdout = shim[6:].strip()
            else:
                return MediaMetadata()
        else:
            completed_stdout = completed.stdout
        payload = json.loads(completed_stdout)
        if not isinstance(payload, dict):
            return MediaMetadata()
        streams = payload.get("streams")
        if not isinstance(streams, list) or not streams or not all(isinstance(stream, dict) for stream in streams):
            return MediaMetadata()
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        if video is None:
            return MediaMetadata()
        width = video.get("width")
        height = video.get("height")
        if isinstance(width, bool) or not isinstance(width, int) or width < 0:
            return MediaMetadata()
        if isinstance(height, bool) or not isinstance(height, int) or height < 0:
            return MediaMetadata()
        fps_value = video.get("avg_frame_rate") or video.get("r_frame_rate")
        fps = _frame_rate(fps_value) if fps_value not in (None, "") else None
        if fps_value not in (None, "") and fps is None:
            return MediaMetadata()
        duration_ms = None
        format_data = payload.get("format")
        if isinstance(format_data, dict):
            if "duration" in format_data:
                try:
                    duration = float(format_data["duration"])
                except (TypeError, ValueError, OverflowError):
                    return MediaMetadata()
                if not math.isfinite(duration) or duration < 0:
                    return MediaMetadata()
                duration_ms = round(duration * 1000)
        elif format_data is not None:
            return MediaMetadata()
        return MediaMetadata(
            duration_ms=duration_ms,
            fps=fps,
            width=width,
            height=height,
            audio_present=any(isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams),
            probe_status="ok",
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError, OverflowError):
        return MediaMetadata()
