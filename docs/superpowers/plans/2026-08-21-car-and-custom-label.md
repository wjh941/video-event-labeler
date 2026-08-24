# Car and Custom Label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the fixed `car_enter_frame` label and reviewer-created custom event labels without changing the approved CSV schema or folder-derived `behavior_class` rule.

**Architecture:** Fixed labels remain in the module-level tuple, the canonical source for import inference, API validation, and the picker. A small server-side validator permits safe custom event names; the browser adds them as the same event cards used for fixed labels, so existing CSV `behavior_id` and `events` columns require no schema changes.

**Tech Stack:** Python 3 standard library (`csv`, `http.server`, `unittest`), vanilla HTML/CSS/JavaScript, UTF-8 BOM CSV.

## Global Constraints

- Preserve the exact reference CSV headers: `sample_id,video_path,lighting,lighting_evidence,behavior_class,behavior_id,security_zone_points,person_tag_list,events`.
- Keep event times as integer milliseconds and display input as `H:MM:SS.mmm`.
- Keep `behavior_class` as the first directory below the imported video root.
- A path marked `neg` imports only `normal_scene`, regardless of other filename labels.
- Custom labels are 1 to 64 trimmed characters and cannot contain `,`, carriage return, or newline.
- Do not introduce dependencies or alter existing valid CSV rows.

---

## File Structure

- `video_event_labeler.py`: behavior constants, custom-label validation, behavior controls, and event-card JavaScript.
- `test_video_event_labeler.py`: data-layer and HTML-contract tests.
- `README.md`: supported-label and custom-label user guidance.
- `C:\Users\16102\video-event-labeler-release\video_event_labeler.py`: release copy of the finished app.
- `C:\Users\16102\video-event-labeler-release\README.md`: release copy of the documentation.

### Task 1: Define and Persist Event Labels

**Files:**

- Modify: `test_video_event_labeler.py: LabelRuleTests and ImportTests`
- Modify: `video_event_labeler.py: BEHAVIOR_LABELS, BEHAVIOR_CLASSES, validate_events, _update_row`

**Interfaces:**

- Consumes: `infer_prelabels(relative_path: Path) -> tuple[str, list[str]]` and `validate_events(events, permitted_labels, review) -> list[dict[str, object]]`.
- Produces: `is_valid_custom_label(label: str) -> bool`, which `validate_events` uses for new non-fixed labels.

- [ ] **Step 1: Write failing fixed-label and custom-label tests**

```python
def test_car_enter_frame_is_inferred_from_its_standard_filename(self):
    self.assertEqual(
        labeler.infer_prelabels(Path("车辆/pos/cam04/car_enter_frame-pos-001.mp4")),
        ("pos", ["car_enter_frame"]),
    )

def test_custom_label_is_saved_with_a_millisecond_interval(self):
    events = labeler.validate_events(
        [{"event_type": "车辆驶入画面", "start_time_ms": 1001, "end_time_ms": 2002}],
        set(labeler.BEHAVIOR_LABELS),
        review=True,
    )
    self.assertEqual(events[0]["event_type"], "车辆驶入画面")
```

Add invalid values `""`, `"  "`, `"a,b"`, and `"a\nb"`; each must raise `ValueError`. Add a data-layer update test that saves `车辆驶入画面` and verifies `behavior_id`, parsed `events`, and the folder-derived `behavior_class`.

- [ ] **Step 2: Run the targeted tests and verify they fail**

```powershell
python -B -m unittest -v test_video_event_labeler.LabelRuleTests test_video_event_labeler.ImportTests
```

Expected: the car test has no inferred label and the valid custom-label test reports `unsupported behavior label`.

- [ ] **Step 3: Implement the minimal data-layer change**

```python
BEHAVIOR_LABELS = (
    # existing labels
    "cat_enter_frame",
    "dog_enter_frame",
    "car_enter_frame",
    # remaining labels
)

def is_valid_custom_label(label: str) -> bool:
    return 1 <= len(label) <= 64 and "," not in label and "\r" not in label and "\n" not in label
```

Add `"car_enter_frame": "车辆进入画面"` to `BEHAVIOR_CLASSES`. In `validate_events`, trim first; accept a label when it is already permitted or passes `is_valid_custom_label`. Preserve duplicate-label, time-range, review, and `normal_scene` rules. The canonical label is already searched by `infer_prelabels`; do not add an unsafe `car_in` alias.

- [ ] **Step 4: Run the data-layer tests and verify they pass**

```powershell
python -B -m unittest -v test_video_event_labeler.LabelRuleTests test_video_event_labeler.ImportTests
```

Expected: all tests pass, including car inference, negative precedence, custom-label validation, persistence, and folder-class preservation.

### Task 2: Add Custom Event Controls to the Reviewer UI

**Files:**

- Modify: `test_video_event_labeler.py: HtmlContractTests`
- Modify: `video_event_labeler.py: HTML behavior section and final JavaScript override block`

**Interfaces:**

- Consumes: `renderEventCard(event)`, `currentEvents()`, `addEventSegment()`, and the existing `POST /api/update` event payload.
- Produces: `addCustomEventSegment()`, which validates `#custom-behavior` then delegates to `addEventSegment(value)`.

- [ ] **Step 1: Write failing HTML contract tests**

```python
def test_interface_has_custom_event_controls(self):
    self.assertIn('id="custom-behavior"', labeler.HTML)
    self.assertIn('id="add-custom-event"', labeler.HTML)
    self.assertIn('function addCustomEventSegment()', labeler.HTML)
```

The existing loop in `test_interface_includes_each_fixed_behavior_value` also exposes the missing `car_enter_frame` option.

- [ ] **Step 2: Run the HTML contract tests and verify they fail**

```powershell
python -B -m unittest -v test_video_event_labeler.HtmlContractTests
```

Expected: failures identify the missing car option and custom input/button/function.

- [ ] **Step 3: Implement one shared client-side segment flow**

```javascript
function addCustomEventSegment(){
  const input=$("custom-behavior"),value=input.value.trim();
  if(!value||value.length>64||/[\,\r\n]/.test(value)){
    setStatus("自定义标签需为 1-64 个字符，且不能包含逗号或换行",true);return;
  }
  addEventSegment(value);input.value="";
}
```

Add `car_enter_frame` to the fixed `<select>`. Place `<input id="custom-behavior" maxlength="64">` and `<button id="add-custom-event">` below it with a compact, responsive two-column layout. Refactor `addEventSegment` to accept an optional label so fixed and custom controls share `normal_scene` exclusivity and card construction. Bind the button and the input's Enter key to `addCustomEventSegment()`.

- [ ] **Step 4: Run HTML, Python, and browser-script checks**

```powershell
python -B -m unittest -v test_video_event_labeler.HtmlContractTests
python -B -c "compile(open('video_event_labeler.py', encoding='utf-8').read(), 'video_event_labeler.py', 'exec'); print('syntax OK')"
node --input-type=module -e "import fs from 'node:fs'; const html=fs.readFileSync('video_event_labeler.py','utf8'); for(const script of [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match=>match[1])) new Function(script); console.log('javascript OK')"
```

Expected: HTML tests pass, Python reports `syntax OK`, and Node reports `javascript OK`.

### Task 3: Document, Package, and Verify the Release

**Files:**

- Modify: `README.md: supported behavior labels and review workflow`
- Modify: `C:\Users\16102\video-event-labeler-release\README.md`
- Modify: `C:\Users\16102\video-event-labeler-release\video_event_labeler.py`

**Interfaces:**

- Consumes: the tested `D:\default file\视频标注工具\video_event_labeler.py` and `README.md`.
- Produces: a release repository containing matching source and documentation, ready for `git push origin main`.

- [ ] **Step 1: Update user documentation**

Add `car_enter_frame` to the supported-label list. Explain that `car_enter_frame` is recognized from its standard filename label, while custom labels are manually entered per video, stored in the existing CSV fields, and cannot include commas or line breaks. Retain the current `neg` and folder-name `behavior_class` documentation.

- [ ] **Step 2: Copy the completed source and README to the release repository**

```powershell
Copy-Item -LiteralPath 'D:\default file\视频标注工具\video_event_labeler.py' -Destination 'C:\Users\16102\video-event-labeler-release\video_event_labeler.py' -Force
Copy-Item -LiteralPath 'D:\default file\视频标注工具\README.md' -Destination 'C:\Users\16102\video-event-labeler-release\README.md' -Force
```

- [ ] **Step 3: Run final targeted verification**

```powershell
Set-Location 'D:\default file\视频标注工具'
python -B -m unittest -v test_video_event_labeler.LabelRuleTests test_video_event_labeler.ImportTests test_video_event_labeler.CliTests test_video_event_labeler.HtmlContractTests
python -B -c "compile(open('video_event_labeler.py', encoding='utf-8').read(), 'video_event_labeler.py', 'exec'); print('syntax OK')"
Set-Location 'C:\Users\16102\video-event-labeler-release'
git diff --check
git status --short
```

Expected: targeted tests and syntax pass; `git diff --check` is clean; status lists only the intended source and README changes before commit.

- [ ] **Step 4: Commit and publish the release**

```powershell
Set-Location 'C:\Users\16102\video-event-labeler-release'
git add video_event_labeler.py README.md
git commit -m "feat: add car and custom event labels"
git push origin main
```

Expected: a committed local release. If this environment cannot reach GitHub, leave the commit ready and provide the user with `git push origin main`.

## Self-Review

- Spec coverage: Task 1 covers the fixed label, automatic import, negative precedence, server validation, CSV persistence, millisecond review, and folder class. Task 2 covers the reviewer UI, normal-scene behavior, and responsive controls. Task 3 covers documentation, release synchronization, verification, and publication.
- Placeholder scan: there are no TODOs, vague validation steps, or undefined interface names.
- Type consistency: event payloads remain `list[dict[str, object]]` with `event_type: str` and integer-or-null millisecond times; JavaScript creates that existing object shape and CSV headers remain unchanged.

