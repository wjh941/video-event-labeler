# Self-Owned Windows Folder Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the Windows folder picker reliably from the background HTTP server without reading the global foreground window.

**Architecture:** Keep the existing Python-to-STA-PowerShell boundary and process lifecycle checks. Replace the cross-process browser owner handle with a tiny transparent, topmost WinForms owner created and disposed inside the PowerShell child process.

**Tech Stack:** Python 3.11 standard library, Windows PowerShell 5, .NET WinForms, `unittest`, Node.js `vm` for embedded JavaScript syntax validation.

## Global Constraints

- Add no dependency.
- Preserve the three-second picker startup check and 300-second selection timeout.
- Preserve process termination and reaping on startup or selection timeout.
- Preserve manual path import and browser button restoration.
- Keep the change limited to `video_event_labeler.py` and `test_video_event_labeler.py`.

---

### Task 1: Replace Foreground Ownership With a Self-Owned Form

**Files:**
- Modify: `test_video_event_labeler.py:804-890`
- Modify: `video_event_labeler.py:781-895`

**Interfaces:**
- Consumes: `choose_video_root() -> Path | None`, `_process_has_visible_window(process_id: int) -> bool`, and `_terminate_picker_process(process) -> tuple[str, str]`.
- Produces: unchanged `choose_video_root() -> Path | None` behavior with no `GetForegroundWindow()` dependency.

- [ ] **Step 1: Write the failing regression test**

Remove `_foreground_window_handle` from `picker_patches`. Replace the test that expects an early failure when the foreground handle is zero with a test that calls `choose_video_root()` while `Popen` returns a visible fake process, then checks the generated PowerShell:

```python
def test_windows_picker_uses_its_own_topmost_owner(self):
    process = self.FakePickerProcess()
    popen, old_run, visible, startup_timeout = self.picker_patches(process, True)

    with popen as start, old_run, visible, startup_timeout:
        selected = labeler.choose_video_root()

    self.assertIsNone(selected)
    script = start.call_args.args[0][-1]
    for marker in (
        "System.Windows.Forms.Form",
        "$owner.ShowInTaskbar=$false",
        "$owner.Opacity=0",
        "$owner.TopMost=$true",
        "$owner.Show()",
        "$owner.Activate()",
        "ShowDialog($owner)",
        "$owner.Dispose()",
    ):
        with self.subTest(marker=marker):
            self.assertIn(marker, script)
    self.assertNotIn("GetForegroundWindow", script)
```

Update the successful-selection and selection-timeout tests to unpack four patches instead of five. Remove assertions for `NativeWindowOwner` and `[IntPtr]4321`; retain the `FolderBrowserDialog` and `ShowDialog($owner)` assertions.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m unittest -q test_video_event_labeler.FolderPickerDispatchTests.test_windows_picker_uses_its_own_topmost_owner
```

Expected: FAIL because the current implementation calls `_foreground_window_handle()` and the generated script does not create `System.Windows.Forms.Form`.

- [ ] **Step 3: Implement the minimal self-owned picker**

Delete `_foreground_window_handle()`. Delete the `owner_handle` check and the `NativeWindowOwner` C# type from `choose_video_root()`.

Build the PowerShell script with a standard WinForms owner and bounded disposal:

```python
dialog_script = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "Add-Type -AssemblyName System.Drawing;"
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$owner=New-Object System.Windows.Forms.Form;"
    "$owner.ShowInTaskbar=$false;"
    "$owner.FormBorderStyle=[System.Windows.Forms.FormBorderStyle]::FixedToolWindow;"
    "$owner.StartPosition=[System.Windows.Forms.FormStartPosition]::CenterScreen;"
    "$owner.Size=New-Object System.Drawing.Size(1,1);"
    "$owner.Opacity=0;"
    "$owner.TopMost=$true;"
    "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
    "$dialog.Description='选择视频文件夹';"
    "$dialog.ShowNewFolderButton=$false;"
    "try{$owner.Show();$owner.Activate();"
    "$result=$dialog.ShowDialog($owner);"
    "if($result -eq [System.Windows.Forms.DialogResult]::OK) "
    "{[Console]::WriteLine($dialog.SelectedPath)}}"
    "finally{$dialog.Dispose();$owner.Close();$owner.Dispose()}"
)
```

Do not change the existing `Popen`, visible-window polling, timeout, cleanup, or result parsing code.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m unittest -q test_video_event_labeler.FolderPickerDispatchTests
```

Expected: `Ran 5 tests` and `OK`.

- [ ] **Step 5: Run a bounded real-Windows diagnostic**

Run `choose_video_root()` with `PICKER_SELECTION_TIMEOUT_SECONDS=2`, instrumenting `_process_has_visible_window` to print each observed state.

Expected: at least one `visible=True`, followed after two seconds by the existing selection-timeout `ValueError`. The PowerShell process must exit before the command returns.

- [ ] **Step 6: Commit the implementation**

```powershell
git add video_event_labeler.py test_video_event_labeler.py
git commit -m "fix: make Windows folder picker self-owned"
```

### Task 2: Verify, Restart, and Push

**Files:**
- Verify: `video_event_labeler.py`
- Verify: `test_video_event_labeler.py`

**Interfaces:**
- Consumes: committed `main` branch and local server URL `http://127.0.0.1:8765`.
- Produces: verified `origin/main` containing the self-owned picker fix.

- [ ] **Step 1: Run the complete verification suite**

Run:

```powershell
python -W error::ResourceWarning -m unittest -q
python -m py_compile video_event_labeler.py test_video_event_labeler.py
git diff --check
node --input-type=module -e "import {execFileSync} from 'child_process'; import vm from 'node:vm'; const html=execFileSync('python',['-c','import video_event_labeler; print(video_event_labeler.HTML)'],{encoding:'utf8'}); const scripts=html.split('<script>').slice(1).map(part=>part.split('</script>')[0]); for (const source of scripts) new vm.Script(source); console.log('scripts='+scripts.length)"
```

Expected: all unit tests pass, compilation and whitespace checks exit zero, and JavaScript reports `scripts=6`.

- [ ] **Step 2: Restart only the process listening on port 8765**

Resolve the exact PID with:

```powershell
netstat -ano | Select-String ':8765'
```

Stop only that PID, then run `python video_event_labeler.py` in a persistent terminal session.

- [ ] **Step 3: Verify the live service**

Run:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/status | ConvertTo-Json -Compress
```

Expected: valid JSON containing the `ready`, `mode`, `csv_name`, `video_root_name`, and `csv_revision` fields.

- [ ] **Step 4: Confirm repository state and push**

Run:

```powershell
git status --short --branch
git push origin main
git status --short --branch
```

Expected before push: clean `main` ahead of `origin/main`. Expected after push: clean `main` with no ahead/behind marker.
