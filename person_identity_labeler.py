#!/usr/bin/env python3
"""
Local video + CSV annotation tool.

Run:
    python person_identity_labeler.py --csv video_labeler_manifest.csv --video-root .

Or pass paths directly:
    python person_identity_labeler.py --video "F:\\data\\clip.mp4" --csv "F:\\data\\clip.csv"

The browser UI is served by this script so it can write the edited values back
to the CSV without requiring third-party Python packages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from video_labeler.services import AnnotationService
from video_labeler.storage.csv_adapter import import_csv
from video_labeler.storage.sqlite_store import SQLiteStore
from video_labeler.storage.sqlite_store import ConflictError
from video_labeler.media_index import index_media


ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "gb18030", "cp936", "cp1252")
MS_RE = r'(?:"?(-?\d+(?:\.\d+)?)\s*(?:ms)?"?)'

AGE_GROUP_OPTIONS = ("child", "adult", "elderly", "unknown")
FAMILIARITY_OPTIONS = ("familiar", "stranger", "unknown", "not_visible")
PERSON_FIELDS = (
    "person_id",
    "age_group",
    "face_familiarity",
    "body_reid_familiarity",
)

# ===== 只需要修改这里 =====
DEFAULT_VIDEO_PATH = ""
DEFAULT_CSV_PATH = "video_labeler_manifest.csv"


def clean_input_path(value: str) -> Path:
    """Remove common shell/clipboard quoting around a Windows path."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return Path(value).expanduser()


def prompt_for_file(label: str, suffixes: Optional[Tuple[str, ...]] = None) -> Path:
    while True:
        candidate = clean_input_path(input(label))
        if not candidate.is_file():
            print(f"文件不存在，请重新输入: {candidate}")
            continue
        if suffixes and candidate.suffix.lower() not in suffixes:
            print(f"文件扩展名需要是 {', '.join(suffixes)}")
            continue
        return candidate.resolve()


def detect_csv_format(csv_path: Path) -> Tuple[str, str, List[str], List[Dict[str, str]]]:
    raw = csv_path.read_bytes()
    last_error: Optional[Exception] = None

    for encoding in ENCODING_CANDIDATES:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

        try:
            sample = text[:8192]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","

            reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames:
                raise ValueError("CSV 没有表头")
            rows = [dict(row) for row in reader]
            return encoding, delimiter, fieldnames, rows
        except (csv.Error, ValueError) as exc:
            last_error = exc

    raise ValueError(f"无法读取 CSV: {csv_path}") from last_error


def ensure_required_fields(fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    if "sample_id" not in fieldnames:
        raise ValueError("CSV 缺少必需字段: sample_id")

    for field in (
        "behavior_id",
        "events",
        "person_count",
        "person_identity_attributes",
    ):
        if field not in fieldnames:
            fieldnames.append(field)
            for row in rows:
                row[field] = ""

    for row in rows:
        for field in fieldnames:
            row.setdefault(field, "")

    if "person_tag_list" in fieldnames:
        fieldnames.remove("person_tag_list")
        for row in rows:
            row.pop("person_tag_list", None)


def parse_person_attributes(value: Any) -> List[Dict[str, str]]:
    """Parse and normalize person identity JSON stored in one CSV cell."""
    if value is None:
        return []
    if isinstance(value, list):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []

    people: List[Dict[str, str]] = []
    for index, raw_person in enumerate(parsed, start=1):
        if not isinstance(raw_person, dict):
            continue
        person_id = str(raw_person.get("person_id") or f"p{index}").strip()
        age_group = str(raw_person.get("age_group") or "unknown").strip()
        face = str(raw_person.get("face_familiarity") or "unknown").strip()
        body = str(raw_person.get("body_reid_familiarity") or "unknown").strip()
        people.append(
            {
                "person_id": person_id or f"p{index}",
                "age_group": age_group if age_group in AGE_GROUP_OPTIONS else "unknown",
                "face_familiarity": face if face in FAMILIARITY_OPTIONS else "unknown",
                "body_reid_familiarity": body if body in FAMILIARITY_OPTIONS else "unknown",
            }
        )
    return people


def format_person_attributes(people: List[Dict[str, str]]) -> str:
    """Serialize normalized person identity attributes for a CSV cell."""
    return json.dumps(
        [
            {field: str(person.get(field) or "") for field in PERSON_FIELDS}
            for person in people
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def numeric_ms(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("时间值不能是布尔值")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if text.lower() in {"null", "none"}:
            return None
        match = re.fullmatch(
            r"-?\d+(?:\.\d+)?\s*(?:ms)?",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"无法识别时间值: {value}")
        number = float(re.sub(r"\s*ms\s*$", "", text, flags=re.IGNORECASE))

    if number < 0:
        raise ValueError("时间值不能小于 0")
    return int(round(number))


def split_behavior_ids(value: str) -> List[str]:
    return [
        item.strip()
        for item in re.split(r"[，,\n;；]+", value or "")
        if item.strip()
    ]


def parse_event_text(events_text: str, fallback_behavior_id: str) -> List[Dict[str, Any]]:
    """Read strict JSON, the dataset's 70ms-style text, or empty events."""
    text = (events_text or "").strip()
    fallback_ids = split_behavior_ids(fallback_behavior_id)

    def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_type": str(event.get("event_type") or "").strip(),
            "start_time_ms": numeric_ms(event.get("start_time_ms")),
            "end_time_ms": numeric_ms(event.get("end_time_ms")),
        }

    if text:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if isinstance(parsed, list):
                events = [
                    normalize_event(event)
                    for event in parsed
                    if isinstance(event, dict)
                ]
                events = [event for event in events if event["event_type"]]
                if events:
                    return events
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        events = []
        for object_match in re.finditer(r"\{[^{}]*\}", text, flags=re.DOTALL):
            object_text = object_match.group(0)
            type_match = re.search(
                r'"event_type"\s*:\s*"([^"]*)"',
                object_text,
                flags=re.IGNORECASE,
            )

            def parse_embedded_ms(field_name: str) -> Optional[int]:
                match = re.search(
                    rf'"{field_name}"\s*:\s*(null|"?-?\d+(?:\.\d+)?\s*ms?"?)',
                    object_text,
                    flags=re.IGNORECASE,
                )
                if not match or match.group(1).lower() == "null":
                    return None
                return numeric_ms(match.group(1).replace('"', ""))

            if type_match:
                events.append(
                    {
                        "event_type": type_match.group(1).strip(),
                        "start_time_ms": parse_embedded_ms("start_time_ms"),
                        "end_time_ms": parse_embedded_ms("end_time_ms"),
                    }
                )
        if events:
            return events

    if fallback_ids:
        return [
            {
                "event_type": event_type,
                "start_time_ms": None,
                "end_time_ms": None,
            }
            for event_type in fallback_ids
        ]
    return [{"event_type": "", "start_time_ms": None, "end_time_ms": None}]


def format_events(behaviors: List[Dict[str, Any]]) -> str:
    """Format multiple events using the dataset's literal ms suffix convention."""
    formatted_events = []
    for behavior in behaviors:
        event_type = json.dumps(
            str(behavior["event_type"]).strip(),
            ensure_ascii=False,
        )
        start_time_ms = numeric_ms(behavior.get("start_time_ms"))
        end_time_ms = numeric_ms(behavior.get("end_time_ms"))
        start = "null" if start_time_ms is None else f"{start_time_ms}ms"
        end = "null" if end_time_ms is None else f"{end_time_ms}ms"
        formatted_events.append(
            f'{{"event_type":{event_type},\n'
            f'"start_time_ms":{start},\n'
            f'"end_time_ms":{end}}}'
        )
    return "[" + ",\n".join(formatted_events) + "]"


def serialize_csv(
    csv_path: Path,
    encoding: str,
    delimiter: str,
    fieldnames: List[str],
    rows: List[Dict[str, str]],
) -> None:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        delimiter=delimiter,
        lineterminator="\r\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fieldnames})

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{csv_path.stem}.",
        suffix=".tmp",
        dir=str(csv_path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as temp_file:
            temp_file.write(output.getvalue().encode(encoding))
        os.replace(temp_name, csv_path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


class AppState:
    def __init__(
        self,
        csv_path: Optional[Path],
        video_root: Path,
        default_video_path: Optional[Path] = None,
        service: Optional[AnnotationService] = None,
        store: Optional[SQLiteStore] = None,
    ):
        if service is None:
            if csv_path is None:
                raise ValueError("csv_path is required without SQLite service")
            (
                self.encoding,
                self.delimiter,
                self.fieldnames,
                self.rows,
            ) = detect_csv_format(csv_path)
            ensure_required_fields(self.fieldnames, self.rows)
            self.csv_path = csv_path.resolve()
        else:
            self.encoding, self.delimiter = "utf-8-sig", ","
            self.fieldnames = ["sample_id", "video_path", "person_count", "person_identity_attributes"]
            self.rows = []
            self.csv_path = csv_path.resolve() if csv_path else None
        self.video_root = video_root.resolve()
        self.default_video_path = (
            default_video_path.resolve() if default_video_path else None
        )
        self.lock = threading.RLock()
        self.service = service
        self.store = store

    @classmethod
    def from_db(cls, db_path: Path, video_root: Path, csv_path: Optional[Path] = None) -> "AppState":
        root = video_root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"video directory does not exist: {root}")
        store = SQLiteStore(db_path.expanduser().resolve())
        if csv_path and csv_path.is_file():
            import_csv(csv_path, store, root)
        index_media(root, store)
        state = cls(csv_path, root, service=AnnotationService(store, root), store=store)
        state._refresh_db_rows()
        return state

    def _refresh_db_rows(self) -> None:
        if self.service is None or self.store is None:
            return
        self.rows = []
        for sample in self.store.list_samples(limit=10**9, offset=0):
            item = self.service.get_row(sample.sample_id).as_dict()
            self.rows.append({
                "sample_id": sample.sample_id,
                "video_path": sample.relative_path,
                "person_count": str(item.get("person_count", 0)),
                "person_identity_attributes": json.dumps(item.get("person_identity_attributes", []), ensure_ascii=False, separators=(",", ":")),
            })

    def db_revision(self) -> str:
        if self.store is None:
            return self.csv_revision()
        digest = hashlib.sha256()
        for sample in self.store.list_samples(limit=10**9, offset=0):
            digest.update(f"{sample.sample_id}:{sample.revision};".encode("utf-8"))
        return digest.hexdigest()

    def video_path_for_row(self, index: int) -> Path:
        row = self.rows[index]
        raw_path = str(row.get("video_path") or "").strip()
        if raw_path:
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = self.video_root / candidate
        elif self.default_video_path:
            candidate = self.default_video_path
        else:
            candidate = self.video_root / str(row.get("sample_id") or "")
        return candidate.expanduser().resolve()

    def csv_revision(self) -> str:
        digest = hashlib.sha256()
        with self.csv_path.open("rb") as csv_file:
            for chunk in iter(lambda: csv_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def row_payload(self, index: int) -> Dict[str, Any]:
        row = self.rows[index]
        behaviors = parse_event_text(row.get("events", ""), row.get("behavior_id", ""))
        people = parse_person_attributes(row.get("person_identity_attributes", ""))
        return {
            "row_index": index,
            "sample_id": row.get("sample_id", ""),
            "video_path": str(self.video_path_for_row(index)),
            "video_name": self.video_path_for_row(index).name,
            "video_url": f"/video?row={index}",
            "behavior_id": row.get("behavior_id", ""),
            "person_count": len(people),
            "person_identity_attributes": people,
            "events": row.get("events", ""),
            "behaviors": behaviors,
        }

    def state_payload(self) -> Dict[str, Any]:
        with self.lock:
            if self.service is not None:
                self._refresh_db_rows()
            return {
                "video_root": str(self.video_root),
                "csv_path": str(self.csv_path),
                "csv_revision": self.db_revision(),
                "row_count": len(self.rows),
                "rows": [self.row_payload(i) for i in range(len(self.rows))],
            }

    def save_row(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            row_index = int(payload.get("row_index"))
        except (TypeError, ValueError) as exc:
            raise ValueError("row_index 无效") from exc

        if self.service is not None and self.store is not None:
            with self.lock:
                self._refresh_db_rows()
                if not 0 <= row_index < len(self.rows):
                    raise ValueError("row_index out of range")
                sample_id = str(payload.get("sample_id") or self.rows[row_index].get("sample_id") or "").strip()
                sample = self.store.get_sample(sample_id)
                if sample is None:
                    raise ValueError("sample_id not found")
                expected = payload.get("csv_revision")
                if expected not in (None, "") and expected != self.db_revision():
                    raise CsvConflictError("SQLite dataset was modified externally; reload before saving")
                raw_people = payload.get("people", parse_person_attributes(self.rows[row_index].get("person_identity_attributes", "")))
                if not isinstance(raw_people, list):
                    raise ValueError("people must be an array")
                result = self.service.save_people(sample_id, raw_people, expected_revision=sample.revision)
                self._refresh_db_rows()
                return {"sample_id": sample_id, "person_count": len(raw_people),
                        "person_identity_attributes": self.rows[row_index]["person_identity_attributes"],
                        "csv_revision": self.db_revision()}

        if not 0 <= row_index < len(self.rows):
            raise ValueError("row_index 超出 CSV 行范围")

        sample_id = str(payload.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id 不能为空")

        raw_people = payload.get("people")
        if raw_people is None:
            raw_people = parse_person_attributes(
                self.rows[row_index].get("person_identity_attributes", "")
            )
        if not isinstance(raw_people, list):
            raise ValueError("人员身份属性必须是数组")
        people: List[Dict[str, str]] = []
        person_ids = set()
        for index, raw_person in enumerate(raw_people, start=1):
            if not isinstance(raw_person, dict):
                raise ValueError(f"第 {index} 个人员格式无效")
            person_id = str(raw_person.get("person_id") or "").strip()
            if not person_id:
                raise ValueError(f"第 {index} 个人员编号不能为空")
            if person_id in person_ids:
                raise ValueError(f"人员编号重复: {person_id}")
            person_ids.add(person_id)
            age_group = str(raw_person.get("age_group") or "unknown").strip()
            face = str(raw_person.get("face_familiarity") or "unknown").strip()
            body = str(raw_person.get("body_reid_familiarity") or "unknown").strip()
            if age_group not in AGE_GROUP_OPTIONS:
                raise ValueError(f"第 {index} 个人员年龄段无效")
            if face not in FAMILIARITY_OPTIONS:
                raise ValueError(f"第 {index} 个人员人脸熟悉度无效")
            if body not in FAMILIARITY_OPTIONS:
                raise ValueError(f"第 {index} 个人员体态熟悉度无效")
            people.append(
                {
                    "person_id": person_id,
                    "age_group": age_group,
                    "face_familiarity": face,
                    "body_reid_familiarity": body,
                }
            )
        if "person_count" in payload and payload.get("person_count") not in (None, ""):
            count_text = str(payload.get("person_count")).strip()
            if not re.fullmatch(r"\d+", count_text):
                raise ValueError("人员数必须是非负整数")
            if int(count_text) != len(people):
                raise ValueError("人员数必须与人员列表数量一致")

        with self.lock:
            expected_revision = payload.get("csv_revision")
            if expected_revision not in (None, "") and expected_revision != self.csv_revision():
                raise CsvConflictError("CSV 已被外部修改，请刷新页面后重新确认")
            row = self.rows[row_index]
            if sample_id != str(row.get("sample_id") or "").strip():
                raise ValueError("人物标注不能修改 sample_id")
            row["person_count"] = str(len(people))
            row["person_identity_attributes"] = format_person_attributes(people)
            shutil.copy2(self.csv_path, self.csv_path.with_suffix(".bak"))
            serialize_csv(
                self.csv_path,
                self.encoding,
                self.delimiter,
                self.fieldnames,
                self.rows,
            )
            return self.row_payload(row_index)


class CsvConflictError(Exception):
    """Raised when another annotation process changed the CSV first."""


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Video CSV 标注器</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17202a;
      --muted: #65717d;
      --line: #d8dee5;
      --panel: #ffffff;
      --soft: #f3f6f8;
      --accent: #1f6f8b;
      --accent-dark: #18566b;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: #edf1f4;
      font: 14px/1.45 "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    button, input, select, textarea { font: inherit; }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      min-height: 72px;
      padding: 14px 24px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }
    .title { margin: 0; font-size: 20px; font-weight: 700; }
    .meta {
      margin-top: 3px;
      max-width: 72vw;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 10px;
      white-space: nowrap;
    }
    .toolbar label { color: var(--muted); }
    select, input, textarea {
      width: 100%;
      color: var(--ink);
      background: #fff;
      border: 1px solid #bfc8d1;
      border-radius: 5px;
      padding: 8px 9px;
      outline: none;
    }
    select:focus, input:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(31, 111, 139, .14);
    }
    button {
      border: 1px solid #b8c3cc;
      border-radius: 5px;
      padding: 8px 12px;
      color: var(--ink);
      background: #fff;
      cursor: pointer;
    }
    button:hover { border-color: var(--accent); color: var(--accent-dark); }
    button.primary {
      color: white;
      background: var(--accent);
      border-color: var(--accent);
      font-weight: 700;
    }
    button.primary:hover { background: var(--accent-dark); color: white; }
    button.subtle { color: var(--muted); }
    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 1.55fr) minmax(320px, .85fr);
      height: calc(100vh - 72px);
      min-height: 0;
      overflow: hidden;
    }
    .video-pane {
      min-width: 0;
      height: 100%;
      padding: 24px;
      overflow: hidden;
    }
    .video-frame {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 360px;
      aspect-ratio: 16 / 9;
      background: #111820;
      border: 1px solid #202b35;
      border-radius: 6px;
      overflow: hidden;
    }
    video {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #111820;
    }
    .timebar {
      display: flex;
      justify-content: space-between;
      margin: 9px 1px 14px;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }
    .video-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .seek-group {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px;
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 5px;
    }
    .seek-label {
      padding: 0 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .seek-group button {
      padding: 6px 9px;
      font-size: 12px;
    }
    .video-actions .hint {
      margin-left: 4px;
      color: var(--muted);
    }
    .editor {
      height: 100%;
      min-height: 0;
      padding: 24px;
      background: var(--panel);
      border-left: 1px solid var(--line);
      overflow-x: hidden;
      overflow-y: auto;
    }
    .editor h2 {
      margin: 0 0 18px;
      font-size: 17px;
    }
    .field { margin-bottom: 14px; }
    .field label, .field-heading {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .02em;
    }
    .row-select { margin-bottom: 20px; }
    .behavior-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .behavior-card {
      padding: 12px;
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 5px;
    }
    .behavior-card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 9px;
    }
    .behavior-card-title {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .behavior-name {
      margin-bottom: 9px;
      background: white;
    }
    .behavior-time-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .behavior-time-label {
      display: block;
      margin-bottom: 4px;
      color: var(--muted);
      font-size: 12px;
    }
    .behavior-time-value {
      margin-bottom: 6px;
      background: white;
      font-variant-numeric: tabular-nums;
    }
    .behavior-time-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
    }
    .behavior-time-actions button {
      flex: 1 1 auto;
      padding: 6px 7px;
      font-size: 12px;
    }
    .play-segment { color: var(--accent-dark); }
    .person-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .person-card {
      padding: 10px;
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 5px;
    }
    .person-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .person-grid label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .person-grid input, .person-grid select { margin-top: 4px; }
    .person-card-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-top: 8px;
    }
    .person-card-title { color: var(--muted); font-size: 12px; font-weight: 700; }
    .remove-person { color: var(--danger); border-color: #e5b5af; font-size: 12px; }
    .add-person {
      width: 100%;
      margin-top: 10px;
      border-style: dashed;
    }
    .empty-people { color: var(--muted); font-size: 12px; }
    .remove-behavior {
      padding: 4px 7px;
      color: var(--danger);
      border-color: #e5b5af;
      font-size: 12px;
    }
    .add-behavior {
      width: 100%;
      margin-top: 10px;
      border-style: dashed;
    }
    .footer-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-top: 18px;
    }
    .nav-actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
    }
    #status {
      min-height: 20px;
      color: var(--muted);
      font-size: 12px;
    }
    #status.error { color: var(--danger); }
    #status.success { color: #26734d; }
    @media (max-width: 900px) {
      .topbar { align-items: flex-start; flex-direction: column; gap: 8px; }
      .meta { max-width: 92vw; }
      .workspace {
        grid-template-columns: 1fr;
        height: auto;
        min-height: calc(100vh - 72px);
        overflow: visible;
      }
      .video-pane {
        height: auto;
        overflow: visible;
      }
      .editor {
        height: auto;
        border-top: 1px solid var(--line);
        border-left: 0;
        overflow: visible;
      }
      .video-pane, .editor { padding: 16px; }
      .video-frame { min-height: 220px; }
    }
    @media (max-width: 520px) {
      .behavior-time-grid { grid-template-columns: 1fr; }
      .toolbar { width: 100%; }
      .toolbar select { width: 100%; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <div>
      <h1 class="title">Video CSV 标注器</h1>
      <div class="meta" id="sourceMeta">正在加载...</div>
    </div>
    <div class="toolbar">
      <label for="speed">全局播放速度</label>
      <select id="speed" aria-label="全局播放速度">
        <option value="0.25">0.25x</option>
        <option value="0.5">0.5x</option>
        <option value="1" selected>1x</option>
        <option value="1.5">1.5x</option>
        <option value="2">2x</option>
        <option value="3">3x</option>
        <option value="4">4x</option>
        <option value="5">5x</option>
      </select>
    </div>
  </header>

  <main class="workspace">
    <section class="video-pane">
      <div class="video-frame">
        <video id="video" controls preload="metadata"></video>
      </div>
      <div class="timebar">
        <span id="currentTime">00:00.000</span>
        <span id="duration">--:--.---</span>
      </div>
      <div class="video-actions">
        <div class="seek-group">
          <span class="seek-label">5 秒</span>
          <button type="button" data-seek="-5">后退</button>
          <button type="button" data-seek="5">前进</button>
        </div>
        <div class="seek-group">
          <span class="seek-label">10 秒</span>
          <button type="button" data-seek="-10">后退</button>
          <button type="button" data-seek="10">前进</button>
        </div>
        <div class="seek-group">
          <span class="seek-label">1 分钟</span>
          <button type="button" data-seek="-60">后退</button>
          <button type="button" data-seek="60">前进</button>
        </div>
        <span class="hint">时间点取自当前播放位置</span>
      </div>
    </section>

    <aside class="editor">
      <h2>CSV 字段</h2>
      <div class="row-select field">
        <label for="rowSelect">当前记录</label>
        <select id="rowSelect"></select>
      </div>

      <div class="field">
        <label for="sampleId">sample_id</label>
        <input id="sampleId" autocomplete="off" readonly>
      </div>
      <div class="field">
        <label for="personCount">person_count</label>
        <input id="personCount" type="number" min="0" step="1" value="0">
      </div>
      <div class="field">
        <div class="field-heading">person_identity_attributes</div>
        <div id="personList" class="person-list"></div>
        <button id="addPerson" class="add-person" type="button">+ 添加人员</button>
      </div>
      <div class="field">
          <div class="field-heading">CSV 中的事件（只读，可播放检查）</div>
        <div id="behaviorList" class="behavior-list"></div>
        <button id="addBehavior" class="add-behavior" type="button" disabled>行为事件由主标注器维护</button>
      </div>

      <div class="footer-actions">
        <div id="status" role="status"></div>
        <div class="nav-actions">
          <button id="previousButton" type="button">保存并上一段</button>
          <button id="nextButton" type="button">保存并下一段</button>
          <span id="quality-warning" aria-live="polite"></span>
          <button id="saveButton" class="primary" type="button">保存到 CSV</button>
        </div>
      </div>
    </aside>
  </main>

  <script>
    const video = document.getElementById("video");
    const speed = document.getElementById("speed");
    const rowSelect = document.getElementById("rowSelect");
    const sampleId = document.getElementById("sampleId");
    const personCount = document.getElementById("personCount");
    const personList = document.getElementById("personList");
    const behaviorList = document.getElementById("behaviorList");
    const status = document.getElementById("status");
    const currentTime = document.getElementById("currentTime");
    const duration = document.getElementById("duration");

    let appState = null;
    let selectedIndex = 0;
    let behaviors = [];
    let people = [];
    let saving = false;
    let dirty = false;
    const RESUME_STORAGE_KEY = "video-labeler:last-sample";
    const DRAFT_STORAGE_KEY = "video-labeler:person-draft";
    let draftTimer = null;
    function writeDraft(){try{localStorage.setItem(DRAFT_STORAGE_KEY,JSON.stringify({sample_id:appState?.rows?.[selectedIndex]?.sample_id,people,behaviors}))}catch{}}
    function saveDraft(){clearTimeout(draftTimer);draftTimer=setTimeout(writeDraft,250)}
    function restoreDraft(){try{return JSON.parse(localStorage.getItem(DRAFT_STORAGE_KEY)||"null")}catch{return null}}
    window.addEventListener("beforeunload",event=>{if(dirty){writeDraft();event.preventDefault();event.returnValue=""}});
    let segmentTimer = null;
    let csvRevision = "";
    const BEHAVIOR_OPTIONS = [
      "person_fall",
      "climb_fence",
      "peep_car_window",
      "pickup_package",
      "linger_wander",
      "cat_enter_frame",
      "dog_enter_frame",
      "stranger_enter_frame",
      "normal_scene",
    ];
    const AGE_GROUP_OPTIONS = ["child", "adult", "elderly", "unknown"];
    const FAMILIARITY_OPTIONS = ["familiar", "stranger", "unknown", "not_visible"];
    const AGE_GROUP_LABELS = {
      child: "Child",
      adult: "Adult",
      elderly: "Elderly",
      unknown: "Unknown",
    };

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (character) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[character]));
    }

    function clearSegmentTimer() {
      if (segmentTimer !== null) {
        window.clearInterval(segmentTimer);
        segmentTimer = null;
      }
    }

    function nextPersonId() {
      let number = people.length + 1;
      while (people.some((person) => person.person_id === `p${number}`)) number += 1;
      return `p${number}`;
    }

    function personDefaults() {
      return {
        person_id: nextPersonId(),
        age_group: "unknown",
        face_familiarity: "unknown",
        body_reid_familiarity: "unknown",
      };
    }

    function resizePeople(count) {
      const target = Math.max(0, Math.trunc(Number(count)));
      while (people.length < target) people.push(personDefaults());
      people.length = target;
    }

    function selectOptions(values, selected) {
      return values.map((value) =>
        `<option value="${value}" ${value === selected ? "selected" : ""}>${AGE_GROUP_LABELS[value] || value}</option>`
      ).join("");
    }

    function renderPeopleList() {
      if (!people.length) {
        personList.innerHTML = '<div class="empty-people">暂无人员（人员数为 0）</div>';
        return;
      }
      personList.innerHTML = people.map((person, index) => `
        <div class="person-card" data-person-index="${index}">
          <div class="person-card-footer">
            <span class="person-card-title">人员 ${index + 1}</span>
            <button class="remove-person" type="button" data-remove-person>删除</button>
          </div>
          <div class="person-grid">
            <label>编号<input data-person-field="person_id" value="${escapeHtml(person.person_id)}" autocomplete="off"></label>
            <label>年龄段<select data-person-field="age_group">${selectOptions(AGE_GROUP_OPTIONS, person.age_group)}</select></label>
            <label>人脸熟悉度<select data-person-field="face_familiarity">${selectOptions(FAMILIARITY_OPTIONS, person.face_familiarity)}</select></label>
            <label>体态熟悉度<select data-person-field="body_reid_familiarity">${selectOptions(FAMILIARITY_OPTIONS, person.body_reid_familiarity)}</select></label>
          </div>
        </div>
      `).join("");
    }

    function setStatus(message, kind = "") {
      status.textContent = message;
      status.className = kind;
    }

    function formatMs(value) {
      if (value === null || value === undefined || value === "") return "";
      return `${Math.round(Number(value))}ms`;
    }

    function formatClock(seconds) {
      if (!Number.isFinite(seconds)) return "--:--:--.---";
      return formatTimestamp(Math.max(0, Math.round(seconds * 1000)));
    }

    function formatTimestamp(value) {
      if (value === null || value === undefined || value === "") return "";
      const ms = Math.max(0, Math.round(Number(value)));
      const hours = Math.floor(ms / 3600000);
      const minutes = Math.floor((ms % 3600000) / 60000);
      const seconds = Math.floor((ms % 60000) / 1000);
      const millis = ms % 1000;
      return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
    }

    function currentVideoMs() {
      if (!Number.isFinite(video.currentTime)) return null;
      return Math.max(0, Math.round(video.currentTime * 1000));
    }

    function playEventSegment(index) {
      const behavior = behaviors[index];
      const startMs = Number(behavior?.start_time_ms);
      const endMs = Number(behavior?.end_time_ms);
      if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs < startMs) {
        setStatus("请先设置有效的开始和结束时间", "error");
        return;
      }
      clearSegmentTimer();
      const startSeconds = Math.max(0, startMs / 1000);
      const endSeconds = Math.max(startSeconds, endMs / 1000);
      const startPlayback = () => {
        video.currentTime = Number.isFinite(video.duration)
          ? Math.min(startSeconds, video.duration)
          : startSeconds;
        video.play().catch(() => {
          setStatus("浏览器阻止了自动播放，请点击视频播放", "error");
        });
        segmentTimer = window.setInterval(() => {
          if (video.currentTime >= endSeconds) {
            video.pause();
            clearSegmentTimer();
          }
        }, 50);
      };
      if (video.readyState >= 1) {
        startPlayback();
      } else {
        video.addEventListener("loadedmetadata", startPlayback, { once: true });
      }
    }

    function renderBehaviorList() {
      behaviorList.replaceChildren();
      behaviors.forEach((behavior, index) => {
        const card = document.createElement("div");
        card.className = "behavior-card";

        const header = document.createElement("div");
        header.className = "behavior-card-header";
        const title = document.createElement("div");
        title.className = "behavior-card-title";
        title.textContent = `行为 ${index + 1}`;
        const removeButton = document.createElement("button");
        removeButton.className = "remove-behavior";
        removeButton.type = "button";
        removeButton.textContent = "删除";
        removeButton.disabled = true;
        removeButton.addEventListener("click", () => {
          if (behaviors.length === 1) {
            setStatus("至少需要保留一个行为", "error");
            return;
          }
          behaviors.splice(index, 1);
          renderBehaviorList();
        });
        header.append(title, removeButton);

        const nameInput = document.createElement("select");
        nameInput.className = "behavior-name";
        nameInput.disabled = true;
        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = "请选择行为";
        nameInput.appendChild(emptyOption);
        BEHAVIOR_OPTIONS.forEach((optionValue) => {
          const option = document.createElement("option");
          option.value = optionValue;
          option.textContent = optionValue;
          nameInput.appendChild(option);
        });
        if (
          behavior.event_type &&
          !BEHAVIOR_OPTIONS.includes(behavior.event_type)
        ) {
          const currentOption = document.createElement("option");
          currentOption.value = behavior.event_type;
          currentOption.textContent = `当前值：${behavior.event_type}`;
          nameInput.appendChild(currentOption);
        }
        nameInput.value = behavior.event_type || "";
        nameInput.addEventListener("change", () => {
          behaviors[index].event_type = nameInput.value;
        });

        const timeGrid = document.createElement("div");
        timeGrid.className = "behavior-time-grid";
        for (const [key, label] of [
          ["start_time_ms", "开始时间"],
          ["end_time_ms", "结束时间"],
        ]) {
          const timeBox = document.createElement("div");
          const timeLabel = document.createElement("label");
          timeLabel.className = "behavior-time-label";
          timeLabel.textContent = label;
          const timeValue = document.createElement("input");
          timeValue.className = "behavior-time-value";
          timeValue.type = "text";
          timeValue.readOnly = true;
          timeValue.placeholder = "未设置";
          timeValue.value = formatTimestamp(behavior[key]);
          const actions = document.createElement("div");
          actions.className = "behavior-time-actions";

          const setButton = document.createElement("button");
          setButton.type = "button";
          setButton.textContent = "取当前时间";
          setButton.disabled = true;
          setButton.addEventListener("click", () => {
            behaviors[index][key] = currentVideoMs();
            renderBehaviorList();
          });

          const clearButton = document.createElement("button");
          clearButton.type = "button";
          clearButton.className = "subtle";
          clearButton.textContent = "清空";
          clearButton.disabled = true;
          clearButton.addEventListener("click", () => {
            behaviors[index][key] = null;
            renderBehaviorList();
          });

          actions.append(setButton, clearButton);
          timeBox.append(timeLabel, timeValue, actions);
          timeGrid.appendChild(timeBox);
        }

        const playButton = document.createElement("button");
        playButton.className = "play-segment";
        playButton.type = "button";
        playButton.textContent = "播放片段";
        playButton.addEventListener("click", () => playEventSegment(index));

        card.append(header, nameInput, timeGrid, playButton);
        behaviorList.appendChild(card);
      });
    }

    function renderRow(index) {
      clearSegmentTimer();
      video.pause();
      selectedIndex = Number(index);
      const row = appState.rows[selectedIndex];
      localStorage.setItem(RESUME_STORAGE_KEY, row.sample_id || "");
      if (!row) return;

      sampleId.value = row.sample_id || "";
      video.src = row.video_url || `/video?row=${selectedIndex}`;
      video.load();
      people = (row.person_identity_attributes || []).map((person, index) => ({
        person_id: person.person_id || `p${index + 1}`,
        age_group: AGE_GROUP_OPTIONS.includes(person.age_group) ? person.age_group : "unknown",
        face_familiarity: FAMILIARITY_OPTIONS.includes(person.face_familiarity) ? person.face_familiarity : "unknown",
        body_reid_familiarity: FAMILIARITY_OPTIONS.includes(person.body_reid_familiarity) ? person.body_reid_familiarity : "unknown",
      }));
      personCount.value = String(people.length);
      renderPeopleList();
      behaviors = (row.behaviors || []).map((behavior) => ({
        event_type: behavior.event_type || "",
        start_time_ms: behavior.start_time_ms ?? null,
        end_time_ms: behavior.end_time_ms ?? null,
      }));
      if (!behaviors.length) {
        behaviors = [{ event_type: "", start_time_ms: null, end_time_ms: null }];
      }
      renderBehaviorList();
      const draft = restoreDraft();
      if (draft && draft.sample_id === row.sample_id) {
        if (Array.isArray(draft.people)) people = draft.people;
        if (Array.isArray(draft.behaviors)) behaviors = draft.behaviors;
        personCount.value = String(people.length);
        renderPeopleList();
        renderBehaviorList();
        dirty = true;
        setStatus("宸叉仮澶嶆湭淇濆瓨鑽夌");
      }
      setStatus(`第 ${selectedIndex + 1} / ${appState.row_count} 条`);
    }

    function renderRows() {
      rowSelect.replaceChildren();
      appState.rows.forEach((row, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = `${index + 1}. ${row.sample_id || "(空 sample_id)"}`;
        rowSelect.appendChild(option);
      });
      rowSelect.value = String(selectedIndex);
      renderRow(selectedIndex);
    }

    async function loadState() {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (!response.ok) throw new Error("无法读取 CSV 状态");
      appState = await response.json();
      csvRevision = appState.csv_revision || "";
      document.getElementById("sourceMeta").textContent =
        `${appState.video_root}  |  ${appState.csv_path}  |  ${appState.row_count} 条记录`;
      renderRows();
      const resumeId = localStorage.getItem(RESUME_STORAGE_KEY);
      const resumeIndex = resumeId ? appState.rows.findIndex((row) => row.sample_id === resumeId) : -1;
      if (resumeIndex >= 0) { selectedIndex = resumeIndex; renderRows(); }
    }

    video.addEventListener("timeupdate", () => {
      currentTime.textContent = formatClock(video.currentTime);
    });
    video.addEventListener("loadedmetadata", () => {
      duration.textContent = formatClock(video.duration);
      currentTime.textContent = formatClock(video.currentTime);
    });
    video.addEventListener("pause", clearSegmentTimer);
    speed.addEventListener("change", () => {
      video.playbackRate = Number(speed.value);
    });
    document.querySelectorAll("[data-seek]").forEach((button) => {
      button.addEventListener("click", () => {
        const seconds = Number(button.dataset.seek);
        const target = video.currentTime + seconds;
        video.currentTime = Math.max(
          0,
          Math.min(video.duration || Infinity, target)
        );
      });
    });
    rowSelect.addEventListener("change", () => renderRow(rowSelect.value));
    personCount.addEventListener("change", () => {
      const text = personCount.value.trim();
      const count = Number(text);
      if (!/^\d+$/.test(text) || !Number.isSafeInteger(count)) {
        setStatus("人员数必须是非负整数", "error");
        personCount.value = String(people.length);
        return;
      }
      resizePeople(count);
      personCount.value = String(people.length);
      renderPeopleList();
    });
    document.getElementById("addPerson").addEventListener("click", () => {
      people.push(personDefaults());
      personCount.value = String(people.length);
      renderPeopleList();
    });
    personList.addEventListener("input", (event) => {
      const control = event.target.closest("[data-person-field]");
      if (!control) return;
      const card = control.closest("[data-person-index]");
      const index = Number(card?.dataset.personIndex);
      if (people[index]) { people[index][control.dataset.personField] = control.value; dirty = true; saveDraft(); }
    });
    personList.addEventListener("change", (event) => {
      const control = event.target.closest("[data-person-field]");
      if (!control) return;
      const card = control.closest("[data-person-index]");
      const index = Number(card?.dataset.personIndex);
      if (people[index]) { people[index][control.dataset.personField] = control.value; dirty = true; saveDraft(); }
    });
    personList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-person]");
      if (!button) return;
      const card = button.closest("[data-person-index]");
      people.splice(Number(card?.dataset.personIndex), 1);
      personCount.value = String(people.length);
      renderPeopleList();
    });
    async function saveCurrent(moveBy = 0) {
      if (saving) return;
      saving = true;
      document.getElementById("saveButton").disabled = true;
      setStatus("正在保存...");
      const payload = {
        row_index: selectedIndex,
        sample_id: sampleId.value,
        person_count: people.length,
        people,
        csv_revision: csvRevision,
      };
      try {
        const response = await fetch("/api/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "保存失败");
        csvRevision = result.csv_revision || csvRevision;
        dirty = false;
        appState.rows[selectedIndex] = result.row;
        if (moveBy !== 0) {
          const nextIndex = selectedIndex + moveBy;
          if (nextIndex >= 0 && nextIndex < appState.rows.length) {
            selectedIndex = nextIndex;
            renderRows();
            setStatus(`已保存，已切换到第 ${selectedIndex + 1} / ${appState.row_count} 条`, "success");
          } else {
            renderRows();
            setStatus(moveBy < 0 ? "已保存，当前已经是第一段" : "已保存，当前已经是最后一段", "success");
          }
        } else {
          renderRows();
          rowSelect.value = String(selectedIndex);
          setStatus("已保存到 CSV", "success");
        }
      } catch (error) {
        setStatus(error.message || String(error), "error");
      } finally {
        saving = false;
        document.getElementById("saveButton").disabled = false;
      }
    }

    document.getElementById("saveButton").addEventListener("click", () => saveCurrent(0));
    document.getElementById("previousButton").addEventListener("click", () => saveCurrent(-1));
    document.getElementById("nextButton").addEventListener("click", () => saveCurrent(1));

    loadState().catch((error) => setStatus(error.message || String(error), "error"));
  </script>
</body>
</html>
"""


class VideoCsvHandler(BaseHTTPRequestHandler):
    state: AppState

    def log_message(self, format: str, *args: Any) -> None:
        sys.stdout.write(f"[web] {format % args}\n")

    def send_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/api/state":
            self.send_json(self.state.state_payload())
            return
        if route.startswith("/api/predictions/") and self.state.service is not None:
            prediction = self.state.service.get_prediction(route.rsplit("/", 1)[-1])
            if prediction is None:
                self.send_json({"ok": False, "error": "prediction not found"}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json({"prediction_id": prediction.prediction_id, "sample_id": prediction.sample_id, "task": prediction.task, "label_json": prediction.label_json, "model_name": prediction.model_name, "model_version": prediction.model_version, "confidence": prediction.confidence})
            return
        if route == "/video":
            self.serve_video(send_body=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        if urlparse(self.path).path == "/video":
            self.serve_video(send_body=False)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route.startswith("/api/predictions/") and route.rsplit("/", 1)[-1] in ("accept", "reject"):
            if self.state.service is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            prediction_id, action = route[len("/api/predictions/"):].rsplit("/", 1)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                if not isinstance(payload, dict):
                    raise ValueError("request body must be an object")
                actor = payload.get("actor", "human")
                expected = payload.get("expected_revision")
                if not isinstance(actor, str) or not actor.strip():
                    raise ValueError("actor is required")
                if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 0):
                    raise ValueError("expected_revision must be a non-negative integer")
                if action == "accept":
                    result = self.state.service.accept_prediction(prediction_id, actor.strip(), expected)
                    self.state._refresh_db_rows()
                    self.send_json({"ok": True, "sample_id": result.sample_id, "revision": result.revision, "review_status": result.review_status})
                else:
                    self.state.service.reject_prediction(prediction_id, actor.strip())
                    self.state._refresh_db_rows()
                    self.send_json({"ok": True})
            except ConflictError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.CONFLICT)
            except KeyError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.NOT_FOUND)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if route != "/api/save":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                raise ValueError("请求内容为空或过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            row = self.state.save_row(payload)
            self.send_json({"ok": True, "row": row, "csv_revision": self.state.csv_revision()})
        except CsvConflictError as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.CONFLICT)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self.send_json({"ok": False, "error": f"写入 CSV 失败: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_video(self, send_body: bool) -> None:
        query = parse_qs(urlparse(self.path).query)
        try:
            row_index = int(query.get("row", ["0"])[0])
        except (TypeError, ValueError):
            self.send_error(HTTPStatus.BAD_REQUEST, "invalid video row")
            return
        if not 0 <= row_index < len(self.state.rows):
            self.send_error(HTTPStatus.NOT_FOUND, "CSV row not found")
            return
        video_path = self.state.video_path_for_row(row_index)
        if not video_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "video file not found")
            return

        size = video_path.stat().st_size
        start = 0
        end = size - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            if match.group(1):
                start = int(match.group(1))
            if match.group(2):
                end = int(match.group(2))
            elif match.group(1):
                end = min(start + 1024 * 1024 - 1, size - 1)
            else:
                start = max(0, size - 1024 * 1024)
            if start >= size or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)

        content_length = end - start + 1
        status = HTTPStatus.PARTIAL_CONTENT if range_header else HTTPStatus.OK
        content_type = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(content_length))
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body:
            return

        with video_path.open("rb") as video_file:
            video_file.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = video_file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def choose_server(host: str, requested_port: int) -> Tuple[ThreadingHTTPServer, int]:
    last_error: Optional[OSError] = None
    for port in range(requested_port, requested_port + 20):
        try:
            server = ThreadingHTTPServer((host, port), VideoCsvHandler)
            return server, server.server_port
        except OSError as exc:
            last_error = exc
    raise OSError(f"无法找到可用端口，尝试范围 {requested_port}-{requested_port + 19}") from last_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用网页标注视频中的 CSV 行为时间并回写 CSV")
    parser.add_argument("--video", help="兼容单视频 CSV 的视频文件地址")
    parser.add_argument("--video-root", help="CSV 中相对 video_path 的根目录")
    parser.add_argument("--csv", dest="csv_path", help="对应 CSV 文件地址")
    parser.add_argument("--host", default="127.0.0.1", help="本地服务地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="起始端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不自动打开浏览器")
    parser.add_argument("--db", help="SQLite database path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = clean_input_path(args.csv_path) if args.csv_path else Path(DEFAULT_CSV_PATH)
    video_path = clean_input_path(args.video) if args.video else None
    video_root = (
        clean_input_path(args.video_root)
        if args.video_root
        else csv_path.parent
    )

    if video_path is not None and not video_path.is_file():
        print(f"视频文件不存在: {video_path}", file=sys.stderr)
        return 2
    if not args.db and not csv_path.is_file():
        print(f"CSV 文件不存在: {csv_path}", file=sys.stderr)
        return 2
    if not video_root.is_dir():
        print(f"视频根目录不存在: {video_root}", file=sys.stderr)
        return 2

    try:
        state = AppState.from_db(Path(args.db), video_root.resolve(), csv_path if csv_path.is_file() else None) if args.db else AppState(csv_path.resolve(), video_root.resolve(), video_path)
    except (OSError, ValueError) as exc:
        print(f"读取文件失败: {exc}", file=sys.stderr)
        return 2

    VideoCsvHandler.state = state
    try:
        server, port = choose_server(args.host, args.port)
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    url = f"http://{args.host}:{port}/"
    print(f"已加载视频根目录: {state.video_root}")
    print(f"已加载 CSV:  {state.csv_path}")
    print(f"CSV 记录数:  {len(state.rows)}")
    print(f"网页地址:    {url}")
    print("关闭网页后，在此终端按 Ctrl+C 结束服务。")

    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
