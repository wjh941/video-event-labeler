# 标注断点恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不改变后端和标注数据语义的前提下，让浏览器刷新后恢复上次标注位置、筛选器和播放速度。

**Architecture:** 在现有 `HTML` 的独立脚本片段中读取和校验一个版本化 `localStorage` JSON。脚本包装运行时最终生效的 `renderList()` 与 `openRow()`，在异步 `load()` 完成后恢复界面状态，并通过已有事件写回轻量导航快照。

**Tech Stack:** Python `unittest` 合约测试、内嵌原生 JavaScript、浏览器 `localStorage` API。

## Global Constraints

- 不增加第三方依赖或后端接口。
- 不把未保存的事件/标签值写入 localStorage。
- localStorage 失败必须静默回退，不能阻断标注。
- 先写失败测试，再写最小实现。

---

### Task 1: 锁定断点恢复 HTML 合约

**Files:**
- Modify: `test_video_event_labeler.py`，在现有 HTML 合约测试附近新增断点恢复测试。
- Reference: `docs/superpowers/specs/2026-08-24-annotation-resume-design.md`

**Interfaces:**
- Produces assertions for the resume script markers and integration hooks.

- [ ] **Step 1: Write the failing test**

新增 `test_browser_resume_state_is_exposed`，断言 `labeler.HTML` 包含：

```python
self.assertIn('video-labeler:resume:v1', html)
self.assertIn('localStorage.getItem', html)
self.assertIn('JSON.parse', html)
self.assertIn('video_path', html)
self.assertIn('sample_id', html)
self.assertIn('renderList=resumeRenderList', html)
self.assertIn('openRow=resumeOpenRow', html)
self.assertIn('beforeunload', html)
self.assertIn('resume.video_path', html)
self.assertIn('resume.sample_id', html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test_video_event_labeler.LabelerTests.test_browser_resume_state_is_exposed -v`

Expected: FAIL because the resume script and its markers do not exist yet.

### Task 2: 实现最小断点恢复脚本

**Files:**
- Modify: `video_event_labeler.py`，在现有快捷键脚本前后增加独立 resume script，并保持 `_normalize_html` 的核心脚本去重行为。

**Interfaces:**
- Consumes: existing globals `rows`, `current`, `speed`, `renderList`, `openRow`, `$`, `video`.
- Produces: `readResumeState`, `writeResumeState`, `resumeRenderList`, `resumeOpenRow` and event listeners in served HTML.
- Startup ordering: `_normalize_html` removes legacy standalone load calls and inserts the single initial `load();` after all scripts, so the resume wrappers are installed first.

- [ ] **Step 1: Add versioned state reader/writer**

实现 `readResumeState()`：捕获 localStorage/JSON 异常，校验对象、数据集字符串、路径/样本 ID 字符串、筛选器枚举和倍速枚举；不合法返回 `null`。实现 `writeResumeState()`：当前行和数据集有效时写入最小 JSON，写入异常直接返回。

- [ ] **Step 2: Wrap list rendering and row opening**

保存原函数引用。`resumeRenderList()` 第一次执行时比较数据集，匹配则恢复合法筛选器和倍速按钮状态，然后调用原 `renderList`。`resumeOpenRow(index)` 第一次执行时按 `video_path`、再按 `sample_id` 定位保存行，随后调用原 `openRow` 并写入快照；异常/未匹配保持原索引。

- [ ] **Step 3: Hook state-saving events**

为 `filter` 的 `change`、倍速按钮点击和 `beforeunload` 注册 `writeResumeState`；视频切换由 `resumeOpenRow` 覆盖。保留原有事件处理器，不重复执行原逻辑。

- [ ] **Step 4: Run focused test**

Run: `python -m unittest test_video_event_labeler.LabelerTests.test_browser_resume_state_is_exposed -v`

Expected: PASS.

### Task 3: 全量验证与静态检查

**Files:**
- Verify: `video_event_labeler.py`, `test_video_event_labeler.py`, new spec/plan docs.

- [ ] **Step 1: Run all tests**

Run: `python -m unittest -q`

Expected: all tests pass with zero failures/errors.

- [ ] **Step 2: Compile Python sources**

Run: `python -m py_compile video_event_labeler.py test_video_event_labeler.py`

Expected: exit code 0 and no output.

- [ ] **Step 3: Check effective served HTML markers**

Run: `python -B -c "import video_event_labeler as m; h=m.HTML; print(h.count('function renderList('), h.count('async function openRow('), 'video-labeler:resume:v1' in h)"`

Expected: core function counts remain `1 1`, and resume marker is `True`.
