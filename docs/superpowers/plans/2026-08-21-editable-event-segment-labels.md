# Editable Event Segment Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a reviewer select behavior labels inside event cards and save repeated behavior labels as separate timed segments.

**Architecture:** Keep the reference CSV schema untouched. The server stops rejecting repeated event types but retains all label, time, review, and normal-scene validation. The final browser script renders a select in each event-card header and re-renders cards after a label change to apply normal-scene transitions safely.

**Tech Stack:** Python 3 standard library, `unittest`, vanilla HTML/CSS/JavaScript, existing UTF-8 BOM CSV.

## Global Constraints

- Preserve the exact nine-column reference CSV header and folder-derived `behavior_class`.
- Preserve integer millisecond event times and `H:MM:SS.mmm` input format.
- Custom labels remain 1 to 64 trimmed characters without comma, carriage return, or newline.
- `normal_scene` remains exclusive and requires no time range.
- Repeated fixed or custom event labels must preserve separate card order and time ranges.
- Do not add dependencies or CSV columns.

---

## File Structure

- `video_event_labeler.py`: server event validation and the event-card JavaScript renderer.
- `test_video_event_labeler.py`: duplicate-event data test and card-picker HTML contract.
- `README.md`: explain that labels can be changed in cards and repeated segments are saved independently.
- `C:\Users\16102\video-event-labeler-release\video_event_labeler.py` and `README.md`: tested release copies.

### Task 1: Permit Repeated Timed Labels

**Files:**

- Modify: `test_video_event_labeler.py: LabelRuleTests`
- Modify: `video_event_labeler.py: validate_events`

**Interfaces:**

- Consumes: `validate_events(events: list[dict[str, object]], permitted_labels: set[str], review: bool)`.
- Produces: a cleaned event list that may contain repeated `event_type` values in its original order.

- [ ] **Step 1: Write the failing repeated-label test**

```python
def test_events_accept_repeated_labels_with_independent_ranges(self):
    events = labeler.validate_events(
        [
            {"event_type": "car_enter_frame", "start_time_ms": 1000, "end_time_ms": 2000},
            {"event_type": "car_enter_frame", "start_time_ms": 3000, "end_time_ms": 4000},
        ],
        set(labeler.BEHAVIOR_LABELS),
        review=True,
    )
    self.assertEqual([event["start_time_ms"] for event in events], [1000, 3000])
    value = labeler.events_to_csv_value(events)
    self.assertEqual(
        labeler.parse_events(value, ["car_enter_frame", "car_enter_frame"]),
        events,
    )
```

Also retain the existing `test_events_reject_normal_scene_with_a_positive_label`, which proves repeated positives do not weaken normal-scene exclusivity.

- [ ] **Step 2: Run the focused test and verify it fails**

```powershell
python -B -m unittest -v test_video_event_labeler.LabelRuleTests.test_events_accept_repeated_labels_with_independent_ranges
```

Expected: failure with `ValueError: duplicate behavior label: car_enter_frame`.

- [ ] **Step 3: Remove only duplicate-type rejection**

Delete the `if event_type in event_types: raise ValueError(...)` branch from `validate_events`. Keep adding every type to `event_types`, because it is still used by the existing `normal_scene` and review rules. Change `parse_events` from a label-keyed dictionary to an ordered list so repeated event types are not overwritten; only create empty fallback events from `behavior_ids` when the CSV event value contains no parseable events.

- [ ] **Step 4: Run data-layer regression tests**

```powershell
python -B -m unittest -v test_video_event_labeler.LabelRuleTests test_video_event_labeler.ImportTests
```

Expected: the repeated-label test passes and normal-scene, millisecond, import, custom-label, and CSV tests remain green.

### Task 2: Make Event Labels Editable in Every Card

**Files:**

- Modify: `test_video_event_labeler.py: HtmlContractTests`
- Modify: `video_event_labeler.py: final HTML script block`

**Interfaces:**

- Consumes: `renderEvents(row)`, `currentEvents()`, `stopLoop()`, `renderEventCard(event)`, and the fixed `#behavior-picker` options.
- Produces: `changeEventType(card, value)` and a header select with class `event-type` for every event card.

- [ ] **Step 1: Write the failing browser-contract test**

```python
def test_event_cards_expose_editable_behavior_selectors(self):
    self.assertIn('className="event-type"', labeler.HTML)
    self.assertIn('function changeEventType(card,value)', labeler.HTML)
    self.assertIn('select.addEventListener("change"', labeler.HTML)
```

- [ ] **Step 2: Run the HTML contract test and verify it fails**

```powershell
python -B -m unittest -v test_video_event_labeler.HtmlContractTests.test_event_cards_expose_editable_behavior_selectors
```

Expected: failure because event headers currently render a static `span.event-name`.

- [ ] **Step 3: Render and apply a card-level selector**

In the final script, derive available labels as the fixed `#behavior-picker` option values plus distinct event types already present in the row. Build a `select.event-type` in `renderEventCard` and select the card's current event type. Its `change` listener calls `changeEventType(card, value)`.

Implement `changeEventType(card, value)` by collecting `currentEvents()`, replacing only the selected card type, calling `stopLoop()`, and calling `renderEvents({events})`. Preserve start/end values for positive-to-positive changes. When switching to `normal_scene`, require the existing confirmation and render only that event with null times. When switching out of `normal_scene`, render the selected positive event with null times. Mark the row dirty after a successful transition.

- [ ] **Step 4: Run UI checks**

```powershell
python -B -m unittest -v test_video_event_labeler.HtmlContractTests
python -B -c "compile(open('video_event_labeler.py', encoding='utf-8').read(), 'video_event_labeler.py', 'exec'); print('syntax OK')"
node --input-type=module -e "import fs from 'node:fs'; const text=fs.readFileSync('video_event_labeler.py','utf8'); [...text.matchAll(/<script>([\s\S]*?)<\/script>/g)].forEach(match=>new Function(match[1])); console.log('javascript OK');"
```

Expected: all HTML tests pass and both compilers report success.

### Task 3: Document, Synchronize, and Verify

**Files:**

- Modify: `README.md`
- Modify: `C:\Users\16102\video-event-labeler-release\README.md`
- Modify: `C:\Users\16102\video-event-labeler-release\video_event_labeler.py`

**Interfaces:**

- Consumes: tested D-drive source and README.
- Produces: release copies whose SHA-256 values match the tested source files.

- [ ] **Step 1: Update the README**

State that a card's header selector can correct its behavior label after creation, and that the same label may appear in several independently timed segments. Retain `normal_scene`, custom-label, `neg`, and `behavior_class` rules.

- [ ] **Step 2: Copy the finished files to the release repository**

```powershell
Copy-Item -LiteralPath 'D:\default file\视频标注工具\video_event_labeler.py' -Destination 'C:\Users\16102\video-event-labeler-release\video_event_labeler.py' -Force
Copy-Item -LiteralPath 'D:\default file\视频标注工具\README.md' -Destination 'C:\Users\16102\video-event-labeler-release\README.md' -Force
```

- [ ] **Step 3: Run final targeted verification**

```powershell
Set-Location 'D:\default file\视频标注工具'
python -B -m unittest -v test_video_event_labeler.LabelRuleTests test_video_event_labeler.ImportTests test_video_event_labeler.CliTests test_video_event_labeler.HtmlContractTests
Set-Location 'C:\Users\16102\video-event-labeler-release'
git diff --check
git status --short
```

Expected: targeted suite is green, the release diff has no whitespace errors, and only `README.md` and `video_event_labeler.py` are ready for commit.

## Self-Review

- Spec coverage: Task 1 enables repeated labels and preserves normal-scene validation. Task 2 provides card-local label changes, fixed and current custom options, time preservation, and loop reset. Task 3 documents, copies, and verifies the delivery.
- Placeholder scan: no unfinished placeholders, undefined helper names, or vague validation requirements remain.
- Type consistency: browser events continue to use `{event_type, start_time_ms, end_time_ms}` and the server continues to consume that exact shape.
