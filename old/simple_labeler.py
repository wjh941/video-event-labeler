#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""简单视频标注工具。

启动后输入视频根目录和 CSV 路径，浏览器打开 http://127.0.0.1:8765。
"""

import csv
import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlparse

PORT = 8765
VIDEO_ROOT = r"D:\yolo视频评测集_720p\窥视车窗"
CSV_PATH = r"D:\yolo视频评测集_720p\窥视车窗\yolo_behavior_eval_manifest.csv"
CSV_ENCODING = "utf-8-sig"
CSV_LOCK = threading.Lock()
FIELDS = ("person_tag_list", "start_time", "end_time")


def clean_path(value):
    return value.strip().strip('"').strip("'")


def detect_encoding(path):
    raw = open(path, "rb").read()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            pass
    return "utf-8-sig"


def read_rows():
    with CSV_LOCK, open(CSV_PATH, "r", encoding=CSV_ENCODING, newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        for field in FIELDS:
            if not row.get(field, "").strip():
                row[field] = "null"
    return rows


def write_rows(rows):
    fieldnames = list(rows[0].keys()) if rows else []
    for field in FIELDS:
        if field not in fieldnames:
            fieldnames.append(field)
            for row in rows:
                row[field] = "null"
    with CSV_LOCK, open(CSV_PATH, "w", encoding=CSV_ENCODING, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def relative_video_path(video_path):
    video_path = video_path.replace("\\", os.sep)
    root = os.path.normcase(os.path.abspath(VIDEO_ROOT))
    absolute = os.path.normcase(os.path.abspath(video_path))
    try:
        relative = os.path.relpath(absolute, root)
        if relative != ".." and not relative.startswith(".." + os.sep):
            return relative
    except ValueError:
        pass
    return video_path.lstrip("\\/")


def safe_video_path(relative):
    root = os.path.normcase(os.path.abspath(VIDEO_ROOT))
    full = os.path.normcase(os.path.abspath(os.path.join(VIDEO_ROOT, relative)))
    if full == root or not full.startswith(root + os.sep):
        return None
    return full


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>视频标注工具</title>
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{display:flex;flex-direction:column;background:#1b1b1b;color:#ddd;font-family:Arial,"Microsoft YaHei",sans-serif}
header{height:42px;display:flex;align-items:center;justify-content:space-between;padding:0 12px;background:#292929;border-bottom:1px solid #444}
h1{font-size:15px;margin:0}.toolbar{display:flex;align-items:center;gap:8px;font-size:12px}
button,select,input{font:inherit}.toolbar button,.tag button,.nav button,.capture{background:#3a3a3a;color:#ddd;border:1px solid #555;border-radius:4px;padding:5px 9px;cursor:pointer}
.toolbar button.active{background:#267548;border-color:#45a66a}.toolbar button:hover,.capture:hover,.nav button:hover{background:#505050}
.layout{display:flex;min-height:0;flex:1}.viewer{display:flex;flex:1;min-width:0;flex-direction:column;background:#000}
.video-box{display:flex;flex:1;min-height:0;align-items:center;justify-content:center;padding:8px}
video{display:block;max-width:100%;max-height:100%;object-fit:contain;background:#000}
.video-meta{padding:6px 10px;background:#222;border-top:1px solid #444;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.side{width:330px;display:flex;flex-direction:column;min-height:0;background:#252525;border-left:1px solid #444}
.section{padding:10px;border-bottom:1px solid #3c3c3c}.title{font-size:12px;color:#999;margin-bottom:7px}
.tag{display:flex;gap:6px}.tag button{flex:1;padding:9px 4px}.tag button.selected{background:#2f78a8;border-color:#62b5ee}.tag button.null.selected{background:#555;border-color:#aaa}
.time{display:grid;grid-template-columns:42px 1fr auto;gap:6px;align-items:center;margin-bottom:6px;font-size:12px;color:#aaa}
.time input{width:100%;padding:6px;background:#363636;border:1px solid #555;color:#eee;border-radius:4px;text-align:center;font-family:monospace}
.nav{display:flex;gap:6px}.nav button{flex:1}.status{font-size:12px;padding:6px 10px;color:#7ad68b;background:#1b2a1e}.status.error{color:#ff8b8b;background:#321d1d}
.filters{display:flex;gap:5px;padding:7px;border-bottom:1px solid #3c3c3c}.filters select{flex:1;min-width:0;background:#363636;color:#ddd;border:1px solid #555;border-radius:4px;padding:5px}
.list{overflow:auto;flex:1;padding:4px}.item{display:flex;align-items:center;gap:5px;padding:6px;border-radius:4px;cursor:pointer;font-size:12px}.item:hover{background:#383838}.item.active{background:#30465a}
.item .num{width:25px;text-align:right;color:#777}.item .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}.badge{font-size:10px;padding:2px 4px;border-radius:3px;background:#444;color:#aaa;white-space:nowrap}.badge.tagged{background:#398c55;color:white}.badge.timed{background:#9a7a25;color:white}
@media(max-width:800px){.side{width:290px}.video-meta{font-size:10px}}
</style>
</head>
<body>
<header>
  <h1>视频标注工具</h1>
  <div class="toolbar">
    <span>倍速</span>
    <button data-speed="0.5">0.5x</button>
    <button data-speed="1" class="active">1x</button>
    <button data-speed="2">2x</button>
    <span id="counter">0 / 0</span>
  </div>
</header>
<main class="layout">
  <section class="viewer">
    <div class="video-box"><video id="video" controls preload="metadata"></video></div>
    <div class="video-meta" id="meta">加载中...</div>
  </section>
  <aside class="side">
    <section class="section">
      <div class="title">人员标签</div>
      <div class="tag">
        <button data-tag="acquaintance">熟人</button>
        <button data-tag="stranger">陌生人</button>
        <button data-tag="null" class="null">null</button>
      </div>
    </section>
    <section class="section">
      <div class="title">行为时间段</div>
      <div class="time"><span>开始</span><input id="start" placeholder="null"><button class="capture" id="capture-start">截取</button></div>
      <div class="time"><span>结束</span><input id="end" placeholder="null"><button class="capture" id="capture-end">截取</button></div>
      <button class="capture" id="clear-time" style="width:100%">清除为 null</button>
    </section>
    <section class="section">
      <div class="nav">
        <button id="previous">上一条</button>
        <button id="next">下一条</button>
      </div>
    </section>
    <div class="status" id="status">就绪</div>
    <div class="filters">
      <select id="status-filter">
        <option value="">全部</option>
        <option value="untagged">未标人员</option>
        <option value="tagged">已标人员</option>
        <option value="timed">已标时间</option>
        <option value="complete">全部完成</option>
      </select>
    </div>
    <div class="list" id="list"></div>
  </aside>
</main>
<script>
const video=document.getElementById("video");
const statusBox=document.getElementById("status");
let rows=[],current=-1,speed=1,dirty=false;
const $=id=>document.getElementById(id);
function status(text,error=false){statusBox.textContent=text;statusBox.className=error?"status error":"status"}
function isNull(value){return !value||value==="null"}
function formatTime(seconds){const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=Math.floor(seconds%60);return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`}
function tagValue(){return document.querySelector(".tag button.selected")?.dataset.tag||rows[current]?.person_tag_list||"null"}
function refreshTag(tag){document.querySelectorAll(".tag button").forEach(b=>b.classList.toggle("selected",b.dataset.tag===tag))}
function badge(row){
  let html="";
  if(!isNull(row.person_tag_list))html+=`<span class="badge tagged">${row.person_tag_list==="acquaintance"?"熟人":"陌生人"}</span>`;
  if(!isNull(row.start_time)||!isNull(row.end_time))html+=`<span class="badge timed">${isNull(row.start_time)?"__":row.start_time}-${isNull(row.end_time)?"__":row.end_time}</span>`;
  return html||`<span class="badge">未标</span>`;
}
function renderList(){
  const filter=$("status-filter").value;
  const list=$("list");list.innerHTML="";
  rows.forEach((row,index)=>{
    const tagged=!isNull(row.person_tag_list),timed=!isNull(row.start_time)||!isNull(row.end_time),complete=tagged&&!isNull(row.start_time)&&!isNull(row.end_time);
    if(filter==="untagged"&&tagged)return;
    if(filter==="tagged"&&!tagged)return;
    if(filter==="timed"&&!timed)return;
    if(filter==="complete"&&!complete)return;
    const item=document.createElement("div");item.className="item"+(index===current?" active":"");
    item.innerHTML=`<span class="num">${index+1}</span><span class="name" title="${row.sample_id}">${row.sample_id}</span>${badge(row)}`;
    item.onclick=()=>openRow(index);list.appendChild(item);
  });
  $("counter").textContent=current<0?`${rows.length} 条`:`${current+1} / ${rows.length}`;
}
async function save(){
  if(current<0||!dirty)return true;
  const row=rows[current];
  const body={sample_id:row.sample_id,person_tag_list:tagValue(),start_time:$("start").value.trim()||"null",end_time:$("end").value.trim()||"null"};
  status("保存中...");
  try{
    const response=await fetch("/api/update",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const result=await response.json();
    if(!result.ok)throw new Error("保存失败");
    Object.assign(row,body);dirty=false;status("已保存");renderList();return true;
  }catch(error){status(error.message||"保存失败",true);return false}
}
async function openRow(index){
  if(index<0||index>=rows.length)return;
  if(!(await save()))return;
  current=index;const row=rows[index];
  video.src=row.video_url;video.playbackRate=speed;
  $("meta").textContent=`${row.sample_id} | ${row.data_stratum||""} | ${row.lighting||""} | ${row.data_source||""}`;
  refreshTag(row.person_tag_list);
  $("start").value=isNull(row.start_time)?"":row.start_time;
  $("end").value=isNull(row.end_time)?"":row.end_time;
  $("previous").disabled=index===0;$("next").disabled=index===rows.length-1;
  dirty=false;renderList();status("就绪");
}
async function load(){
  const response=await fetch("/api/videos");rows=await response.json();renderList();
  if(rows.length)openRow(0);
}
document.querySelectorAll(".toolbar button").forEach(button=>button.onclick=()=>{document.querySelectorAll(".toolbar button").forEach(b=>b.classList.remove("active"));button.classList.add("active");speed=Number(button.dataset.speed);video.playbackRate=speed});
document.querySelectorAll(".tag button").forEach(button=>button.onclick=async()=>{refreshTag(button.dataset.tag);dirty=true;await save()});
document.getElementById("capture-start").onclick=async()=>{$("start").value=formatTime(video.currentTime);dirty=true;await save()};
document.getElementById("capture-end").onclick=async()=>{$("end").value=formatTime(video.currentTime);dirty=true;await save()};
document.getElementById("clear-time").onclick=async()=>{$("start").value="";$("end").value="";dirty=true;await save()};
["start","end"].forEach(id=>$(id).oninput=()=>dirty=true);
$("previous").onclick=()=>openRow(current-1);$("next").onclick=()=>openRow(current+1);
$("status-filter").onchange=renderList;
document.onkeydown=e=>{if(e.target.tagName==="INPUT")return;if(e.key==="ArrowLeft")openRow(current-1);if(e.key==="ArrowRight")openRow(current+1);if(e.key===" ") {e.preventDefault();video.paused?video.play():video.pause()}};
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
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            self.send_bytes(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
            return
        if path == "/api/videos":
            rows = read_rows()
            for row in rows:
                row["video_url"] = "/video/" + relative_video_path(row.get("video_path", "")).replace(os.sep, "/")
            self.send_bytes(200, "application/json; charset=utf-8", json.dumps(rows, ensure_ascii=False).encode("utf-8"))
            return
        if path.startswith("/video/"):
            relative = path[len("/video/"):].replace("/", os.sep)
            full = safe_video_path(relative)
            if not full or not os.path.isfile(full):
                self.send_error(404)
                return
            self.send_video(full)
            return
        self.send_error(404)

    def send_video(self, path):
        size = os.path.getsize(path)
        start = 0
        end = size - 1
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            value = range_header[6:].split("-", 1)
            start = int(value[0] or 0)
            end = int(value[1]) if len(value) > 1 and value[1] else size - 1
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as file:
            file.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path != "/api/update":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        rows = read_rows()
        found = False
        for row in rows:
            if row.get("sample_id") == data.get("sample_id"):
                for field in FIELDS:
                    if field in data:
                        row[field] = data[field] or "null"
                found = True
                break
        if not found:
            self.send_bytes(404, "application/json", b'{"ok":false}')
            return
        write_rows(rows)
        self.send_bytes(200, "application/json", b'{"ok":true}')

    def log_message(self, *_):
        return


def main():
    global CSV_ENCODING
    print("=====简单视频标注工具=====")
    if not os.path.isdir(VIDEO_ROOT):
        raise SystemExit(f"视频文件夹不存在: {VIDEO_ROOT}")
    if not os.path.isfile(CSV_PATH):
        raise SystemExit(f"CSV 文件不存在: {CSV_PATH}")

    CSV_ENCODING = detect_encoding(CSV_PATH)
    rows = read_rows()
    if not rows:
        raise SystemExit("CSV 没有数据")
    write_rows(rows)

    print(f"CSV 编码: {CSV_ENCODING}")
    print(f"视频目录: {VIDEO_ROOT}")
    print(f"CSV 文件: {CSV_PATH}")
    print(f"浏览器打开: http://127.0.0.1:{PORT}")
    print("快捷键：←上一条，→下一条，空格播放暂停")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
