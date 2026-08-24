# Editable Event Segment Labels Design

## Goal

Let reviewers select or change the behavior label directly in every event segment card, and allow the same behavior label to occur in multiple independent time intervals.

## Card Behavior

- Every event card displays an editable behavior select instead of a static label.
- Each card offers every fixed behavior label and all custom labels already present in the current video.
- The existing fixed-label picker and custom-label input continue to create new event cards. The card picker lets a reviewer correct the label afterwards.
- Changing a label keeps that card's entered start and end milliseconds whenever both labels are positive.
- Changing a positive card to `normal_scene` asks for confirmation, then keeps that card and removes all other positive cards.
- Changing a `normal_scene` card to a positive label creates the normal timed card with empty start and end fields.
- Existing custom-label validation remains: 1 to 64 trimmed characters, no comma, carriage return, or newline.

## Repeated Labels

- Multiple cards may use the same fixed or custom event label.
- The server removes its duplicate-label rejection while retaining all other validation: valid label text, non-negative integer milliseconds, end later than start, review-time completeness, and exclusive `normal_scene`.
- `behavior_id` and `events` preserve each event in card order, including repeated event type names. The CSV header and folder-derived `behavior_class` are unchanged.

## Verification

- Verify the server accepts two `car_enter_frame` intervals and preserves both in the CSV event value.
- Verify `normal_scene` remains invalid when combined with any positive event, including duplicate positives.
- Verify the HTML includes the per-card event-type picker and label-change handler.
- Compile all browser scripts and run the existing data-layer, HTML-contract, and CLI tests.
