# 视频标注脚本加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 在保持现有标注契约的情况下，提高单文件视频标注脚本的可靠性和重复操作效率。

**Architecture:** 继续使用 Python 标准库 HTTP 服务和内嵌 HTML。后端由 `AppState` 持有 CSV 快照，所有更新在现有线程锁内完成并刷新快照；视频 Range 处理保持同步流式读取。前端保留一套完整脚本实现，避免重复声明覆盖。

**Tech Stack:** Python 3 standard library, `unittest`, browser-native JavaScript.

## Global Constraints

- 不新增第三方依赖。
- 不改变 `events`、`start_time`、`end_time`、固定标签和备份目录的兼容格式。
- 不改变现有 HTTP 路由和标注工作流。
- 所有行为修改先写失败测试，再写最小实现。

### Task 1: 加固 CSV 读写和状态快照

**Files:**
- Modify: `video_event_labeler.py:218-240,357-385,447-483,511-568`
- Test: `test_video_event_labeler.py`

- [ ] 写测试：重复表头/重复视频路径拒绝；写入调用文件 `flush`/`fsync`；状态读取使用缓存且更新后刷新。
- [ ] 运行目标测试确认失败。
- [ ] 实现字段和视频标识校验；给 `AppState` 增加带锁快照读取和失效刷新；原子写入在替换前同步临时文件并清理失败产物。
- [ ] 运行目标测试和全量测试。

### Task 2: 修复 HTTP Range 边界

**Files:**
- Modify: `video_event_labeler.py:746-778`
- Test: `test_video_event_labeler.py`

- [ ] 写测试：空文件、`bytes=-N`、超出结尾、反向范围和非法范围的响应状态及头部。
- [ ] 运行目标测试确认失败。
- [ ] 实现统一 Range 解析，合法请求返回 `206`，非法请求返回 `416` 和 `Content-Range: bytes */size`；响应体按不超过 `1 MiB` 的块读取，避免整段视频进入内存。
- [ ] 运行目标测试和全量测试。

### Task 3: 收敛前端重复脚本

**Files:**
- Modify: `video_event_labeler.py:623-681`
- Test: `test_video_event_labeler.py`

- [ ] 写 HTML 契约测试，确保核心函数只出现一个定义，且完整实现仍包含事件编辑、循环播放、过滤和审核导航。
- [ ] 运行目标测试确认失败。
- [ ] 删除前半段被后续脚本覆盖的重复实现，保留完整实现并将初始化放在唯一脚本末尾。
- [ ] 运行 HTML 契约测试、全量测试和 Python 编译检查。

### Task 4: 端到端回归验证

**Files:**
- Test: `test_video_event_labeler.py`

- [ ] 运行 `python -m unittest -v`，覆盖外部 CSV 损坏时的 `400` 响应。
- [ ] 运行 `python -m py_compile video_event_labeler.py test_video_event_labeler.py`。
- [ ] 检查 `git status`；当前目录若仍无 Git 仓库，则记录无法提交而不影响文件交付。
