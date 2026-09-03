# Task 2 Report: Evidence identity and safe references

## Changes

- `EvidenceService` accepts an optional `media_root`.
- Empty evidence IDs are assigned `evidence-<uuid4 hex>` before persistence; each `attach()` returns the persisted `Evidence`.
- Local evidence paths are resolved and rejected when they escape `media_root`. Non-local URI schemes remain supported.
- `SQLiteStore.upsert_prediction()` validates every evidence reference before writing. Unknown IDs and IDs belonging to another sample raise `ValueError`; empty references remain valid.
- Existing person identity attributes, zero-person samples, and CSV behavior are unchanged.

## TDD evidence

Added `tests/test_evidence.py` covering distinct generated IDs, media-root escape rejection, and unknown prediction evidence rejection. The initial run failed with three expected failures because the interface and guards were absent.

## Verification

`python -m pytest -q tests/test_evidence.py tests/test_exports.py tests/test_sqlite_store.py`

Result: **19 passed**.

`git diff --check` completed without whitespace errors.

## Concerns

`file://` URIs are treated as local paths and normalized to resolved paths under the configured root. Other URI schemes are treated as remote references and are not filesystem-resolved.

## Review fixes

- `attach()` now checks existing evidence IDs for sample ownership and returns the row reloaded from SQLite after persistence.
- Windows drive-letter paths (for example, `C:\\outside.mp4`) are recognized as local before URI parsing and are subject to `media_root` confinement.
- Added regression coverage for both cases and for cross-sample ID reuse.

Updated verification: **21 passed** across the focused evidence, export, and SQLite suites.
