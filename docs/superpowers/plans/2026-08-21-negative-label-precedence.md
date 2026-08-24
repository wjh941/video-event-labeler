# Negative Label Precedence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Treat a standalone `neg` marker anywhere in an imported relative video path as an overriding `normal_scene` label.

**Architecture:** Keep label inference in `infer_prelabels`. Add one compiled regular expression that recognizes only explicit `neg` tokens separated by path separators, hyphens, underscores, whitespace, plus signs, or file-extension dots; execute it before positive-label scanning.

**Tech Stack:** Python 3 standard library (`re`, `pathlib`, `unittest`), GitHub CLI for the final public Gist.

## Global Constraints

- A standalone negative marker returns exactly `("neg", ["normal_scene"])` even when positive tags are present.
- `negative` and `negation` are not negative markers.
- Existing positive label ordering and `dog_out -> dog_enter_frame` mapping remain unchanged.
- New manifests continue to use the exact reference nine-column schema, UTF-8 BOM, and millisecond event format.
- The delivery directory is not a Git repository; do not attempt commits.

---

## File Structure

- Modify: `video_event_labeler.py`
  - Add a compiled standalone-negative-marker pattern and check it first in `infer_prelabels`.
- Modify: `test_video_event_labeler.py`
  - Add behavior tests for filename and directory negative markers, word-boundary rejection, and imported reference-row output.

### Task 1: Make Standalone Negative Markers Override Labels

**Files:**
- Modify: `test_video_event_labeler.py:25-87, 95-140`
- Modify: `video_event_labeler.py:64-127`

**Interfaces:**
- Consumes: `infer_prelabels(relative_path: Path) -> tuple[str, list[str]]`.
- Produces: the unchanged function signature with a stronger `neg` precedence guarantee.

- [ ] **Step 1: Write failing negative-marker tests**

Add these tests to `LabelRuleTests`:

```python
def test_filename_neg_marker_overrides_all_positive_labels(self):
    result = labeler.infer_prelabels(
        Path("pos/cam04/dog_out+fall-neg-001.mp4")
    )
    self.assertEqual(result, ("neg", ["normal_scene"]))

def test_neg_marker_does_not_match_larger_words(self):
    result = labeler.infer_prelabels(
        Path("pos/cam04/negative_scene+fall-001.mp4")
    )
    self.assertEqual(result, ("pos", ["person_fall"]))
```

Add an `ImportTests` case that imports `stranger_enter_frame_neg_001.mp4` and asserts `behavior_id == "normal_scene"` and `behavior_class == "正常场景"`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest -v test_video_event_labeler.LabelRuleTests.test_filename_neg_marker_overrides_all_positive_labels test_video_event_labeler.LabelRuleTests.test_neg_marker_does_not_match_larger_words test_video_event_labeler.ImportTests.test_import_negative_filename_uses_normal_scene
```

Expected: the filename-negative cases fail because the current code only recognizes a path component equal to `neg`.

- [ ] **Step 3: Implement the minimal shared inference change**

Define a compiled pattern beside the existing event patterns:

```python
NEGATIVE_MARKER = re.compile(r"(?:^|[/\\\-_+\s.])neg(?=$|[/\\\-_+\s.])")
```

At the start of `infer_prelabels`, after assembling the normalized slash-separated `text`, return immediately when `NEGATIVE_MARKER.search(text)` succeeds:

```python
if NEGATIVE_MARKER.search(text):
    return "neg", ["normal_scene"]
```

Then retain only the `pos` stratum branch and existing positive-label scan. Do not alter `make_import_row`; it already derives `behavior_id` and `behavior_class` from the returned labels.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all three tests PASS.

- [ ] **Step 5: Record the non-commit checkpoint**

Run:

```powershell
Test-Path -LiteralPath '.git'
```

Expected: `False`; no commit is possible in this delivery directory.

### Task 2: Verify and Publish the Final Public Gist

**Files:**
- Verify: `video_event_labeler.py`
- Verify: `test_video_event_labeler.py`
- Publish: `video_event_labeler.py`, `README.md`

**Interfaces:**
- Consumes: the completed negative-label regression suite and the installed GitHub CLI at `C:\Users\16102\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe`.
- Produces: a public Gist URL containing the final tool source and README only.

- [ ] **Step 1: Run complete verification**

Run:

```powershell
python -B -m unittest -v
python -m py_compile .\video_event_labeler.py
```

Expected: every test passes and compilation exits with code `0`.

- [ ] **Step 2: Refresh GitHub authorization for Gist publishing**

Run the previously approved authorization request:

```powershell
& 'C:\Users\16102\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe' auth refresh -h github.com -s gist
```

Expected: the account `wjh941` grants the `gist` token scope through the CLI's interactive authorization flow.

- [ ] **Step 3: Create and verify the public Gist**

Run:

```powershell
& 'C:\Users\16102\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe' gist create --public --desc '本地多行为视频事件标注工具' 'video_event_labeler.py' 'README.md'
```

Expected: one `https://gist.github.com/...` URL. Open its metadata through `gh gist view <id> --json isPublic,files,url` and confirm `isPublic=true` with exactly the two expected files.

- [ ] **Step 4: Record the non-commit checkpoint**

Run the Task 1 Step 5 command and record the expected `False` result.
