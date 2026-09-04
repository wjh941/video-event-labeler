# Pagination, Prediction Review, and Quality Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scalable row pagination, an in-browser model-prediction review workflow, and a live dataset-quality dashboard to both annotation adapters without breaking existing CSV/SQLite workflows.

**Architecture:** Keep `SQLiteStore` and `AnnotationService` as the source of data and validation. Add optional query parameters to existing row endpoints for pagination/filtering, plus dedicated `/api/predictions` and `/api/quality` endpoints. The embedded browser pages consume these APIs incrementally; existing no-parameter endpoints retain their current response shape for compatibility.

**Tech Stack:** Python 3.11 standard library, `sqlite3`, `http.server`, embedded browser JavaScript, pytest, Ruff, mypy, GitHub Actions.

## Global Constraints

- Runtime dependencies remain empty; do not add a frontend framework or server dependency.
- Python support remains `>=3.11` as declared in `pyproject.toml`.
- SQLite remains the source of truth; CSV remains an explicit compatibility format.
- `person_identity_attributes` remains the only structured person field; do not reintroduce `person_tag_list`.
- Existing no-argument `/api/videos` and `/api/state` responses remain backward compatible.
- Writes continue through existing revision checks, audit records, atomic backups, and path validation.
- All new behavior requires regression tests before implementation and must pass full CI checks.

---

### Task 1: Add paginated row and aggregate service contracts

**Files:**
- Modify: `video_labeler/services.py`
- Modify: `video_labeler/storage/sqlite_store.py`
- Test: `tests/test_services.py`, `tests/test_sqlite_store.py`

**Interfaces:**
- Add `AnnotationService.count_rows(status: str | None = None) -> int`.
- Extend `AnnotationService.list_rows(offset: int = 0, limit: int = 100, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]` with validated non-negative offset and positive bounded limit.
- Add `AnnotationService.list_prediction_records(status: str | None = None, task: str | None = None, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]` returning prediction metadata plus decoded label JSON when valid.
- Add `AnnotationService.quality_snapshot(mode: str = "draft") -> dict[str, Any]` returning `stats`, `quality`, and `generated_at` keys.
- Add SQLite count/list helpers that use parameterized SQL and deterministic ordering.

- [ ] **Step 1: Write failing service/store tests**

  Cover count by status, offset/limit boundaries, deterministic row order, prediction filtering, and quality snapshot keys. Assert invalid limits raise `ValueError`.

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run: `python -m pytest -q tests/test_services.py tests/test_sqlite_store.py -k "pagination or prediction_records or quality_snapshot"`

- [ ] **Step 3: Implement the minimal storage/service methods**

  Keep SQL sorting by `sample_id`/`prediction_id`, cap limits at 500, and call existing `dataset_stats()` and `validate_dataset()` rather than duplicating validation logic.

- [ ] **Step 4: Run focused tests and verify they pass**

  Run the same command; expected result: all new tests pass.

- [ ] **Step 5: Commit**

  Commit message: `feat: add paginated annotation service contracts`

### Task 2: Add backward-compatible quality and prediction HTTP APIs

**Files:**
- Modify: `video_event_labeler.py`
- Modify: `person_identity_labeler.py`
- Test: `tests/test_http_integration.py`, `tests/test_ui_predictions.py`

**Interfaces:**
- Event adapter: add `GET /api/videos?offset=<n>&limit=<n>&status=<status>&q=<text>` with the existing array response plus `X-Total-Count` and `X-Page-Offset` headers when query parameters are present.
- Person adapter: add equivalent optional pagination query parameters to `/api/state`; preserve the current full-state response when absent.
- Both adapters: add `GET /api/predictions?status=draft|accepted|rejected&task=event|person&offset=<n>&limit=<n>` returning `{items, total, offset, limit}`.
- Both adapters: add `GET /api/quality?mode=draft|strict` returning the service quality snapshot.
- Prediction accept/reject routes continue accepting `actor` and `expected_revision`; malformed query values return HTTP 400.

- [ ] **Step 1: Write failing HTTP tests**

  Assert pagination headers/metadata, search filtering, prediction list filtering, quality response shape, 400 for invalid query values, and unchanged no-query response shapes.

- [ ] **Step 2: Run focused HTTP tests and verify failure**

  Run: `python -m pytest -q tests/test_http_integration.py tests/test_ui_predictions.py -k "pagination or quality or prediction"`

- [ ] **Step 3: Implement query parsing and routes**

  Use one shared integer-query helper per adapter, reject negative offsets and limits outside `1..500`, URL-decode search text, and never expose filesystem paths beyond fields already returned by the adapters.

- [ ] **Step 4: Run focused HTTP tests and verify pass**

  Run the same command; expected result: all new route tests pass.

- [ ] **Step 5: Commit**

  Commit message: `feat: expose quality prediction and paginated APIs`

### Task 3: Build the prediction review panel

**Files:**
- Modify: `video_event_labeler.py` embedded `HTML`
- Modify: `person_identity_labeler.py` embedded `HTML_PAGE`
- Test: `tests/test_ui_predictions.py`, `tests/test_ui_contracts.py`, `test_video_event_labeler.py`, `test_person_identity_labeler.py`

**Interfaces:**
- Add a prediction panel with `data-prediction-list`, task/status filters, confidence display, label JSON preview, and accept/reject actions.
- Browser calls `GET /api/predictions`, then `POST /api/predictions/<id>/accept` or `/reject` with `actor` and current revision.
- Accept refreshes the current row and quality summary; reject removes the card without changing annotations.

- [ ] **Step 1: Add failing HTML contract tests**

  Require prediction list loading, confidence/model rendering, accept/reject functions, revision propagation, and error handling markers in both pages.

- [ ] **Step 2: Run UI contract tests and verify failure**

  Run: `python -m pytest -q tests/test_ui_contracts.py test_video_event_labeler.py test_person_identity_labeler.py -k "prediction"`

- [ ] **Step 3: Implement the shared browser workflow in each embedded page**

  Keep controls compact, disable an action while its request is pending, show server conflict errors, and use existing `setStatus()`/`request()` helpers.

- [ ] **Step 4: Run UI tests and verify pass**

  Run the same command; expected result: all prediction contract tests pass.

- [ ] **Step 5: Commit**

  Commit message: `feat: add in-browser prediction review panels`

### Task 4: Replace full-list loading with paginated annotation navigation

**Files:**
- Modify: `video_event_labeler.py` embedded `HTML`
- Modify: `person_identity_labeler.py` embedded `HTML_PAGE`
- Test: `tests/test_ui_contracts.py`, `test_video_event_labeler.py`, `test_person_identity_labeler.py`

**Interfaces:**
- Add page size control (`50`, `100`, `200`), search input, status filter, previous/next page buttons, and a visible `page/total` indicator.
- Event page requests `/api/videos` with optional query parameters and uses response headers to retain total count.
- Person page requests `/api/state?offset=...&limit=...`; row saves continue sending the global `row_index`/`sample_id` and current revision.
- Existing keyboard previous/next row behavior remains within the current page and moves across page boundaries when possible.

- [ ] **Step 1: Add failing browser contract tests**

  Require query-string construction, page controls, total-count rendering, and refresh-after-save markers while preserving existing row selectors.

- [ ] **Step 2: Run the contract tests and verify failure**

  Run: `python -m pytest -q tests/test_ui_contracts.py test_video_event_labeler.py test_person_identity_labeler.py -k "page or search or filter"`

- [ ] **Step 3: Implement incremental row loading**

  Avoid rebuilding the entire list on every playback tick, reset selection safely after search/filter changes, preserve local drafts by `sample_id`, and keep empty/loading/error states visible.

- [ ] **Step 4: Run focused tests and verify pass**

  Run the same command; expected result: all pagination contract tests pass.

- [ ] **Step 5: Commit**

  Commit message: `feat: paginate annotation browser rows`

### Task 5: Build the quality dashboard

**Files:**
- Modify: `video_event_labeler.py` embedded `HTML`
- Modify: `person_identity_labeler.py` embedded `HTML_PAGE`
- Test: `tests/test_ui_contracts.py`, `tests/test_ui_recovery.py`, `tests/test_quality.py`

**Interfaces:**
- Add a quality panel showing sample count, reviewed/draft/rejected counts, completion rate, event/person totals, prediction totals, and error/warning counts.
- Add draft/strict mode selector and refresh action.
- Render the first page of quality issues with sample IDs; clicking an issue selects that sample when it is loaded.
- Refresh quality data after import, save, prediction accept/reject, and page/filter changes.

- [ ] **Step 1: Add failing dashboard contract and payload tests**

  Assert metric keys, strict/draft mode behavior, issue rendering, and refresh hooks.

- [ ] **Step 2: Run quality/UI tests and verify failure**

  Run: `python -m pytest -q tests/test_quality.py tests/test_ui_contracts.py -k "quality or dashboard"`

- [ ] **Step 3: Implement dashboard rendering and safe formatting**

  Format rates as percentages, durations as hours/minutes, cap issue rendering to 100 items, and escape all server-provided text through DOM APIs.

- [ ] **Step 4: Run focused tests and verify pass**

  Run the same command; expected result: all dashboard tests pass.

- [ ] **Step 5: Commit**

  Commit message: `feat: add live dataset quality dashboard`

### Task 6: Integration, documentation, and release verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`
- Test: all existing tests plus new integration coverage

**Interfaces:**
- Document pagination parameters, prediction review workflow, quality endpoint, and behavior when running in headless mode.
- Keep commands copyable on Windows PowerShell and Linux shells.

- [ ] **Step 1: Add end-to-end integration tests**

  Start each adapter with a temporary SQLite dataset, list a page, accept/reject a prediction, fetch quality, save an annotation, and verify the revision/quality counters change.

- [ ] **Step 2: Run the complete verification suite**

  Run:
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider`
  `ruff check --no-cache video_labeler`
  `mypy video_labeler --exclude 'video_labeler/(storage|services|quality)'`
  `python -B -m compileall video_labeler video_event_labeler.py person_identity_labeler.py run_video_annotation.py`
  `git diff --check`

- [ ] **Step 3: Update docs and changelog**

  Explain the default 100-row page, filters, prediction actions, strict/draft quality modes, and the fact that SQLite remains authoritative.

- [ ] **Step 4: Run the full suite again after documentation changes**

  Confirm no generated files or temporary media remain in the worktree.

- [ ] **Step 5: Commit and push**

  Commit message: `feat: scale annotation review and quality monitoring`

  After pushing, query the GitHub Actions run for the pushed SHA and report its conclusion.
