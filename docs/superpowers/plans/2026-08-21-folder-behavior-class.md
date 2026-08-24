# Folder-Based Behavior Class Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write `behavior_class` from the first directory below the imported video root, retain it when labels are edited, and improve in-browser review navigation and clip checking.

**Architecture:** Add one path helper in `video_event_labeler.py`. New imports, re-imported rows, and reference-row saves use this helper, while event labels continue to populate only `behavior_id` and `events`. Extend the existing no-dependency browser script with visible filtered-row navigation, a derived progress summary, and one active event-clip loop. Existing rows whose video path is no longer inside the imported root are left unchanged.

**Tech Stack:** Python 3 standard library, `unittest`, CSV manifests encoded as UTF-8 with BOM.

## Global Constraints

- The class for `D:\dapeng-test\跌倒\pos\clip.mp4` imported from `D:\dapeng-test` is exactly `跌倒`.
- A video directly inside the imported root uses the root directory name.
- Re-importing may change only `behavior_class` for a matched video; it must preserve manual event and person-tag fields.
- Do not add third-party dependencies or alter the reference manifest header.
- Keep the public release directory limited to `README.md` and `video_event_labeler.py`.

---

### Task 1: Derive and Refresh Folder Classes

**Files:**
- Modify: `video_event_labeler.py:296-410`
- Modify: `video_event_labeler.py:481-541`
- Modify: `test_video_event_labeler.py:117-223`
- Modify: `test_video_event_labeler.py:226-365`

**Interfaces:**
- Produces: `folder_behavior_class(root: Path, source: Path) -> str | None`.
- Consumes: imported `root`, discovered video `source`, and an existing CSV row's `video_path`.
- Produces: folder-derived `behavior_class` for new rows, matched re-imported rows, and reference-manifest save responses.

- [ ] **Step 1: Write the failing tests**

Replace label-derived class assertions with folder-derived expectations and add direct data-layer coverage:

```python
def test_reimport_refreshes_folder_class_and_keeps_annotations(self):
    video = self.root / "跌倒" / "pos" / "fall-pos.mp4"
    video.parent.mkdir(parents=True)
    video.touch()
    manifest, _ = labeler.import_video_directory(self.root)
    rows, fields = labeler.read_csv_rows(manifest, "utf-8-sig")
    rows[0]["behavior_class"] = "人员跌倒"
    rows[0]["person_tag_list"] = "stranger"
    rows[0]["events"] = '[{"event_type":"person_fall","start_time_ms":1000ms,"end_time_ms":2000ms}]'
    labeler.write_csv_atomic(manifest, rows, fields, "utf-8-sig", {})

    labeler.import_video_directory(self.root)
    refreshed, _ = labeler.read_csv_rows(manifest, "utf-8-sig")

    self.assertEqual(refreshed[0]["behavior_class"], "跌倒")
    self.assertEqual(refreshed[0]["person_tag_list"], "stranger")
    self.assertIn('"start_time_ms":1000ms', refreshed[0]["events"])
```

Add a direct `_update_row` test using a video at `self.root / "跌倒" / "pos" / "fall-pos.mp4"`; update its event to `stranger_enter_frame` with `1000ms` and `2000ms`, then assert `behavior_id == "stranger_enter_frame"` and `behavior_class == "跌倒"`. Update existing API class assertions for that same fixture from label names to `跌倒`.

- [ ] **Step 2: Run the new non-HTTP tests and verify they fail**

Run:

```powershell
python -B -m unittest -v test_video_event_labeler.ImportTests
```

Expected: FAIL because imported and saved rows still use `behavior_class_value(labels)`.

- [ ] **Step 3: Implement the minimal shared rule**

Add this helper immediately before `make_import_row`:

```python
def folder_behavior_class(root: Path, source: Path) -> str | None:
    root = root.resolve()
    source = source if source.is_absolute() else root / source
    try:
        relative = source.resolve().relative_to(root)
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) > 1 else root.name
```

Use `folder_behavior_class(root, source) or root.name` in `make_import_row`. In `import_video_directory`, build a normalized-path-to-source lookup from `sources`; for each existing row with a matching source and a `behavior_class` column, set the class from this helper. Write the CSV when rows were added, classes changed, or the manifest is new.

In `_update_row`, after reference-event labels are validated, derive the class from `state.video_root` and the saved row's `video_path`. Replace `behavior_class_value(labels)` only when the helper returns a value. Leave rows outside the configured root unchanged.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```powershell
python -B -m unittest -v test_video_event_labeler.LabelRuleTests test_video_event_labeler.ImportTests test_video_event_labeler.CliTests test_video_event_labeler.HtmlContractTests
```

Expected: 23 tests pass. The HTTP API test class is intentionally excluded because the current command environment cannot complete loopback requests even to a standard-library HTTP server.

- [ ] **Step 5: Run syntax verification**

Run:

```powershell
python -B -c "compile(open('video_event_labeler.py', encoding='utf-8').read(), 'video_event_labeler.py', 'exec'); print('syntax OK')"
```

Expected: `syntax OK`.

### Task 2: Add Review Navigation, Progress, and Event-Clip Looping

**Files:**
- Modify: `video_event_labeler.py:559-627`
- Modify: `test_video_event_labeler.py:373-403`

**Interfaces:**
- Consumes: the current row index, the current filter, and event-card start/end inputs.
- Produces: `moveVisibleRow(delta: number)`, `stopLoop()`, a per-card loop action, and an independent New Event Segment action.
- Produces: an on-screen summary with current visible position, visible total, ready count, and needs-time count.

- [ ] **Step 1: Extend the failing HTML contract tests**

In `test_interface_exposes_import_draft_review_and_behavior_controls`, replace the old `id="add-behavior"` requirement with `id="add-event-segment"`, then add the following required identifiers:

```python
'id="previous-row"',
'id="next-row"',
'id="progress"',
'id="add-event-segment"',
```

Extend `test_reference_browser_payload_uses_video_path_and_event_state` to require the loop and navigation implementation markers:

```python
"function moveVisibleRow(delta)",
"function stopLoop()",
'video.addEventListener("timeupdate"',
```

- [ ] **Step 2: Run the HTML contract tests and verify they fail**

Run:

```powershell
python -B -m unittest -v test_video_event_labeler.HtmlContractTests
```

Expected: FAIL because the previous/next controls, progress target, and loop functions do not exist yet.

- [ ] **Step 3: Implement the minimal browser behavior**

Keep the video viewer as its own grid cell and add no floating controls or absolute-positioned layers over it. Add an icon-only navigation group in the side-panel action row, using HTML entities and Chinese titles:

```html
<button id="previous-row" class="icon" title="上一条" aria-label="上一条">&larr;</button>
<button id="next-row" class="icon" title="下一条" aria-label="下一条">&rarr;</button>
```

Add `<span id="progress" class="progress"></span>` beside the filter control. Extract the filtered row calculation used by `renderList` into `visibleRows()`. On every list render, set the progress text to `第 {position} / {visible.length} 条 | 可审核 {ready} | 需补时间 {needsTime}`; use `第 0 / 0 条` when no row is visible. Disable previous/next at their boundaries.

Add `moveVisibleRow(delta)` that finds the current row in `visibleRows()` and calls `openRow()` for the neighbor. Bind the two buttons and the existing arrow-key handlers to this function so all visible navigation saves a dirty draft through `openRow()` first.

Add `let loopRange = null` plus:

```javascript
function stopLoop(){
  loopRange=null;
  eventList.querySelectorAll(".loop.active").forEach(button=>button.classList.remove("active"));
}

video.addEventListener("timeupdate",()=>{
  if(loopRange&&video.currentTime>=loopRange.end){
    video.currentTime=loopRange.start;
    video.play().catch(()=>{});
  }
});
```

For each non-`normal_scene` event card and each simple-manifest time card, append a `循环片段` button. It derives its start/end from that card's inputs, stays disabled unless both parse and `end > start`, and on click stops any prior loop, seeks to the start, marks itself active, and plays. Make every time input/capture/clear action call `stopLoop()` before marking the draft dirty. Make `openRow()` call `stopLoop()` before changing `video.src`. Render a normal-scene card without time inputs or loop control, and make `currentEvents()` return null times for it.

Rename the add control to `id="add-event-segment"` with the visible text `新建事件片段`. Its handler reads the selected fixed behavior label and always calls `renderEventCard({event_type:value,start_time_ms:null,end_time_ms:null})`; remove the duplicate-label rejection so the same behavior can have multiple independently timed segments. Retain the existing normal-scene/positive mutual exclusion confirmation, because a normal scene cannot coexist with a positive segment.

- [ ] **Step 4: Run focused UI and data tests**

Run:

```powershell
python -B -m unittest -v test_video_event_labeler.LabelRuleTests test_video_event_labeler.ImportTests test_video_event_labeler.CliTests test_video_event_labeler.HtmlContractTests
```

Expected: 23 tests pass, including the folder-class data tests and browser contract checks.

- [ ] **Step 5: Manually verify the running page**

Start the script with an imported video root, then verify: the previous button is disabled on the first visible row; the progress count changes after filtering; a valid event's loop control repeats its precise time interval; and changing either time stops looping.

### Task 3: Update the Public Release Copy

**Files:**
- Modify: `C:\Users\16102\video-event-labeler-release\video_event_labeler.py`
- Verify: `C:\Users\16102\video-event-labeler-release\README.md`

**Interfaces:**
- Consumes: the verified final source at `D:\default file\视频标注工具\video_event_labeler.py`.
- Produces: one Git commit on `main` that contains the updated script and no non-delivery files.

- [ ] **Step 1: Copy only the verified script**

Run:

```powershell
Copy-Item -LiteralPath 'D:\default file\视频标注工具\video_event_labeler.py' -Destination 'C:\Users\16102\video-event-labeler-release\video_event_labeler.py' -Force
```

- [ ] **Step 2: Verify release contents and source identity**

Run:

```powershell
Get-FileHash -Algorithm SHA256 'D:\default file\视频标注工具\video_event_labeler.py','C:\Users\16102\video-event-labeler-release\video_event_labeler.py'
git -C 'C:\Users\16102\video-event-labeler-release' status --short
git -C 'C:\Users\16102\video-event-labeler-release' ls-files
```

Expected: matching source hashes; only `README.md` and `video_event_labeler.py` are tracked.

- [ ] **Step 3: Commit the release update**

Run:

```powershell
git -C 'C:\Users\16102\video-event-labeler-release' add video_event_labeler.py
git -C 'C:\Users\16102\video-event-labeler-release' commit -m "Improve review controls and folder classes"
```

- [ ] **Step 4: Verify the release commit**

Run:

```powershell
git -C 'C:\Users\16102\video-event-labeler-release' status --short
git -C 'C:\Users\16102\video-event-labeler-release' log -1 --oneline
git -C 'C:\Users\16102\video-event-labeler-release' show --check --format= HEAD
```

Expected: clean working tree, one `Improve review controls and folder classes` commit, and no whitespace errors.
