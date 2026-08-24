# Folder Picker Error Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect Windows folder-picker processes that fail to expose a visible window, terminate them promptly, and direct users to the reliable path-import workflow.

**Architecture:** Replace the blocking `subprocess.run` call with an observable `subprocess.Popen` lifecycle. A small Win32 helper detects visible windows owned by the picker process during a 3-second startup period; an invisible or timed-out process is terminated and converted to a specific user-facing error. The browser prevents duplicate native-picker requests and always restores its button.

**Tech Stack:** Python 3 standard library (`ctypes`, `subprocess`, `time`), Win32 user32 APIs, embedded HTML/JavaScript, `unittest` mocks.

## Global Constraints

- Add no third-party runtime dependencies.
- Detect a visible picker window within 3 seconds.
- Allow a visible picker up to 300 seconds for selection.
- Terminate and reap failed or timed-out child processes.
- Keep manual path import usable and preserve its input value.
- Preserve the Tk fallback on non-Windows platforms.

---

### Task 1: Detect and Clean Up Invisible Windows Picker Processes

**Files:**
- Modify: `video_event_labeler.py:776-811`
- Test: `test_video_event_labeler.py:775-792`

**Interfaces:**
- Add `PICKER_STARTUP_TIMEOUT_SECONDS = 3.0`, `PICKER_SELECTION_TIMEOUT_SECONDS = 300.0`, and `PICKER_POLL_INTERVAL_SECONDS = 0.1`.
- Add `_process_has_visible_window(process_id: int) -> bool` using Win32 top-level window enumeration.
- Add `_terminate_picker_process(process: subprocess.Popen[str]) -> tuple[str, str]` that terminates and reaps the child with a bounded wait.
- Keep `choose_video_root() -> Path | None` as the public picker API.

- [ ] **Step 1: Write failing lifecycle tests**

Create a lightweight fake process with `pid`, `poll`, `communicate`, `terminate`, and `kill`. Add these tests:

```python
def test_windows_picker_terminates_when_no_visible_window_appears():
    process = FakePickerProcess(poll_results=[None])
    with patch.object(labeler.os, "name", "nt"), \
         patch.object(labeler.subprocess, "Popen", return_value=process), \
         patch.object(labeler, "_process_has_visible_window", return_value=False), \
         patch.object(labeler, "PICKER_STARTUP_TIMEOUT_SECONDS", 0):
        with self.assertRaisesRegex(ValueError, "系统文件夹选择器不可用"):
            labeler.choose_video_root()
    self.assertTrue(process.terminated)
```

Add a visible-window success test returning `D:\videos`, and a visible-window selection-timeout test whose first `communicate` call raises `subprocess.TimeoutExpired`; assert termination and the timeout message.

- [ ] **Step 2: Run the lifecycle tests and verify RED**

```powershell
python -m unittest -q test_video_event_labeler.FolderPickerDispatchTests
```

Expected: failures because `choose_video_root` still calls `subprocess.run` and the visibility helper does not exist.

- [ ] **Step 3: Implement Win32 visibility detection**

Use `ctypes.WINFUNCTYPE` and `ctypes.windll.user32.EnumWindows`. For each top-level window, call `GetWindowThreadProcessId`; return true only when the owner PID matches and `IsWindowVisible(hwnd)` is true. Return false on non-Windows systems or Win32 API errors.

- [ ] **Step 4: Implement the observable picker lifecycle**

Start the existing PowerShell command with:

```python
process = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    errors="replace",
)
```

Poll until process exit, visible window, or the 3-second deadline. On invisible timeout, terminate/reap and raise:

```text
系统文件夹选择器不可用，请在路径框中输入视频目录并点击“按路径导入”
```

After visibility, call `communicate(timeout=300)`. On `TimeoutExpired`, terminate/reap and raise:

```text
文件夹选择超时，请重试或使用“按路径导入”
```

Parse return code, stderr, and selected stdout path as before.

- [ ] **Step 5: Run lifecycle tests and verify GREEN**

```powershell
python -m unittest -q test_video_event_labeler.FolderPickerDispatchTests
```

Expected: all picker lifecycle and HTTP responsiveness tests pass.

- [ ] **Step 6: Commit backend detection**

```powershell
git add video_event_labeler.py test_video_event_labeler.py
git commit -m "fix: detect unavailable folder picker windows"
```

### Task 2: Prevent Duplicate Browser Picker Requests

**Files:**
- Modify: `video_event_labeler.py:876`
- Test: `test_video_event_labeler.py` in `HtmlContractTests`

**Interfaces:**
- Replace the inline native-picker handler with `async function importWithFolderPicker()`.
- The function disables `#import-folder` before the request and restores it in `finally`.

- [ ] **Step 1: Write the failing HTML contract test**

```python
def test_native_picker_button_is_restored_after_request():
    self.assertIn("async function importWithFolderPicker()", labeler.HTML)
    self.assertIn("button.disabled=true", labeler.HTML)
    self.assertIn("finally{button.disabled=false}", labeler.HTML)
```

- [ ] **Step 2: Run the contract test and verify RED**

```powershell
python -m unittest -q test_video_event_labeler.HtmlContractTests.test_native_picker_button_is_restored_after_request
```

Expected: failure because the existing handler does not disable or restore the button.

- [ ] **Step 3: Implement the guarded handler**

Define `importWithFolderPicker`, set the existing status message, disable the button before awaiting `request`, keep the existing success/error behavior, and restore the button only in `finally`. Assign it to `$("import-folder").onclick`.

- [ ] **Step 4: Run the contract test and JavaScript syntax check**

```powershell
python -m unittest -q test_video_event_labeler.HtmlContractTests.test_native_picker_button_is_restored_after_request
node --input-type=module -e "import {execFileSync} from 'child_process'; import vm from 'node:vm'; const html=execFileSync('python',['-c','import video_event_labeler; print(video_event_labeler.HTML)'],{encoding:'utf8'}); const scripts=html.split('<script>').slice(1).map(part=>part.split('</script>')[0]); for (const source of scripts) new vm.Script(source); console.log('scripts='+scripts.length)"
```

Expected: the test passes and all six scripts compile.

- [ ] **Step 5: Commit browser detection UX**

```powershell
git add video_event_labeler.py test_video_event_labeler.py
git commit -m "fix: report native folder picker failures"
```

### Task 3: Full Verification and Runtime Check

**Files:**
- Verify: `video_event_labeler.py`, `test_video_event_labeler.py`

- [ ] **Step 1: Run all automated checks**

```powershell
python -W error::ResourceWarning -m unittest -q
python -m py_compile video_event_labeler.py test_video_event_labeler.py
git diff --check
```

Expected: all tests pass, compilation exits zero, and no whitespace errors are reported.

- [ ] **Step 2: Restart the local service**

Stop only the running `video_event_labeler.py` Python process, start the updated script on port 8765, and preserve the terminal session.

- [ ] **Step 3: Verify HTTP health and error response**

```powershell
curl.exe --max-time 5 -sS -o NUL -w "page_http=%{http_code}\n" http://127.0.0.1:8765/
curl.exe --max-time 5 -sS http://127.0.0.1:8765/api/status
```

Expected: `page_http=200` and a valid JSON status response. In an environment without visible native dialogs, clicking the native picker returns the explicit fallback error in about 3 seconds while the status endpoint remains responsive.
