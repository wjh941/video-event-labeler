# Task 7 report

- CSV import/export now consume row iterators, expose deterministic per-row progress callbacks, and raise `CancellationError` between rows.
- JSONL export backups use UTC timestamped filenames.
- Event and person labelers debounce draft snapshots, restore matching drafts, and warn before unloading with unsaved changes.
- Existing CSV semantics, person identity attributes, zero-person rows, and path validation remain unchanged.

Verification: `python -m pytest -q tests/test_streaming_io.py tests/test_ui_recovery.py tests/test_csv_adapter.py tests/test_exports.py tests/test_ui_contracts.py` (19 passed).
