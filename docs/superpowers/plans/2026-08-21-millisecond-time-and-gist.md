# Millisecond Time and Gist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display and capture event times at exact three-digit millisecond precision while preserving the legacy `ms` CSV format, then publish the runnable source as a public GitHub Gist.

**Architecture:** Python continues to hold integer millisecond values and serialize them as `1234ms`. Only the embedded JavaScript display function changes from variable decimal rendering to zero-padded three-digit milliseconds; the public Gist is created by GitHub CLI from three explicit files.

**Tech Stack:** Python 3 standard library, browser-native JavaScript, `unittest`, GitHub CLI.

## Global Constraints

- Browser display and capture format is `H:MM:SS.mmm`, including trailing zeros.
- Browser capture uses `Math.floor(video.currentTime * 1000)`.
- `events` CSV persists integer milliseconds with the exact legacy `ms` suffix.
- Existing `H:MM:SS` and shorter decimal input remains accepted.
- The public Gist contains only `video_event_labeler.py`, `test_video_event_labeler.py`, and `README.md`.
- Do not publish videos, CSV files, backups, screenshots, design documents, product documents, or `old`.
- The delivery directory is not a Git repository; do not create commits or branches.

---

### Task 1: Enforce Three-Digit Millisecond Time Formatting

**Files:**
- Modify: `D:\default file\视频标注工具\test_video_event_labeler.py`
- Modify: `D:\default file\视频标注工具\video_event_labeler.py`

**Interfaces:**
- Consumes: `format_time_text(value: int | None) -> str` and browser `timeText(ms)`.
- Produces: fixed `H:MM:SS.mmm` display values while leaving `events_to_csv_value` output in legacy integer-`ms` form.

- [ ] **Step 1: Write failing precision tests**

```python
def test_time_text_always_keeps_three_millisecond_digits(self):
    self.assertEqual(labeler.format_time_text(62000), "0:01:02.000")
    self.assertEqual(labeler.format_time_text(62250), "0:01:02.250")
    self.assertEqual(labeler.format_time_text(62253), "0:01:02.253")

def test_browser_time_formatter_uses_three_digit_milliseconds(self):
    self.assertIn('String(milliseconds).padStart(3,"0")', labeler.HTML)
```

The first test catches removal of trailing zeros; the second catches a browser formatter that silently loses millisecond precision.

- [ ] **Step 2: Run the focused tests and verify expected failure**

Run: `python -m unittest test_video_event_labeler.LabelRuleTests test_video_event_labeler.HtmlContractTests -v`

Expected: FAIL because `format_time_text(62000)` currently returns `0:01:02`, and the current browser formatter has no millisecond-padding expression.

- [ ] **Step 3: Implement fixed-width display formatting**

```python
def format_time_text(value: int | None) -> str:
    if value is None:
        return ""
    total_seconds, milliseconds = divmod(value, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
```

Replace the JavaScript formatter with an integer-millisecond calculation and fixed output:

```javascript
function timeText(ms) {
  if (ms === null || ms === undefined) return "";
  const total = Math.trunc(ms);
  const hours = Math.floor(total / 3600000);
  const minutes = Math.floor(total % 3600000 / 60000);
  const seconds = Math.floor(total % 60000 / 1000);
  const milliseconds = total % 1000;
  return `${hours}:${String(minutes).padStart(2,"0")}:${String(seconds).padStart(2,"0")}.${String(milliseconds).padStart(3,"0")}`;
}
```

Keep `Math.floor(video.currentTime * 1000)` for capture and leave `events_to_csv_value` unchanged.

- [ ] **Step 4: Run focused tests and full verification**

Run: `python -m unittest test_video_event_labeler.LabelRuleTests test_video_event_labeler.HtmlContractTests -v`

Expected: PASS.

Run: `python -m unittest -v`

Expected: every test passes.

Run: `python -m py_compile video_event_labeler.py test_video_event_labeler.py`

Expected: exit code 0 with no output.

### Task 2: Create the Public Source-Only Gist

**Files:**
- Read only: `D:\default file\视频标注工具\video_event_labeler.py`
- Read only: `D:\default file\视频标注工具\test_video_event_labeler.py`
- Read only: `D:\default file\视频标注工具\README.md`

**Interfaces:**
- Consumes: an installed, authenticated `gh` executable and the verified Task 1 files.
- Produces: one public GitHub Gist URL.

- [ ] **Step 1: Verify the GitHub CLI is callable and authenticated**

Run: `gh auth status`

Expected: exit code 0 and an authenticated GitHub account with Gist creation permission. If `gh` is not found or authentication fails, stop before any remote action and report the exact requirement.

- [ ] **Step 2: Verify the explicit public file set**

Run:

```powershell
Get-Item -LiteralPath '.\video_event_labeler.py','.\test_video_event_labeler.py','.\README.md' |
  Select-Object Name,Length
```

Expected: exactly three source/test/instruction files. Do not use a wildcard or directory argument.

- [ ] **Step 3: Create the public Gist**

Run:

```powershell
gh gist create --public --desc 'Local video event labeler with millisecond event timing' `
  .\video_event_labeler.py .\test_video_event_labeler.py .\README.md
```

Expected: exit code 0 and one GitHub Gist URL. Record the URL for the user.

- [ ] **Step 4: Verify the published file list**

Run: `gh gist view <created-gist-url> --files`

Expected: exactly `video_event_labeler.py`, `test_video_event_labeler.py`, and `README.md`; no data or archive files.
