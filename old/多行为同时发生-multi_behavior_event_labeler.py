#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多行为视频 events 标注工具。

示例：
    python multi_behavior_event_labeler.py

指定其他数据集：
    python multi_behavior_event_labeler.py --csv "D:\\data\\manifest.csv" --video-root "D:\\data\\videos"
"""

import argparse
import csv
import json
import mimetypes
import os
import re
import shutil
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DEFAULT_CSV = Path(r"E:\data\yolo视频评测集_720p\多行为同时发生\yolo_behavior_eval_manifest.csv")
DEFAULT_VIDEO_ROOT = DEFAULT_CSV.parent
CSV_PATH = None
VIDEO_ROOT = None
CSV_ENCODING = None
CSV_LOCK = threading.Lock()
SCRIPT_DIR = Path(__file__).resolve().parent
BACKUP_PATH = None
PERSON_TAG_VALUES = ("stranger", "acquaintance", "null")


def validate_person_tag(value):
    if value not in PERSON_TAG_VALUES:
        raise ValueError("person_tag_list must be stranger, acquaintance, or null")
    return value


def ensure_person_tag_column(fieldnames):
    if "person_tag_list" not in fieldnames:
        fieldnames.append("person_tag_list")


def detect_encoding(path):
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            pass
    raise ValueError(f"无法识别 CSV 编码: {path}")


def parse_behavior_ids(value):
    return [part.strip() for part in re.split(r"[，,]", value or "") if part.strip()]


def parse_events(value, behavior_ids):
    """Read the project's ms-suffixed event format into browser-friendly objects."""
    parsed = {}
    if value:
        pattern = re.compile(
            r'"event_type"\s*:\s*"([^"]+)"\s*,\s*'
            r'"start_time_ms"\s*:\s*(null|\d+\s*ms)\s*,\s*'
            r'"end_time_ms"\s*:\s*(null|\d+\s*ms)',
            re.DOTALL,
        )
        for event_type, start, end in pattern.findall(value):
            parsed[event_type] = {
                "event_type": event_type,
                "start_time_ms": None if start == "null" else int(re.search(r"\d+", start).group()),
                "end_time_ms": None if end == "null" else int(re.search(r"\d+", end).group()),
            }

    return [
        parsed.get(
            behavior_id,
            {"event_type": behavior_id, "start_time_ms": None, "end_time_ms": None},
        )
        for behavior_id in behavior_ids
    ]


def events_to_csv_value(events):
    """Match the requested CSV representation; csv.writer escapes inner quotes."""
    lines = []
    for event in events:
        start = "null" if event["start_time_ms"] is None else f'{event["start_time_ms"]}ms'
        end = "null" if event["end_time_ms"] is None else f'{event["end_time_ms"]}ms'
        lines.append(
            '{"event_type":"%s",\n"start_time_ms":%s,\n"end_time_ms":%s}'
            % (event["event_type"], start, end)
        )
    return "[\n" + ",\n".join(lines) + "\n]"


def read_rows():
    with CSV_LOCK, CSV_PATH.open("r", encoding=CSV_ENCODING, newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return rows, fieldnames


def write_rows(rows, fieldnames):
    global BACKUP_PATH
    if BACKUP_PATH is None:
        backup_dir = SCRIPT_DIR / "event_labeler_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{CSV_PATH.stem}.before_event_labeling_{timestamp}.csv"
        suffix = 2
        while backup_path.exists():
            backup_path = backup_dir / f"{CSV_PATH.stem}.before_event_labeling_{timestamp}_{suffix}.csv"
            suffix += 1
        shutil.copy2(CSV_PATH, backup_path)
        BACKUP_PATH = backup_path

    with CSV_LOCK, CSV_PATH.open("w", encoding=CSV_ENCODING, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return BACKUP_PATH


def relative_video_path(video_path):
    source = Path(video_path)
    try:
        return source.relative_to(VIDEO_ROOT)
    except ValueError:
        root_name = VIDEO_ROOT.name
        normalized = video_path.replace("\\", "/")
        marker = f"/{root_name}/"
        if marker in normalized:
            return Path(normalized.split(marker, 1)[1])
        return Path(source.name)


def safe_video_path(relative_path):
    root = VIDEO_ROOT.resolve()
    full = (VIDEO_ROOT / relative_path).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        return None
    return full


def time_text_from_ms(value):
    if value is None:
        return ""
    total_seconds = value / 1000
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    if seconds.is_integer():
        second_text = f"{int(seconds):02d}"
    else:
        second_text = f"{seconds:05.2f}".rstrip("0").rstrip(".")
    return f"{hours}:{minutes:02d}:{second_text}"


def milliseconds_from_text(value):
    value = (value or "").strip().lower()
    if value in ("", "null"):
        return None
    match = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", value)
    if not match:
        raise ValueError(f"时间格式应为 0:00:07，当前为: {value}")
    hours, minutes, seconds = match.groups()
    if int(minutes) >= 60 or float(seconds) >= 60:
        raise ValueError(f"时间格式不合法: {value}")
    return int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000))


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>多行为 Events 标注</title>
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:#1a1a1a;color:#ddd;display:flex;flex-direction:column;font-family:Arial,"Microsoft YaHei",sans-serif;overflow:hidden}
header{align-items:center;background:#272727;border-bottom:1px solid #404040;display:flex;height:44px;justify-content:space-between;padding:0 14px}
h1{font-size:15px;margin:0}.toolbar{align-items:center;display:flex;font-size:12px;gap:7px}
button,input,select{font:inherit}.toolbar button,.nav button,.save{background:#383838;border:1px solid #555;border-radius:4px;color:#ddd;cursor:pointer;padding:5px 9px}
.toolbar button.active{background:#287849;border-color:#47a86d;color:#fff}.toolbar button:hover,.nav button:hover{background:#4c4c4c}
.layout{display:flex;flex:1;min-height:0}.viewer{background:#000;display:flex;flex:1;flex-direction:column;min-width:0}
.video-box{align-items:center;display:flex;flex:1;justify-content:center;min-height:0;padding:8px}
video{background:#000;display:block;max-height:100%;max-width:100%;object-fit:contain}
.meta{background:#202020;border-top:1px solid #3b3b3b;font-size:12px;overflow:hidden;padding:7px 10px;text-overflow:ellipsis;white-space:nowrap}
.meta strong{color:#65aef1}.side{background:#252525;border-left:1px solid #404040;display:flex;flex-direction:column;min-height:0;width:390px}
.section{border-bottom:1px solid #3d3d3d;padding:10px}.section-title{color:#9a9a9a;font-size:12px;margin-bottom:8px}
.event-card{background:#2d2d2d;border:1px solid #484848;border-radius:5px;margin-bottom:8px;padding:9px}
.event-card:last-child{margin-bottom:0}.event-name{color:#71b9f1;font-size:13px;font-weight:600;margin-bottom:7px}
.time-row{align-items:center;display:grid;gap:6px;grid-template-columns:40px 1fr auto;margin-top:5px}
.time-row label{color:#aaa;font-size:12px}.time-row input{background:#373737;border:1px solid #575757;border-radius:4px;color:#eee;font-family:Consolas,monospace;padding:6px;text-align:center}
.time-row input:focus{border-color:#62aee7;outline:none}.capture{background:#414141;border:1px solid #5a5a5a;border-radius:4px;color:#ddd;cursor:pointer;font-size:11px;padding:6px 8px}
.capture:hover{background:#5c5c5c}.action-row{display:flex;gap:6px}.save{background:#276e9f;border-color:#459bd3;flex:1;font-weight:600}.save:hover{background:#3585bc}
.nav{display:flex;gap:6px;margin-top:6px}.nav button{flex:1}.status{background:#1d2d20;color:#80da93;font-size:12px;padding:6px 10px}.status.error{background:#311e1e;color:#fb9595}
.filter{border-bottom:1px solid #3d3d3d;display:flex;gap:6px;padding:7px}.filter select{background:#373737;border:1px solid #555;border-radius:4px;color:#ddd;flex:1;padding:5px}
.list{flex:1;overflow:auto;padding:4px}.item{align-items:center;border-radius:4px;cursor:pointer;display:flex;font-size:12px;gap:6px;padding:7px}.item:hover{background:#393939}.item.active{background:#30485d}
.number{color:#777;text-align:right;width:27px}.sample{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.badge{background:#444;border-radius:3px;color:#aaa;font-size:10px;padding:2px 5px}.badge.done{background:#367d50;color:#fff}.badge.partial{background:#987829;color:#fff}
@media(max-width:900px){.side{width:340px}}@media(max-width:700px){.side{width:300px}.event-card{padding:6px}}
</style>
</head>
<body>
<header>
  <h1>多行为 Events 标注</h1>
  <div class="toolbar">
    <span>全局倍速</span>
    <button data-speed="0.5">0.5x</button>
    <button data-speed="1" class="active">1x</button>
    <button data-speed="2">2x</button>
    <span id="counter">0 / 0</span>
  </div>
</header>
<main class="layout">
  <section class="viewer">
    <div class="video-box"><video id="video" controls preload="metadata"></video></div>
    <div class="meta" id="meta">加载中...</div>
  </section>
  <aside class="side">
    <section class="section">
      <div class="section-title">人员标签</div>
      <select id="person-tag">
        <option value="stranger">1. stranger</option>
        <option value="acquaintance">2. acquaintance</option>
        <option value="null">3. null</option>
      </select>
    </section>
    <section class="section">
      <div class="section-title">事件时间段</div>
      <div id="event-editor"></div>
      <div class="action-row">
        <button id="save" class="save">保存</button>
      </div>
      <div class="nav">
        <button id="previous">上一条</button>
        <button id="next">保存并下一条</button>
      </div>
    </section>
    <div id="status" class="status">就绪</div>
    <div class="filter">
      <select id="filter">
        <option value="">全部视频</option>
        <option value="unfinished">未完成</option>
        <option value="complete">已完成</option>
      </select>
    </div>
    <div id="list" class="list"></div>
  </aside>
</main>
<script>
const video = document.getElementById("video");
const eventEditor = document.getElementById("event-editor");
const personTag = document.getElementById("person-tag");
let rows = [], current = -1, speed = 1, dirty = false;
const el = (id) => document.getElementById(id);

function personTagValue(value) {
  return ["stranger", "acquaintance", "null"].includes(value) ? value : "null";
}

function status(text, error=false) {
  el("status").textContent = text;
  el("status").className = error ? "status error" : "status";
}
function timeText(ms) {
  if (ms === null || ms === undefined) return "";
  const seconds = ms / 1000;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}
function parseTime(text) {
  const value = text.trim();
  if (!value || value === "null") return null;
  const match = value.match(/^(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$/);
  if (!match) throw new Error(`时间格式应为 0:00:07：${value}`);
  const [, h, m, s] = match;
  if (Number(m) >= 60 || Number(s) >= 60) throw new Error(`时间格式不合法：${value}`);
  return Math.round((Number(h) * 3600 + Number(m) * 60 + Number(s)) * 1000);
}
function getEvents() {
  return [...eventEditor.querySelectorAll(".event-card")].map(card => ({
    event_type: card.dataset.eventType,
    start_time_ms: parseTime(card.querySelector(".start").value),
    end_time_ms: parseTime(card.querySelector(".end").value)
  }));
}
function eventState(row) {
  const events = row.events || [];
  const complete = events.length > 0 && events.every(event => event.start_time_ms !== null && event.end_time_ms !== null);
  const started = events.some(event => event.start_time_ms !== null || event.end_time_ms !== null);
  return complete ? "complete" : (started ? "partial" : "empty");
}
function badge(row) {
  const state = eventState(row);
  if (state === "complete") return '<span class="badge done">已完成</span>';
  if (state === "partial") return '<span class="badge partial">部分填写</span>';
  return '<span class="badge">未标</span>';
}
function renderList() {
  const filter = el("filter").value;
  const list = el("list");
  list.innerHTML = "";
  rows.forEach((row, index) => {
    const state = eventState(row);
    if (filter === "complete" && state !== "complete") return;
    if (filter === "unfinished" && state === "complete") return;
    const item = document.createElement("div");
    item.className = `item${index === current ? " active" : ""}`;
    item.innerHTML = `<span class="number">${index + 1}</span><span class="sample" title="${row.sample_id}">${row.sample_id}</span>${badge(row)}`;
    item.onclick = () => openRow(index);
    list.appendChild(item);
  });
  el("counter").textContent = current < 0 ? `${rows.length} 条` : `${current + 1} / ${rows.length}`;
}
function renderEvents(events) {
  eventEditor.innerHTML = "";
  events.forEach((event, index) => {
    const card = document.createElement("div");
    card.className = "event-card";
    card.dataset.eventType = event.event_type;
    card.innerHTML = `
      <div class="event-name">${event.event_type}</div>
      <div class="time-row"><label>开始</label><input class="start" placeholder="null" value="${timeText(event.start_time_ms)}"><button class="capture start-capture">截取</button></div>
      <div class="time-row"><label>结束</label><input class="end" placeholder="null" value="${timeText(event.end_time_ms)}"><button class="capture end-capture">截取</button></div>
    `;
    card.querySelectorAll("input").forEach(input => input.addEventListener("input", () => dirty = true));
    card.querySelector(".start-capture").onclick = async () => {
      card.querySelector(".start").value = timeText(Math.floor(video.currentTime * 1000));
      dirty = true;
      await saveCurrent();
    };
    card.querySelector(".end-capture").onclick = async () => {
      card.querySelector(".end").value = timeText(Math.floor(video.currentTime * 1000));
      dirty = true;
      await saveCurrent();
    };
    eventEditor.appendChild(card);
  });
}
async function saveCurrent() {
  if (current < 0 || !dirty) return true;
  let events;
  try {
    events = getEvents();
    for (const event of events) {
      if (event.start_time_ms !== null && event.end_time_ms !== null && event.end_time_ms <= event.start_time_ms) {
        throw new Error(`${event.event_type} 的结束时间必须晚于开始时间`);
      }
    }
  } catch (error) {
    status(error.message, true);
    return false;
  }
  status("保存中...");
  try {
    const response = await fetch("/api/update", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({sample_id: rows[current].sample_id, person_tag_list: personTag.value, events})
    });
    const result = await response.json();
    if (!result.ok) throw new Error(result.error || "保存失败");
    rows[current].events = events;
    rows[current].person_tag_list = personTag.value;
    dirty = false;
    renderList();
    status(`已保存；备份文件：${result.backup_name}`);
    return true;
  } catch (error) {
    status(error.message || "保存失败", true);
    return false;
  }
}
async function openRow(index) {
  if (index < 0 || index >= rows.length) return;
  if (!(await saveCurrent())) return;
  current = index;
  const row = rows[index];
  video.src = row.video_url;
  video.playbackRate = speed;
  el("meta").innerHTML = `<strong>${row.sample_id}</strong> &nbsp;|&nbsp; ${row.behavior_id} &nbsp;|&nbsp; ${row.data_stratum} &nbsp;|&nbsp; ${row.lighting}`;
  personTag.value = personTagValue(row.person_tag_list);
  renderEvents(row.events);
  el("previous").disabled = index === 0;
  el("next").disabled = index === rows.length - 1;
  dirty = false;
  renderList();
  status("就绪");
}
async function load() {
  const response = await fetch("/api/videos");
  rows = await response.json();
  renderList();
  if (rows.length) await openRow(0);
}
document.querySelectorAll(".toolbar button").forEach(button => button.onclick = () => {
  document.querySelectorAll(".toolbar button").forEach(item => item.classList.remove("active"));
  button.classList.add("active");
  speed = Number(button.dataset.speed);
  video.playbackRate = speed;
});
el("save").onclick = saveCurrent;
personTag.onchange = () => { dirty = true; };
el("previous").onclick = () => openRow(current - 1);
el("next").onclick = async () => { if (await saveCurrent()) openRow(current + 1); };
el("filter").onchange = renderList;
document.addEventListener("keydown", event => {
  if (event.target.tagName === "INPUT" || event.target.tagName === "SELECT") return;
  if (event.key === "ArrowLeft") openRow(current - 1);
  if (event.key === "ArrowRight") openRow(current + 1);
  if (event.key === " ") { event.preventDefault(); video.paused ? video.play() : video.pause(); }
});
load();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        request_path = unquote(urlparse(self.path).path)
        if request_path in ("/", "/index.html"):
            self.send_bytes(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
            return

        if request_path == "/api/videos":
            rows, _ = read_rows()
            response = []
            for row in rows:
                behavior_ids = parse_behavior_ids(row.get("behavior_id", ""))
                event_data = parse_events(row.get("events", ""), behavior_ids)
                output = dict(row)
                output["events"] = event_data
                output["video_url"] = "/video/" + relative_video_path(row.get("video_path", "")).as_posix()
                response.append(output)
            self.send_bytes(200, "application/json; charset=utf-8", json.dumps(response, ensure_ascii=False).encode("utf-8"))
            return

        if request_path.startswith("/video/"):
            source = safe_video_path(Path(request_path[len("/video/"):]))
            if not source or not source.is_file():
                self.send_error(404)
                return
            self.send_video(source)
            return

        self.send_error(404)

    def send_video(self, source):
        total_size = source.stat().st_size
        start = 0
        end = total_size - 1
        requested_range = self.headers.get("Range")
        if requested_range and requested_range.startswith("bytes="):
            requested_start, requested_end = requested_range[6:].split("-", 1)
            start = int(requested_start or 0)
            end = int(requested_end) if requested_end else total_size - 1
            end = min(end, total_size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
        else:
            self.send_response(200)

        self.send_header("Content-Type", mimetypes.guess_type(str(source))[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()

        with source.open("rb") as file:
            file.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self):
        request_path = unquote(urlparse(self.path).path)
        if request_path != "/api/update":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            sample_id = payload["sample_id"]
            person_tag = validate_person_tag(payload["person_tag_list"])
            provided_events = payload["events"]
            rows, fieldnames = read_rows()
            ensure_person_tag_column(fieldnames)
            matched = False

            for row in rows:
                if row.get("sample_id") != sample_id:
                    continue
                allowed = parse_behavior_ids(row.get("behavior_id", ""))
                cleaned = []
                for event in provided_events:
                    event_type = event.get("event_type")
                    if event_type not in allowed:
                        raise ValueError(f"不允许的事件类型: {event_type}")
                    start = event.get("start_time_ms")
                    end = event.get("end_time_ms")
                    if start is not None and not isinstance(start, int):
                        raise ValueError("开始时间必须是毫秒整数或 null")
                    if end is not None and not isinstance(end, int):
                        raise ValueError("结束时间必须是毫秒整数或 null")
                    cleaned.append({"event_type": event_type, "start_time_ms": start, "end_time_ms": end})

                if [event["event_type"] for event in cleaned] != allowed:
                    raise ValueError("事件列表必须与 behavior_id 完全对应")
                row["events"] = events_to_csv_value(cleaned)
                row["person_tag_list"] = person_tag
                matched = True
                break

            if not matched:
                self.send_bytes(404, "application/json", b'{"ok":false,"error":"sample_id not found"}')
                return

            if "events" not in fieldnames:
                fieldnames.append("events")
            backup_path = write_rows(rows, fieldnames)
            body = json.dumps(
                {"ok": True, "backup_name": str(backup_path)},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_bytes(200, "application/json; charset=utf-8", body)
        except (KeyError, ValueError, json.JSONDecodeError) as error:
            body = json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False).encode("utf-8")
            self.send_bytes(400, "application/json; charset=utf-8", body)
        except OSError as error:
            body = json.dumps({"ok": False, "error": f"CSV 写入失败: {error}"}, ensure_ascii=False).encode("utf-8")
            self.send_bytes(500, "application/json; charset=utf-8", body)

    def log_message(self, *_):
        return


def main():
    global CSV_PATH, VIDEO_ROOT, CSV_ENCODING
    parser = argparse.ArgumentParser(description="多行为 events 标注网页")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="要写入的 manifest CSV")
    parser.add_argument("--video-root", type=Path, default=DEFAULT_VIDEO_ROOT, help="视频根目录")
    parser.add_argument("--port", type=int, default=8765, help="本地网页端口")
    args = parser.parse_args()

    CSV_PATH = args.csv.resolve()
    VIDEO_ROOT = args.video_root.resolve()
    if not CSV_PATH.is_file():
        raise SystemExit(f"CSV 文件不存在: {CSV_PATH}")
    if not VIDEO_ROOT.is_dir():
        raise SystemExit(f"视频根目录不存在: {VIDEO_ROOT}")

    CSV_ENCODING = detect_encoding(CSV_PATH)
    rows, fields = read_rows()
    if not rows:
        raise SystemExit("CSV 中没有数据")
    if "events" not in fields:
        raise SystemExit("CSV 缺少 events 字段")

    print(f"CSV: {CSV_PATH}")
    print(f"视频目录: {VIDEO_ROOT}")
    print(f"CSV 编码: {CSV_ENCODING}")
    print(f"浏览器打开: http://127.0.0.1:{args.port}")
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
