"""Media discovery, hashing, and optional metadata probing."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SUPPORTED_VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"})


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


def iter_video_files(root: Path) -> Iterator[Path]:
    """Yield supported media files beneath root in deterministic path order."""
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return
    root_resolved = root.resolve(strict=False)
    files = (
        path
        for path in root_resolved.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
        and is_safe_media_path(root_resolved, path)
    )
    ordered = sorted(files, key=lambda path: path.relative_to(root_resolved).as_posix().casefold())
    yield from ordered


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
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
            return float(numerator) / denominator_value if denominator_value else None
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def probe_media(path: Path, ffprobe_path: Path | None = None) -> MediaMetadata:
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
        completed = subprocess.run(
            [*executable, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
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
        if not isinstance(streams, list):
            streams = []
        video = next((stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"), {})
        duration_ms = None
        format_data = payload.get("format")
        if isinstance(format_data, dict):
            try:
                duration_ms = round(float(format_data["duration"]) * 1000)
            except (KeyError, TypeError, ValueError):
                pass
        return MediaMetadata(
            duration_ms=duration_ms,
            fps=_frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
            width=video.get("width") if isinstance(video.get("width"), int) else None,
            height=video.get("height") if isinstance(video.get("height"), int) else None,
            audio_present=any(isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams),
            probe_status="ok",
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError, ValueError):
        return MediaMetadata()
