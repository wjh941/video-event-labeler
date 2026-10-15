# Changelog

## 0.3.0（未发布）

### 新增

- CI 回归纳入根目录三份测试脚本（`test_video_event_labeler.py` / `test_person_identity_labeler.py` / `test_integration_workflow.py`，共 80 个用例）：此前不在 pytest `testpaths` 内，CI 从未执行。
- 为两份 `AppState`（事件标注页与人物标注页）补充同源差异评估注释：锁类型、行缓存策略、DB revision 算法（json 载荷哈希 vs 逐条拼接哈希，摘要对外可见、是 HTTP 乐观锁契约的一部分）等，并互指同源。

### 变更

- 流式 SHA-256 收敛为单一实现 `video_labeler/hashing.py` 的 `sha256_file`，根脚本 `video_event_labeler.py`、`video_labeler/media.py`、`video_labeler/storage/csv_adapter.py` 改为复用。
- HH:MM:SS[.sss] 毫秒解析/格式化收敛为单一实现 `video_labeler/timeutil.py`（`parse_time_text` / `format_time_text`），根脚本删除各自的重复实现。
- `CsvConflictError` 收敛进 `video_labeler/web_common.py`，两个根脚本改为导入。

### 修复

- 移除 `probe_media` 解析 `@echo {json}` Windows 测试 shim 的后门：ffprobe 以非零码退出时不再回读可执行文件内容，一律按探测失败处理；相关测试改为在 tmp_path 写真实的 fake ffprobe 脚本（经注入的 `ffprobe_path` 调用，向 stdout 输出 ffprobe 风格 JSON），并新增"非零退出即使 stdout 合法也判 unavailable"的失败路径用例。
- 测试文件显式设置 `no_proxy`/`NO_PROXY` 豁免回环地址（127.0.0.1、localhost），修复系统代理把本地测试服务请求交给代理导致全部超时的问题。
- 文档修正：人物标注页独立启动默认端口为 8765（8865 为组合启动器传入值）；README 测试章节对齐 CI 现状。

## 0.2.0

- SQLite schema version 3 and transactional repository.
- CSV compatibility import/export with deterministic IDs, stale-source detection, backups, and atomic writes.
- Quality validation, statistics, JSONL export, browser resume hooks, and CI checks.
