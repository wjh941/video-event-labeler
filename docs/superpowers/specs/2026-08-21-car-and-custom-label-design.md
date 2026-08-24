# Car and Custom Label Design

## Goal

Add the fixed `car_enter_frame` behavior label and let reviewers add one-off custom behavior labels while annotating an event video.

## Fixed Label

- `car_enter_frame` is a standard selectable behavior beside the existing cat and dog entry labels.
- A video path or filename containing the exact standard name `car_enter_frame` is preselected during import.
- No broad `car_in` alias is added. It would incorrectly match existing names such as `strange_car_invasion`.
- The existing `neg` path marker still takes precedence and produces only `normal_scene`.

## Custom Labels

- The behavior panel contains a compact custom-label text input and an Add Custom Event Segment control.
- Adding a label creates a normal, independently timed event card with empty start and end times. It follows the same capture, deletion, loop playback, draft, and review rules as fixed labels.
- A custom name is stored exactly as entered after trimming its outer whitespace. It may contain Chinese or English text, but must be 1 to 64 characters and cannot contain a comma, a carriage return, or a newline.
- Custom labels are accepted by the server for new events and remain readable and editable from saved CSV rows.
- Custom labels are not added permanently to the fixed-label dropdown and are not inferred from filenames. Reviewers enter them again when needed, keeping the normal picker compact and preventing unintended automatic labels.
- `normal_scene` remains exclusive. Selecting or entering it uses the existing confirmation behavior that clears positive events; adding any positive event clears it.
- `behavior_class` remains the imported folder name. Neither fixed nor custom event labels overwrite it.

## Verification

- Import a filename containing `car_enter_frame` and verify its automatic label.
- Verify an `neg` filename containing `car_enter_frame` still imports only as `normal_scene`.
- Verify valid Chinese custom labels are accepted, serialized, parsed, and can be reviewed with valid millisecond ranges.
- Reject empty labels and labels containing commas or line breaks.
- Verify the HTML contains the new fixed label and custom-label controls.
