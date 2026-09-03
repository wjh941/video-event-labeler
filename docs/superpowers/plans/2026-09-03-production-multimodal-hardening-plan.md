# Production Multimodal Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SQLite the reliable default for the video/event/person annotation workflow while adding media indexing, audit/recovery, prediction review, evidence safety, strict quality gates, backups, streaming exchange, and end-to-end verification.

**Architecture:** Keep the existing domain/storage/services/adapters split. Add focused services for media indexing and database maintenance, extend `AnnotationService` for audit and prediction decisions, and keep the two existing local HTTP UIs as thin adapters over the service layer. CSV remains an explicit compatibility format.

**Tech Stack:** Python 3.10+ (CI baseline 3.11), stdlib `sqlite3`/`http.server`/`csv`, optional `ffprobe`, pytest, ruff, mypy.

## Global Constraints

- Preserve `person_identity_attributes`; never reintroduce `person_tag`.
- Zero-person samples remain valid.
- Local media paths must stay inside the configured root after resolution.
- Every new behavior gets a failing test before production code.
- Do not add runtime dependencies.
- Existing CSV files remain importable/exportable.

---

### Task 1: SQLite-first startup and media indexing

**Files:**
- Modify: `run_video_annotation.py:14-65`
- Modify: `video_event_labeler.py` DB startup and `AppState`
- Modify: `person_identity_labeler.py` DB startup and `AppState`
- Create: `video_labeler/media_index.py`
- Test: `tests/test_media_index.py`, `tests/test_launcher.py`

**Interfaces:**
- Add `index_media(root: Path, store: SQLiteStore, ffprobe_path: Path | None = None) -> MediaIndexReport`.
- Add `MediaIndexReport` with `scanned`, `indexed`, `skipped`, and `errors` fields.
- `run_video_annotation.py` resolves `--db` to `<video-root>/dataset.db` when omitted and forwards it to both adapters.

- [ ] **Step 1: Write failing tests**

```python
def test_index_media_populates_video_asset(tmp_path, store):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    report = index_media(tmp_path, store)
    assert report.scanned == 1
    assert store.get_media_assets(sample_id_for_path("clip.mp4", sha256_file(video)))

def test_launcher_defaults_db_under_video_root():
    args = build_parser().parse_args(["--video-root", "D:/videos"])
    assert args.db is None
    assert default_db_path(args.video_root) == Path("D:/videos/dataset.db")
```

- [ ] **Step 2: Run `python -m pytest -q tests/test_media_index.py tests/test_launcher.py` and confirm the missing API/default fails.**
- [ ] **Step 3: Implement deterministic discovery, sample upsert, streaming hash, optional probe, and idempotent media upsert. Make launcher pass the resolved database path by default.**
- [ ] **Step 4: Re-run the focused tests and then `python -m pytest -q tests/test_media.py tests/test_media_index.py tests/test_launcher.py`.**
- [ ] **Step 5: Commit `feat: make sqlite startup index media by default`.**

### Task 2: Evidence identity and safe references

**Files:**
- Modify: `video_labeler/evidence.py`
- Modify: `video_labeler/storage/sqlite_store.py:304-326`
- Modify: `video_labeler/domain.py:150-176`
- Test: `tests/test_evidence.py`

**Interfaces:**
- `EvidenceService(store, media_root: Path | None = None)` validates local `uri` and returns the persisted `Evidence` from `attach()`.
- Empty `evidence_id` becomes `evidence-<uuid4 hex>`.
- `validate_evidence_references(sample_id, evidence_ids)` raises `ValueError` for unknown IDs or wrong sample IDs.

- [ ] **Step 1: Add tests for two empty-ID evidence records receiving different IDs, path escape rejection, and unknown prediction evidence rejection.**
- [ ] **Step 2: Run `python -m pytest -q tests/test_evidence.py` and verify failure.**
- [ ] **Step 3: Generate IDs before persistence, resolve local paths under `media_root`, and validate evidence references before storing predictions.**
- [ ] **Step 4: Run `python -m pytest -q tests/test_evidence.py tests/test_exports.py tests/test_sqlite_store.py`.**
- [ ] **Step 5: Commit `fix: make evidence ids and references safe`.**

### Task 3: Automatic annotation revisions and restore

**Files:**
- Modify: `video_labeler/services.py:107-132`
- Modify: `video_labeler/storage/sqlite_store.py:208-304,358-364`
- Create: `video_labeler/serialization.py`
- Test: `tests/test_revisions.py`

**Interfaces:**
- `AnnotationService.save_events(..., actor="human")` and `save_people(..., actor="human")` write one revision containing canonical before/after JSON.
- `restore_revision(sample_id, revision, actor, expected_revision=None) -> SaveResult` restores both child collections atomically and writes a new revision.
- Add `SQLiteStore.get_revision(sample_id, revision) -> sqlite3.Row | None`.

- [ ] **Step 1: Add tests proving save creates before/after JSON, restore creates a new revision, and stale restore raises `ConflictError`.**
- [ ] **Step 2: Run `python -m pytest -q tests/test_revisions.py` and verify failure.**
- [ ] **Step 3: Serialize sorted event/person payloads, capture the previous snapshot, write the child replacement and revision row in one transaction, and implement restore.**
- [ ] **Step 4: Run focused revision tests plus `tests/test_services.py tests/test_sqlite_store.py`.**
- [ ] **Step 5: Commit `feat: add annotation audit history and restore`.**

### Task 4: Prediction review loop

**Files:**
- Modify: `video_labeler/services.py`
- Modify: `video_labeler/storage/sqlite_store.py:328-356`
- Modify: `video_event_labeler.py` and `person_identity_labeler.py` API handlers
- Test: `tests/test_predictions.py`, `tests/test_ui_predictions.py`

**Interfaces:**
- `list_predictions(sample_id) -> list[Prediction]`.
- `accept_prediction(prediction_id, actor, expected_revision=None) -> SaveResult` converts `task == "event"` labels to an accepted `Event` and `task == "person"` labels to an accepted `Person`; unsupported labels return `ValueError` without mutation.
- `reject_prediction(prediction_id, actor) -> None` marks a draft prediction rejected and records an audit summary.
- Add `POST /api/predictions/{id}/accept` and `/reject` in DB mode; return 409 for stale revisions.

- [ ] **Step 1: Add tests for event acceptance, person acceptance, rejection, repeat-decision failure, and API status codes.**
- [ ] **Step 2: Run `python -m pytest -q tests/test_predictions.py tests/test_ui_predictions.py` and verify failure.**
- [ ] **Step 3: Implement label JSON validation/conversion, decision metadata, revision/audit writes, and adapter endpoints.**
- [ ] **Step 4: Run focused tests and all service/UI contract tests.**
- [ ] **Step 5: Commit `feat: close the prediction human-review loop`.**

### Task 5: Strict quality validation and reproducible manifests

**Files:**
- Modify: `video_labeler/quality.py:50-220`
- Modify: `video_labeler/cli.py`
- Test: `tests/test_quality.py`, `tests/test_exports.py`

**Interfaces:**
- `validate_dataset(store, mode="draft")`; `mode="strict"` promotes completeness issues to errors.
- `export_jsonl(..., manifest_path: Path | None = None, split_seed: str = "video-labeler-v1")` writes stable `train`, `validation`, or `test` split based on sample hash.
- Manifest includes schema version, source hashes, max revision, counts, and split seed.

- [ ] **Step 1: Add tests for strict missing media, duration overflow, duplicate person IDs, deterministic splits, and manifest contents.**
- [ ] **Step 2: Run the focused tests and verify failure.**
- [ ] **Step 3: Implement mode-aware issue severity, overlap/range/duplicate checks, stable hash split, and atomic manifest output.**
- [ ] **Step 4: Run `python -m pytest -q tests/test_quality.py tests/test_exports.py`.**
- [ ] **Step 5: Commit `feat: add strict quality gates and dataset manifests`.**

### Task 6: Database maintenance and recovery commands

**Files:**
- Create: `video_labeler/maintenance.py`
- Modify: `video_labeler/cli.py`
- Modify: `video_labeler/storage/sqlite_store.py:28-48`
- Test: `tests/test_maintenance.py`

**Interfaces:**
- `backup_database(store, output: Path) -> Path` uses `sqlite3.Connection.backup` and atomic replace.
- `check_database(store) -> DatabaseCheck` runs `PRAGMA integrity_check` and reports schema version.
- CLI commands: `backup-db`, `check-db`.
- Migration acquires the sibling file lock before connection pragmas/migration and creates a timestamped pre-migration backup when the DB already exists.

- [ ] **Step 1: Add tests for backup restore, integrity success/failure reporting, and migration lock ordering through a concurrent open.**
- [ ] **Step 2: Run `python -m pytest -q tests/test_maintenance.py` and verify failure.**
- [ ] **Step 3: Implement maintenance helpers, CLI output/exit codes, and initialization lock ordering.**
- [ ] **Step 4: Run focused tests plus schema/fault recovery tests.**
- [ ] **Step 5: Commit `feat: add database backup and integrity commands`.**

### Task 7: Streaming exchange and UI resilience

**Files:**
- Modify: `video_labeler/storage/csv_adapter.py:198-326`
- Modify: `video_labeler/quality.py` JSONL backup path
- Modify: `video_event_labeler.py` and `person_identity_labeler.py` save/resume JavaScript
- Test: `tests/test_streaming_io.py`, `tests/test_ui_recovery.py`

**Interfaces:**
- `import_csv(..., progress: Callable[[int], None] | None = None, cancel: Callable[[], bool] | None = None)` processes rows incrementally.
- `export_csv(..., progress=..., cancel=...)` writes rows without an accumulated `rows` list.
- JSONL backups use timestamped filenames.
- UI saves a draft snapshot with debounce, restores it after reload, and warns before closing with unsaved changes.

- [ ] **Step 1: Add tests for progress callbacks, cancellation, large-row bounded processing, timestamped JSONL backup, and resume recovery.**
- [ ] **Step 2: Run focused tests and verify failure.**
- [ ] **Step 3: Replace list materialization with iterators, invoke callbacks at deterministic row intervals, add cancellation exceptions, and add UI draft recovery hooks.**
- [ ] **Step 4: Run focused tests plus all CSV/UI tests.**
- [ ] **Step 5: Commit `feat: stream imports and recover unsaved drafts`.**

### Task 8: HTTP integration, documentation, and CI alignment

**Files:**
- Create: `tests/test_http_integration.py`
- Modify: `README.md`, `docs/architecture.md`, `docs/data-model.md`, `CHANGELOG.md`
- Modify: `.github/workflows/ci.yml`, `pyproject.toml`

**Interfaces:**
- Test helpers start each adapter with an ephemeral localhost port and a temporary SQLite database.
- CI must either test every declared Python version or declare the verified support floor as Python 3.11 consistently in package metadata/docs.
- README commands must show SQLite-default startup and explicit CSV compatibility commands.

- [ ] **Step 1: Add end-to-end tests for status, video list, Range playback, save, 409 conflict, 404, path escape, and prediction endpoints.**
- [ ] **Step 2: Run `python -m pytest -q tests/test_http_integration.py` and verify missing coverage/failures.**
- [ ] **Step 3: Implement adapter fixes needed by the tests, align `requires-python` and CI matrix, and rewrite garbled/ambiguous setup instructions in UTF-8.**
- [ ] **Step 4: Run compileall, full pytest, ruff, and mypy.**
- [ ] **Step 5: Commit `test: cover db http workflow and align ci support`.**

## Final verification

- [ ] Run `python -m compileall video_labeler video_event_labeler.py person_identity_labeler.py run_video_annotation.py`.
- [ ] Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider`.
- [ ] Run `ruff check video_labeler`.
- [ ] Run `mypy video_labeler --exclude 'video_labeler/(storage|services|quality)' --cache-dir .mypy-cache`.
- [ ] Run `git diff --check` and inspect `git status --short`.
- [ ] Push and inspect the newest GitHub Actions run; report any failure rather than relying on local results.

