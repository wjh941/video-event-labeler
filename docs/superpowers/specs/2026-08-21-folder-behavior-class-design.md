# Folder-Based Behavior Class Design

## Goal

Populate `behavior_class` from the first directory below the imported video root instead of deriving it from event labels.

For an imported root `D:\dapeng-test`, a video at `D:\dapeng-test\跌倒\pos\clip.mp4` has `behavior_class` set to `跌倒`.

## Rules

- New rows use the first component of the video path relative to the imported root.
- A video directly inside the imported root uses the root directory name as its class.
- Re-importing refreshes `behavior_class` for every discovered video, including rows created by an earlier tool version.
- Re-importing preserves all other existing annotation fields, including `events` and `person_tag_list`.
- Saving an edited event row keeps its folder-derived `behavior_class`; changing event labels never changes its class.

## Implementation

Add one small helper that derives a class from an imported root and video path. Use it for new rows, for matching existing rows during re-import, and when saving a reference-manifest event row.

The existing label-to-Chinese-name mapping remains available for legacy compatibility but no longer writes `behavior_class` for newly imported or updated reference rows.

## Verification

- Import nested videos and assert their class is the first folder below the root.
- Re-import a row with the old label-derived class and assert the class is refreshed while annotations remain unchanged.
- Update a row's event labels through the data-layer update path and assert its class remains folder-derived.

## Review Navigation and Clip Checking

- Add visible Previous and Next controls. They use the same draft-save behavior as keyboard navigation before changing the current row.
- Show a progress summary for the currently visible rows: current position, total visible rows, rows ready for review, and rows that still need time ranges.
- Add a loop control to each event card. It is enabled only when that card has a valid start and end time.
- Starting a loop seeks to that card's start time and repeats playback between its start and end times. Starting another card's loop, editing a looped time, or changing rows stops the old loop.
- Normal-scene rows have no timed event card and therefore no loop control.
- Keep every new control in the side operation panel. The video viewer retains its own grid area with no floating control layer, so playback is never obscured by page controls.
- Replace the single-use behavior add action with a New Event Segment action. It creates a new independent card from the selected fixed behavior label, permits multiple segments of the same label, and starts with blank start/end times for manual capture or entry.
