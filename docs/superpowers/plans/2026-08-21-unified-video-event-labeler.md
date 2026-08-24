# Unified Video Event Labeler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one local Python video event labeler that imports video folders, prelabels known behavior classes, supports human audit, and continues to edit both legacy CSV formats.

**Architecture:** A single standard-library Python script owns CSV schema adaptation, deterministic import/prelabel logic, a local HTTP API, safe video serving, and an embedded browser interface. Pure CSV and label functions are tested first; the HTTP layer then composes them, and the interface consumes only JSON APIs.

**Tech Stack:** Python 3 standard library (`argparse`, `csv`, `dataclasses`, `http.server`, `json`, `pathlib`, `tempfile`, `threading`, optional `tkinter`), browser-native HTML/CSS/JavaScript, `unittest`.

## Global Constraints

- Deliver all new files under `D:\default file\视频标注工具\`; the main program is `video_event_labeler.py`.
- Use no third-party dependencies and no build process.
- New behavior cards may only use these labels: `person_fall`, `climb_fence`, `peep_car_window`, `pickup_package`, `linger_wander`, `stay_long`, `cat_enter_frame`, `dog_enter_frame`, `stranger_enter_frame`, `approach_risk_zone`, `normal_scene`.
- `normal_scene` is mutually exclusive with all positive behavior labels.
- Directory component `neg` preselects `normal_scene`; `pos` is authoritative over filename words such as `Negative`; `dog_out` maps to `dog_enter_frame`.
- Imported events always start with null times and `review_status=pending`; only explicit review marks a row `reviewed`.
- Positive events require integer millisecond start/end values with `end > start` before review. Reviewed `normal_scene` needs no time interval.
- Preserve and re-save legacy `events` labels. New labels remain restricted to the fixed list.
- Write CSV data atomically and create one timestamped backup before the first modification of each pre-existing CSV in a process run.
- The target folder is not a Git repository. Do not initialize one or create commits.

## File Structure

- Create: `D:\default file\视频标注工具\video_event_labeler.py` - complete application, including pure data functions, CSV persistence, HTTP handler, and embedded UI.
- Create: `D:\default file\视频标注工具\test_video_event_labeler.py` - unit and HTTP integration tests using temporary directories.
- Create: `D:\default file\视频标注工具\README.md` - concise Windows launch and data-format instructions.
- Move after verification: `D:\default file\多行为同时发生-multi_behavior_event_labeler.py` to `D:\default file\视频标注工具\old\多行为同时发生-multi_behavior_event_labeler.py`.
- Move after verification: `D:\default file\simple_labeler.py` to `D:\default file\视频标注工具\old\simple_labeler.py`.
- Move after verification: `D:\default file\test_multi_behavior_event_labeler.py` to `D:\default file\视频标注工具\old\test_multi_behavior_event_labeler.py`.
- Existing: `D:\default file\视频标注工具\docs\superpowers\specs\2026-08-21-unified-video-event-labeler-design.md`.

---

### Task 1: Define and Test Label, Time, and Schema Rules

**Files:**
- Create: `D:\default file\视频标注工具\test_video_event_labeler.py`
- Create: `D:\default file\视频标注工具\video_event_labeler.py`

**Interfaces:**
- Produces: `BEHAVIOR_LABELS: tuple[str, ...]`, `detect_manifest_mode(fieldnames: list[str]) -> str`, `infer_prelabels(relative_path: Path) -> tuple[str, list[str]]`, `parse_time_text(value: str) -> int | None`, `format_time_text(value: int | None) -> str`, `validate_events(events: list[dict[str, object]], permitted_labels: set[str], review: bool) -> list[dict[str, object]]`.
- Consumes: no project code; all functions operate on passed values.

- [ ] **Step 1: Write failing label and schema tests**

```python
class LabelRuleTests(unittest.TestCase):
    def test_neg_path_wins_over_filename_event_words(self):
        stratum, labels = labeler.infer_prelabels(
            Path("窥视/neg/outdoor/cam04/strangers_peep_car-neg-001.mp4")
        )
        self.assertEqual((stratum, labels), ("neg", ["normal_scene"]))

    def test_pos_path_collects_multiple_labels_and_maps_dog_out(self):
        stratum, labels = labeler.infer_prelabels(
            Path("进入/pos/cam04/dog_out+fall-pos-001.mp4")
        )
        self.assertEqual(stratum, "pos")
        self.assertEqual(labels, ["person_fall", "dog_enter_frame"])

    def test_events_reject_normal_scene_with_a_positive_label(self):
        with self.assertRaisesRegex(ValueError, "normal_scene"):
            labeler.validate_events(
                [
                    {"event_type": "normal_scene", "start_time_ms": None, "end_time_ms": None},
                    {"event_type": "person_fall", "start_time_ms": None, "end_time_ms": None},
                ],
                set(labeler.BEHAVIOR_LABELS),
                review=False,
            )
```

- [ ] **Step 2: Run the focused test module and verify expected failure**

Run: `python -m unittest test_video_event_labeler.LabelRuleTests -v`

Expected: FAIL because `video_event_labeler` does not exist yet.

- [ ] **Step 3: Implement the pure rules with deterministic label order**

```python
BEHAVIOR_LABELS = (
    "person_fall", "climb_fence", "peep_car_window", "pickup_package",
    "linger_wander", "stay_long", "cat_enter_frame", "dog_enter_frame",
    "stranger_enter_frame", "approach_risk_zone", "normal_scene",
)

def infer_prelabels(relative_path: Path) -> tuple[str, list[str]]:
    text = "/".join(part.casefold() for part in relative_path.parts)
    parts = {part.casefold() for part in relative_path.parts}
    stratum = "pos" if "pos" in parts else "neg" if "neg" in parts else ""
    if stratum == "neg":
        return stratum, ["normal_scene"]
    matches = {
        label for label, needles in LABEL_NEEDLES.items()
        if any(needle in text for needle in needles)
    }
    return stratum, [label for label in BEHAVIOR_LABELS if label in matches and label != "normal_scene"]
```

Define `LABEL_NEEDLES` with the exact Chinese/English mappings in the design document, including `dog_out`. Implement `detect_manifest_mode` to return `"events"` when `events` exists, `"simple"` when both `start_time` and `end_time` exist, and raise `ValueError` otherwise. Implement `validate_events` to reject non-dict data, unknown new labels, duplicate event types, mixed `normal_scene`, non-integer milliseconds, and review requests with missing/invalid positive time ranges. Preserve an existing event type when it appears in `permitted_labels`.

- [ ] **Step 4: Add time conversion tests and implementation**

```python
def test_time_text_round_trips_fractional_seconds(self):
    self.assertEqual(labeler.parse_time_text("0:01:02.25"), 62250)
    self.assertEqual(labeler.format_time_text(62250), "0:01:02.25")

def test_review_requires_complete_positive_intervals(self):
    with self.assertRaisesRegex(ValueError, "开始和结束"):
        labeler.validate_events(
            [{"event_type": "person_fall", "start_time_ms": 1, "end_time_ms": None}],
            set(labeler.BEHAVIOR_LABELS),
            review=True,
        )
```

Use `H:MM:SS` or `H:MM:SS.sss` input, return `None` for empty/`null`, reject out-of-range minutes/seconds, and round to milliseconds.

- [ ] **Step 5: Run the Task 1 suite**

Run: `python -m unittest test_video_event_labeler.LabelRuleTests -v`

Expected: PASS with all label, validation, and time tests green.

### Task 2: Build Tested CSV Import and Atomic Persistence

**Files:**
- Modify: `D:\default file\视频标注工具\test_video_event_labeler.py`
- Modify: `D:\default file\视频标注工具\video_event_labeler.py`

**Interfaces:**
- Consumes: `infer_prelabels`, `validate_events`, `detect_manifest_mode` from Task 1.
- Produces: `read_csv_rows(path: Path, encoding: str) -> tuple[list[dict[str, str]], list[str]]`, `events_to_csv_value(events: list[dict[str, object]]) -> str`, `parse_events(value: str, behavior_ids: list[str]) -> list[dict[str, object]]`, `import_video_directory(root: Path, manifest_name: str = "video_labeler_manifest.csv") -> tuple[Path, int]`, `write_csv_atomic(path: Path, rows: list[dict[str, str]], fieldnames: list[str], encoding: str, backups: dict[Path, Path]) -> Path | None`.

- [ ] **Step 1: Write failing import and persistence tests**

```python
class ImportTests(unittest.TestCase):
    def test_import_creates_relative_rows_and_prelabels(self):
        (self.root / "跌倒" / "pos").mkdir(parents=True)
        (self.root / "跌倒" / "pos" / "cam04-fall-pos.mp4").touch()

        manifest, added = labeler.import_video_directory(self.root)

        rows, fields = labeler.read_csv_rows(manifest, "utf-8-sig")
        self.assertEqual(added, 1)
        self.assertIn("review_status", fields)
        self.assertEqual(rows[0]["video_path"], "跌倒/pos/cam04-fall-pos.mp4")
        self.assertEqual(rows[0]["behavior_id"], "person_fall")
        self.assertEqual(rows[0]["review_status"], "pending")

    def test_reimport_adds_only_new_videos(self):
        manifest, _ = labeler.import_video_directory(self.root)
        _, added = labeler.import_video_directory(self.root)
        rows, _ = labeler.read_csv_rows(manifest, "utf-8-sig")
        self.assertEqual((added, len(rows)), (0, 1))
```

- [ ] **Step 2: Run import tests and verify expected failure**

Run: `python -m unittest test_video_event_labeler.ImportTests -v`

Expected: FAIL because `import_video_directory` is undefined.

- [ ] **Step 3: Implement scanning, row generation, and duplicate-safe import**

```python
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
MANIFEST_FIELDS = [
    "sample_id", "video_path", "data_stratum", "behavior_id", "events",
    "person_tag_list", "review_status",
]

def make_import_row(root: Path, source: Path) -> dict[str, str]:
    relative = source.relative_to(root)
    stratum, labels = infer_prelabels(relative)
    events = [{"event_type": label, "start_time_ms": None, "end_time_ms": None} for label in labels]
    return {
        "sample_id": relative.with_suffix("").as_posix(),
        "video_path": relative.as_posix(),
        "data_stratum": stratum,
        "behavior_id": ",".join(labels),
        "events": events_to_csv_value(events),
        "person_tag_list": "null",
        "review_status": "pending",
    }
```

Sort paths by case-folded POSIX relative path. When a manifest already exists, use its `video_path` values normalized with `Path(...).as_posix().casefold()` to identify existing rows, append only missing rows, and preserve every existing row value/field order. New manifests use `utf-8-sig`.

- [ ] **Step 4: Add failing backup, round-trip, and simple-schema tests**

```python
def test_first_write_to_existing_csv_creates_one_backup(self):
    path = self.root / "manifest.csv"
    path.write_text("sample_id,start_time,end_time\na,0:00:01,0:00:02\n", encoding="utf-8")
    backups = {}
    rows, fields = labeler.read_csv_rows(path, "utf-8")
    backup = labeler.write_csv_atomic(path, rows, fields, "utf-8", backups)
    self.assertTrue(backup.is_file())
    self.assertEqual(labeler.write_csv_atomic(path, rows, fields, "utf-8", backups), backup)

def test_simple_schema_is_detected_without_events(self):
    self.assertEqual(labeler.detect_manifest_mode(["sample_id", "start_time", "end_time"]), "simple")
```

- [ ] **Step 5: Implement legacy parsing and atomic writes**

Serialize new multi-event values in the existing `123ms` representation so prior downstream CSV consumers continue to work. Parse both this representation and standard JSON-like event values defensively. Before replacing an existing CSV for the first time, copy it to `event_labeler_backups/<stem>.before_event_labeling_<timestamp>.csv`; create the backup directory beside the target CSV. Write through `tempfile.NamedTemporaryFile(dir=path.parent, delete=False, newline="")`, flush and close it, then call `os.replace(temp_path, path)`. Remove a leftover temp file if writing raises.

For simple mode, add `person_tag_list` and `review_status` if absent but retain `start_time` and `end_time`; do not add or overwrite `events`.

- [ ] **Step 6: Run the CSV suite**

Run: `python -m unittest test_video_event_labeler.ImportTests -v`

Expected: PASS for creation, re-import, backup, format detection, and event round-trip tests.

### Task 3: Expose the Dataset Through a Validated Local HTTP API

**Files:**
- Modify: `D:\default file\视频标注工具\test_video_event_labeler.py`
- Modify: `D:\default file\视频标注工具\video_event_labeler.py`

**Interfaces:**
- Consumes: all Task 1 validators and Task 2 CSV/import functions.
- Produces: `AppState`, `LabelerHTTPServer`, `Handler`, `create_server(state: AppState, port: int = 0) -> LabelerHTTPServer`, `safe_video_path(root: Path, relative: str) -> Path | None`.

- [ ] **Step 1: Write failing HTTP integration tests with a temporary dataset**

```python
class ApiTests(unittest.TestCase):
    def test_update_draft_keeps_pending_and_review_requires_times(self):
        status, body = self.post_update({
            "sample_id": "跌倒/pos/fall-pos",
            "person_tag_list": "stranger",
            "events": [{"event_type": "person_fall", "start_time_ms": None, "end_time_ms": None}],
            "review": False,
        })
        self.assertEqual((status, body["review_status"]), (200, "pending"))

        status, body = self.post_update({
            "sample_id": "跌倒/pos/fall-pos",
            "person_tag_list": "stranger",
            "events": [{"event_type": "person_fall", "start_time_ms": None, "end_time_ms": None}],
            "review": True,
        })
        self.assertEqual((status, body["ok"]), (400, False))

    def test_video_path_traversal_is_not_served(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.url + "/video/../../secret.mp4")
        self.assertEqual(raised.exception.code, 404)
```

Use `urllib.request` and a real `LabelerHTTPServer` running in a daemon thread, as in the old test file. Read the CSV after each update to assert persistence instead of asserting only response JSON.

- [ ] **Step 2: Run API tests and verify expected failure**

Run: `python -m unittest test_video_event_labeler.ApiTests -v`

Expected: FAIL because `create_server` and `/api/update` do not exist.

- [ ] **Step 3: Implement state, dataset APIs, and safe video ranges**

```python
@dataclass
class AppState:
    csv_path: Path | None = None
    video_root: Path | None = None
    csv_encoding: str = "utf-8-sig"
    backups: dict[Path, Path] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

def safe_video_path(root: Path, relative: str) -> Path | None:
    candidate = (root / Path(relative)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate
```

Implement these endpoints:

- `GET /api/status` returns `{ready, mode, csv_name, video_root_name}`.
- `GET /api/videos` returns row JSON with parsed `events` in events mode, or `start_time`/`end_time` in simple mode, plus `/video/<relative path>` URLs.
- `POST /api/update` validates and persists a draft or review request. In events mode, derive `behavior_id` from submitted event order. In simple mode, accept only `start_time` and `end_time` and retain its legacy columns.
- `POST /api/import-folder` calls `choose_video_root()` (a minimal `tkinter.filedialog.askdirectory` wrapper), imports the selected root, reconfigures `AppState`, and returns the current status. If no folder is selected or tkinter fails, return a descriptive 400 response without modifying state.
- `GET /video/<relative path>` applies `safe_video_path`, returns 404 for invalid/missing files, and honors a valid single byte range for browser seeking. Invalid ranges return 416.

Do not log request payloads or absolute file paths. Return JSON error bodies for API validation errors.

- [ ] **Step 4: Add and implement reviewed-normal and legacy update coverage**

```python
def test_reviewed_normal_scene_allows_null_times(self):
    status, body = self.post_update({
        "sample_id": "窥视/neg/normal-neg",
        "person_tag_list": "null",
        "events": [{"event_type": "normal_scene", "start_time_ms": None, "end_time_ms": None}],
        "review": True,
    })
    self.assertEqual((status, body["review_status"]), (200, "reviewed"))

def test_simple_update_keeps_start_and_end_columns(self):
    status, body = self.post_simple_update("simple-1", "0:00:03", "0:00:08", review=False)
    self.assertEqual(status, 200)
    self.assertEqual(self.read_simple_row()["end_time"], "0:00:08")
```

Permit legacy event types already present in that row, while rejecting an unknown newly added type. Determine the permitted set by unioning `BEHAVIOR_LABELS` with the row's current parsed event types. Serialize all API errors as `{ "ok": false, "error": "..." }`.

- [ ] **Step 5: Run the HTTP suite**

Run: `python -m unittest test_video_event_labeler.ApiTests -v`

Expected: PASS for draft/review updates, normal-scene review, legacy simple saves, import failure handling, path traversal, and video byte-range tests.

### Task 4: Add the Single-Page Human Review Interface

**Files:**
- Modify: `D:\default file\视频标注工具\test_video_event_labeler.py`
- Modify: `D:\default file\视频标注工具\video_event_labeler.py`

**Interfaces:**
- Consumes: `GET /api/status`, `GET /api/videos`, `POST /api/import-folder`, and `POST /api/update` from Task 3.
- Produces: `HTML: str` served at `/` and the `--port` runnable entry point.

- [ ] **Step 1: Write failing UI contract tests**

```python
class HtmlContractTests(unittest.TestCase):
    def test_interface_exposes_import_draft_review_and_behavior_controls(self):
        for identifier in (
            'id="import-folder"', 'id="behavior-picker"', 'id="add-behavior"',
            'id="save-draft"', 'id="review-next"', 'id="filter"',
        ):
            self.assertIn(identifier, labeler.HTML)

    def test_interface_includes_each_fixed_behavior_value(self):
        for behavior in labeler.BEHAVIOR_LABELS:
            self.assertIn(f'value="{behavior}"', labeler.HTML)
```

- [ ] **Step 2: Run UI contract tests and verify expected failure**

Run: `python -m unittest test_video_event_labeler.HtmlContractTests -v`

Expected: FAIL because the embedded page does not exist yet.

- [ ] **Step 3: Implement the responsive reviewer UI**

Build a dense, dark local-workflow layout with video at left and a fixed-width right control panel. Keep the existing 0.5x/1x/2x playback controls, keyboard left/right navigation, and spacebar play/pause. Add a clear command button for importing a folder, then reload status and list after the API returns.

For events mode, render person tags as three mutually exclusive buttons, a fixed-label `<select id="behavior-picker">`, an add button, and event cards. Each card uses DOM node creation with `textContent`/`value` instead of interpolating filenames or event types into `innerHTML`. Cards offer start/end inputs, capture-current-video-time buttons, clear-time, and delete. Prevent duplicate event types in the client before calling the API.

For simple mode, render one start/end card and keep `start_time`/`end_time` payloads. Render filters for `all`, `pending`, `reviewed`, and `needs-time`; `needs-time` is true only for a non-normal event with a missing time. Keep the row list stable when filters hide the active item.

Implement `saveDraft()` with `review: false` and `reviewCurrent(goNext)` with `review: true`. The latter advances only after a successful response. Switching between `normal_scene` and positives calls browser `confirm()` before clearing conflicting unsaved cards. Display status text from successful and failed API responses.

- [ ] **Step 4: Run UI contract tests and a server smoke check**

Run: `python -m unittest test_video_event_labeler.HtmlContractTests -v`

Expected: PASS.

Run: `python video_event_labeler.py --help`

Expected: exit code 0 and options for `--video-root`, `--csv`, and `--port`.

### Task 5: Write Operating Instructions, Verify the Product, and Archive the Old Version

**Files:**
- Create: `D:\default file\视频标注工具\README.md`
- Modify: `D:\default file\视频标注工具\video_event_labeler.py`
- Modify: `D:\default file\视频标注工具\test_video_event_labeler.py`
- Create: `D:\default file\视频标注工具\old\`
- Move: the three exact legacy files listed in File Structure.

**Interfaces:**
- Consumes: completed CLI, server, importer, UI, and test suite from Tasks 1-4.
- Produces: a self-contained user-facing delivery folder with archived originals.

- [ ] **Step 1: Write final failing CLI parser tests**

```python
class CliTests(unittest.TestCase):
    def test_parser_accepts_video_root_csv_and_port(self):
        args = labeler.build_parser().parse_args([
            "--video-root", "D:/videos", "--csv", "D:/videos/manifest.csv", "--port", "8766",
        ])
        self.assertEqual((args.video_root, args.csv, args.port), (Path("D:/videos"), Path("D:/videos/manifest.csv"), 8766))
```

- [ ] **Step 2: Run the CLI test and verify expected failure**

Run: `python -m unittest test_video_event_labeler.CliTests -v`

Expected: FAIL because `build_parser` is undefined or does not expose all options.

- [ ] **Step 3: Implement final CLI behavior and README**

Implement `build_parser()` with `--video-root`, `--csv`, and `--port` (default `8765`). When `--video-root` is present and `--csv` is omitted, import or update `<video-root>/video_labeler_manifest.csv`; when `--csv` is present, use it with `--video-root` or its parent. With no inputs, start the local page in unconfigured state so the import command can open the folder dialog.

Write `README.md` with these exact commands:

```powershell
cd 'D:\default file\视频标注工具'
python .\video_event_labeler.py
python .\video_event_labeler.py --video-root 'D:\dapeng-test'
python .\video_event_labeler.py --video-root 'D:\videos' --csv 'D:\videos\existing.csv'
```

Document the browser address `http://127.0.0.1:8765`, the fixed behavior labels, the pending/reviewed distinction, and the location/purpose of automatic CSV backups.

- [ ] **Step 4: Run the complete verification set before moving old files**

Run: `python -m unittest -v`

Expected: exit code 0 with every test passing.

Run: `python -m py_compile video_event_labeler.py test_video_event_labeler.py`

Expected: exit code 0 with no output.

Run: `python video_event_labeler.py --help`

Expected: exit code 0 and all three documented command-line options.

Run: `python video_event_labeler.py --port 8766`

Expected: the console reports `http://127.0.0.1:8766`, and a browser request to `/api/status` returns `ready: false`. Stop this smoke-test server cleanly after inspection. Directory import behavior is already covered with temporary test folders; do not create or modify a manifest in `D:\dapeng-test` during verification.

- [ ] **Step 5: Archive exactly the old implementation files**

First verify source files exist and target files do not:

```powershell
Test-Path -LiteralPath 'D:\default file\多行为同时发生-multi_behavior_event_labeler.py'
Test-Path -LiteralPath 'D:\default file\simple_labeler.py'
Test-Path -LiteralPath 'D:\default file\test_multi_behavior_event_labeler.py'
Test-Path -LiteralPath 'D:\default file\视频标注工具\old\多行为同时发生-multi_behavior_event_labeler.py'
```

Create `D:\default file\视频标注工具\old\` and move only the three listed files into it. Do not move CSV backups, data, unrelated scripts, or directories.

- [ ] **Step 6: Repeat final verification from the delivery directory**

Run: `python -m unittest -v`

Expected: exit code 0 with all new tests passing.

Run: `Get-ChildItem -LiteralPath 'D:\default file\视频标注工具' -Force | Select-Object Name`

Expected: `video_event_labeler.py`, `test_video_event_labeler.py`, `README.md`, `docs`, and `old` are present.

Run: `Get-ChildItem -LiteralPath 'D:\default file\视频标注工具\old' -File | Select-Object Name`

Expected: the two original labeler scripts and their original test are present.
