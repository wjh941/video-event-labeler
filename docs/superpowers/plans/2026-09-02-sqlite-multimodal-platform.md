# SQLite Multimodal Annotation Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有行为标注器和人物标注器改造成以 SQLite 为内部主存储、兼容 CSV 导入导出的本地多模态数据标注平台。

**Architecture:** 新增 `video_labeler` Python 包，将领域模型、SQLite repository、CSV 适配器、媒体索引、质量检查和 HTTP API 分层。现有三个脚本保留为兼容入口，页面通过 application service 操作 SQLite，不再直接把 CSV 当作运行时数据库。视频、音频、文本、事件、人物、模型预测和证据使用可扩展的关系表关联。

**Tech Stack:** Python 3.10+ standard library、SQLite 3、内置 `http.server`、JSON/CSV、可选外部 `ffprobe`（存在时读取媒体元数据，不存在时降级为未知）、pytest、Ruff、Mypy、GitHub Actions。

## Global Constraints

- SQLite 是程序内部唯一数据源；CSV 仅用于兼容导入/导出。
- 新 CSV 不强制增加列；导出同时生成同名 `.meta.json` 旁车文件记录 `schema_version` 和数据库 revision。
- 人员字段使用 `person_count` 与 `person_identity_attributes`，允许人员数量为 `0`，不生成新的 `person_tag_list`。
- 年龄段只允许 `child`、`adult`、`elderly`、`unknown`。
- 人脸和体态熟悉度只允许 `familiar`、`stranger`、`unknown`、`not_visible`。
- 不生成新视频文件；事件回看使用原始媒体的时间区间和 HTTP Range 响应。
- 默认 HTTP 只绑定 `127.0.0.1`；所有媒体路径必须位于配置的视频根目录内。
- 写操作必须使用 SQLite 事务；CSV 导出必须使用临时文件、`fsync`、原子替换和备份。
- 运行时不增加必需的第三方 Python 依赖；测试工具只用于开发环境。
- 旧 CSV 的 `person_tag_list` 只在迁移适配器中识别，迁移后从新模型和新 CSV 中移除。

---

### Task 1: 建立包结构、领域模型和 schema migration

**Files:**
- Create: `video_labeler/__init__.py`
- Create: `video_labeler/domain.py`
- Create: `video_labeler/schema.py`
- Create: `tests/conftest.py`
- Create: `tests/test_domain.py`
- Create: `tests/test_schema.py`
- Modify: `pyproject.toml`

**Interfaces:**
- `domain.py` 提供 `Sample`, `MediaAsset`, `Event`, `Person`, `Evidence`, `Prediction` dataclass，以及枚举常量。
- `schema.py` 提供 `CURRENT_SCHEMA_VERSION: int`, `initialize_schema(connection) -> None`, `migrate_schema(connection, target_version: int = CURRENT_SCHEMA_VERSION) -> None`。
- `schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)` 记录已执行迁移。
- `tests/conftest.py` 提供 `store(tmp_path) -> SQLiteStore` fixture，以及 `adult_person(person_id="p1")` 和 `fall_event()` 测试工厂，后续任务直接复用这些确定性夹具。

- [ ] **Step 1: Write failing domain tests**

```python
def test_person_requires_declared_enums():
    with pytest.raises(ValueError):
        Person(sample_id="s1", person_id="p1", age_group="teen")

def test_event_end_must_be_after_start():
    with pytest.raises(ValueError):
        Event(sample_id="s1", event_type="fall", start_time_ms=20, end_time_ms=10)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_domain.py tests/test_schema.py`

Expected: collection fails because `video_labeler` and the requested types do not exist.

- [ ] **Step 3: Implement typed models and constraints**

Implement frozen dataclasses with explicit defaults and validation. `Person` must contain `person_id`, `age_group`, `face_familiarity`, and `body_reid_familiarity`; `person_count` is derived from stored persons and is never independently trusted. `Event` accepts nullable times only for draft records and rejects negative values or `end_time_ms < start_time_ms`. Add the `store`, `adult_person`, and `fall_event` fixtures exactly as named in the interfaces block.

```python
import pytest
from video_labeler.domain import Event, Person

@pytest.fixture
def store(tmp_path):
    from video_labeler.storage.sqlite_store import SQLiteStore
    return SQLiteStore(tmp_path / "dataset.db")

def adult_person(person_id="p1"):
    return Person(person_id=person_id, sample_id="s1", age_group="adult", face_familiarity="stranger", body_reid_familiarity="unknown")

def fall_event():
    return Event(sample_id="s1", event_type="person_fall", start_time_ms=100, end_time_ms=200)
```

- [ ] **Step 4: Implement schema v1**

Create tables `datasets`, `samples`, `media_assets`, `events`, `persons`, `evidence`, `model_predictions`, and `annotation_revisions` with foreign keys, indexes on `sample_id`, `status`, and `(modality, uri)`. Enable `PRAGMA foreign_keys=ON` and store timestamps as UTC ISO-8601 text.

- [ ] **Step 5: Add packaging metadata and rerun tests**

Add a `pyproject.toml` with Python `>=3.10`, a package discovery rule, and development commands for `pytest`, `ruff`, and `mypy` without runtime dependencies.

Run: `python -m pytest -q tests/test_domain.py tests/test_schema.py`

Expected: all domain and schema tests pass.

- [ ] **Step 6: Commit**

```powershell
git add video_labeler pyproject.toml tests/test_domain.py tests/test_schema.py
git commit -m "feat: add typed multimodal domain and schema migrations"
```

### Task 2: Implement transactional SQLite repository

**Files:**
- Create: `video_labeler/storage/__init__.py`
- Create: `video_labeler/storage/sqlite_store.py`
- Create: `tests/test_sqlite_store.py`

**Interfaces:**
- `SQLiteStore(path: Path)` opens the database, configures WAL and foreign keys, and calls `migrate_schema`.
- `SQLiteStore.transaction() -> ContextManager[sqlite3.Connection]` starts `BEGIN IMMEDIATE`, commits on success, and rolls back on exception.
- `SQLiteStore.connection() -> sqlite3.Connection` exposes a read-only connection for diagnostics; all writes still go through `transaction()`.
- `upsert_sample(sample: Sample) -> None`, `get_sample(sample_id: str) -> Sample | None`, `list_samples(limit: int, offset: int, status: str | None = None) -> list[Sample]`.
- `replace_events(sample_id: str, events: Sequence[Event], expected_revision: int | None) -> int` and `replace_persons(sample_id: str, people: Sequence[Person], expected_revision: int | None) -> int` return the new sample revision and reject stale revisions with `ConflictError`.
- `get_events(sample_id: str) -> list[Event]`, `get_persons(sample_id: str) -> list[Person]`, and `sample_revision(sample_id: str) -> int`.

- [ ] **Step 1: Write failing repository tests**

```python
def test_transaction_rolls_back_on_error(tmp_path):
    store = SQLiteStore(tmp_path / "dataset.db")
    with pytest.raises(RuntimeError):
        with store.transaction() as connection:
            connection.execute("INSERT INTO datasets(dataset_id, root_path) VALUES (?, ?)", ("d1", "."))
            raise RuntimeError("abort")
    assert store.connection().execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 0

def test_stale_revision_does_not_overwrite_people(tmp_path):
    store = make_store_with_sample(tmp_path)
    revision = store.sample_revision("s1")
    store.replace_persons("s1", [adult_person("p1")], revision)
    with pytest.raises(ConflictError):
        store.replace_persons("s1", [], revision)
    assert len(store.get_persons("s1")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_sqlite_store.py`

Expected: import or method failures because the repository is not implemented.

- [ ] **Step 3: Implement connection lifecycle and transactions**

Use one connection per store guarded by an `RLock`; set `busy_timeout=5000`, `journal_mode=WAL`, `synchronous=FULL`, and `foreign_keys=ON`. Expose `close()` and make `transaction()` the only write path.

- [ ] **Step 4: Implement CRUD and optimistic revision checks**

Store sample revisions as integers incremented inside the same transaction as event/person replacement. Compare `expected_revision` under `BEGIN IMMEDIATE`; raise `ConflictError` before deleting or inserting any records. Use `executemany` for batch imports.

- [ ] **Step 5: Add process-level lock coverage**

Create `video_labeler/storage/file_lock.py` using a lock file and Windows `msvcrt.locking` with a portable fallback. Acquire it around database migration and CSV export. Test two threads/processes attempting the same lock and verify the second waits or receives a bounded timeout.

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest -q tests/test_sqlite_store.py`

Expected: transaction, revision, locking, and CRUD tests pass.

```powershell
git add video_labeler/storage tests/test_sqlite_store.py
git commit -m "feat: add transactional sqlite repository"
```

### Task 3: Add CSV import/export compatibility and legacy migration

**Files:**
- Create: `video_labeler/storage/csv_adapter.py`
- Create: `tests/test_csv_adapter.py`
- Modify: `video_labeler/schema.py`
- Modify: `README.md`

**Interfaces:**
- `import_csv(path: Path, store: SQLiteStore, video_root: Path) -> ImportReport`.
- `export_csv(store: SQLiteStore, path: Path, video_root: Path) -> ExportReport`.
- `ImportReport` contains `created`, `updated`, `skipped`, `stale`, and `errors` counts.
- `export_csv` writes `<path>.meta.json` with `schema_version`, `exported_at`, `database_revision`, and `sample_count`.

- [ ] **Step 1: Write failing round-trip and migration tests**

```python
def test_legacy_person_tag_is_removed_and_people_are_empty(tmp_path, store):
    csv_path = tmp_path / "legacy.csv"
    csv_path.write_text("sample_id,video_path,person_tag_list,events\\ns1,a.mp4,stranger,[]\\n", encoding="utf-8-sig")
    report = import_csv(csv_path, store, tmp_path)
    assert report.created == 1
    assert store.get_persons("s1") == []

def test_export_preserves_event_and_person_semantics(tmp_path, store):
    report = export_csv(store, tmp_path / "out.csv", tmp_path)
    with (tmp_path / "out.csv").open(newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    assert json.loads(row["person_identity_attributes"]) == [{"person_id": "p1", "age_group": "adult", "face_familiarity": "stranger", "body_reid_familiarity": "unknown"}]
    assert (tmp_path / "out.csv.meta.json").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_csv_adapter.py`

Expected: adapter functions and `ImportReport` are missing.

- [ ] **Step 3: Implement deterministic sample identity and import**

Normalize paths relative to `video_root`, compute SHA-256 in 1 MiB chunks, and derive `sample_id` from normalized relative path plus hash. Repeated imports must update metadata for the same source and never duplicate a sample. Preserve unknown CSV columns in an `extra_json` field.

- [ ] **Step 4: Implement event/person JSON conversion**

Parse the existing `events` JSON format and `person_identity_attributes`; normalize missing values to declared defaults; derive `person_count`; reject invalid JSON only when the row claims to be reviewed, otherwise add an import error and keep the row as draft. Recognize `person_tag_list` only to discard it during migration.

- [ ] **Step 5: Implement crash-safe export**

Acquire the export lock, create a timestamped backup, write UTF-8 with BOM to a sibling temporary file, flush and `os.fsync`, then call `os.replace`. Preserve the established ten-column order and unknown columns after the known columns. Write the `.meta.json` sidecar with the database revision.

- [ ] **Step 6: Run tests, update compatibility documentation, and commit**

Run: `python -m pytest -q tests/test_csv_adapter.py test_integration_workflow.py`

Expected: legacy migration, idempotent import, round-trip export, backup, and sidecar tests pass.

```powershell
git add video_labeler/storage/csv_adapter.py video_labeler/schema.py tests/test_csv_adapter.py README.md
git commit -m "feat: add csv compatibility adapter and migrations"
```

### Task 4: Index media assets and expose safe metadata

**Files:**
- Create: `video_labeler/media.py`
- Create: `tests/test_media.py`
- Modify: `video_labeler/storage/sqlite_store.py`

**Interfaces:**
- `iter_video_files(root: Path) -> Iterator[Path]` yields supported files in deterministic order.
- `sha256_file(path: Path) -> str` hashes streams without loading the full file.
- `probe_media(path: Path, ffprobe_path: Path | None = None) -> MediaMetadata` returns duration, fps, width, height, audio presence, and a `probe_status`.
- `is_safe_media_path(root: Path, candidate: Path) -> bool` rejects paths outside the configured root after `resolve()`.

- [ ] **Step 1: Write failing media tests**

```python
def test_scan_is_deterministic_and_ignores_unknown_extensions(tmp_path):
    (tmp_path / "b.mp4").touch()
    (tmp_path / "a.mkv").touch()
    (tmp_path / "ignore.txt").touch()
    assert [p.name for p in iter_video_files(tmp_path)] == ["a.mkv", "b.mp4"]

def test_path_traversal_is_rejected(tmp_path):
    assert not is_safe_media_path(tmp_path, (tmp_path / ".." / "outside.mp4"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_media.py`

Expected: missing media functions.

- [ ] **Step 3: Implement deterministic scanning and hashing**

Support `.mp4`, `.avi`, `.mov`, `.mkv`, `.webm`, `.m4v`; sort by normalized relative path; return `stale` when an existing hash changes. Never follow a resolved path outside the root.

- [ ] **Step 4: Implement optional metadata probing**

If `ffprobe` is configured and exits successfully, parse its JSON output. If it is absent, times out, or returns malformed output, return `probe_status="unavailable"` and leave unknown fields null; do not fail the entire import.

- [ ] **Step 5: Persist media assets and run tests**

Add `SQLiteStore.upsert_media_asset(asset: MediaAsset) -> None` and `get_media_assets(sample_id: str) -> list[MediaAsset]`. Run `python -m pytest -q tests/test_media.py tests/test_sqlite_store.py` and commit.

```powershell
git add video_labeler/media.py video_labeler/storage/sqlite_store.py tests/test_media.py
git commit -m "feat: index media assets with safe metadata probing"
```

### Task 5: Add application services and migrate script backends

**Files:**
- Create: `video_labeler/services.py`
- Create: `tests/test_services.py`
- Modify: `video_event_labeler.py`
- Modify: `person_identity_labeler.py`
- Modify: `run_video_annotation.py`

**Interfaces:**
- `AnnotationService(store: SQLiteStore, video_root: Path)` exposes `list_rows(offset, limit, filters)`, `get_row(sample_id)`, `save_events(sample_id, events, expected_revision)`, `save_people(sample_id, people, expected_revision)`, and `export_csv(path)`.
- Returned row payload keeps current browser keys (`sample_id`, `video_url`, `behaviors`, `person_identity_attributes`, `csv_revision`) so existing UI behavior remains compatible during migration.
- `video_event_labeler.py` and `person_identity_labeler.py` become thin HTTP/HTML adapters; they must not call CSV serialization directly.

- [ ] **Step 1: Write failing service contract tests**

```python
def test_event_save_and_person_save_share_one_revision(tmp_path):
    service = make_service(tmp_path)
    first = service.save_events("s1", [fall_event()], expected_revision=0)
    second = service.save_people("s1", [adult_person("p1")], expected_revision=first.revision)
    assert second.revision == first.revision + 1
    assert service.get_row("s1").person_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_services.py`

Expected: `AnnotationService` is missing and current scripts still own persistence.

- [ ] **Step 3: Implement service row projection**

Build one normalized payload from SQLite, including safe `video_url` identifiers rather than raw filesystem paths. Keep `person_count` derived from people and preserve read-only event data in the person view.

- [ ] **Step 4: Adapt the event HTTP API**

Map `/api/status`, `/api/videos`, `/api/update`, and `/video/<sample_id>` to the service. Return HTTP `409` for stale revisions and `404` for missing media with ASCII protocol reasons and UTF-8 JSON details.

- [ ] **Step 5: Adapt the person HTTP API**

Map `/api/state`, `/api/save`, and `/video?sample_id=...` to the same service. Save only people; do not accept event mutations from this endpoint. Keep Range support and stop serving arbitrary paths.

- [ ] **Step 6: Update the combined launcher and run integration tests**

Make `run_video_annotation.py` initialize or migrate the database once, then pass `--db` and `--video-root` to both adapters. Keep `--person-only`, `--no-browser`, and configurable ports.

Run: `python -m pytest -q tests/test_services.py test_integration_workflow.py test_person_identity_labeler.py test_video_event_labeler.py`

Expected: old CSV workflow tests and new SQLite service tests pass together.

- [ ] **Step 7: Commit**

```powershell
git add video_labeler/services.py video_event_labeler.py person_identity_labeler.py run_video_annotation.py tests/test_services.py
git commit -m "feat: route annotation scripts through sqlite services"
```

### Task 6: Add evidence, model-provider contracts, and human confirmation

**Files:**
- Create: `video_labeler/providers.py`
- Create: `video_labeler/evidence.py`
- Create: `tests/test_providers.py`
- Create: `tests/test_evidence.py`
- Modify: `video_labeler/services.py`

**Interfaces:**
- `AnnotationProvider` protocol: `predict(sample: Sample) -> Sequence[Prediction]`.
- `MockAnnotationProvider` returns deterministic predictions for demonstrations and tests.
- `EvidenceService.attach(evidence: Evidence) -> None`, `list_for_sample(sample_id: str) -> list[Evidence]`.
- `AnnotationService.accept_prediction(prediction_id: str, annotator: str) -> int` copies a prediction into final events/persons in one transaction and records a revision.

- [ ] **Step 1: Write failing provider and provenance tests**

```python
def test_prediction_keeps_model_provenance():
    prediction = MockAnnotationProvider().predict(sample)[0]
    assert prediction.model_name == "mock"
    assert prediction.model_version
    assert 0 <= prediction.confidence <= 1

def test_accepting_prediction_creates_revision(tmp_path):
    service = make_service(tmp_path, provider=MockAnnotationProvider())
    prediction_id = service.predict("s1")[0].prediction_id
    service.accept_prediction(prediction_id, annotator="demo")
    assert service.revisions("s1")[-1].actor == "demo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_providers.py tests/test_evidence.py`

Expected: provider and evidence interfaces are undefined.

- [ ] **Step 3: Implement provider and evidence persistence**

Persist predictions separately from final labels. Require source, model version, confidence, and evidence references for every prediction. Evidence may reference video time ranges, audio ranges, images, or text; missing modalities remain valid.

- [ ] **Step 4: Implement accept/reject transitions**

Add `accept_prediction` and `reject_prediction` operations, each recording actor, timestamp, application version, and before/after JSON summary in `annotation_revisions`. A rejected prediction must never appear as a final event or person.

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest -q tests/test_providers.py tests/test_evidence.py tests/test_services.py`

```powershell
git add video_labeler/providers.py video_labeler/evidence.py video_labeler/services.py tests/test_providers.py tests/test_evidence.py
git commit -m "feat: add multimodal evidence and model provenance"
```

### Task 7: Build validation, statistics, and ML-ready exports

**Files:**
- Create: `video_labeler/quality.py`
- Create: `video_labeler/cli.py`
- Create: `tests/test_quality.py`
- Create: `tests/test_exports.py`
- Modify: `video_labeler/__init__.py`
- Modify: `README.md`

**Interfaces:**
- `validate_dataset(store: SQLiteStore) -> QualityReport` with error and warning records containing `sample_id`, field, code, and message.
- `dataset_stats(store: SQLiteStore) -> dict[str, int | float]`.
- `export_jsonl(store: SQLiteStore, path: Path) -> ExportReport`.
- CLI commands: `python -m video_labeler validate --db dataset.db`, `stats`, and `export --format jsonl`.

- [ ] **Step 1: Write failing quality and export tests**

```python
def test_quality_report_flags_event_outside_duration(tmp_path):
    report = validate_dataset(make_store_with_bad_event(tmp_path))
    assert {item.code for item in report.errors} == {"event_out_of_bounds"}

def test_jsonl_export_contains_modalities_and_provenance(tmp_path):
    export_jsonl(store, tmp_path / "train.jsonl")
    record = json.loads((tmp_path / "train.jsonl").read_text().splitlines()[0])
    assert {"sample", "media", "events", "persons", "evidence", "provenance"} <= record.keys()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest -q tests/test_quality.py tests/test_exports.py`

Expected: quality and CLI/export functions are missing.

- [ ] **Step 3: Implement cross-field validation**

Check media existence and probe status, event bounds when duration is known, positive event duration, unique person IDs, enum values, prediction review state, stale source hashes, and missing required metadata. Separate blocking errors from non-blocking warnings.

- [ ] **Step 4: Implement statistics and JSONL export**

Compute sample counts, duration totals, modality availability, behavior distribution, age/familiarity distribution, completion rate, and error rate. Write one deterministic JSON object per sample with nested events, persons, evidence, and provenance.

- [ ] **Step 5: Wire CLI and document commands**

Use `argparse` subcommands with exit code `0` for no errors and `1` for quality errors. Add examples and output interpretation to README.

- [ ] **Step 6: Run tests and commit**

Run: `python -m pytest -q tests/test_quality.py tests/test_exports.py tests/test_integration_workflow.py`

```powershell
git add video_labeler/quality.py video_labeler/cli.py video_labeler/__init__.py tests/test_quality.py tests/test_exports.py README.md
git commit -m "feat: add dataset quality checks and jsonl exports"
```

### Task 8: Improve browser workflow without expanding persistence responsibilities

**Files:**
- Modify: `person_identity_labeler.py`
- Modify: `video_event_labeler.py`
- Create: `tests/test_ui_contracts.py`

**Interfaces:**
- Both pages continue to consume the service payload and send `csv_revision`/sample revision tokens.
- Person page keeps `person_count=0`, structured attributes, row-specific video, and read-only event segment playback.

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_pages_expose_unsaved_state_and_revision_handling():
    assert "csv_revision" in EVENT_HTML
    assert "csv_revision" in PERSON_HTML
    assert "409" in PERSON_HTML or "CSV" in PERSON_HTML

def test_person_page_has_quality_and_resume_hooks():
    assert "person_identity_attributes" in PERSON_HTML
    assert "playEventSegment" in PERSON_HTML
    assert "localStorage" in PERSON_HTML
```

- [ ] **Step 2: Implement minimal workflow improvements**

Add an explicit unsaved indicator, local last-sample resume, disabled save buttons while requests are active, retry after transient network errors, and a quality-warning badge sourced from the API. Preserve read-only event controls on the person page.

- [ ] **Step 3: Test browser contracts and HTTP behavior**

Run: `python -m pytest -q tests/test_ui_contracts.py tests/test_services.py`; manually verify row switching, segment playback, zero-person save, stale revision response, and missing-video 404 with a temporary dataset.

- [ ] **Step 4: Commit**

```powershell
git add person_identity_labeler.py video_event_labeler.py tests/test_ui_contracts.py
git commit -m "feat: improve annotation resume and save feedback"
```

### Task 9: Add CI, static checks, fault tests, and performance benchmark

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `tests/test_fault_recovery.py`
- Create: `tests/test_performance.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

**Interfaces:**
- CI runs on Windows and Ubuntu with Python 3.10 and 3.11.
- `python -m pytest -q` remains the documented test command.

- [ ] **Step 1: Write fault and benchmark tests**

```python
def test_interrupted_csv_export_keeps_original(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "replace", fail_once)
    with pytest.raises(OSError):
        export_csv(store, tmp_path / "manifest.csv", tmp_path)
    assert original_csv.read_bytes() == original_bytes

def test_hundred_thousand_sample_query_is_bounded(tmp_path):
    seed_samples(SQLiteStore(tmp_path / "dataset.db"), 100_000)
    elapsed = timed(lambda: store.list_samples(limit=100, offset=50_000))
    assert elapsed < 5.0
```

- [ ] **Step 2: Configure tooling**

Configure Ruff for `E`, `F`, `I`, and `UP` rules, Mypy in non-strict mode for `video_labeler`, and pytest coverage output. Exclude archived `old/` scripts from linting while keeping them in the repository.

- [ ] **Step 3: Add GitHub Actions matrix**

Run checkout, setup-python, `python -m pip install pytest ruff mypy`, `python -m compileall`, Ruff, Mypy, and pytest. Upload the coverage XML as an artifact on failure or success.

- [ ] **Step 4: Run local checks and commit**

Run:

```powershell
python -m compileall video_labeler video_event_labeler.py person_identity_labeler.py run_video_annotation.py
ruff check video_labeler tests
mypy video_labeler
python -m pytest -q
```

Expected: all checks pass, the fault test proves the original export remains intact, and the 100k-row pagination benchmark stays below two seconds on the development machine.

```powershell
git add .github/workflows/ci.yml tests/test_fault_recovery.py tests/test_performance.py pyproject.toml README.md
git commit -m "ci: add static checks fault tests and benchmark"
```

### Task 10: Prepare interview-grade documentation and release demo

**Files:**
- Create: `docs/architecture.md`
- Create: `docs/data-model.md`
- Create: `docs/demo_dataset/README.md`
- Create: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `PRODUCT.md`

**Interfaces:**
- Documentation must describe the actual CLI flags and API behavior implemented by Tasks 1-9.
- Demo data must not contain private or personally identifying media.

- [ ] **Step 1: Write documentation checks**

```python
def test_documented_commands_exist():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "python -m video_labeler validate" in readme
    assert "run_video_annotation.py" in readme
```

- [ ] **Step 2: Document architecture and data lineage**

Include one component diagram, one ingest-to-export data-flow diagram, table definitions, migration rules, revision conflict behavior, security boundary, and the human-versus-model acceptance flow.

- [ ] **Step 3: Add reproducible demo instructions**

Document how to create a small synthetic dataset, run import, inspect model proposals, confirm annotations, run validation, export JSONL/CSV, and reproduce the benchmark. Do not require proprietary models or private media.

- [ ] **Step 4: Add release notes and verify the clean checkout**

Record schema version, migration compatibility, test count, benchmark machine assumptions, and known limitations. Run the complete CI command set from Task 9 in a clean checkout and verify `git status --short` is empty.

- [ ] **Step 5: Commit**

```powershell
git add docs/architecture.md docs/data-model.md docs/demo_dataset/README.md CHANGELOG.md README.md PRODUCT.md
git commit -m "docs: prepare multimodal annotation platform demo"
```

## Review Checkpoints

- After Task 2: verify SQLite transactions and conflict behavior before migrating either UI.
- After Task 3: run a real copy of the existing manifest through import/export and compare event/person semantics.
- After Task 5: manually run both existing browser workflows against SQLite before adding model features.
- After Task 7: inspect quality report and JSONL output with a sample dataset.
- After Task 9: require CI green before claiming production-grade stability.

## Final Acceptance

- Existing event and person pages operate through SQLite without direct CSV writes.
- Legacy CSVs import without losing events or unknown columns; new exports contain no `person_tag_list`.
- Repeated directory imports are idempotent and detect replaced media by SHA-256.
- Concurrent or stale saves return a conflict and never silently overwrite newer data.
- A sample can link video, optional audio/transcript, events, persons, evidence, and model predictions with provenance.
- Validation, stats, CSV export, and JSONL export are reproducible from documented commands.
- Automated tests cover migrations, transactions, HTTP APIs, path safety, crash recovery, and a 100k-row pagination benchmark.
