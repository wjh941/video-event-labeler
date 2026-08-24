# 标注效率交互 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 为现有本地视频标注页面增加键盘优先的连续标注操作，不改变后端协议或 CSV 格式。

**Architecture:** 在现有内嵌 HTML 的末尾增加一个独立脚本，使用事件委托跟踪当前事件卡，并调用已有保存、导航、标签和时间格式化函数。快捷键只在页面工作区生效，输入控件和组合键保留浏览器默认行为。

**Tech Stack:** Python 3 standard library, embedded browser-native JavaScript, `unittest`.

## Global Constraints

- 不新增第三方依赖。
- 不改变 CSV、HTTP API 和现有鼠标操作。
- 快捷键只在无输入焦点、无修饰键时生效。
- 所有行为修改先写失败测试，再写最小实现。

### Task 1: 键盘交互契约

**Files:**
- Modify: `test_video_event_labeler.py`
- Test target: `HtmlContractTests`

- [ ] 写失败测试，断言实际 HTML 包含 `S/R/N/P/I/O` 快捷键、标签数字键、焦点保护和待处理导航函数。
- [ ] 运行该测试，确认因脚本尚未存在而失败。

### Task 2: 实现快捷键和时间捕获

**Files:**
- Modify: `video_event_labeler.py`，在 `HTML` 末尾追加浏览器脚本
- Test: `test_video_event_labeler.py`

- [ ] 添加 `focusedEventCard`、`captureShortcutTime`、`moveNeedsTime` 和统一 `keydown` 处理器。
- [ ] 复用 `save(false)`、`save(true)`、`openRow`、`moveVisibleRow`、`setTag`、`eventState`，保存失败时不改变当前行。
- [ ] 对 `input/select/textarea/contenteditable`、修饰键和无当前视频做保护。
- [ ] 运行 HTML 契约测试确认通过。

### Task 3: 回归验证

**Files:**
- Test: `test_video_event_labeler.py`

- [ ] 运行 `python -m unittest -v`。
- [ ] 运行 `python -m py_compile video_event_labeler.py test_video_event_labeler.py`。
- [ ] 检查新增脚本不会重复定义现有核心函数。
