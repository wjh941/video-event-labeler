"""H:MM:SS[.sss] text helpers for the events/manifest CSV format.

毫秒语义：parse_time_text 返回整数毫秒并严格校验（分钟/秒 < 60，仅接受完整 H:MM:SS）。
注意：兄弟仓库 review-clones/video_labeler（桌面版，独立包）的 time 解析为秒语义
（ffmpeg_service.parse_time_to_seconds / format_seconds），两仓无依赖关系，
契约不同，请勿互相合并。
"""

from __future__ import annotations

import re

TIME_PATTERN = re.compile(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)")


def parse_time_text(value: str) -> int | None:
    """Convert H:MM:SS[.sss] text to milliseconds."""
    text = (value or "").strip().lower()
    if text in ("", "null"):
        return None
    match = TIME_PATTERN.fullmatch(text)
    if not match:
        raise ValueError("time must use H:MM:SS or H:MM:SS.sss")
    hours, minutes, seconds = match.groups()
    if int(minutes) >= 60 or float(seconds) >= 60:
        raise ValueError("time has an invalid minute or second value")
    return int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000))


def format_time_text(value: int | None) -> str:
    """Format milliseconds as fixed-width H:MM:SS.mmm."""
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("milliseconds must be a non-negative integer or null")
    total_seconds, milliseconds = divmod(value, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
