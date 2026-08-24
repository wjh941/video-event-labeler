# Manual Video Root Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reliable browser path-input workflow for importing a local video directory without depending on a visible system folder dialog.

**Architecture:** Keep `POST /api/import-folder` as the single import endpoint. It will accept an optional `video_root` path in its JSON body; when absent, it will preserve the native picker behavior. Both paths will use a shared state-update helper so manifest creation, encoding detection, locking, and snapshot refresh remain identical. The HTML will add a path input and explicit import button while retaining the existing picker button.

**Tech Stack:** Python 3 standard library HTTP server, embedded HTML/JavaScript, `unittest`, `urllib` test client.

## Global Constraints

- Do not add third-party runtime dependencies.
- Trim and validate user-provided paths before scanning.
- Do not mutate `AppState` until directory import and manifest loading succeed.
- Preserve the native picker for empty request bodies.
- Keep the HTTP server responsive while an import request is running.

---

### Task 1: Add a Shared Import-State Helper and Path Payload API

**Files:**
- Modify: `video_event_labeler.py:1188-1218` for import handling and state updates
- Test: `test_video_event_labeler.py` in `ApiTests`

**Interfaces:**
- Add `apply_imported_root(state: AppState, root: Path) -> tuple[Path, int]` that calls `import_video_directory`, updates `csv_path`, `video_root`, `csv_encoding`, and the cached snapshot under `state.lock`, then returns `(manifest, added)`.
- `POST /api/import-folder` accepts `{ "video_root": "D:/videos" }`; an absent key keeps calling `choose_video_root()`.

- [ ] **Step 1: Write failing API tests**

Add tests that post JSON to `/api/import-folder` with a temporary valid directory and assert `200`, `ready: true`, the imported root name, and the generated manifest. Add separate tests for an empty path, a missing path, and a path pointing to a file; each must return `400` and leave the initial state unready.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```powershell
python -m unittest -q test_video_event_labeler.ApiTests.test_import_folder_from_payload
python -m unittest -q test_video_event_labeler.ApiTests.test_import_folder_rejects_invalid_paths
```

Expected: failures because the endpoint currently ignores `video_root` and still invokes the native picker.

- [ ] **Step 3: Implement the shared helper and request parsing**

Parse the request body exactly as `/api/update` does. Normalize an optional string path with `.strip()`, reject non-string or empty values with `ValueError("video_root is required")`, resolve it, and reject missing/non-directory paths with a clear `ValueError`. Use `apply_imported_root` for both payload paths and the picker path. Catch `ValueError` and `OSError` as HTTP 400 as the current endpoint does.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run the two commands from Step 2 and expect `OK`.

- [ ] **Step 5: Commit the backend change**

```powershell
git add video_event_labeler.py test_video_event_labeler.py
git commit -m "feat: support importing video roots by path"
```

### Task 2: Add the Browser Path-Import Controls

**Files:**
- Modify: `video_event_labeler.py:804-850` in the embedded HTML and JavaScript
- Test: `test_video_event_labeler.py` in `HtmlContractTests`

**Interfaces:**
- Add `id="video-root-path"` text input and `id="import-path"` button.
- Add `importVideoRoot()` JavaScript that sends `{video_root: value}` to `POST /api/import-folder`, reloads the dataset on success, and preserves the typed value on failure.

- [ ] **Step 1: Write failing HTML contract tests**

Assert that `HTML` contains the path input, path-import button, `video_root`, and `importVideoRoot` markers. Assert that the error path calls `setStatus` and does not clear the input.

- [ ] **Step 2: Run the focused contract tests and confirm they fail**

```powershell
python -m unittest -q test_video_event_labeler.HtmlContractTests.test_path_import_controls_are_exposed
```

Expected: failure because the controls and handler do not exist.

- [ ] **Step 3: Implement the controls and handler**

Place the input and button in the top bar with stable sizing and a clear placeholder such as `D:\videos`. On click, trim the value, show a validation message for empty input without making a request, then call the existing `request` helper with JSON. On success call `load()` and display the imported-directory status; on failure display the server error while leaving the input untouched. Keep the existing `import-folder` button and native-picker behavior.

- [ ] **Step 4: Run the focused contract tests and verify embedded JavaScript syntax**

Run:

```powershell
python -m unittest -q test_video_event_labeler.HtmlContractTests.test_path_import_controls_are_exposed
python -m py_compile video_event_labeler.py
```

Expected: `OK` and a zero exit code.

- [ ] **Step 5: Commit the browser change**

```powershell
git add video_event_labeler.py test_video_event_labeler.py
git commit -m "feat: add manual video root import control"
```

### Task 3: Full Verification and Runtime Handoff

**Files:**
- Verify: `video_event_labeler.py`, `test_video_event_labeler.py`, `README.md`

- [ ] **Step 1: Update README usage**

Document the path-input workflow and the CLI fallback command:

```powershell
python video_event_labeler.py --video-root "D:\videos"
```

- [ ] **Step 2: Run the complete verification suite**

```powershell
python -W error::ResourceWarning -m unittest -q
python -m py_compile video_event_labeler.py test_video_event_labeler.py
git diff --check
```

Expected: all tests pass, compilation succeeds, and `git diff --check` reports no whitespace errors.

- [ ] **Step 3: Restart and verify the local service**

Stop the previous `video_event_labeler.py` process, start the updated script, and verify:

```powershell
curl.exe --max-time 5 http://127.0.0.1:8765/
curl.exe --max-time 5 http://127.0.0.1:8765/api/status
```

Both requests must return successfully before handing the URL back to the user.
