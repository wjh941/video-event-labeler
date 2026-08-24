# Reference Manifest Format Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and maintain video event manifests that exactly follow the approved nine-column reference CSV format.

**Architecture:** Keep the single-file standard-library application. Add small schema, metadata, and behavior-class helpers beside the current manifest helpers; distinguish reference manifests from legacy event/simple manifests by their exact header. New imports and reference-manifest saves use the fixed schema, while legacy inputs retain their existing layout.

**Tech Stack:** Python 3 standard library (`csv`, `pathlib`, `unittest`), embedded browser JavaScript, no new dependencies.

## Global Constraints

- New manifests use UTF-8 with BOM and exactly this ordered header: `sample_id,video_path,lighting,lighting_evidence,behavior_class,behavior_id,security_zone_points,person_tag_list,events`.
- Event serialization remains the established multiline, integer-millisecond `ms` representation.
- New manifest rows use filename `sample_id`, absolute Windows `video_path`, inferred lighting, `人工确认`, and literal `null` zone points.
- New and reference manifests never persist `data_stratum` or `review_status`.
- Keep legacy event and simple CSVs readable and preserve their original column layout on save.
- Preserve the existing backup and atomic replacement behavior.
- The target directory is not a Git repository; do not attempt commits.

---

## File Structure

- Modify: `video_event_labeler.py`
  - Define the reference schema and label-to-class mapping.
  - Create reference-format rows, prevent duplicate imports using absolute paths, and save reference manifests without private fields.
  - Use absolute-path row identity for updates and adapt the browser review state to a non-persisted status.
- Modify: `test_video_event_labeler.py`
  - Replace generic-import assertions with reference-schema checks and add save/API/browser-contract regression tests.
- Modify: `README.md`
  - Document the reference CSV contract and retained legacy compatibility.

## Task 1: Create Reference-Schema Imports

**Files:**
- Modify: `test_video_event_labeler.py:72-161`
- Modify: `video_event_labeler.py:25-377`

**Interfaces:**
- Consumes: `infer_prelabels(relative_path: Path) -> tuple[str, list[str]]` and `events_to_csv_value(events) -> str`.
- Produces: `MANIFEST_FIELDS: list[str]`, `behavior_class_value(labels: list[str]) -> str`, `infer_lighting(relative_path: Path) -> str`, and `make_import_row(root: Path, source: Path) -> dict[str, str]` using the reference schema.

- [ ] **Step 1: Write failing import-format tests**

Replace the current relative-path import assertion with a test that creates two videos and asserts the generated CSV columns and representative rows exactly:

```python
def test_import_creates_reference_rows_with_metadata(self):
    fall = self.root / "daytime" / "fall-pos.mp4"
    multi = self.root / "night_black_white" / "stranger_enter_frame+linger_wander-pos.mp4"
    for video in (fall, multi):
        video.parent.mkdir(parents=True, exist_ok=True)
        video.touch()

    manifest, added = labeler.import_video_directory(self.root)
    rows, fields = labeler.read_csv_rows(manifest, "utf-8-sig")
    by_name = {row["sample_id"]: row for row in rows}

    self.assertEqual(fields, labeler.MANIFEST_FIELDS)
    self.assertEqual(added, 2)
    self.assertEqual(by_name[fall.name]["video_path"], str(fall.resolve()))
    self.assertEqual(by_name[fall.name]["lighting"], "白天")
    self.assertEqual(by_name[fall.name]["behavior_class"], "人员跌倒")
    self.assertEqual(by_name[multi.name]["lighting"], "红外")
    self.assertEqual(by_name[multi.name]["behavior_id"], "stranger_enter_frame,linger_wander")
    self.assertEqual(by_name[multi.name]["behavior_class"], "入侵,徘徊")
    self.assertEqual(by_name[multi.name]["security_zone_points"], "null")
```

Add a second test that calls `import_video_directory` twice and asserts the second call adds zero rows when the stored `video_path` is absolute.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest -v test_video_event_labeler.ImportTests.test_import_creates_reference_rows_with_metadata test_video_event_labeler.ImportTests.test_reimport_adds_only_new_videos
```

Expected: FAIL because the current importer uses the seven-column generic schema and relative paths.

- [ ] **Step 3: Implement the minimal reference-schema helpers and importer changes**

Replace the generic `MANIFEST_FIELDS` value and add focused helpers:

```python
MANIFEST_FIELDS = [
    "sample_id", "video_path", "lighting", "lighting_evidence",
    "behavior_class", "behavior_id", "security_zone_points",
    "person_tag_list", "events",
]

BEHAVIOR_CLASSES = {
    "person_fall": "人员跌倒", "climb_fence": "翻越围栏",
    "peep_car_window": "窥视车窗", "pickup_package": "拾取包裹",
    "linger_wander": "徘徊", "stay_long": "长时间逗留",
    "cat_enter_frame": "猫进入画面", "dog_enter_frame": "狗进入画面",
    "stranger_enter_frame": "入侵", "approach_risk_zone": "靠近风险区域",
    "normal_scene": "正常场景",
}

def behavior_class_value(labels: list[str]) -> str:
    return ",".join(BEHAVIOR_CLASSES[label] for label in labels)

def infer_lighting(relative_path: Path) -> str:
    text = "/".join(part.casefold() for part in relative_path.parts)
    if "daytime" in text:
        return "白天"
    if "night_black_white" in text:
        return "红外"
    return "黑夜" if "night" in text else ""
```

Update `infer_prelabels` so each label checks both its canonical label text and its existing aliases. For positive samples, order labels by their first match position in the normalized relative path, using the `BEHAVIOR_LABELS` order only as a tie-breaker. Keep the `neg -> normal_scene` override. Make `make_import_row` use `source.name`, `str(source.resolve())`, the helpers above, `lighting_evidence="人工确认"`, `security_zone_points="null"`, and an empty initial `person_tag_list`. Update duplicate detection to normalize both stored absolute paths and candidate source paths before comparison. Keep `events_to_csv_value` unchanged.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Step 2 command. Expected: both tests PASS.

- [ ] **Step 5: Record the non-commit checkpoint**

Run:

```powershell
Test-Path -LiteralPath '.git'
```

Expected: `False`; record that no commit is possible because this delivery directory is not a Git repository.

## Task 2: Save Reference Manifests Without Private Fields

**Files:**
- Modify: `test_video_event_labeler.py:166-262`
- Modify: `video_event_labeler.py:428-491, 591-609`

**Interfaces:**
- Consumes: `MANIFEST_FIELDS`, `behavior_class_value`, `validate_events`, `write_csv_atomic`, and the `AppState` CSV lock.
- Produces: `is_reference_manifest(fieldnames: list[str]) -> bool` and reference-aware `_update_row(state, payload) -> dict[str, object]`.

- [ ] **Step 1: Write a failing API persistence test**

Add a test that starts the existing local HTTP server over an imported manifest and saves two ordered events using the row's full filename and absolute path:

```python
def test_reference_update_keeps_exact_header_and_behavior_classes(self):
    row = self.read_event_row()
    status, body = self.post_update({
        "sample_id": row["sample_id"],
        "video_path": row["video_path"],
        "person_tag_list": "stranger",
        "events": [
            {"event_type": "stranger_enter_frame", "start_time_ms": 1710, "end_time_ms": 19000},
            {"event_type": "linger_wander", "start_time_ms": 2400, "end_time_ms": 46327},
        ],
        "review": True,
    })
    saved, fields = labeler.read_csv_rows(self.manifest, "utf-8-sig")

    self.assertEqual(status, 200)
    self.assertTrue(body["ok"])
    self.assertEqual(fields, labeler.MANIFEST_FIELDS)
    self.assertNotIn("review_status", fields)
    self.assertEqual(saved[0]["behavior_id"], "stranger_enter_frame,linger_wander")
    self.assertEqual(saved[0]["behavior_class"], "入侵,徘徊")
    self.assertIn('"start_time_ms":1710ms', saved[0]["events"])
```

Update the existing API payloads to use the new filename `sample_id` and include `video_path` where row identity is needed.

- [ ] **Step 2: Run the persistence test and verify RED**

Run:

```powershell
python -m unittest -v test_video_event_labeler.ApiTests.test_reference_update_keeps_exact_header_and_behavior_classes
```

Expected: FAIL because `_update_row` currently appends `review_status` and does not update `behavior_class`.

- [ ] **Step 3: Implement reference-aware save and row identity**

Add an exact-header predicate and use it before any schema mutation:

```python
def is_reference_manifest(fieldnames: list[str]) -> bool:
    return fieldnames == MANIFEST_FIELDS
```

For a reference manifest, find a row by an optional `video_path` payload value before falling back to `sample_id`; set the selected row's `events`, comma-joined `behavior_id`, matching `behavior_class`, and `person_tag_list`; then write with the unchanged fixed `fieldnames`. Do not call `_ensure_columns(..., "review_status")` and do not assign `row["review_status"]` in this branch. Keep the current `review_status` behavior only for legacy manifests.

Change `_rows_for_client` so a reference row receives a transient client review state derived from its parsed events rather than a CSV field. Ensure `video_url` continues to serve an absolute `video_path` safely under `video_root`.

- [ ] **Step 4: Run focused API regressions and verify GREEN**

Run:

```powershell
python -m unittest -v test_video_event_labeler.ApiTests
```

Expected: all API tests PASS, including range serving and legacy simple-CSV preservation.

- [ ] **Step 5: Record the non-commit checkpoint**

Run the Task 1 Step 5 command and record the expected `False` result.

## Task 3: Align Browser Review State and Documentation

**Files:**
- Modify: `test_video_event_labeler.py:265-290`
- Modify: `video_event_labeler.py:500-568`
- Modify: `README.md`

**Interfaces:**
- Consumes: API rows with `events`, `behavior_id`, `behavior_class`, `lighting`, `video_path`, and transient review state.
- Produces: browser update payloads containing `sample_id` and `video_path`, and filters based on event completeness rather than persisted reference-manifest status.

- [ ] **Step 1: Write failing browser-contract tests**

Add tests that require the browser source to send a row's absolute path and render reference metadata without `data_stratum`:

```python
def test_reference_browser_payload_uses_video_path_and_event_state(self):
    self.assertIn("video_path:row.video_path", labeler.HTML)
    self.assertIn('value="ready"', labeler.HTML)
    self.assertNotIn("row.data_stratum", labeler.HTML)
```

Add an assertion that the manifest documentation names all nine reference columns.

- [ ] **Step 2: Run browser/documentation tests and verify RED**

Run:

```powershell
python -m unittest -v test_video_event_labeler.HtmlContractTests
```

Expected: FAIL because the browser currently renders `data_stratum` and filters by persisted `pending` / `reviewed` values.

- [ ] **Step 3: Implement the smallest UI and README changes**

In the embedded JavaScript:

```javascript
function isVisible(row){
  const filter=$("filter").value;
  return filter==="all" || eventState(row)===filter;
}
```

Replace the `pending` and `reviewed` filter options with `needs-time` and `ready`. Render badges from `eventState(row)`, show `row.lighting` and `row.behavior_class` in metadata, include `video_path:row.video_path` in `buildPayload`, and stop assigning server-returned `review_status` into reference rows after save. Keep the existing add/remove behavior controls and fixed three-digit millisecond formatter.

Update `README.md` to state the exact nine generated columns, UTF-8 BOM encoding, absolute video paths, reference-compatible `events`, and legacy-read compatibility.

- [ ] **Step 4: Run browser/documentation tests and verify GREEN**

Run the Step 2 command. Expected: all `HtmlContractTests` PASS.

- [ ] **Step 5: Record the non-commit checkpoint**

Run the Task 1 Step 5 command and record the expected `False` result.

## Task 4: Verify the Delivered Format Against the Reference Contract

**Files:**
- Verify: `video_event_labeler.py`
- Verify: `test_video_event_labeler.py`
- Verify: `README.md`

**Interfaces:**
- Consumes: completed import, save, compatibility, and browser-contract tests.
- Produces: fresh verification evidence for the release and later public Gist creation.

- [ ] **Step 1: Run the full test suite**

Run:

```powershell
python -m unittest -v
```

Expected: all tests PASS with no failures or errors.

- [ ] **Step 2: Compile the delivery script**

Run:

```powershell
python -m py_compile .\video_event_labeler.py
```

Expected: exit code `0` with no output.

- [ ] **Step 3: Inspect a generated manifest against the contract**

Run a temporary-directory import test or the corresponding focused test, then verify through `read_csv_rows` that the header equals `labeler.MANIFEST_FIELDS`, the output begins with UTF-8 BOM bytes, each `video_path` is absolute, and no private field is present. Compare the nine headers and the `events` field convention against `D:\0818 03\0818_cam03_clips_sample_sorted_by_start_event_labeler_manifest.csv`.

- [ ] **Step 4: Record the non-commit checkpoint**

Run the Task 1 Step 5 command and record the expected `False` result. Do not create or publish the Gist in this task; public release remains a separate action after this format change is verified and GitHub CLI availability is rechecked.
