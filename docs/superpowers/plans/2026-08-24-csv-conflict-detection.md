# CSV 外部修改冲突检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在保存标注前检测 CSV 是否被外部修改，冲突时返回 409 且绝不覆盖外部内容。

**Architecture:** `AppState` 缓存 CSV SHA-256 revision，并提供锁内刷新/读取方法。API status 将 revision 交给浏览器，更新请求携带客户端 revision；服务端在现有 `_update_row` 锁内做前置比较，成功写入后返回新 revision，前端处理 409 并保留 dirty 状态。

**Tech Stack:** Python 标准库 `hashlib`、现有 `unittest`/`http.server`、内嵌原生 JavaScript。

## Global Constraints

- 不增加第三方依赖或数据库。
- 冲突时不调用 `write_csv_atomic`，不创建备份，不清除未保存前端状态。
- 缺少 `csv_revision` 的旧客户端保持兼容；新页面必须发送 revision。
- 先写失败测试并确认 RED，再修改生产代码。

---

### Task 1: 锁定服务端冲突合约

**Files:**
- Modify: `test_video_event_labeler.py`，在 `ApiTests` 附近添加 revision/409 测试。

**Interfaces:**
- Consumes: `AppState.status()`, `/api/update`, existing `post_update()` helper.
- Produces: expected `csv_revision` response field and `409` conflict behavior.

- [ ] **Step 1: Write failing tests**

添加以下测试行为：

```python
def test_status_exposes_csv_revision(self):
    revision = self.state.status()["csv_revision"]
    self.assertRegex(revision, r"^[0-9a-f]{64}$")

def test_stale_revision_returns_409_without_overwriting_external_change(self):
    revision = self.state.status()["csv_revision"]
    rows, fields = labeler.read_csv_rows(self.manifest, "utf-8-sig")
    rows[0]["lighting"] = "external-edit"
    with self.manifest.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "sample_id": rows[0]["sample_id"],
        "video_path": rows[0]["video_path"],
        "person_tag_list": "stranger",
        "events": [{"event_type": "person_fall", "start_time_ms": None, "end_time_ms": None}],
        "review": False,
    }
    status, body = self.post_update({**payload, "csv_revision": revision})
    self.assertEqual((status, body["ok"]), (409, False))
    self.assertEqual(self.read_event_row()["lighting"], "external-edit")
```

- [ ] **Step 2: Run focused tests to verify RED**

Run: `python -m unittest test_video_event_labeler.ApiTests.test_status_exposes_csv_revision test_video_event_labeler.ApiTests.test_stale_revision_returns_409_without_overwriting_external_change -v`

Expected: FAIL because status has no revision and update does not reject stale revisions.

### Task 2: Implement revision-aware AppState and API

**Files:**
- Modify: `video_event_labeler.py` imports, `AppState`, `_update_row`, and `Handler.do_POST`.

**Interfaces:**
- Produces: `AppState.csv_revision()` returning a 64-character SHA-256 string; `status()` field `csv_revision`; `CsvConflictError`; update response field `csv_revision`.

- [ ] **Step 1: Add SHA-256 revision calculation**

Use `hashlib.sha256()` and 1 MiB reads. Cache the digest in `AppState._revision_cache`; when a forced revision check differs, reread/validate the CSV before continuing.

- [ ] **Step 2: Add locked update precondition**

When payload `csv_revision` is a string, compare it to the refreshed current revision before selecting/mutating a row. Raise `CsvConflictError` on mismatch. Keep absent-token requests compatible.

- [ ] **Step 3: Return HTTP 409 and new revision**

Catch `CsvConflictError` before generic value errors and send `409` with `{"ok": false, "error": "CSV was modified externally; reload before saving"}`. Include the new digest in successful update responses.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run: `python -m unittest test_video_event_labeler.ApiTests.test_status_exposes_csv_revision test_video_event_labeler.ApiTests.test_stale_revision_returns_409_without_overwriting_external_change -v`

Expected: PASS.

### Task 3: Thread revision through the browser client

**Files:**
- Modify: `video_event_labeler.py` embedded HTML (`request`, `buildPayload`, `load`, `save`).
- Modify: `test_video_event_labeler.py` HTML contract tests.

**Interfaces:**
- Consumes: `/api/status.csv_revision`, `/api/update.csv_revision` and HTTP status on errors.
- Produces: every new-page update payload includes `csv_revision`; successful save advances it; 409 preserves dirty state and displays conflict text.

- [ ] **Step 1: Write failing HTML contract assertions**

Assert markers for `csvRevision`, `payload.csv_revision=csvRevision`, `result.csv_revision`, `error.status=409`, and the conflict message.

- [ ] **Step 2: Run focused HTML test to verify RED**

Run: `python -m unittest test_video_event_labeler.HtmlContractTests.test_browser_csv_conflict_handling_is_exposed -v`

Expected: FAIL because the browser has no revision state or 409 branch.

- [ ] **Step 3: Implement the minimal browser changes**

Add a `csvRevision` variable, set it from status, include it in both simple/events payloads, update it after successful save, and attach `status` to request errors so `save()` can handle 409 without setting `dirty=false`.

- [ ] **Step 4: Run focused HTML test to verify GREEN**

Run: `python -m unittest test_video_event_labeler.HtmlContractTests.test_browser_csv_conflict_handling_is_exposed -v`

Expected: PASS.

### Task 4: Full verification

**Files:**
- Verify: `video_event_labeler.py`, `test_video_event_labeler.py`, new spec/plan docs.

- [ ] **Step 1: Run all tests**

Run: `python -m unittest -q`

Expected: all tests pass.

- [ ] **Step 2: Compile Python sources**

Run: `python -m py_compile video_event_labeler.py test_video_event_labeler.py`

Expected: exit code 0.

- [ ] **Step 3: Check effective HTML and JavaScript**

Run: `python -B -c "import re,subprocess,video_event_labeler as m; s=re.findall(r'<script>(.*?)</script>',m.HTML,re.S); r=[subprocess.run(['node','--check'],input=x,text=True,capture_output=True) for x in s]; print(len(s), [x.returncode for x in r], m.HTML.count('function renderList('), m.HTML.count('function openRow('))"`

Expected: six scripts, all return codes `0`, core function counts remain `1 1`, and `const originalRequest=request` appears before the standalone initial `load();`.
